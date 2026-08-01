"""
Duplicate detection and cleanup: hashing files to find identical content,
letting the user pick which copies to delete, and "deleting" by moving to a
hidden, reversible trash folder.

PERFORMANCE (#4): Partial and full content hashes are cached to SQLite via
cache_store.py so unchanged files skip rehashing on subsequent sessions.
"""

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from utils import file_hash, partial_hash, concurrent_hash_all, format_size, unique_target_path, TRASH_DIR_NAME
from menus import confirm_dry_run_then_execute
from mover import perform_move
from undo import save_run_log


def find_duplicates(files: list, max_workers: int = None, size_cache: dict = None):
    """
    Three-stage duplicate search, cheapest filter first:
      1. Group by file size - a size-unique file can't have a duplicate,
         so it's never hashed at all.
      2. Within each size group, hash only the first 8 KB. Most same-size
         files that AREN'T duplicates differ early, so this throws out the
         vast majority of false candidates almost for free.
      3. Only files that still match after step 2 get a full-content hash
         to confirm they're byte-identical.
    Steps 2 and 3 hash multiple files concurrently, since that's where
    most of the wall-clock time goes on large folders.

    If size_cache is provided (from recursive_scan), uses cached sizes
    instead of calling stat() per file — eliminates ~50K redundant syscalls.

    PERFORMANCE (#4): Checks cache_store for previously computed partial and
    full hashes. Only uncached files are hashed, then new results are
    batch-stored to SQLite.

    Returns (duplicate_groups, unreadable_files) - see module docstring.
    """
    by_size = defaultdict(list)
    unreadable = []
    # Also build stat info for cache lookups
    file_stats = {}
    for f in files:
        if size_cache is not None:
            sz = size_cache.get(str(f))
            if sz is None:
                try:
                    st = f.stat()
                    sz = st.st_size
                    file_stats[f] = (st.st_mtime, sz)
                except OSError:
                    unreadable.append(f)
                    continue
                if sz > 0:
                    by_size[sz].append(f)
            else:
                try:
                    file_stats[f] = (f.stat().st_mtime, sz)
                except OSError:
                    pass
                if sz > 0:
                    by_size[sz].append(f)
        else:
            try:
                st = f.stat()
                file_stats[f] = (st.st_mtime, st.st_size)
                by_size[st.st_size].append(f)
            except OSError:
                unreadable.append(f)

    size_groups = [g for size, g in by_size.items() if len(g) > 1 and size > 0]
    stage2_candidates = [f for g in size_groups for f in g]
    if not stage2_candidates:
        return [], unreadable

    # Try cache (#4)
    try:
        import cache_store
        cache_available = cache_store._DB_PATH is not None
    except (ImportError, AttributeError):
        cache_available = False

    # Stage 2: partial hash with cache
    partial_hashes = {}
    uncached_stage2 = []

    if cache_available and file_stats:
        batch_keys = [(str(f), "partial", file_stats[f][0], file_stats[f][1])
                      for f in stage2_candidates if f in file_stats]
        cached_results = cache_store.get_cached_hashes_batch(batch_keys)
        for f in stage2_candidates:
            if f in file_stats:
                key = (str(f), "partial", file_stats[f][0], file_stats[f][1])
                if key in cached_results:
                    partial_hashes[f] = cached_results[key]
                else:
                    uncached_stage2.append(f)
            else:
                uncached_stage2.append(f)
    else:
        uncached_stage2 = stage2_candidates

    new_partial, unreadable_p = concurrent_hash_all(uncached_stage2, partial_hash, max_workers)
    partial_hashes.update(new_partial)
    unreadable.extend(unreadable_p)

    # Batch-store new partial hashes
    if cache_available and new_partial:
        store_entries = []
        for f, h in new_partial.items():
            if f in file_stats:
                mtime, size = file_stats[f]
                store_entries.append((str(f), "partial", mtime, size, h))
        if store_entries:
            cache_store.put_cached_hashes_batch(store_entries)

    # Sub-group within each original size group
    stage3_candidates = []
    for group in size_groups:
        by_partial = defaultdict(list)
        for f in group:
            if f in partial_hashes:
                by_partial[partial_hashes[f]].append(f)
        stage3_candidates.extend(sub for sub in by_partial.values() if len(sub) > 1)

    if not stage3_candidates:
        return [], unreadable

    stage3_flat = [f for sub in stage3_candidates for f in sub]

    # Stage 3: full hash with cache
    full_hashes = {}
    uncached_stage3 = []

    if cache_available and file_stats:
        batch_keys = [(str(f), "full", file_stats[f][0], file_stats[f][1])
                      for f in stage3_flat if f in file_stats]
        cached_results = cache_store.get_cached_hashes_batch(batch_keys)
        for f in stage3_flat:
            if f in file_stats:
                key = (str(f), "full", file_stats[f][0], file_stats[f][1])
                if key in cached_results:
                    full_hashes[f] = cached_results[key]
                else:
                    uncached_stage3.append(f)
            else:
                uncached_stage3.append(f)
    else:
        uncached_stage3 = stage3_flat

    new_full, unreadable_f = concurrent_hash_all(uncached_stage3, file_hash, max_workers)
    full_hashes.update(new_full)
    unreadable.extend(unreadable_f)

    # Batch-store new full hashes
    if cache_available and new_full:
        store_entries = []
        for f, h in new_full.items():
            if f in file_stats:
                mtime, size = file_stats[f]
                store_entries.append((str(f), "full", mtime, size, h))
        if store_entries:
            cache_store.put_cached_hashes_batch(store_entries)

    by_full = defaultdict(list)
    for f, h in full_hashes.items():
        by_full[h].append(f)

    duplicate_groups = [g for g in by_full.values() if len(g) > 1]
    return duplicate_groups, unreadable


def review_duplicate_selection(duplicate_groups: list) -> list:
    """Walk the user through each duplicate set and collect files chosen for deletion."""
    to_delete = []
    for i, group in enumerate(duplicate_groups, start=1):
        try:
            size_label = format_size(group[0].stat().st_size)
        except OSError:
            size_label = "unknown size"
        print(f"\n  Duplicate set {i}/{len(duplicate_groups)}  ({size_label} each):")
        for j, f in enumerate(group, start=1):
            print(f"    {j}. {f}")

        raw = input("    Enter number(s) to DELETE (comma separated), or press Enter to keep all: ").strip()
        if not raw:
            continue

        indices = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(group):
                indices.add(int(part))

        if not indices:
            continue
        if len(indices) >= len(group):
            print("    Can't delete every copy in a set - at least one must stay. Skipping this set.")
            continue

        for idx in indices:
            to_delete.append(group[idx - 1])

    return to_delete


def move_to_trash(files: list, folder: Path, dry_run: bool = False):
    """
    'Delete' files by moving them into a hidden, timestamped trash folder
    instead of erasing them - this keeps deletions reversible via --undo,
    same as every other operation in this tool. You can empty the trash
    folder yourself later once you're confident you don't need it.
    """
    trash_dir = folder / TRASH_DIR_NAME / datetime.now().strftime("%Y%m%d_%H%M%S")

    planned_names = set()
    pairs = []
    for f in files:
        if not f.exists():
            print(f"    Skipped (not found): {f}")
            continue
        target = unique_target_path(trash_dir, f.name, planned_names, dry_run)
        planned_names.add(target.name)
        pairs.append((f, target))

    log_entries = perform_move(
        pairs, dry_run=dry_run,
        on_success=lambda src, dst: print(f"    Moved to trash: {src}"),
    )
    moved = len(pairs) if dry_run else len(log_entries)
    return moved, log_entries


def handle_duplicate_review(duplicate_groups: list, folder: Path):
    """Offer to review and delete (trash) chosen copies from each duplicate set."""
    choice = input("\nReview duplicate sets now and choose which copies to delete? (y/n): ").strip().lower()
    if choice != "y":
        return

    to_delete = review_duplicate_selection(duplicate_groups)
    if not to_delete:
        print("\nNo files selected for deletion.")
        return

    reclaim = sum(f.stat().st_size for f in to_delete if f.exists())
    print(f"\n{len(to_delete)} file(s) selected for deletion (~{format_size(reclaim)} to reclaim).")
    print("(These move to a hidden trash folder, not permanently erased - use --undo to restore them.)")

    log_entries = confirm_dry_run_then_execute(
        lambda dry_run: move_to_trash(to_delete, folder, dry_run=dry_run)[1],
        confirm_msg="Continue and delete (move to trash) these file(s)? (y/n): ",
        cancel_msg="Cancelled. No files were deleted.",
        apply_prompt="\nApply this deletion for real now? (y/n): ",
        no_change_msg="No files were deleted.",
    )
    if log_entries is not None:
        print(f"\n{len(log_entries)} file(s) moved to trash.")
        save_run_log(folder, log_entries)

"""
Duplicate detection and cleanup: hashing files to find identical content,
letting the user pick which copies to delete, and "deleting" by moving to a
hidden, reversible trash folder.
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
    Returns (duplicate_groups, unreadable_files) - see module docstring.
    """
    by_size = defaultdict(list)
    unreadable = []
    for f in files:
        if size_cache is not None:
            sz = size_cache.get(str(f))
            if sz is None:
                try:
                    sz = f.stat().st_size
                except OSError:
                    unreadable.append(f)
                    continue
            if sz > 0:
                by_size[sz].append(f)
        else:
            try:
                by_size[f.stat().st_size].append(f)
            except OSError:
                unreadable.append(f)

    size_groups = [g for size, g in by_size.items() if len(g) > 1 and size > 0]
    stage2_candidates = [f for g in size_groups for f in g]
    if not stage2_candidates:
        return [], unreadable

    partial_hashes, unreadable_p = concurrent_hash_all(stage2_candidates, partial_hash, max_workers)
    unreadable.extend(unreadable_p)

    # Sub-group within each original size group so files of different
    # sizes never get compared against each other.
    stage3_candidates = []
    for group in size_groups:
        by_partial = defaultdict(list)
        for f in group:
            if f in partial_hashes:
                by_partial[partial_hashes[f]].append(f)
        stage3_candidates.extend(sub for sub in by_partial.values() if len(sub) > 1)

    if not stage3_candidates:
        return [], unreadable

    # A full-hash match implies identical bytes, which implies identical
    # size - so it's safe to hash every remaining candidate in one batch
    # and group purely by hash, without re-partitioning by size/partial hash.
    stage3_flat = [f for sub in stage3_candidates for f in sub]
    full_hashes, unreadable_f = concurrent_hash_all(stage3_flat, file_hash, max_workers)
    unreadable.extend(unreadable_f)

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
"""
The core organize-by-type workflow: choosing categories, building move
plans (including the before/after size-and-age separation rules), moving
files with collision/duplicate handling, and fixing misplaced files that
ended up in the wrong category folder.
"""

from collections import defaultdict
from pathlib import Path

from utils import get_file_size_mb, get_file_age_days, file_hash, unique_target_path
from menus import select_multiple, confirm_dry_run_then_execute
from mover import perform_move
from undo import save_run_log


# ---------------------------------------------------------------------------
# Category selection
# ---------------------------------------------------------------------------
def choose_categories(files_by_category: dict, category_map: dict) -> list:
    available = [c for c in files_by_category if c in category_map and files_by_category[c]]
    if files_by_category.get("Others"):
        available.append("Others")

    labels = [f"{c}  ({len(files_by_category[c])} file(s))" for c in available]
    return select_multiple(
        available, labels,
        header="\nWhich categories do you want to organize into folders?",
        prompt="\nEnter numbers separated by commas (e.g. 1,3,4), or 0 for all: ",
    )


def choose_extensions_subset(category: str, files: list) -> list:
    """When the target folder already exists, ask which file types to move now."""
    ext_groups = defaultdict(list)
    for f in files:
        ext_groups[f.suffix.lower() or "(no extension)"].append(f)

    exts = list(ext_groups.keys())
    labels = [f"{ext}  ({len(ext_groups[ext])} file(s))" for ext in exts]
    header = (f"\n  The folder '{category}' already exists.\n"
              f"  Which file type(s) inside '{category}' do you want to move now?")
    chosen_exts = select_multiple(exts, labels, header=header, indent="    ")

    chosen_files = []
    for ext in chosen_exts:
        chosen_files.extend(ext_groups[ext])
    return chosen_files


# ---------------------------------------------------------------------------
# Size / Age separation plans
# ---------------------------------------------------------------------------
def build_pre_plan(files_by_category: dict, folder: Path, rules: dict):
    """
    Build the BEFORE-organizing plan: pulls large and/or old files out of the
    whole scanned set into top-level 'Large Files' / 'General Old' folders.
    If both rules are active, size is applied first so a file that's both
    large and old only ends up in one place (Large Files).
    Returns (plan, moved_set) where moved_set is the set of files claimed,
    so the caller can remove them from category-based organizing.
    """
    remaining = [f for files in files_by_category.values() for f in files]
    plan = []
    moved = set()

    if rules["want_size"]:
        matched = [f for f in remaining if get_file_size_mb(f) >= rules["size_mb"]]
        if matched:
            plan.append(("Large Files", folder / "Large Files", matched))
            moved.update(matched)
        remaining = [f for f in remaining if f not in moved]

    if rules["want_age"]:
        matched = [f for f in remaining if get_file_age_days(f) >= rules["age_days"]]
        if matched:
            plan.append(("General Old", folder / "General Old", matched))
            moved.update(matched)

    return plan, moved


def choose_post_categories(category_plan: list) -> list:
    """Ask which of the organized category folders to also apply size/age rules to."""
    eligible = [name for name, dest, files in category_plan if files or dest.exists()]
    return select_multiple(
        eligible, eligible,
        header="\nWhich category folder(s) do you want to apply large/old separation to?",
    )


def build_post_plan(post_categories: list, category_plan: list, folder: Path, rules: dict):
    """
    Build the AFTER-organizing plan: for each chosen category folder (e.g.
    Videos), creates 'Large Videos' / 'Old Videos' subfolders inside it and
    moves matching files in. Candidates are files already sitting in that
    category folder (from previous runs) plus files this run's category
    plan is about to place there.

    Note: the "eventual" path (destination/filename) is used as the move
    source for this stage, since by the time this stage actually executes,
    the category-organizing stage has already placed the file there. If a
    same-named file causes the category stage to rename on collision, this
    stage's prediction of that filename can be off in that edge case.
    """
    post_plan = []
    for category in post_categories:
        destination = folder / category
        candidates = []
        if destination.exists():
            candidates.extend([p for p in destination.iterdir() if p.is_file()])
        for name, dest, files in category_plan:
            if name == category:
                candidates.extend(files)

        remaining = list(candidates)

        if rules["want_size"]:
            matched = [f for f in remaining if get_file_size_mb(f) >= rules["size_mb"]]
            remaining = [f for f in remaining if f not in matched]
            if matched:
                sub = f"Large {category}"
                entries = [destination / f.name for f in matched]
                post_plan.append((sub, destination / sub, entries))

        if rules["want_age"]:
            matched = [f for f in remaining if get_file_age_days(f) >= rules["age_days"]]
            if matched:
                sub = f"Old {category}"
                entries = [destination / f.name for f in matched]
                post_plan.append((sub, destination / sub, entries))

    return post_plan


# ---------------------------------------------------------------------------
# Plan building / execution
# ---------------------------------------------------------------------------
def build_move_plan(folder: Path, files_by_category: dict, chosen_categories: list,
                     extension_selections: dict = None):
    """
    extension_selections: optional {category: [files...]}. For a category
    whose destination folder already exists, this exact file list is used
    instead of falling back to choose_extensions_subset()'s console
    prompt. The GUI collects this choice through its own dialog and passes
    it in here - console input() and the Tkinter event loop don't mix
    (mixing them crashes with "can't re-enter readline"), so the GUI must
    never let this function reach for stdin. --cli passes nothing and
    keeps the original interactive behavior.
    """
    plan = []
    for category in chosen_categories:
        files = files_by_category.get(category, [])
        if not files:
            continue

        destination = folder / category

        if destination.exists():
            if extension_selections is not None:
                files_to_move = extension_selections.get(category, [])
            else:
                files_to_move = choose_extensions_subset(category, files)
        else:
            files_to_move = files

        plan.append((category, destination, files_to_move))
    return plan


def move_files(files: list, destination: Path, dry_run: bool = False):
    """
    Work out where each file in `files` should land inside `destination`,
    handling name clashes (append ' (1)', ' (2)', ...) and true duplicates
    (same content already at the destination gets skipped, not re-copied),
    then hand the resolved (source, destination) pairs to perform_move().
    Returns (moved_count, duplicate_count, log_entries).
    """
    planned_names = set()
    if destination.exists():
        planned_names.update(p.name for p in destination.iterdir())

    pairs = []
    duplicates = 0

    for f in files:
        if not f.exists():
            # Can legitimately happen in the after-organizing stage if an
            # earlier stage renamed/skipped the file due to a collision.
            print(f"    Skipped (not found - likely moved/renamed earlier this run): {f}")
            continue

        target = destination / f.name
        collision = (dry_run and target.name in planned_names) or (not dry_run and target.exists())

        if collision:
            existing = destination / target.name
            is_duplicate = False
            if existing.exists() and existing.is_file():
                try:
                    is_duplicate = file_hash(f) == file_hash(existing)
                except Exception:
                    is_duplicate = False

            if is_duplicate:
                tag = "[DRY RUN] " if dry_run else ""
                print(f"    {tag}Duplicate skipped: {f.name} (identical file already in {destination.name})")
                duplicates += 1
                continue

            # f.name is already known to collide, so force the " (1)", " (2)",
            # ... search rather than re-checking the un-suffixed name.
            target = unique_target_path(destination, f.name, planned_names | {f.name}, dry_run)

        planned_names.add(target.name)
        pairs.append((f, target))

    log_entries = perform_move(pairs, dry_run=dry_run)
    moved = len(pairs) if dry_run else len(log_entries)
    return moved, duplicates, log_entries


def execute_plan(plan: list, dry_run: bool = False, label: str = None):
    """Run (or preview) a plan of (name, destination, files) tuples."""
    if not plan:
        return []

    if label is None:
        label = "Previewing changes (dry run)..." if dry_run else "Organizing files..."
    print(f"\n{label}")
    print("-" * 40)

    run_log = []
    total_duplicates = 0

    for name, destination, files_to_move in plan:
        if not files_to_move:
            print(f"  Skipped '{name}' (nothing selected).")
            continue

        moved, duplicates, entries = move_files(files_to_move, destination, dry_run=dry_run)
        run_log.extend(entries)
        total_duplicates += duplicates

        verb = "would move" if dry_run else "moved"
        extra = f" ({duplicates} duplicate(s) skipped)" if duplicates else ""
        print(f"  '{name}': {verb} {moved} file(s) -> {destination}{extra}")

    return run_log


def run_all_stages(pre_plan: list, category_plan: list, post_plan: list, dry_run: bool = False):
    """Execute the three stages in the correct dependency order and combine their logs."""
    run_log = []
    run_log += execute_plan(pre_plan, dry_run=dry_run,
                             label="Applying size/age rules (before organizing)...")
    run_log += execute_plan(category_plan, dry_run=dry_run,
                             label="Organizing by type...")
    run_log += execute_plan(post_plan, dry_run=dry_run,
                             label="Applying size/age rules (after organizing)...")

    if dry_run:
        print("\nDry run complete. No files were actually moved.")
    else:
        print("\nDone. Files not selected/matched were left in place.")

    return run_log


# ---------------------------------------------------------------------------
# Mismatch fix
# ---------------------------------------------------------------------------
def find_mismatched_files(folder: Path, category_map: dict, ext_to_category: dict):
    """
    Check the direct contents of existing category folders (Images, Videos,
    ...) for files whose extension doesn't belong there (e.g. a .docx sitting
    inside Images). Only looks at each category folder's direct children, not
    nested subfolders like 'Videos/Large Videos' (those are expected to hold
    that category's files by design).
    Returns {current_folder_name: [(file, correct_category), ...]}.
    """
    mismatches = defaultdict(list)
    folder_names = list(category_map.keys()) + ["Others"]

    for name in folder_names:
        cat_dir = folder / name
        if not cat_dir.is_dir():
            continue
        for item in cat_dir.iterdir():
            if not item.is_file():
                continue
            correct_category = ext_to_category.get(item.suffix.lower(), "Others")
            if correct_category != name:
                mismatches[name].append((item, correct_category))

    return mismatches


def handle_mismatch_fix(mismatches: dict, folder: Path):
    """Offer to move misplaced files out of category folders they don't belong in."""
    total = sum(len(v) for v in mismatches.values())
    print(f"\nFound {total} misplaced file(s) inside existing category folders:")
    for folder_name, items in mismatches.items():
        for f, correct in items:
            print(f"    {f.name}  is in '{folder_name}'  but belongs in '{correct}'")

    choice = input("\nMove these files to their correct folders now? (y/n): ").strip().lower()
    if choice != "y":
        print("Left as-is.")
        return

    grouped = defaultdict(list)
    for folder_name, items in mismatches.items():
        for f, correct in items:
            grouped[correct].append(f)
    plan = [(name, folder / name, files) for name, files in grouped.items()]

    run_log = confirm_dry_run_then_execute(
        lambda dry_run: execute_plan(
            plan, dry_run=dry_run,
            label="Fixing misplaced files (preview)..." if dry_run else "Fixing misplaced files..."),
        confirm_msg="Continue and move these file(s) now? (y/n): ",
        cancel_msg="Cancelled.",
        apply_prompt="\nApply this for real now? (y/n): ",
        no_change_msg="No files were moved.",
    )
    if run_log is not None:
        save_run_log(folder, run_log)
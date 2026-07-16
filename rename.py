"""
Admin-only bulk rename: pick a category and files, pick a rename operation
(remove/replace text, add prefix/suffix), preview, then apply.
"""

from pathlib import Path

from menus import select_multiple, confirm_dry_run_then_execute
from mover import perform_move
from undo import save_run_log


def choose_rename_source(files_by_category: dict) -> list:
    """Admin picks a category, then which specific files within it to rename."""
    available = [c for c, files in files_by_category.items() if files]
    if not available:
        print("  Nothing available to rename.")
        return []

    print("\n  Which category do you want to bulk-rename files in?")
    for i, c in enumerate(available, start=1):
        print(f"    {i}. {c}  ({len(files_by_category[c])} file(s))")
    raw = input("  Enter a number: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(available)):
        print("  Invalid selection.")
        return []

    category = available[int(raw) - 1]
    files = files_by_category[category]

    labels = [f"{f.name}   ({f.parent})" for f in files]
    return select_multiple(
        files, labels,
        header=f"\n  Files in '{category}':",
        prompt="  Enter number(s) to select (comma separated), or 0 for all: ",
        indent="    ",
    )


def ask_rename_operation():
    """Returns a tuple describing the rename rule, e.g. ('remove', 'image-')."""
    print("\n  Choose a rename operation:")
    print("    1. Remove text from filenames        e.g. 'image-dogs.jpg' -> 'dogs.jpg'")
    print("    2. Replace text in filenames          e.g. 'image-' -> 'pic_'")
    print("    3. Add a prefix")
    print("    4. Add a suffix (before the extension)")
    choice = input("  Enter 1-4: ").strip()

    if choice == "1":
        text = input("  Text to remove (case-sensitive): ")
        return ("remove", text) if text else None
    if choice == "2":
        old = input("  Text to find: ")
        new = input("  Replace with (leave blank to remove it): ")
        return ("replace", old, new) if old else None
    if choice == "3":
        prefix = input("  Prefix to add: ")
        return ("prefix", prefix) if prefix else None
    if choice == "4":
        suffix = input("  Suffix to add: ")
        return ("suffix", suffix) if suffix else None

    print("  Invalid choice.")
    return None


def apply_rename_rule(stem: str, rule: tuple) -> str:
    """Apply the rename rule to a filename stem (without extension)."""
    op = rule[0]
    if op == "remove":
        return stem.replace(rule[1], "")
    if op == "replace":
        return stem.replace(rule[1], rule[2])
    if op == "prefix":
        return rule[1] + stem
    if op == "suffix":
        return stem + rule[1]
    return stem


def build_rename_plan(files: list, rule: tuple) -> list:
    """
    Returns a list of (old_path, new_path) tuples. Handles collisions - both
    against other untouched files already in the folder and against other
    files being renamed in this same batch - by appending ' (1)', ' (2)', etc.
    """
    plan = []
    reserved_by_dir = {}

    for f in files:
        parent = f.parent
        if parent not in reserved_by_dir:
            reserved_by_dir[parent] = set(p.name for p in parent.iterdir()) if parent.exists() else set()

        new_stem = apply_rename_rule(f.stem, rule).strip()
        if not new_stem:
            new_stem = f.stem  # never produce a blank filename - fall back to the original
        new_name = f"{new_stem}{f.suffix}"

        candidate = new_name
        counter = 1
        while candidate in reserved_by_dir[parent] and candidate != f.name:
            candidate = f"{new_stem} ({counter}){f.suffix}"
            counter += 1

        reserved_by_dir[parent].add(candidate)
        plan.append((f, parent / candidate))

    return plan


def execute_rename_plan(plan: list, dry_run: bool = False) -> list:
    """Renames files (a move within the same directory). Returns log entries for undo."""
    pairs = [(old, new) for old, new in plan if old.name != new.name]

    log_entries = perform_move(pairs, dry_run=dry_run)
    renamed = len(pairs) if dry_run else len(log_entries)

    verb = "would rename" if dry_run else "renamed"
    print(f"\n  {renamed} file(s) {verb}.")
    return log_entries


def handle_bulk_rename(files_by_category: dict, folder: Path):
    print("\n" + "=" * 50)
    print("  ADMIN: BULK RENAME")
    print("=" * 50)

    selected_files = choose_rename_source(files_by_category)
    if not selected_files:
        return

    rule = ask_rename_operation()
    if not rule:
        print("  No valid operation chosen.")
        return

    plan = build_rename_plan(selected_files, rule)
    changed = [(old, new) for old, new in plan if old.name != new.name]

    print("\n  Preview:")
    for old, new in changed:
        print(f"    {old.name}  ->  {new.name}")
    unchanged_count = len(plan) - len(changed)
    if unchanged_count:
        print(f"    ({unchanged_count} file(s) unchanged by this rule - skipped)")

    if not changed:
        print("\n  Nothing would change with this rule.")
        return

    log_entries = confirm_dry_run_then_execute(
        lambda dry_run: execute_rename_plan(plan, dry_run=dry_run),
        confirm_msg="  Continue and rename these file(s) now? (y/n): ",
        cancel_msg="  Cancelled.",
        dry_run_prompt="\n  Preview as a dry run first? (y/n): ",
        apply_prompt="\n  Apply this rename for real now? (y/n): ",
        no_change_msg="  No files were renamed.",
    )
    if log_entries is not None:
        save_run_log(folder, log_entries)
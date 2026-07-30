#!/usr/bin/env python3
"""
File Manager App
-----------------
A CLI tool that scans a folder, shows what file types live there, and lets
you sort files into extension-based folders (Images, Videos, Compressed, etc).

By default this launches a graphical, keyboard-navigable app: a folder
browser, then a menu of buttons for Storage Analyzer, Organize, Duplicate
Cleaner (with duplicate/similar images shown as thumbnails right in the
window - click one to open it full-size, then use Left/Right to browse
the rest), Rename, Manage Categories, Undo History (lists every past run,
not just the latest, so you can pick which one to restore), and a Recycle
Bin (Trash / Restore / Delete permanently / Empty trash) instead of a
hidden trash folder you'd otherwise have to dig through by hand.

Pass --cli to use the classic plain-text menu instead:
  [1] Overview            - counts/sizes by type and category, duplicate summary
  [2] Organize            - fix misplaced files, then sort loose files by type
                             (with optional size/age separation rules)
  [3] Duplicate Cleaner   - find identical-content files, review and trash copies
  [4] Rename (Admin)      - bulk find/replace, prefix/suffix across filenames
  [5] Manage Categories   - add/edit/remove config.json categories, no hand-editing
  [6] Storage Analyzer    - bar chart of disk usage by category
  [7] Undo History        - list past runs and pick which one to restore
  [8] Recycle Bin         - Trash / Restore / Delete permanently / Empty trash
  [0] Exit

Every action that touches disk offers a dry-run preview first, and every
real move is logged so it can be undone. config.json (next to this script,
or via --config) customizes categories, exclusion patterns, and the admin
PIN for bulk rename - see the note below.

Setting up the admin PIN: add "admin_pin": "your-pin-here" to config.json,
then choose Rename (you'll be prompted for the PIN - hidden as you type in
--cli mode, masked in an entry box in the GUI). Note this is a lightweight
local deterrent against accidental use, not real security - anyone with
file access to config.json can read or change the PIN.

Run it with:       python file_manager.py                 (GUI)
Text menu:         python file_manager.py --cli
Undo with:         python file_manager.py --undo --path /some/folder

Platform notes: the GUI (Tkinter) runs out of the box on Windows, macOS,
and Linux - no extra install beyond Python itself (Pillow is optional, for
duplicate-image thumbnails: pip install Pillow). Tkinter does not run on
Android; on Android (e.g. inside Termux) use --cli, which is plain Python
and works anywhere.

Project layout:
  file_manager.py   Main entry point (this file) - CLI args and the text menu loop.
  gui.py            The graphical app (default) - folder browser, buttons, progress
                     bars, keyboard navigation, and inline duplicate-image thumbnails.
  config.py         Loading/saving config.json and merging it with the built-in categories.
  categories.py     Interactive category editor (Manage Categories menu option).
  scanner.py        Top-level and recursive folder scanning.
  overview.py       The folder overview panel.
  storage_analyzer.py  Per-category disk usage + bar-chart rendering.
  duplicates.py     Content-based duplicate detection and trash review.
  image_duplicates.py  Perceptual (visual) duplicate detection for images.
  organizer.py      Category selection, move-plan building/execution, mismatch fixing.
  rename.py         Admin-only bulk rename feature.
  mover.py          Shared move-execution engine used by organizer/duplicates/rename.
  undo.py           The move log, run history, and undo system.
  menus.py          Generic, reusable interactive prompts (--cli mode).
  utils.py          Shared constants and stateless helpers (sizes, hashing, filenames).
"""

import argparse
from pathlib import Path

from utils import SCRIPT_PATH, DEFAULT_CONFIG_NAME, format_size
from config import load_config, build_ext_to_category
from categories import edit_categories
from scanner import get_target_folder, scan_folder, recursive_scan, bucket_files
from overview import print_overview
from storage_analyzer import compute_storage_usage, print_storage_analyzer
from menus import verify_admin_access, ask_separation_rules, ask_continue_after_pre, confirm_dry_run_then_execute
from duplicates import find_duplicates, handle_duplicate_review
from image_duplicates import find_similar_images, handle_similar_image_review, ask_similarity_threshold
from organizer import (
    choose_categories, build_pre_plan, choose_post_categories, build_post_plan,
    build_move_plan, run_all_stages, find_mismatched_files, handle_mismatch_fix,
)
from rename import handle_bulk_rename
from undo import save_run_log, undo_last_run, list_run_logs, restore_run
from recycle_bin import list_trash_items, restore_item, delete_item_permanently, empty_trash


def parse_args():
    parser = argparse.ArgumentParser(description="Organize a folder's files by type.")
    parser.add_argument("--cli", action="store_true",
                         help="Use the classic plain-text menu instead of the graphical app.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Force every action this run to preview as a dry run first.")
    parser.add_argument("--path", type=str, default=None,
                         help="Folder to organize. If omitted, you'll be prompted for it.")
    parser.add_argument("--config", type=str, default=None,
                         help="Path to a config.json. Defaults to config.json next to this script.")
    parser.add_argument("--undo", action="store_true",
                         help="Undo the most recent run in the target folder and exit.")
    parser.add_argument("--view-only", action="store_true",
                         help="Just show the overview panel and exit - no menu, no changes.")
    parser.add_argument("--admin", action="store_true",
                         help="Authenticate as admin at startup (otherwise you're prompted when you pick Rename).")
    return parser.parse_args()


def resolve_folder(args) -> Path:
    if args.path:
        folder = Path(args.path).expanduser().resolve()
        if not folder.is_dir():
            print(f"  '{folder}' is not a valid folder.")
            return None
        return folder
    return get_target_folder()


# ---------------------------------------------------------------------------
# Menu actions - each re-scans fresh, so nothing here relies on stale data
# from an earlier action in the same session.
# ---------------------------------------------------------------------------
def run_overview(folder: Path, ext_to_category: dict, exclude_patterns: list):
    print("\n  Scanning folder recursively for the overview (this may take a moment)...")
    all_files, excluded_count, size_cache = recursive_scan(folder, exclude_patterns)
    if not all_files:
        print_overview(folder, {}, {}, {}, excluded_count, [])
        return
    files_by_category, ext_counts, ext_sizes, _ = bucket_files(all_files, ext_to_category, size_cache)
    duplicate_groups, unreadable_files = find_duplicates(all_files, size_cache=size_cache)
    print_overview(folder, ext_counts, ext_sizes, files_by_category, excluded_count,
                   duplicate_groups, unreadable_files, size_cache=size_cache)


def run_organize(folder: Path, category_map: dict, ext_to_category: dict,
                  exclude_patterns: list, force_dry_run: bool):
    # Fix misplaced files first, so type-organizing works from a clean slate.
    mismatches = find_mismatched_files(folder, category_map, ext_to_category)
    if mismatches:
        handle_mismatch_fix(mismatches, folder)

    files_by_category, ext_counts, ext_sizes, excluded_count = scan_folder(folder, ext_to_category, exclude_patterns)
    if not ext_counts:
        print("\n  No loose files at the top level to organize by type.")
        return

    rules = ask_separation_rules()

    pre_plan = []
    stop_after_pre = False
    if rules and rules["timing"] == "before":
        pre_plan, moved_set = build_pre_plan(files_by_category, folder, rules)
        if pre_plan:
            for cat in list(files_by_category.keys()):
                files_by_category[cat] = [f for f in files_by_category[cat] if f not in moved_set]
                if not files_by_category[cat]:
                    del files_by_category[cat]
            total_pre = sum(len(files) for _, _, files in pre_plan)
            print(f"\n{total_pre} file(s) set aside before type-organizing:")
            for name, dest, files in pre_plan:
                print(f"  {name}: {len(files)} file(s)")
            stop_after_pre = ask_continue_after_pre()
        else:
            print("\nNo files matched the size/age thresholds - nothing set aside.")

    chosen_categories = [] if stop_after_pre else choose_categories(files_by_category, category_map)
    if not chosen_categories and not pre_plan:
        print("\nNothing selected. No changes made.")
        return

    category_plan = build_move_plan(folder, files_by_category, chosen_categories)

    post_plan = []
    if rules and rules["timing"] == "after":
        if category_plan:
            post_categories = choose_post_categories(category_plan)
            if post_categories:
                post_plan = build_post_plan(post_categories, category_plan, folder, rules)
        else:
            print("\nNo categories were organized, so there's nothing to apply after-organizing rules to.")

    run_log = confirm_dry_run_then_execute(
        lambda dry_run: run_all_stages(pre_plan, category_plan, post_plan, dry_run=dry_run),
        confirm_msg=f"\nThis will reorganize files inside '{folder}'. Continue? (y/n): ",
        cancel_msg="Cancelled. No files were moved.",
        force_dry_run_first=True if force_dry_run else None,
        dry_run_prompt="\nRun as a dry run first (preview only, no files moved)? (y/n): ",
        no_change_msg="No files were moved.",
    )
    if run_log is not None:
        save_run_log(folder, run_log)


def run_duplicate_cleaner(folder: Path, exclude_patterns: list):
    print("\n  Scanning folder recursively for duplicates (this may take a moment)...")
    all_files, _, size_cache = recursive_scan(folder, exclude_patterns)
    duplicate_groups, unreadable_files = find_duplicates(all_files, size_cache=size_cache)

    if not duplicate_groups:
        print("\n  No exact (byte-identical) duplicate files found.")
        if unreadable_files:
            print(f"  ({len(unreadable_files)} file(s) could not be checked - locked or permission denied.)")
    else:
        extra_copies = sum(len(g) - 1 for g in duplicate_groups)
        print(f"\n  Found {len(duplicate_groups)} duplicate set(s) ({extra_copies} extra copy/copies).")
        handle_duplicate_review(duplicate_groups, folder)

    choice = input(
        "\n  Also scan for similar-LOOKING images - e.g. a resized, re-saved, or\n"
        "  lightly edited copy that isn't byte-identical? (y/n): "
    ).strip().lower()
    if choice != "y":
        return

    threshold = ask_similarity_threshold()
    print("\n  Scanning images for visual similarity (this may take a moment)...")
    similar_groups, unreadable_images, unavailable = find_similar_images(all_files, threshold=threshold)

    if unavailable:
        print("\n  This feature needs the Pillow library - install it with: pip install Pillow")
        return

    if not similar_groups:
        print("\n  No similar-looking images found.")
        if unreadable_images:
            print(f"  ({len(unreadable_images)} image(s) could not be checked - corrupt or unreadable.)")
        return

    print(f"\n  Found {len(similar_groups)} set(s) of similar-looking images.")
    handle_similar_image_review(similar_groups, folder)


def run_storage_analyzer(folder: Path, ext_to_category: dict, exclude_patterns: list):
    print("\n  Scanning folder recursively for the storage analyzer (this may take a moment)...")
    all_files, _, size_cache = recursive_scan(folder, exclude_patterns)
    files_by_category, _, _, _ = bucket_files(all_files, ext_to_category, size_cache)
    print_storage_analyzer(compute_storage_usage(files_by_category))


def run_undo_history(folder: Path):
    runs = list_run_logs(folder)
    if not runs:
        print(f"\n  No previous runs found in '{folder}'.")
        return

    print("\n  History:")
    print("  " + "-" * 44)
    for i, r in enumerate(runs, start=1):
        print(f"    Run #{i}   {r['label']}   ({r['count']} file(s) moved)")

    raw = input("\n  Enter a run number to restore, or press Enter to cancel: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(runs)):
        print("  Cancelled.")
        return

    run = runs[int(raw) - 1]
    confirm = input(f"  Restore {run['count']} file(s) from Run #{raw} ({run['label']})? (y/n): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    restored, total = restore_run(
        run["path"],
        on_restore=lambda dst, src: print(f"    Restored: {dst.name} -> {src}"),
        on_skip=lambda p, reason: print(f"    Skipped ({reason}): {p}"),
    )
    print(f"\n  Restored {restored}/{total} file(s).")


def run_recycle_bin(folder: Path):
    items = list_trash_items(folder)
    if not items:
        print("\n  Trash is empty.")
        return

    print("\n  Trash:")
    print("  " + "-" * 44)
    for i, item in enumerate(items, start=1):
        origin = item["original_source"] or "(unknown)"
        print(f"    {i}. {item['name']}   ({format_size(item['size'])})   from: {origin}")

    print("\n  1. Restore file(s)")
    print("  2. Delete file(s) permanently")
    print("  3. Empty trash (delete everything, permanently)")
    print("  4. Back")
    choice = input("  Enter 1-4: ").strip()

    if choice in ("1", "2"):
        raw = input("  Enter number(s) to select (comma separated): ").strip()
        indices = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(items):
                indices.add(int(part))
        if not indices:
            print("  Nothing selected.")
            return
        selected = [items[i - 1] for i in indices]

        if choice == "1":
            for item in selected:
                ok, msg = restore_item(item)
                print(f"    {item['name']}: {msg}")
        else:
            confirm = input(f"  Permanently delete {len(selected)} file(s)? This CANNOT be undone. (y/n): ").strip().lower()
            if confirm != "y":
                print("  Cancelled.")
                return
            for item in selected:
                ok, msg = delete_item_permanently(item)
                print(f"    {item['name']}: {msg}")

    elif choice == "3":
        confirm = input(f"  Permanently delete ALL {len(items)} file(s) in the trash? This CANNOT be undone. (y/n): ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return
        count = empty_trash(folder)
        print(f"  Emptied trash - {count} file(s) deleted permanently.")

    else:
        print("  Cancelled.")


def run_rename(folder: Path, ext_to_category: dict, exclude_patterns: list,
                admin_pin, admin_mode: bool) -> bool:
    """Returns the (possibly newly granted) admin_mode for the rest of the session."""
    if not admin_mode:
        admin_mode = verify_admin_access(admin_pin)
        if not admin_mode:
            return False

    all_files, _, _ = recursive_scan(folder, exclude_patterns)
    files_by_category, _, _, _ = bucket_files(all_files, ext_to_category)
    handle_bulk_rename(files_by_category, folder)
    return admin_mode


def show_main_menu(admin_mode: bool) -> str:
    print("\n" + "=" * 50)
    print("  FILE MANAGER")
    print("=" * 50)
    print("  [1] Overview")
    print("  [2] Organize")
    print("  [3] Duplicate Cleaner")
    print(f"  [4] Rename{'' if admin_mode else ' (Admin - PIN required)'}")
    print("  [5] Manage Categories")
    print("  [6] Storage Analyzer")
    print("  [7] Undo History")
    print("  [8] Recycle Bin")
    print("  [0] Exit")
    return input("  Enter 1-0: ").strip()


def main():
    args = parse_args()

    if args.undo:
        folder = resolve_folder(args)
        if folder:
            undo_last_run(folder)
        return

    config_path = Path(args.config).expanduser().resolve() if args.config else (SCRIPT_PATH.parent / DEFAULT_CONFIG_NAME)

    if args.view_only:
        category_map, exclude_patterns, admin_pin = load_config(config_path)
        ext_to_category = build_ext_to_category(category_map)
        folder = resolve_folder(args)
        if not folder:
            return
        run_overview(folder, ext_to_category, exclude_patterns)
        print("\n(--view-only: showing the overview only, no changes made.)")
        return

    if not args.cli:
        try:
            # This imports your updated Eel launch_gui function
            from gui import launch_gui
            initial_folder = None
            if args.path:
                initial_folder = Path(args.path).expanduser().resolve()
                if not initial_folder.is_dir():
                    print(f"  '{initial_folder}' is not a valid folder.")
                    initial_folder = None
            
            # Start the Web-Desktop Window
            launch_gui(config_path, initial_folder)
            return
        except Exception as e:
            print(f"  Could not start the Web GUI ({e}). Falling back to the --cli text menu.")
    
    print("=" * 50)
    print("   FILE MANAGER - organize your folder by type")
    print("=" * 50)

    category_map, exclude_patterns, admin_pin = load_config(config_path)
    ext_to_category = build_ext_to_category(category_map)

    admin_mode = verify_admin_access(admin_pin) if args.admin else False

    folder = resolve_folder(args)
    if not folder:
        return

    while True:
        choice = show_main_menu(admin_mode)

        if choice == "1":
            run_overview(folder, ext_to_category, exclude_patterns)
        elif choice == "2":
            run_organize(folder, category_map, ext_to_category, exclude_patterns, args.dry_run)
        elif choice == "3":
            run_duplicate_cleaner(folder, exclude_patterns)
        elif choice == "4":
            admin_mode = run_rename(folder, ext_to_category, exclude_patterns, admin_pin, admin_mode)
        elif choice == "5":
            category_map = edit_categories(config_path, category_map)
            ext_to_category = build_ext_to_category(category_map)
        elif choice == "6":
            run_storage_analyzer(folder, ext_to_category, exclude_patterns)
        elif choice == "7":
            run_undo_history(folder)
        elif choice == "8":
            run_recycle_bin(folder)
        elif choice == "0":
            print("\n  Goodbye.")
            return
        else:
            print("  Invalid choice - enter a number from 0 to 8.")


if __name__ == "__main__":
    import multiprocessing
    # MUST be the first executable line: without this, PyInstaller-frozen builds
    # re-execute the app in every ProcessPoolExecutor worker, causing a fork-bomb
    # or RuntimeError on Windows when "Similar Images" is first triggered.
    multiprocessing.freeze_support()
    main()
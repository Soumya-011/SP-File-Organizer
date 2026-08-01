#!/usr/bin/env python3
"""
Organization endpoints — bulk organize, separate organize, size/age separation,
mismatch fixing, empty folder cleanup, and organize preview.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import eel

from utils import format_size
from scanner import scan_folder
from organizer import (
    build_move_plan, move_files, build_pre_plan, build_post_plan,
    execute_plan, find_mismatched_files
)
from undo import save_run_log
from gui_state import (
    APP_STATE, _push_ui_progress, clear_cache,
    _get_all_folders, _filter_safe_paths, get_cached_scans,
)


@eel.expose
def get_organize_preview(chosen_categories):
    """Return the list of files that will be moved for each category."""
    folders = _get_all_folders()
    merged_loose = defaultdict(list)
    for f in folders:
        loose, _, _, _ = scan_folder(f, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
        for cat, files in loose.items():
            merged_loose[cat].extend(files)

    preview = []
    for cat in chosen_categories:
        files = merged_loose.get(cat, [])
        for fp in files:
            try:
                preview.append({
                    "category": cat,
                    "file_name": fp.name,
                    "source_folder": str(fp.parent),
                    "file_size": format_size(fp.stat().st_size),
                })
            except OSError:
                preview.append({
                    "category": cat,
                    "file_name": fp.name,
                    "source_folder": str(fp.parent),
                    "file_size": "N/A",
                })
    return {"status": "success", "entries": preview, "total": len(preview)}


@eel.expose
def trigger_bulk_organization(chosen_categories, destination_folder_str=None):
    """Organize files into the chosen destination folder."""
    if destination_folder_str:
        destination_folder = Path(destination_folder_str)
        if not destination_folder.is_dir():
            return {"status": "error", "message": "Destination folder does not exist."}
    else:
        destination_folder = APP_STATE["folder"]
        if not destination_folder or not destination_folder.is_dir():
            return {"status": "error", "message": "No workspace folder selected"}

    # Gather ALL loose files from every active folder
    folders = _get_all_folders()
    merged_loose = defaultdict(list)
    for f in folders:
        loose, _, _, _ = scan_folder(f, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
        for cat, files in loose.items():
            merged_loose[cat].extend(files)

    extension_selections = {}
    for category in chosen_categories:
        extension_selections[category] = merged_loose.get(category, [])

    plan = build_move_plan(destination_folder, merged_loose, chosen_categories, extension_selections=extension_selections)

    # Count total files for progress tracking
    total_files = sum(len(files) for _, _, files in plan)
    done_files = 0
    run_log = []

    for idx, (name, dest, files) in enumerate(plan):
        _push_ui_progress(f"Moving category '{name}' ({idx+1}/{len(plan)})...", done_files, total_files)
        moved, _, entries = move_files(files, dest, dry_run=False)
        done_files += moved
        run_log.extend(entries)
        _push_ui_progress(f"Moved '{name}' ({done_files}/{total_files} files)", done_files, total_files)

    if run_log:
        save_run_log(destination_folder, run_log)
        clear_cache()

    return {"status": "success", "moved": len(run_log)}


@eel.expose
def trigger_separate_organization(folder_categories_map):
    """Organize files SEPARATELY — each folder's loose files go into that folder's
    own subfolders."""
    total_moved = 0
    all_folders = list(folder_categories_map.items())
    for fi, (folder_str, chosen_categories) in enumerate(all_folders):
        folder = Path(folder_str)
        if not folder.is_dir():
            continue
        _push_ui_progress(f"Scanning folder {fi+1}/{len(all_folders)}...", total_moved, -1)
        loose, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])

        extension_selections = {}
        for category in chosen_categories:
            extension_selections[category] = loose.get(category, [])

        plan = build_move_plan(folder, loose, chosen_categories, extension_selections=extension_selections)
        run_log = []
        for name, dest, files in plan:
            _, _, entries = move_files(files, dest, dry_run=False)
            run_log.extend(entries)
            total_moved += len(entries)
            _push_ui_progress(f"Moved '{name}' in {folder.name}...", total_moved, -1)

        if run_log:
            save_run_log(folder, run_log)

    if total_moved > 0:
        clear_cache()

    return {"status": "success", "moved": total_moved}


@eel.expose
def trigger_separation_organization(rule_type, timing, limit_value):
    """Size/age-based separation (before or after organizing)."""
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"status": "error", "message": "No workspace folder selected"}
    val = float(limit_value)
    rules = {
        "timing": timing, "want_size": (rule_type == "size"), "want_age": (rule_type == "age"),
        "size_mb": val if rule_type == "size" else None, "age_days": val if rule_type == "age" else None
    }

    _push_ui_progress(f"Computing {rule_type} separation rules...", 0, -1)
    _, _, loose_files_by_category = get_cached_scans()
    run_log = []

    if timing == "before":
        plan, _ = build_pre_plan(loose_files_by_category, folder, rules)
        _push_ui_progress(f"Executing Pre-Separation ({rule_type})...", 0, -1)
        run_log = execute_plan(plan, dry_run=False, label="Pre-Separation")
    else:
        all_categories = list(APP_STATE["category_map"].keys()) + ["Others"]
        mock_category_plan = [(cat, folder / cat, loose_files_by_category.get(cat, [])) for cat in all_categories]
        plan = build_post_plan(all_categories, mock_category_plan, folder, rules)
        _push_ui_progress(f"Executing Post-Separation ({rule_type})...", 0, -1)
        run_log = execute_plan(plan, dry_run=False, label="Post-Separation")

    if run_log:
        save_run_log(folder, run_log)
        clear_cache()

    return {"status": "success", "moved": len(run_log)}


@eel.expose
def fix_mismatched_files(selected_targets=None):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return 0
    mismatches = find_mismatched_files(folder, APP_STATE["category_map"], APP_STATE["ext_to_category"])
    grouped = {}
    for folder_name, items in mismatches.items():
        for f, correct in items:
            if selected_targets is None or correct in selected_targets:
                grouped.setdefault(correct, []).append(f)

    if not grouped: return 0
    plan = [(name, folder / name, files) for name, files in grouped.items()]
    run_log = execute_plan(plan, dry_run=False, label="Fixing misplaced files")
    if run_log:
        save_run_log(folder, run_log)
        clear_cache()
    return len(run_log)


@eel.expose
def get_empty_folders_data():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return []

    empty_list = []
    empty_paths_set = set()

    for root, dirs, files in os.walk(folder, topdown=False):
        current_dir = Path(root)
        if current_dir == folder: continue
        if any(internal in current_dir.parts for internal in [".file_manager_logs", ".file_manager_trash"]): continue

        try:
            is_empty = True
            for item in current_dir.iterdir():
                if item.is_file():
                    is_empty = False
                    break
                if item.is_dir() and str(item) not in empty_paths_set:
                    is_empty = False
                    break

            if is_empty:
                empty_paths_set.add(str(current_dir))
                empty_list.append({
                    "name": current_dir.name,
                    "path": str(current_dir),
                    "rel_path": str(current_dir.relative_to(folder))
                })
        except OSError:
            pass

    return empty_list


@eel.expose
def purge_selected_empty_folders(folder_paths):
    """Move selected empty folders to trash (undoable)."""
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"status": "error", "message": "No workspace folder"}

    paths_to_remove = [Path(p) for p in _filter_safe_paths(folder_paths, folder)]
    paths_to_remove.sort(key=lambda p: len(p.parts))

    batch_name = f"batch_vacuum_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    trash_dir = folder / ".file_manager_trash" / batch_name

    log_entries = []
    moved = 0

    for p in paths_to_remove:
        if not p.exists(): continue
        trash_dir.mkdir(parents=True, exist_ok=True)

        dst = trash_dir / p.name
        counter = 1
        while dst.exists():
            dst = trash_dir / f"{p.name}_{counter}"
            counter += 1

        try:
            shutil.move(str(p), str(dst))
            log_entries.append({"source": str(p), "destination": str(dst)})
            moved += 1
        except Exception:
            pass

    if log_entries:
        save_run_log(folder, log_entries)
        clear_cache()

    return {"status": "success", "purged": moved}


@eel.expose
def delete_empty_folders_permanently(folder_paths):
    """Permanently delete empty folders (bypass trash — no undo)."""
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"status": "error", "message": "No workspace folder"}

    paths_to_remove = [Path(p) for p in _filter_safe_paths(folder_paths, folder)]
    paths_to_remove.sort(key=lambda p: len(p.parts), reverse=True)  # deepest first

    deleted = 0
    failed = 0

    for p in paths_to_remove:
        if not p.exists(): continue
        try:
            if any(item.exists() for item in p.iterdir()):
                failed += 1
                continue
            shutil.rmtree(str(p))
            deleted += 1
        except Exception:
            failed += 1

    if deleted > 0:
        clear_cache()

    return {"status": "success", "deleted": deleted, "failed": failed}


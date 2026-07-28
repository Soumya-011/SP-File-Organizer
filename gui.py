#!/usr/bin/env python3
"""
Eel Web-Desktop GUI Engine Wrapper for File Manager.
Binds native business logic algorithms directly to WebGL/DOM view layers.
"""

import os
import sys
import json
import base64
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import eel
import tkinter as tk
from tkinter import filedialog

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Core Engine Imports
from utils import format_size, get_file_size_mb, get_file_age_days
from config import load_config, build_ext_to_category, load_raw_config, save_raw_config, DEFAULT_CATEGORY_MAP
from categories import normalize_extensions
from scanner import scan_folder, recursive_scan, bucket_files
import cache_store
from storage_analyzer import compute_storage_usage
from duplicates import find_duplicates, move_to_trash
from image_duplicates import find_similar_images, is_image_file
from organizer import (
    build_move_plan, move_files, build_pre_plan, build_post_plan, 
    execute_plan, find_mismatched_files
)
from undo import list_run_logs, restore_run, save_run_log
from recycle_bin import list_trash_items, restore_item, empty_trash
from rename import build_rename_plan, execute_rename_plan

eel.init('web')

APP_STATE = {
    "folder": None,
    "config_path": None,
    "category_map": {},
    "exclude_patterns": [],
    "admin_pin": None,
    "admin_mode": False,
    "ext_to_category": {},

    # ----------------------------------------------------
    # Multi-Folder Comparison (unlimited folders)
    # ----------------------------------------------------
    "comparison_folders": [],

    # ----------------------------------------------------
    # Optimization: In-Memory Scans
    # ----------------------------------------------------
    "cached_files": None,
    "cached_categories": None,
    "cached_loose_categories": None,
    "cached_duplicates": None,
    "cached_similar": None,
    "cached_similar_threshold": None,
    "cached_similar_unreadable": 0,
    "cached_similar_unavailable": False,

    # Pagination state for duplicate groups
    "dup_page": 0,
    "dup_page_size": 25,
}

def _get_all_folders():
    """Return a list of all active workspace folders (primary + comparison)."""
    folders = []
    if APP_STATE["folder"] and APP_STATE["folder"].is_dir():
        folders.append(APP_STATE["folder"])
    for cf in APP_STATE.get("comparison_folders", []):
        if cf and cf.is_dir():
            folders.append(cf)
    return folders

def clear_cache():
    """Wipes the cached memory so the next action triggers a fresh scan."""
    APP_STATE["cached_files"] = None
    APP_STATE["cached_categories"] = None
    APP_STATE["cached_loose_categories"] = None
    APP_STATE["cached_duplicates"] = None
    APP_STATE["cached_similar"] = None
    APP_STATE["cached_similar_threshold"] = None
    APP_STATE["cached_similar_unreadable"] = 0
    APP_STATE["cached_similar_unavailable"] = False
    APP_STATE["cached_size_cache"] = None

def get_cached_duplicates():
    """Exact-duplicate groups, computed once per scan and reused until clear_cache()."""
    if APP_STATE["cached_duplicates"] is None:
        all_files, _, _ = get_cached_scans()
        APP_STATE["cached_duplicates"] = find_duplicates(all_files, size_cache=APP_STATE.get("cached_size_cache"))[0] if all_files else []
    return APP_STATE["cached_duplicates"]

def get_cached_similar_images(threshold: int):
    """Returns (groups, unreadable_count, unavailable). Cached per-threshold
    so switching tabs at the same threshold doesn't re-hash every image."""
    key = APP_STATE.get("cached_similar_threshold")
    if key != threshold or APP_STATE.get("cached_similar") is None:
        all_files, _, _ = get_cached_scans()
        groups, unreadable, unavailable = find_similar_images(all_files, threshold=threshold)
        APP_STATE["cached_similar"] = groups
        APP_STATE["cached_similar_unreadable"] = len(unreadable)
        APP_STATE["cached_similar_unavailable"] = unavailable
        APP_STATE["cached_similar_threshold"] = threshold
    return (APP_STATE["cached_similar"],
            APP_STATE.get("cached_similar_unreadable", 0),
            APP_STATE.get("cached_similar_unavailable", False))

def get_cached_scans():
    """Returns cached files instantly if already scanned, otherwise does a fast scan.
    Merges files from the primary folder AND the comparison folder (if set).

    PERFORMANCE (#2): Multi-folder scans run in parallel threads.
    """
    if APP_STATE["cached_files"] is None:
        folders = _get_all_folders()
        if not folders:
            return [], {}, {}

        def _scan_one_folder(folder):
            r_files, _, r_size_cache = recursive_scan(folder, APP_STATE["exclude_patterns"])
            # bucket_files now returns 4 values (added file_to_cat)
            r_by_cat, _, _, _ = bucket_files(r_files, APP_STATE["ext_to_category"], r_size_cache)
            l_by_cat, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
            return r_files, r_size_cache, r_by_cat, l_by_cat

        # (#2) Parallel folder scanning
        merged_files = []
        merged_size_cache = {}
        merged_by_cat = defaultdict(list)
        merged_loose = defaultdict(list)

        if len(folders) > 1:
            with ThreadPoolExecutor(max_workers=min(len(folders), 4)) as pool:
                results = list(pool.map(_scan_one_folder, folders))
        else:
            results = [_scan_one_folder(f) for f in folders]

        for r_files, r_size_cache, r_by_cat, l_by_cat in results:
            merged_files.extend(r_files)
            merged_size_cache.update(r_size_cache)
            for cat, files in r_by_cat.items():
                merged_by_cat[cat].extend(files)
            for cat, files in l_by_cat.items():
                merged_loose[cat].extend(files)

        APP_STATE["cached_files"] = merged_files
        APP_STATE["cached_categories"] = dict(merged_by_cat)
        APP_STATE["cached_loose_categories"] = dict(merged_loose)
        APP_STATE["cached_size_cache"] = merged_size_cache

    return APP_STATE["cached_files"], APP_STATE["cached_categories"], APP_STATE["cached_loose_categories"]

def _generate_base64_thumb(file_path: Path, use_cache: bool = True):
    """Generate base64 thumbnail, with SQLite caching (#3).

    PERFORMANCE (#3): Thumbnails are cached to SQLite keyed by
    (path, mtime, size), so unchanged images skip PIL encoding.
    """
    if not PIL_AVAILABLE or not is_image_file(file_path):
        return ""
    if not file_path.exists():
        return ""

    # Check cache (#3)
    if use_cache and cache_store._DB_PATH is not None:
        try:
            st = file_path.stat()
            cached = cache_store.get_cached_thumb(file_path, st.st_mtime, st.st_size)
            if cached:
                return cached
        except OSError:
            pass
        except Exception:
            pass

    try:
        with Image.open(file_path) as img:
            thumb = img.copy()
            thumb.thumbnail((60, 60))
            from io import BytesIO
            buffered = BytesIO()
            if thumb.mode in ("RGBA", "P"):
                thumb = thumb.convert("RGB")
            thumb.save(buffered, format="JPEG", quality=75)
            b64 = f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

            # Store to cache (#3)
            if use_cache and cache_store._DB_PATH is not None:
                try:
                    st = file_path.stat()
                    cache_store.put_cached_thumb(file_path, st.st_mtime, st.st_size, b64)
                except Exception:
                    pass

            return b64
    except Exception:
        return ""

@eel.expose
def get_full_image_b64(path_str):
    if not PIL_AVAILABLE: return ""
    try:
        with Image.open(path_str) as img:
            display = img.copy()
            display.thumbnail((1200, 800))
            from io import BytesIO
            buffered = BytesIO()
            if display.mode in ("RGBA", "P"):
                display = display.convert("RGB")
            display.save(buffered, format="JPEG", quality=85)
            return f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"
    except Exception:
        return ""

def initialize_runtime_configs(config_path: Path, initial_folder: Path = None):
    APP_STATE["config_path"] = config_path
    if initial_folder:
        APP_STATE["folder"] = Path(initial_folder)
        # Initialize cache store (#3, #4, #7, #9)
        cache_store.init_cache_db(APP_STATE["folder"])
    cmap, excludes, pin = load_config(config_path)
    APP_STATE["category_map"] = cmap
    APP_STATE["exclude_patterns"] = excludes
    APP_STATE["admin_pin"] = pin
    APP_STATE["ext_to_category"] = build_ext_to_category(cmap)
    clear_cache()

@eel.expose
def get_system_metadata():
    return {
        "folder": str(APP_STATE["folder"]) if APP_STATE["folder"] else "",
        "comparison_folders": [str(f) for f in APP_STATE.get("comparison_folders", [])],
        "admin_mode": APP_STATE["admin_mode"],
        "has_pin": bool(APP_STATE["admin_pin"])
    }

@eel.expose
def verify_admin_pin(pin_attempt):
    if str(pin_attempt) == str(APP_STATE["admin_pin"]):
        APP_STATE["admin_mode"] = True
        return {"status": "success"}
    return {"status": "error", "message": "Incorrect PIN"}

@eel.expose
def select_folder_native():
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    chosen = filedialog.askdirectory(title="Select Folder to Organize", initialdir=str(APP_STATE["folder"] or ""))
    root.destroy()
    if chosen:
        APP_STATE["folder"] = Path(chosen)
        clear_cache()
        return {"status": "success", "path": chosen}
    return {"status": "cancelled"}

@eel.expose
def add_comparison_folder():
    """Open a folder picker and add to the comparison folder list."""
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    chosen = filedialog.askdirectory(title="Select Comparison Folder", initialdir=str(APP_STATE["folder"] or ""))
    root.destroy()
    if not chosen:
        return {"status": "cancelled"}
    chosen_path = Path(chosen)
    # Prevent duplicates
    all_existing = [APP_STATE["folder"].resolve()] + [f.resolve() for f in APP_STATE.get("comparison_folders", [])]
    if chosen_path.resolve() in all_existing:
        return {"status": "error", "message": "This folder is already in the workspace."}
    APP_STATE.setdefault("comparison_folders", []).append(chosen_path)
    clear_cache()
    return {"status": "success", "path": chosen}

@eel.expose
def remove_comparison_folder(folder_path_str):
    """Remove a specific comparison folder by its path."""
    target = Path(folder_path_str).resolve()
    APP_STATE["comparison_folders"] = [f for f in APP_STATE.get("comparison_folders", []) if f.resolve() != target]
    clear_cache()
    return {"status": "success"}

@eel.expose
def get_comparison_folders():
    """Return the current list of comparison folder paths."""
    return [str(f) for f in APP_STATE.get("comparison_folders", [])]

@eel.expose
def execute_storage_telemetry():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"error": "No directory context set"}
        
    # Leverages memory buffer
    all_files, files_by_category, _ = get_cached_scans()
    
    if not all_files:
        return {"total_size_str": "0 B", "total_files": 0, "duplicate_sets": 0, "trash_count": 0, "categories": []}
        
    duplicate_groups = get_cached_duplicates()
    sizes = compute_storage_usage(files_by_category)
    total_bytes = sum(sizes.values())
    categories_data = []
    
    for cat, byte_size in sizes.items():
        pct = int((byte_size / total_bytes * 100)) if total_bytes > 0 else 0
        categories_data.append({
            "name": cat, "size_str": format_size(byte_size), "bytes": byte_size, "percentage": pct
        })
        
    return {
        "total_size_str": format_size(total_bytes), "total_files": len(all_files),
        "duplicate_sets": len(duplicate_groups), "trash_count": len(list_trash_items(folder)),
        "categories": categories_data
    }

@eel.expose
def get_organize_view_data():
    """Returns per-category loose file counts broken down by each active folder.
    Used by the frontend to show the organize-destination modal."""
    folders = _get_all_folders()
    if not folders:
        return {"folders": [], "categories": {}}

    result = {"folders": [], "categories": {}}
    for folder in folders:
        label = folder.name or str(folder)
        result["folders"].append({"path": str(folder), "label": label})
        loose, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
        for cat, files in loose.items():
            if files:
                entry = result["categories"].setdefault(cat, {})
                entry[str(folder)] = len(files)

    return result

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
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"status": "error", "message": "No workspace folder"}
    
    paths_to_remove = [Path(p) for p in folder_paths]
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
def get_mismatched_data():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return []
    mismatches = find_mismatched_files(folder, APP_STATE["category_map"], APP_STATE["ext_to_category"])
    res = []
    for cat, items in mismatches.items():
        for f, correct in items:
            res.append({"name": f.name, "current": cat, "correct": correct})
    return res

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
def get_rule_preview_metrics(rule_type, limit_value):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"count": 0, "size_str": "0 B"}
    
    all_files, _, _ = get_cached_scans()
    matched_count = matched_bytes = 0
    val = float(limit_value)

    for f in all_files:
        try:
            if rule_type == "size" and get_file_size_mb(f) >= val:
                matched_count += 1
                matched_bytes += f.stat().st_size
            elif rule_type == "age" and get_file_age_days(f) >= val:
                matched_count += 1
                matched_bytes += f.stat().st_size
        except OSError:
            continue
    return {"count": matched_count, "size_str": format_size(matched_bytes)}

@eel.expose
def trigger_bulk_organization(chosen_categories, destination_folder_str=None):
    """Organize files into the chosen destination folder.

    If destination_folder_str is None, behaves as before (organize primary only).
    If a folder path is given, ALL loose files from ALL active folders are moved
    into categorized subfolders inside that destination.
    """
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
    run_log = []
    for name, dest, files in plan:
        _, _, entries = move_files(files, dest, dry_run=False)
        run_log.extend(entries)

    if run_log:
        save_run_log(destination_folder, run_log)
        clear_cache()

    return {"status": "success", "moved": len(run_log)}

@eel.expose
def trigger_separate_organization(folder_categories_map):
    """Organize files SEPARATELY — each folder's loose files go into that folder's
    own subfolders. folder_categories_map is a dict like:
        {"H:/one_image": ["Images", "Videos"], "I:/two_image": ["Documents"]}
    """
    total_moved = 0
    for folder_str, chosen_categories in folder_categories_map.items():
        folder = Path(folder_str)
        if not folder.is_dir():
            continue
        loose, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])

        extension_selections = {}
        for category in chosen_categories:
            extension_selections[category] = loose.get(category, [])

        plan = build_move_plan(folder, loose, chosen_categories, extension_selections=extension_selections)
        run_log = []
        for name, dest, files in plan:
            _, _, entries = move_files(files, dest, dry_run=False)
            run_log.extend(entries)

        if run_log:
            save_run_log(folder, run_log)
            total_moved += len(run_log)

    if total_moved > 0:
        clear_cache()

    return {"status": "success", "moved": total_moved}

@eel.expose
def trigger_separation_organization(rule_type, timing, limit_value):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"status": "error", "message": "No workspace folder selected"}
    val = float(limit_value)
    rules = {
        "timing": timing, "want_size": (rule_type == "size"), "want_age": (rule_type == "age"),
        "size_mb": val if rule_type == "size" else None, "age_days": val if rule_type == "age" else None
    }
    
    _, _, loose_files_by_category = get_cached_scans()
    run_log = []

    if timing == "before":
        plan, _ = build_pre_plan(loose_files_by_category, folder, rules)
        run_log = execute_plan(plan, dry_run=False, label="Pre-Separation")
    else:
        all_categories = list(APP_STATE["category_map"].keys()) + ["Others"]
        mock_category_plan = [(cat, folder / cat, loose_files_by_category.get(cat, [])) for cat in all_categories]
        plan = build_post_plan(all_categories, mock_category_plan, folder, rules)
        run_log = execute_plan(plan, dry_run=False, label="Post-Separation")

    if run_log: 
        save_run_log(folder, run_log)
        clear_cache()
        
    return {"status": "success", "moved": len(run_log)}

@eel.expose
def get_duplicate_groups_data(scan_type="exact", hamming_threshold=10, page=0, page_size=25):
    """Paginated duplicate groups — loads one page at a time.

    Thumbnails are NOT generated here. The frontend calls
    get_thumbnails_for_group() on-demand when a group is expanded or
    scrolled into view. This keeps the initial payload small and
    prevents the Eel main thread from blocking on 500+ PIL opens.
    """
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"total_groups": 0, "displayed_groups": [], "page": 0, "total_pages": 0}

    all_files, _, _ = get_cached_scans()

    unreadable_count = 0
    pillow_missing = False

    if scan_type == "exact":
        groups = get_cached_duplicates()
    else:
        groups, unreadable_count, pillow_missing = get_cached_similar_images(int(hamming_threshold))
        if pillow_missing:
            return {
                "total_groups": 0, "displayed_groups": [],
                "page": 0, "total_pages": 0,
                "error": "This feature needs the Pillow library. Install it with: pip install Pillow"
            }

    total_groups = len(groups)
    total_pages = max(1, (total_groups + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, total_groups)
    limited_groups = groups[start:end]

    APP_STATE["dup_page"] = page
    APP_STATE["dup_page_size"] = page_size

    formatted_groups = []
    for idx, group in enumerate(limited_groups, start=start):
        try:
            size_str = format_size(group[0].stat().st_size)
        except OSError:
            size_str = "Unknown"

        # No thumbnails here — lazy-loaded via get_thumbnails_for_group()
        files_list = [{
            "name": f.name, "path": str(f), "is_image": is_image_file(f), "thumb_b64": ""
        } for f in group[:10]]

        formatted_groups.append({"id": idx, "size_str": size_str, "files": files_list})

    return {
        "total_groups": total_groups,
        "displayed_groups": formatted_groups,
        "page": page, "total_pages": total_pages,
        "unreadable_count": unreadable_count
    }


@eel.expose
def get_thumbnails_for_group(group_id, scan_type="exact"):
    """Generate thumbnails for a specific duplicate group on-demand.

    Called by the frontend when a group card is rendered or when the user
    scrolls it into view. Returns only the thumbnail data — no other
    computation — so this stays fast even for large datasets.
    """
    if APP_STATE["cached_duplicates"] is None and APP_STATE["cached_similar"] is None:
        return []

    groups = APP_STATE["cached_duplicates"] if scan_type == "exact" else APP_STATE.get("cached_similar", [])
    if not groups or group_id >= len(groups):
        return []

    group = groups[group_id][:10]
    return [{
        "name": f.name, "path": str(f),
        "thumb_b64": _generate_base64_thumb(f)
    } for f in group]

@eel.expose
def get_thumbnails_for_page(group_ids, scan_type="exact"):
    """Batch endpoint (#8): Fetch thumbnails for ALL groups on a page in one call.

    PERFORMANCE (#8): Instead of N round-trips (one per group), this fetches
    thumbnails for every visible group in a single request. Uses ThreadPoolExecutor
    for parallel PIL encoding, plus the thumbnail cache (#3).

    Args:
        group_ids: list of group IDs to fetch thumbnails for
        scan_type: "exact" or "similar"

    Returns: {group_id: [{name, path, thumb_b64}, ...], ...}
    """
    if APP_STATE["cached_duplicates"] is None and APP_STATE["cached_similar"] is None:
        return {}

    groups = APP_STATE["cached_duplicates"] if scan_type == "exact" else APP_STATE.get("cached_similar", [])
    if not groups:
        return {}

    # Collect all unique image paths from requested groups (with cache lookup)
    result = {}
    to_generate = []  # (group_id, file_idx, file_path) for cache misses

    # First pass: check cache for all images
    cache_entries = []  # (path_str, mtime, size)
    path_to_meta = {}  # path_str -> (mtime, size, file_path, group_id, file_idx)

    for gid in group_ids:
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            continue
        if gid >= len(groups):
            continue
        group = groups[gid][:10]
        result[gid] = []
        for fidx, f in enumerate(group):
            if not is_image_file(f) or not f.exists():
                result[gid].append({"name": f.name, "path": str(f), "thumb_b64": ""})
                continue
            try:
                st = f.stat()
                cache_entries.append((str(f), st.st_mtime, st.st_size))
                path_to_meta[str(f)] = (st.st_mtime, st.st_size, f, gid, fidx)
                result[gid].append({"name": f.name, "path": str(f), "thumb_b64": ""})
            except OSError:
                result[gid].append({"name": f.name, "path": str(f), "thumb_b64": ""})

    # Batch-fetch from cache (#3)
    cached = {}
    if cache_store._DB_PATH is not None and cache_entries:
        cached = cache_store.get_cached_thumbs_batch(cache_entries)

    # Apply cached results and collect misses
    misses = []  # (file_path, group_id, file_idx)
    new_cache_entries = []  # (path_str, mtime, size, b64_data)

    for path_str, (mtime, size, f, gid, fidx) in path_to_meta.items():
        if path_str in cached:
            # Update the result entry
            for entry in result[gid]:
                if entry["path"] == path_str:
                    entry["thumb_b64"] = cached[path_str]
                    break
        else:
            misses.append((f, gid, fidx, mtime, size))

    # Generate thumbnails for cache misses in parallel
    def _gen_one(item):
        f, gid, fidx, mtime, size = item
        return (f, gid, fidx, mtime, size, _generate_base64_thumb(f, use_cache=False))

    if misses:
        if len(misses) > 3:
            with ThreadPoolExecutor(max_workers=4) as pool:
                generated = list(pool.map(_gen_one, misses))
        else:
            generated = [_gen_one(m) for m in misses]

        for f, gid, fidx, mtime, size, b64 in generated:
            # Update result
            for entry in result[gid]:
                if entry["path"] == str(f):
                    entry["thumb_b64"] = b64
                    break
            # Queue for cache storage
            if b64:
                new_cache_entries.append((str(f), mtime, size, b64))

    # Batch-store new thumbnails to cache (#3)
    if new_cache_entries and cache_store._DB_PATH is not None:
        cache_store.put_cached_thumbs_batch(new_cache_entries)

    return result

@eel.expose
def get_total_duplicate_count(scan_type="exact", hamming_threshold=10):
    """Returns only the total duplicate group count — no group data.
    Used by the overview dashboard to display the count without
    triggering the expensive duplicate detection pipeline unless needed."""
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return 0
    if scan_type == "exact":
        groups = get_cached_duplicates()
    else:
        groups, _, _ = get_cached_similar_images(int(hamming_threshold))
    return len(groups) if groups else 0


@eel.expose
def purge_selected_duplicates(file_paths):
    folder = APP_STATE["folder"]
    files_to_trash = [Path(p) for p in file_paths if Path(p).exists()]
    if not files_to_trash: return {"status": "error", "message": "No files selected"}
    moved, log_entries = move_to_trash(files_to_trash, folder, dry_run=False)
    if log_entries: 
        save_run_log(folder, log_entries)
        clear_cache()
    return {"status": "success", "purged": moved}

@eel.expose
def get_categories_data():
    raw_config = load_raw_config(APP_STATE["config_path"])
    overridden = list(raw_config.get("categories", {}).keys())
    cats = []
    for name, exts in APP_STATE["category_map"].items():
        cats.append({
            "name": name,
            "extensions": exts,
            "is_custom": name in overridden or name not in DEFAULT_CATEGORY_MAP
        })
    return cats

@eel.expose
def update_category(name, extensions_str):
    if not APP_STATE["admin_mode"]:
        return {"status": "error", "message": "Admin authentication required."}
        
    exts = normalize_extensions(extensions_str)
    if not exts: return {"status": "error", "message": "No valid extensions provided."}
    raw_config = load_raw_config(APP_STATE["config_path"])
    if "categories" not in raw_config: raw_config["categories"] = {}
    raw_config["categories"][name] = exts
    save_raw_config(APP_STATE["config_path"], raw_config)
    APP_STATE["category_map"][name] = exts
    APP_STATE["ext_to_category"] = build_ext_to_category(APP_STATE["category_map"])
    clear_cache()
    return {"status": "success"}

@eel.expose
def remove_category(name):
    if not APP_STATE["admin_mode"]:
        return {"status": "error", "message": "Admin authentication required."}
        
    raw_config = load_raw_config(APP_STATE["config_path"])
    if "categories" in raw_config and name in raw_config["categories"]:
        del raw_config["categories"][name]
        save_raw_config(APP_STATE["config_path"], raw_config)
    if name in DEFAULT_CATEGORY_MAP:
        APP_STATE["category_map"][name] = list(DEFAULT_CATEGORY_MAP[name])
    else:
        APP_STATE["category_map"].pop(name, None)
    APP_STATE["ext_to_category"] = build_ext_to_category(APP_STATE["category_map"])
    clear_cache()
    return {"status": "success"}

@eel.expose
def get_rename_categories():
    """Only return the category names and counts, NEVER the massive file lists."""
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return []
    _, all_files_by_category, _ = get_cached_scans()
    
    # Send a tiny summary payload
    return [{"name": c, "count": len(files)} for c, files in all_files_by_category.items() if files]

@eel.expose
def preview_rename(category_name, op, arg1, arg2):
    """Expects a category name instead of a massive list of file paths."""
    _, all_files_by_category, _ = get_cached_scans()
    files = all_files_by_category.get(category_name, [])
    if not files: return []
    
    rule = (op, arg1) if op in ("remove", "prefix", "suffix") else (op, arg1, arg2)
    plan = build_rename_plan(files, rule)
    
    # Limit preview to 50 items so we don't overload the WebSocket!
    changed = [{"old": o.name, "new": n.name} for o, n in plan if o.name != n.name]
    return changed[:50] 

@eel.expose
def execute_rename(category_name, op, arg1, arg2):
    if not APP_STATE["admin_mode"]: return 0
    
    _, all_files_by_category, _ = get_cached_scans()
    files = all_files_by_category.get(category_name, [])
    if not files: return 0
    
    rule = (op, arg1) if op in ("remove", "prefix", "suffix") else (op, arg1, arg2)
    plan = build_rename_plan(files, rule)
    
    log_entries = execute_rename_plan(plan, dry_run=False)
    if log_entries: 
        save_run_log(APP_STATE["folder"], log_entries)
        clear_cache()
    return len(log_entries)

@eel.expose
def get_history_and_trash_logs():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"history": [], "trash": []}
    runs = list_run_logs(folder)
    history_list = [{"index": idx + 1, "label": r["label"], "count": r["count"], "path": str(r["path"])} for idx, r in enumerate(runs)]
    trash_items = list_trash_items(folder)
    trash_list = [{"name": t["name"], "size": format_size(t["size"]), "batch": t["batch"], "path": str(t["path"])} for t in trash_items]
    return {"history": history_list, "trash": trash_list}

@eel.expose
def restore_from_bin(path_strs):
    folder = APP_STATE["folder"]
    items = list_trash_items(folder)
    target_paths = {str(Path(p)) for p in path_strs}
    targets = [it for it in items if str(Path(it["path"])) in target_paths]
    restored = 0
    for it in targets:
        ok, msg = restore_item(it)
        if ok: restored += 1
    if restored > 0:
        clear_cache()
    return {"status": "success", "restored": restored}

@eel.expose
def execute_undo_operation(log_path_str):
    restored, total = restore_run(Path(log_path_str))
    clear_cache()
    return {"status": "success", "restored": restored, "total": total}

@eel.expose
def empty_trash_completely():
    count = empty_trash(APP_STATE["folder"])
    clear_cache()
    return {"status": "success", "flushed": count}

def launch_gui(config_path: Path, initial_folder: Path = None):
    initialize_runtime_configs(config_path, initial_folder)
    try:
        eel.start('index.html', size=(1120, 820), mode='chrome')
    except (SystemExit, MemoryError, KeyboardInterrupt):
        pass
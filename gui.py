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
    "ext_to_category": {}
}

def _generate_base64_thumb(file_path: Path):
    if not PIL_AVAILABLE or not is_image_file(file_path):
        return ""
    try:
        with Image.open(file_path) as img:
            thumb = img.copy()
            thumb.thumbnail((60, 60))
            from io import BytesIO
            buffered = BytesIO()
            if thumb.mode in ("RGBA", "P"):
                thumb = thumb.convert("RGB")
            thumb.save(buffered, format="JPEG", quality=75)
            return f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"
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
    cmap, excludes, pin = load_config(config_path)
    APP_STATE["category_map"] = cmap
    APP_STATE["exclude_patterns"] = excludes
    APP_STATE["admin_pin"] = pin
    APP_STATE["ext_to_category"] = build_ext_to_category(cmap)

@eel.expose
def get_system_metadata():
    return {
        "folder": str(APP_STATE["folder"]) if APP_STATE["folder"] else "",
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
        return {"status": "success", "path": chosen}
    return {"status": "cancelled"}

@eel.expose
def execute_storage_telemetry():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"error": "No directory context set"}
    all_files, _ = recursive_scan(folder, APP_STATE["exclude_patterns"])
    if not all_files:
        return {"total_size_str": "0 B", "total_files": 0, "duplicate_sets": 0, "trash_count": 0, "categories": []}
    files_by_category, _, _ = bucket_files(all_files, APP_STATE["ext_to_category"])
    duplicate_groups, _ = find_duplicates(all_files)
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
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return []
    files_by_category, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
    return [{"name": c, "count": len(flist)} for c, flist in files_by_category.items() if flist]

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
    if run_log: save_run_log(folder, run_log)
    return len(run_log)

@eel.expose
def get_rule_preview_metrics(rule_type, limit_value):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return {"count": 0, "size_str": "0 B"}
    all_files, _ = recursive_scan(folder, APP_STATE["exclude_patterns"])
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
def trigger_bulk_organization(chosen_categories):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"status": "error", "message": "No workspace folder selected"}
    files_by_category, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
    
    extension_selections = {}
    for category in chosen_categories:
        extension_selections[category] = files_by_category.get(category, [])
        
    plan = build_move_plan(folder, files_by_category, chosen_categories, extension_selections=extension_selections)
    run_log = []
    for name, dest, files in plan:
        _, _, entries = move_files(files, dest, dry_run=False)
        run_log.extend(entries)
    if run_log: save_run_log(folder, run_log)
    return {"status": "success", "moved": len(run_log)}

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
    files_by_category, _, _, _ = scan_folder(folder, APP_STATE["ext_to_category"], APP_STATE["exclude_patterns"])
    run_log = []

    if timing == "before":
        plan, _ = build_pre_plan(files_by_category, folder, rules)
        run_log = execute_plan(plan, dry_run=False, label="Pre-Separation")
    else:
        all_categories = list(APP_STATE["category_map"].keys()) + ["Others"]
        mock_category_plan = [(cat, folder / cat, files_by_category.get(cat, [])) for cat in all_categories]
        plan = build_post_plan(all_categories, mock_category_plan, folder, rules)
        run_log = execute_plan(plan, dry_run=False, label="Post-Separation")

    if run_log: save_run_log(folder, run_log)
    return {"status": "success", "moved": len(run_log)}

@eel.expose
def get_duplicate_groups_data(scan_type="exact", hamming_threshold=10):
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return []
    all_files, _ = recursive_scan(folder, APP_STATE["exclude_patterns"])
    if scan_type == "exact":
        groups, _ = find_duplicates(all_files)
    else:
        groups, _, _ = find_similar_images(all_files, threshold=int(hamming_threshold))
        
    formatted_groups = []
    for idx, group in enumerate(groups):
        try: size_str = format_size(group[0].stat().st_size)
        except OSError: size_str = "Unknown"
        files_list = [{
            "name": f.name, "path": str(f), "is_image": is_image_file(f), "thumb_b64": _generate_base64_thumb(f)
        } for f in group]
        formatted_groups.append({"id": idx, "size_str": size_str, "files": files_list})
    return formatted_groups

@eel.expose
def purge_selected_duplicates(file_paths):
    folder = APP_STATE["folder"]
    files_to_trash = [Path(p) for p in file_paths if Path(p).exists()]
    if not files_to_trash: return {"status": "error", "message": "No files selected"}
    moved, log_entries = move_to_trash(files_to_trash, folder, dry_run=False)
    if log_entries: save_run_log(folder, log_entries)
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
    return {"status": "success"}

@eel.expose
def get_rename_categories():
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir(): return []
    all_files, _ = recursive_scan(folder, APP_STATE["exclude_patterns"])
    files_by_category, _, _ = bucket_files(all_files, APP_STATE["ext_to_category"])
    return [{"name": c, "files": [str(f) for f in files]} for c, files in files_by_category.items() if files]

@eel.expose
def preview_rename(file_paths, op, arg1, arg2):
    rule = (op, arg1) if op in ("remove", "prefix", "suffix") else (op, arg1, arg2)
    paths = [Path(p) for p in file_paths]
    plan = build_rename_plan(paths, rule)
    changed = [{"old": o.name, "new": n.name} for o, n in plan if o.name != n.name]
    return changed

@eel.expose
def execute_rename(file_paths, op, arg1, arg2):
    if not APP_STATE["admin_mode"]: return 0
    rule = (op, arg1) if op in ("remove", "prefix", "suffix") else (op, arg1, arg2)
    paths = [Path(p) for p in file_paths]
    plan = build_rename_plan(paths, rule)
    log_entries = execute_rename_plan(plan, dry_run=False)
    if log_entries: save_run_log(APP_STATE["folder"], log_entries)
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
    return {"status": "success", "restored": restored}

@eel.expose
def execute_undo_operation(log_path_str):
    restored, total = restore_run(Path(log_path_str))
    return {"status": "success", "restored": restored, "total": total}

@eel.expose
def empty_trash_completely():
    count = empty_trash(APP_STATE["folder"])
    return {"status": "success", "flushed": count}

def launch_gui(config_path: Path, initial_folder: Path = None):
    initialize_runtime_configs(config_path, initial_folder)
    try:
        eel.start('index.html', size=(1120, 820), mode='chrome')
    except (SystemExit, MemoryError, KeyboardInterrupt):
        pass
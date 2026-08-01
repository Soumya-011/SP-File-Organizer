#!/usr/bin/env python3
"""
Admin-only endpoints — PIN auth, category management, and bulk rename.
"""

import eel

from config import load_raw_config,build_ext_to_category, save_raw_config, DEFAULT_CATEGORY_MAP
from categories import normalize_extensions
from scanner import scan_folder
from rename import build_rename_plan, execute_rename_plan
from undo import save_run_log
from gui_state import (
    APP_STATE, clear_cache, get_cached_scans,
)


@eel.expose
def verify_admin_pin(pin_attempt):
    if str(pin_attempt) == str(APP_STATE["admin_pin"]):
        APP_STATE["admin_mode"] = True
        return {"status": "success"}
    return {"status": "error", "message": "Incorrect PIN"}


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

    return [{"name": c, "count": len(files)} for c, files in all_files_by_category.items() if files]


@eel.expose
def preview_rename(category_name, op, arg1, arg2):
    """Expects a category name instead of a massive list of file paths."""
    _, all_files_by_category, _ = get_cached_scans()
    files = all_files_by_category.get(category_name, [])
    if not files: return []

    rule = (op, arg1) if op in ("remove", "prefix", "suffix") else (op, arg1, arg2)
    plan = build_rename_plan(files, rule)

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


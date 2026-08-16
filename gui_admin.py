#!/usr/bin/env python3
"""
Admin-only endpoints — PIN auth, category management, and bulk rename.
"""

import eel
import hashlib
import os

from config import load_raw_config, save_raw_config, DEFAULT_CATEGORY_MAP, build_ext_to_category
from categories import normalize_extensions
from rename import build_rename_plan, execute_rename_plan
from undo import save_run_log
from gui_state import (
    APP_STATE, clear_cache, get_cached_scans,
)


@eel.expose
def verify_admin_pin(pin_attempt):
    if not APP_STATE.get("admin_pin"):
        return {"status": "error", "message": "No PIN configured."}
        
    attempt_hash = hashlib.sha256(str(pin_attempt).encode('utf-8')).hexdigest()
    
    if attempt_hash == str(APP_STATE["admin_pin"]):
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


# ---------------------------------------------------------------------------
# Performance settings — scan worker thread count (Low / Medium / High)
# ---------------------------------------------------------------------------
def _worker_tiers(cpu_count: int) -> dict:
    """Low/Medium/High worker-count presets, scaled to the machine's actual
    core count. A hardcoded "4" makes no sense on a 12-core machine (leaves
    8 cores idle during a scan) or a 2-core one (oversubscribes it) — these
    presets scale proportionally instead, same idea as utils.py's existing
    "cpu_count - 1" auto-default, just offering coarser user control over it."""
    return {
        "low": max(1, cpu_count // 4),
        "medium": max(1, cpu_count // 2),
        "high": max(1, cpu_count - 1),
    }


@eel.expose
def get_worker_settings():
    """Read-only info for the Performance settings card: detected CPU count,
    the currently configured max_scan_workers, and which tier (if any) that
    corresponds to. current_max_workers is None when unset, meaning scans
    fall back to utils.concurrent_hash_all()'s own auto default.

    The active tier is read back from the "max_scan_workers_tier" string
    stored in config.json, NOT inferred from the numeric worker count — on
    low-core machines (e.g. a 1-2 core VM), low/medium/high can all compute
    to the same number, which would make a value-based reverse-lookup always
    resolve to whichever tier happens to be listed first, regardless of what
    was actually selected.
    """
    cpu_count = os.cpu_count() or 4
    tiers = _worker_tiers(cpu_count)
    current = APP_STATE.get("max_scan_workers")

    raw_config = load_raw_config(APP_STATE["config_path"])
    stored_tier = raw_config.get("max_scan_workers_tier")
    # Only trust the stored tier label if it still computes to the value
    # actually in effect (guards against a hand-edited config.json going
    # out of sync with its own tier label).
    current_tier = stored_tier if (stored_tier in tiers and tiers[stored_tier] == current) else None

    return {
        "cpu_count": cpu_count,
        "current_max_workers": current,
        "current_tier": current_tier,
        "tiers": tiers,
    }


@eel.expose
def set_worker_tier(tier):
    """Admin-only. Persists the chosen tier's worker count AND its name to
    config.json ("max_scan_workers" + "max_scan_workers_tier") and applies
    the count immediately to APP_STATE — no restart needed, since every scan
    reads APP_STATE["max_scan_workers"] fresh at call time rather than
    caching it once at startup."""
    if not APP_STATE["admin_mode"]:
        return {"status": "error", "message": "Admin authentication required."}

    cpu_count = os.cpu_count() or 4
    tiers = _worker_tiers(cpu_count)
    if tier not in tiers:
        return {"status": "error", "message": f"Unknown tier '{tier}'."}

    value = tiers[tier]
    raw_config = load_raw_config(APP_STATE["config_path"])
    raw_config["max_scan_workers"] = value
    raw_config["max_scan_workers_tier"] = tier
    save_raw_config(APP_STATE["config_path"], raw_config)
    APP_STATE["max_scan_workers"] = value

    return {"status": "success", "tier": tier, "max_workers": value, "cpu_count": cpu_count}


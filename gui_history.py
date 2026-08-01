#!/usr/bin/env python3
"""
History, undo, and recycle bin endpoints.
"""

import eel

from utils import format_size
from undo import list_run_logs, restore_run
from recycle_bin import list_trash_items, restore_item, empty_trash
from gui_state import (
    APP_STATE, clear_cache, invalidate_duplicate_cache,
)
from pathlib import Path


@eel.expose
def get_history_and_trash_logs():
    """Combined history runs + trash items in one call."""
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
        invalidate_duplicate_cache()
    return {"status": "success", "restored": restored}


@eel.expose
def empty_trash_completely():
    count = empty_trash(APP_STATE["folder"])
    clear_cache()
    return {"status": "success", "flushed": count}


@eel.expose
def get_undo_log_details(log_path_str):
    """Return file-level entries from a specific run log for preview."""
    log_path = Path(log_path_str)
    if not log_path.exists():
        return {"status": "error", "message": "Log file not found.", "entries": []}
    try:
        import json as _json
        entries = _json.loads(log_path.read_text())
        details = []
        for e in entries:
            src = Path(e["source"])
            dst = Path(e["destination"])
            details.append({
                "file_name": src.name,
                "source": str(src.parent) if src.parent != Path(".") else str(src),
                "destination": str(dst.parent) if dst.parent != Path(".") else str(dst),
                "destination_name": dst.name,
            })
        return {"status": "success", "entries": details}
    except Exception as ex:
        return {"status": "error", "message": str(ex), "entries": []}


@eel.expose
def execute_undo_operation(log_path_str):
    restored, total = restore_run(Path(log_path_str))
    clear_cache()
    if restored > 0:
        invalidate_duplicate_cache()
    return {"status": "success", "restored": restored, "total": total}

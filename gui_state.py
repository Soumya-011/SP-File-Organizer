#!/usr/bin/env python3
"""
Shared application state, thread-safe accessors, cache management,
path-safety helpers, and the progress bridge used by all GUI endpoint modules.
"""

import os
import threading
import time
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import eel

from utils import format_size, get_file_size_mb, get_file_age_days
from config import load_config, build_ext_to_category, load_raw_config, DEFAULT_CATEGORY_MAP
from scanner import scan_folder, recursive_scan, bucket_files
import cache_store
from storage_analyzer import compute_storage_usage
from duplicates import find_duplicates
from image_duplicates import find_similar_images, is_image_file
from organizer import find_mismatched_files
from recycle_bin import list_trash_items

# --- Thread-safe APP_STATE access ---
# Background threads (similar-image scan, organize) read/write APP_STATE
# concurrently with the Eel main thread. All shared-state reads/writes must
# hold this lock to prevent data races (e.g., background scan writing
# cached_similar while the main thread reads it for pagination).
_STATE_LOCK = threading.Lock()


def _state_get(key, default=None):
    """Thread-safe read from APP_STATE."""
    with _STATE_LOCK:
        return APP_STATE.get(key, default)


def _state_set(**kwargs):
    """Thread-safe bulk write to APP_STATE."""
    with _STATE_LOCK:
        APP_STATE.update(kwargs)


def _state_set_many(items: dict):
    """Thread-safe bulk write from a dict."""
    with _STATE_LOCK:
        APP_STATE.update(items)


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
    "cached_per_folder_loose": None,
    "cached_size_cache": None,
    "cached_duplicates": None,
    "cached_similar": None,
    "cached_similar_threshold": None,
    "cached_similar_unreadable": 0,
    "cached_similar_unavailable": False,

    # Pagination state for duplicate groups
    "dup_page": 0,
    "dup_page_size": 25,

    # Background similar-image scan state
    "_similar_scan_running": False,
    "_similar_scan_thread": None,
}


def _is_path_safe(path: Path, folder: Path) -> bool:
    """Validate that `path` resolves to a location within `folder`.

    Prevents path-traversal attacks where the frontend (or a compromised
    WebSocket client) could pass "../../etc/passwd" to delete/trash/restore
    arbitrary files outside the workspace.
    """
    try:
        resolved = path.resolve()
        return resolved == folder or resolved.is_relative_to(folder)
    except (OSError, ValueError):
        return False


def _filter_safe_paths(path_list, folder: Path) -> list:
    """Return only paths that resolve within the workspace folder."""
    return [p for p in path_list if _is_path_safe(Path(p), folder)]


# --- Real-time progress bridge ---
# Python calls _push_ui_progress() during long operations.
# JS registers a global handler via eel.expose(_on_python_progress).
_progress_counter = 0


def _push_ui_progress(message: str, current: int = 0, total: int = 0):
    """Send a progress update to the JS frontend.

    Throttled to at most once every 200ms to avoid flooding the Eel channel.
    """
    global _progress_counter
    # Throttle: only send if >=200ms since last send
    now = time.time()
    if now - _push_ui_progress.__dict__.setdefault('_last_time', 0) < 0.2:
        return
    _push_ui_progress._last_time = now
    try:
        eel._on_python_progress(message, current, total)()
    except Exception:
        pass  # frontend not listening (normal during CLI mode)


@eel.expose
def _on_python_progress(message, current, total):
    pass  # placeholder; JS side receives the call


# --- Cache management ---


def clear_cache():
    """Wipes the file scan caches so the next action triggers a fresh directory scan.

    Does NOT clear duplicate/similar image caches — those are preserved because:
      - Organize, rename, and fix-misplaced only move/rename files; the actual
        file content (and therefore duplicates) does not change.
      - The SQLite hash cache (#4) makes re-scanning fast, but the directory
        walk + grouping still causes noticeable lag on large folders.
      - Operations that genuinely change the file population (undo restore,
        trash, purge duplicates) call invalidate_duplicate_cache() explicitly.
    """
    APP_STATE["cached_files"] = None
    APP_STATE["cached_categories"] = None
    APP_STATE["cached_loose_categories"] = None
    APP_STATE["cached_per_folder_loose"] = None
    APP_STATE["cached_size_cache"] = None
    # cached_duplicates and cached_similar are intentionally preserved


def invalidate_duplicate_cache():
    """Explicitly clear only the duplicate and similar image caches.

    Call this ONLY when the file population actually changes:
      - Files added back (undo restore)
      - Files removed (trashed / purged duplicates)
      - Workspace changed entirely (new folder)
    """
    APP_STATE["cached_duplicates"] = None
    APP_STATE["cached_similar"] = None
    APP_STATE["cached_similar_threshold"] = None
    APP_STATE["cached_similar_unreadable"] = 0
    APP_STATE["cached_similar_unavailable"] = False


def clear_all_cache():
    """Full cache wipe for workspace changes (new folder, config reload, etc.)."""
    clear_cache()
    invalidate_duplicate_cache()


def _get_all_folders():
    """Return a list of all active workspace folders (primary + comparison)."""
    folders = []
    if APP_STATE["folder"] and APP_STATE["folder"].is_dir():
        folders.append(APP_STATE["folder"])
    for cf in APP_STATE.get("comparison_folders", []):
        if cf and cf.is_dir():
            folders.append(cf)
    return folders


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
            return r_files, r_size_cache, r_by_cat, l_by_cat, folder

        # (#2) Parallel folder scanning
        merged_files = []
        merged_size_cache = {}
        merged_by_cat = defaultdict(list)
        merged_loose = defaultdict(list)
        per_folder_loose = {}  # per-folder loose breakdown for organize view

        if len(folders) > 1:
            with ThreadPoolExecutor(max_workers=min(len(folders), 4)) as pool:
                results = list(pool.map(_scan_one_folder, folders))
        else:
            results = [_scan_one_folder(f) for f in folders]

        for r_files, r_size_cache, r_by_cat, l_by_cat, folder in results:
            merged_files.extend(r_files)
            merged_size_cache.update(r_size_cache)
            for cat, files in r_by_cat.items():
                merged_by_cat[cat].extend(files)
            for cat, files in l_by_cat.items():
                merged_loose[cat].extend(files)
            per_folder_loose[str(folder)] = l_by_cat

        APP_STATE["cached_files"] = merged_files
        APP_STATE["cached_categories"] = dict(merged_by_cat)
        APP_STATE["cached_loose_categories"] = dict(merged_loose)
        APP_STATE["cached_per_folder_loose"] = per_folder_loose
        APP_STATE["cached_size_cache"] = merged_size_cache

    return APP_STATE["cached_files"], APP_STATE["cached_categories"], APP_STATE["cached_loose_categories"]


def get_cached_duplicates():
    """Exact-duplicate groups, computed once per scan and reused until clear_cache()."""
    if APP_STATE["cached_duplicates"] is None:
        all_files, _, _ = get_cached_scans()
        APP_STATE["cached_duplicates"] = find_duplicates(all_files, size_cache=APP_STATE.get("cached_size_cache"))[0] if all_files else []
    return APP_STATE["cached_duplicates"]


def get_cached_similar_images(threshold: int):
    """Returns (groups, unreadable_count, unavailable). Cached per-threshold."""
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


def _build_valid_thumb_keys():
    """Build the set of (path_str, mtime, size) tuples for all current files.
    Used as input to prune_stale_thumbs() on startup."""
    all_files, _, size_cache = get_cached_scans()
    if not all_files:
        return set()
    valid = set()
    for f in all_files:
        try:
            if f in size_cache:
                sz = size_cache[f]
            else:
                sz = f.stat().st_size
            valid.add((str(f), f.stat().st_mtime, sz))
        except OSError:
            continue
    return valid


def initialize_runtime_configs(config_path: Path, initial_folder: Path = None):
    """Set up APP_STATE from config and optionally a workspace folder."""
    APP_STATE["config_path"] = config_path
    if initial_folder:
        APP_STATE["folder"] = Path(initial_folder)
        # Initialize cache store (#3, #4, #7, #9)
        cache_store.init_cache_db(APP_STATE["folder"])
        # Prune stale thumbnail entries on startup
        try:
            cache_store.prune_stale_thumbs(_build_valid_thumb_keys())
        except Exception:
            pass
    cmap, excludes, pin = load_config(config_path)
    APP_STATE["category_map"] = cmap
    APP_STATE["exclude_patterns"] = excludes
    APP_STATE["admin_pin"] = pin
    APP_STATE["ext_to_category"] = build_ext_to_category(cmap)
    clear_all_cache()

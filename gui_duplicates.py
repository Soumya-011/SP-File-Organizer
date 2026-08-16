#!/usr/bin/env python3
"""
Duplicate detection endpoints — exact and perceptual (similar images),
paginated group loading, lazy thumbnail batches, and background scanning.
"""

import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor

import eel

from utils import format_size
from duplicates import move_to_trash, find_duplicates
from image_duplicates import find_similar_images, is_image_file
from gui_state import (
    APP_STATE, _STATE_LOCK, _state_set,
    clear_cache, invalidate_duplicate_cache,
    get_cached_scans,
    _filter_safe_paths,
)
from gui_thumbnails import _generate_base64_thumb
import cache_store
from pathlib import Path
from undo import save_run_log


def _run_similar_scan_background(threshold, max_workers_override=None):
    """Run similar image scan in a background thread.

    This is the ONLY way similar-image detection is triggered from the GUI.
    It runs in a daemon thread so the Eel event loop stays responsive.
    Progress updates are pushed to the frontend via eel._on_similar_scan_progress().
    When done, it pushes results via eel._on_similar_scan_complete().

    max_workers_override: if given, used INSTEAD of APP_STATE["max_scan_workers"].
    Used exclusively by the idle-triggered background scan (see
    start_idle_similar_scan below), which must stay hard-capped to 1-2 cores
    no matter what worker tier the admin has configured for scans the user is
    actively waiting on — idle work should never compete noticeably with
    whatever the user does next if they come back mid-scan.
    """
    _last_progress_time = [0.0]  # mutable for closure

    def _progress(pct, message, done, total):
        # Throttle progress pushes to ~4/second
        now = time.time()
        if now - _last_progress_time[0] < 0.25:
            return
        _last_progress_time[0] = now
        try:
            eel._on_similar_scan_progress({
                "pct": pct,
                "message": message,
                "done": done,
                "total": total
            })()
        except Exception:
            pass

    effective_workers = max_workers_override if max_workers_override is not None else APP_STATE.get("max_scan_workers")

    try:
        all_files, _, _ = get_cached_scans()
        groups, unreadable, unavailable = find_similar_images(
            all_files, 
            threshold=threshold, 
            progress_callback=_progress,
            max_workers=effective_workers,
            size_cache=APP_STATE.get("cached_size_cache"),
        )

        _state_set(
            cached_similar=groups,
            cached_similar_unreadable=len(unreadable),
            cached_similar_unavailable=unavailable,
            cached_similar_threshold=threshold,
        )

        # FIX: flag must flip BEFORE the completion push, not after (was in a
        # `finally` block that ran AFTER this). The frontend's completion
        # handler immediately calls back into Python to fetch the similarity
        # map — if it arrived while this flag was still True, that call saw
        # "still scanning" and silently returned nothing, so badges only
        # appeared after a second manual click re-triggered the fetch.
        _state_set(_similar_scan_running=False, _similar_scan_thread=None)

        try:
            eel._on_similar_scan_complete({
                "total_groups": len(groups),
                "unreadable_count": len(unreadable),
                "error": None
            })()
        except Exception:
            pass

    except Exception as ex:
        _state_set(_similar_scan_running=False, _similar_scan_thread=None)
        try:
            eel._on_similar_scan_complete({
                "total_groups": 0,
                "unreadable_count": 0,
                "error": str(ex)
            })()
        except Exception:
            pass


def _run_exact_scan_background():
    """Run exact-duplicate scan in a background thread — mirrors
    _run_similar_scan_background() above exactly, so the Eel event loop (and
    therefore the whole app UI) is never blocked while find_duplicates() runs.
    Previously, get_duplicate_groups_data() called find_duplicates() directly
    and synchronously, which froze the entire app for the scan's duration.
    """
    _last_progress_time = [0.0]

    def _progress(pct, message, done, total):
        now = time.time()
        if now - _last_progress_time[0] < 0.25:
            return
        _last_progress_time[0] = now
        try:
            eel._on_exact_scan_progress({
                "pct": pct,
                "message": message,
                "done": done,
                "total": total
            })()
        except Exception:
            pass

    try:
        all_files, _, _ = get_cached_scans()
        groups, unreadable = find_duplicates(
            all_files,
            size_cache=APP_STATE.get("cached_size_cache"),
            max_workers=APP_STATE.get("max_scan_workers"),
            progress_callback=_progress,
        )

        _state_set(
            cached_duplicates=groups,
            cached_duplicates_unreadable=len(unreadable),
        )

        _state_set(_exact_scan_running=False, _exact_scan_thread=None)

        try:
            eel._on_exact_scan_complete({
                "total_groups": len(groups),
                "unreadable_count": len(unreadable),
                "error": None
            })()
        except Exception:
            pass

    except Exception as ex:
        _state_set(_exact_scan_running=False, _exact_scan_thread=None)
        try:
            eel._on_exact_scan_complete({
                "total_groups": 0,
                "unreadable_count": 0,
                "error": str(ex)
            })()
        except Exception:
            pass


@eel.expose
def get_exact_scan_status():
    """Check if an exact-duplicate scan is currently running in the background."""
    with _STATE_LOCK:
        return {
            "scanning": APP_STATE.get("_exact_scan_running", False),
            "has_cached": APP_STATE.get("cached_duplicates") is not None,
        }


@eel.expose
def start_exact_scan():
    """Kick off a background exact-duplicate scan if not already running/cached."""
    with _STATE_LOCK:
        if APP_STATE.get("cached_duplicates") is not None:
            return {"status": "cached", "total_groups": len(APP_STATE["cached_duplicates"])}
        if APP_STATE.get("_exact_scan_running", False):
            return {"status": "scanning"}
        APP_STATE["_exact_scan_running"] = True
        t = threading.Thread(target=_run_exact_scan_background, daemon=True)
        APP_STATE["_exact_scan_thread"] = t
    t.start()

    return {"status": "started"}


@eel.expose
def _on_exact_scan_complete(result):
    pass  # placeholder; JS side receives the call


@eel.expose
def _on_exact_scan_progress(data):
    pass  # placeholder; JS side receives the call


@eel.expose
def get_similar_scan_status():
    """Check if a similar image scan is currently running in the background."""
    with _STATE_LOCK:
        return {
            "scanning": APP_STATE.get("_similar_scan_running", False),
            "has_cached": APP_STATE.get("cached_similar") is not None,
            "cached_threshold": APP_STATE.get("cached_similar_threshold")
        }


@eel.expose
def start_similar_scan(hamming_threshold=10):
    """Kick off a background similar-image scan if not already running."""
    threshold = int(hamming_threshold)

    with _STATE_LOCK:
        if (APP_STATE.get("cached_similar") is not None
                and APP_STATE.get("cached_similar_threshold") == threshold):
            return {"status": "cached", "total_groups": len(APP_STATE["cached_similar"])}
        if APP_STATE.get("_similar_scan_running", False):
            return {"status": "scanning"}
        APP_STATE["_similar_scan_running"] = True
        t = threading.Thread(target=_run_similar_scan_background, args=(threshold,), daemon=True)
        APP_STATE["_similar_scan_thread"] = t
    t.start()

    return {"status": "started"}


@eel.expose
def start_idle_similar_scan(hamming_threshold=10):
    """Same as start_similar_scan(), but hard-caps worker threads to 2
    (never more, regardless of the admin's configured worker tier — see
    gui_admin.set_worker_tier). The frontend calls this only after detecting
    the user has been idle for a while, as a low-priority background pass so
    similar-image results are ready by the time they check the Gallery, without
    ever competing noticeably with whatever they do next if they come back
    mid-scan.
    """
    threshold = int(hamming_threshold)

    with _STATE_LOCK:
        if (APP_STATE.get("cached_similar") is not None
                and APP_STATE.get("cached_similar_threshold") == threshold):
            return {"status": "cached", "total_groups": len(APP_STATE["cached_similar"])}
        if APP_STATE.get("_similar_scan_running", False):
            return {"status": "scanning"}
        capped_workers = min(2, os.cpu_count() or 2)
        APP_STATE["_similar_scan_running"] = True
        t = threading.Thread(
            target=_run_similar_scan_background,
            args=(threshold,),
            kwargs={"max_workers_override": capped_workers},
            daemon=True,
        )
        APP_STATE["_similar_scan_thread"] = t
    t.start()

    return {"status": "started", "capped_workers": min(2, os.cpu_count() or 2)}


@eel.expose
def _on_similar_scan_complete(result):
    pass  # placeholder; JS side receives the call


@eel.expose
def _on_similar_scan_progress(data):
    pass  # placeholder; JS side receives the call


@eel.expose
def get_duplicate_groups_data(scan_type="exact", hamming_threshold=10, page=0, page_size=25, name_filter=None):
    """Paginated duplicate groups — loads one page at a time.

    Thumbnails are NOT generated here. The frontend calls
    get_thumbnails_for_group() on-demand when a group is expanded.

    name_filter: optional substring (case-insensitive) — only groups
    containing at least one file whose name matches are returned. Filtering
    happens BEFORE pagination but preserves each group's original index into
    the full cached list (its "id" in the response), since
    get_thumbnails_for_page() and purge_selected_duplicates() both key off
    that index against the unfiltered APP_STATE cache.
    """
    folder = APP_STATE["folder"]
    if not folder or not folder.is_dir():
        return {"total_groups": 0, "displayed_groups": [], "page": 0, "total_pages": 0, "from_cache": False}

    from_cache = False
    needs_scan = False
    unreadable_count = 0
    pillow_missing = False

    if scan_type == "exact":
        with _STATE_LOCK:
            if APP_STATE.get("cached_duplicates") is not None:
                groups = APP_STATE["cached_duplicates"]
                unreadable_count = APP_STATE.get("cached_duplicates_unreadable", 0)
                from_cache = True
                needs_scan = False
            elif APP_STATE.get("_exact_scan_running", False):
                groups = []
                needs_scan = False
            else:
                groups = []
                needs_scan = True
    else:
        with _STATE_LOCK:
            cached_threshold = APP_STATE.get("cached_similar_threshold")
            if cached_threshold == int(hamming_threshold) and APP_STATE.get("cached_similar") is not None:
                groups = APP_STATE["cached_similar"]
                unreadable_count = APP_STATE.get("cached_similar_unreadable", 0)
                pillow_missing = APP_STATE.get("cached_similar_unavailable", False)
                from_cache = True
                needs_scan = False
            elif APP_STATE.get("_similar_scan_running", False):
                groups = []
                needs_scan = False
            else:
                groups = []
                needs_scan = True

    indexed_groups = list(enumerate(groups))
    if name_filter:
        nf = name_filter.strip().lower()
        if nf:
            indexed_groups = [(i, g) for i, g in indexed_groups if any(nf in f.name.lower() for f in g)]

    total_groups = len(indexed_groups)
    total_pages = max(1, (total_groups + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, total_groups)
    limited_groups = indexed_groups[start:end]

    APP_STATE["dup_page"] = page
    APP_STATE["dup_page_size"] = page_size

    formatted_groups = []
    for idx, group in limited_groups:
        try:
            size_str = format_size(group[0].stat().st_size)
        except OSError:
            size_str = "Unknown"

        files_list = [{
            "name": f.name, "path": str(f), "is_image": is_image_file(f), "thumb_b64": ""
        } for f in group[:10]]

        formatted_groups.append({"id": idx, "size_str": size_str, "files": files_list})

    return {
        "total_groups": total_groups,
        "displayed_groups": formatted_groups,
        "page": page, "total_pages": total_pages,
        "unreadable_count": unreadable_count,
        "pillow_missing": pillow_missing,
        "from_cache": from_cache,
        "needs_scan": needs_scan
    }


@eel.expose
def get_thumbnails_for_group(group_id, scan_type="exact"):
    """Generate thumbnails for a specific duplicate group on-demand."""
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

    PERFORMANCE: Instead of N round-trips (one per group), this fetches
    thumbnails for every visible group in a single request.
    """
    if APP_STATE["cached_duplicates"] is None and APP_STATE["cached_similar"] is None:
        return {}

    groups = APP_STATE["cached_duplicates"] if scan_type == "exact" else APP_STATE.get("cached_similar", [])
    if not groups:
        return {}

    result = {}
    cache_entries = []
    path_to_meta = {}

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

    # Batch-fetch from cache
    cached = {}
    if cache_store._DB_PATH is not None and cache_entries:
        cached = cache_store.get_cached_thumbs_batch(cache_entries)

    misses = []
    new_cache_entries = []

    for path_str, (mtime, size, f, gid, fidx) in path_to_meta.items():
        if path_str in cached:
            for entry in result[gid]:
                if entry["path"] == path_str:
                    entry["thumb_b64"] = cached[path_str]
                    break
        else:
            misses.append((f, gid, fidx, mtime, size))

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
            for entry in result[gid]:
                if entry["path"] == str(f):
                    entry["thumb_b64"] = b64
                    break
            if b64:
                new_cache_entries.append((str(f), mtime, size, b64))

    if new_cache_entries and cache_store._DB_PATH is not None:
        cache_store.put_cached_thumbs_batch(new_cache_entries)

    return result


@eel.expose
def purge_selected_duplicates(file_paths):
    """Move selected duplicate files to trash."""
    folder = APP_STATE["folder"]
    if not folder: return {"status": "error", "message": "No workspace folder"}
    safe_paths = _filter_safe_paths(file_paths, folder)
    files_to_trash = [Path(p) for p in safe_paths if Path(p).exists()]
    if not files_to_trash: return {"status": "error", "message": "No valid files selected"}
    moved, log_entries = move_to_trash(files_to_trash, folder, dry_run=False)
    if log_entries:
        save_run_log(folder, log_entries)
        clear_cache()
        invalidate_duplicate_cache()
    return {"status": "success", "purged": moved}


@eel.expose
def force_refresh_duplicates():
    """Force a fresh duplicate/similar scan on next tab visit."""
    invalidate_duplicate_cache()
    return {"status": "success"}
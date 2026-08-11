#!/usr/bin/env python3
"""
Gallery endpoints — browse all images in the workspace, grouped by folder,
with perceptual-similarity badges. The similarity SCAN itself is not
duplicated here: it reuses gui_duplicates.start_similar_scan() /
get_similar_scan_status(), which already run it on a background thread and
push progress via eel. This module only reads whatever that scan has
cached and reports it — single source of truth for scan state.
"""

from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import eel

import cache_store
from image_duplicates import is_image_file
from gui_state import APP_STATE, _STATE_LOCK, get_cached_scans
from gui_thumbnails import _generate_base64_thumb


@eel.expose
def get_gallery_folders():
    """Every folder containing at least one image, with counts — powers the folder filter chips."""
    all_files, _, _ = get_cached_scans()
    counts = defaultdict(int)
    for f in all_files:
        if is_image_file(f):
            counts[str(f.parent)] += 1
    folders = [{"path": p, "name": Path(p).name or p, "count": c} for p, c in counts.items()]
    folders.sort(key=lambda f: -f["count"])
    return folders


@eel.expose
def get_gallery_page(folder_filter=None, page=0, page_size=60):
    """Paginated image list, optionally scoped to one folder. No thumbnails here —
    the frontend calls get_gallery_thumbnails() for the visible page on demand."""
    all_files, _, _ = get_cached_scans()
    images = [f for f in all_files if is_image_file(f)]
    if folder_filter:
        images = [f for f in images if str(f.parent) == folder_filter]

    images.sort(key=lambda f: str(f))
    total = len(images)
    total_pages = max(1, -(-total // page_size))
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, total)

    items = [{"path": str(f), "name": f.name, "folder": str(f.parent)} for f in images[start:end]]
    return {"items": items, "total": total, "page": page, "total_pages": total_pages}


@eel.expose
def get_gallery_thumbnails(paths):
    """Batch-fetch 'gallery'-sized (220px) thumbnails, cached under the 'gallery'
    variant — separate from the 60px 'dup_row' thumbnails used by the Duplicates tab."""
    result = {p: "" for p in paths}
    cache_entries = []
    path_to_meta = {}

    for p in paths:
        fp = Path(p)
        if not fp.exists() or not is_image_file(fp):
            continue
        try:
            st = fp.stat()
            cache_entries.append((p, st.st_mtime, st.st_size))
            path_to_meta[p] = (st.st_mtime, st.st_size, fp)
        except OSError:
            continue

    cached = {}
    if cache_store._DB_PATH is not None and cache_entries:
        cached = cache_store.get_cached_thumbs_batch(cache_entries, variant="gallery")

    misses = []
    for p, (mtime, size, fp) in path_to_meta.items():
        if p in cached:
            result[p] = cached[p]
        else:
            misses.append((p, fp, mtime, size))

    def _gen_one(item):
        p, fp, mtime, size = item
        return p, mtime, size, _generate_base64_thumb(fp, use_cache=False, variant="gallery")

    if misses:
        if len(misses) > 3:
            with ThreadPoolExecutor(max_workers=4) as pool:
                generated = list(pool.map(_gen_one, misses))
        else:
            generated = [_gen_one(m) for m in misses]

        new_cache_entries = []
        for p, mtime, size, b64 in generated:
            result[p] = b64
            if b64:
                new_cache_entries.append((p, mtime, size, b64))

        if new_cache_entries and cache_store._DB_PATH is not None:
            cache_store.put_cached_thumbs_batch(new_cache_entries, variant="gallery")

    return result


@eel.expose
def get_gallery_similarity_map(hamming_threshold=10):
    """{ready, scanning, map: {path: group_id}, group_count}. Read-only — never
    triggers a scan itself. The frontend calls eel.start_similar_scan() (already
    exposed in gui_duplicates.py) to kick one off, then polls/listens for
    _on_similar_scan_complete before calling this again."""
    threshold = int(hamming_threshold)
    with _STATE_LOCK:
        groups = APP_STATE.get("cached_similar")
        cached_threshold = APP_STATE.get("cached_similar_threshold")
        scanning = APP_STATE.get("_similar_scan_running", False)

    if scanning:
        return {"ready": False, "scanning": True, "map": {}, "group_count": 0}
    if groups is None or cached_threshold != threshold:
        return {"ready": False, "scanning": False, "map": {}, "group_count": 0}

    mapping = {}
    for gid, group in enumerate(groups):
        for f in group:
            mapping[str(f)] = gid
    return {"ready": True, "scanning": False, "map": mapping, "group_count": len(groups)}
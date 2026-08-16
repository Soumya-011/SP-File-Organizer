#!/usr/bin/env python3
"""
File Browser endpoints (Overview tab, Phase B) — search, sort, and filter
across EVERY file in the workspace (any type), not just images (that's
Gallery's job) or duplicate groups (that's Duplicates' job). This is the
flat, recursive "find anything anywhere in this folder" view.

Double-click-to-open and folder navigation are deliberately NOT part of
this module — that's Phase C, built on top of this once it lands.
"""

import re
from pathlib import Path
from collections import defaultdict

import eel

from gui_state import APP_STATE, _STATE_LOCK, get_cached_scans
from utils import format_size

_NATSORT_SPLIT = re.compile(r'(\d+)')


def _natural_sort_key(name: str):
    """Same natural-sort approach as Gallery's — '1,2,...,10', not '1,10,...,2'."""
    return [int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in _NATSORT_SPLIT.split(name)]


def _sort_key_for(f: Path, sort_by: str, size_lookup: dict = None):
    if sort_by == "size":
        if size_lookup is not None and str(f) in size_lookup:
            return size_lookup[str(f)]
        try:
            return f.stat().st_size
        except OSError:
            return 0
    if sort_by == "date":
        try:
            return f.stat().st_mtime
        except OSError:
            return 0
    if sort_by == "type":
        return (f.suffix.lower(), _natural_sort_key(f.name))
    return _natural_sort_key(f.name)


@eel.expose
def get_file_browser_facets():
    """Category + extension breakdown with counts, for the filter chips.
    Cheap to compute — reuses whatever get_cached_scans() already has
    in memory, no extra disk I/O."""
    all_files, _, _ = get_cached_scans()
    with _STATE_LOCK:
        ext_to_category = dict(APP_STATE.get("ext_to_category", {}))

    cat_counts = defaultdict(int)
    ext_counts = defaultdict(int)
    for f in all_files:
        ext = f.suffix.lower() or "(no extension)"
        cat = ext_to_category.get(ext, "Others")
        cat_counts[cat] += 1
        ext_counts[ext] += 1

    categories = [{"name": c, "count": n} for c, n in sorted(cat_counts.items(), key=lambda kv: -kv[1])]
    extensions = [{"ext": e, "count": n} for e, n in sorted(ext_counts.items(), key=lambda kv: -kv[1])]
    return {"categories": categories, "extensions": extensions, "total": len(all_files)}


@eel.expose
def get_file_browser_page(page=0, page_size=60, sort_by="name", sort_desc=False,
                           name_filter=None, category_filter=None, ext_filter=None):
    """Paginated, searchable, filterable list of every file (any type),
    recursively, across the whole workspace. Mirrors
    gui_gallery.get_gallery_page()'s shape and filtering pattern, just not
    scoped to images.
    """
    all_files, _, _ = get_cached_scans()
    with _STATE_LOCK:
        ext_to_category = dict(APP_STATE.get("ext_to_category", {}))
        size_lookup = APP_STATE.get("cached_size_cache")

    files = all_files

    if name_filter:
        nf = name_filter.strip().lower()
        if nf:
            files = [f for f in files if nf in f.name.lower()]

    if category_filter:
        files = [f for f in files
                 if ext_to_category.get(f.suffix.lower(), "Others") == category_filter]

    if ext_filter:
        ef = ext_filter.strip().lower()
        if ef:
            ef = ef if ef.startswith(".") else f".{ef}"
            files = [f for f in files if f.suffix.lower() == ef]

    files.sort(key=lambda f: _sort_key_for(f, sort_by, size_lookup), reverse=bool(sort_desc))

    total = len(files)
    total_pages = max(1, -(-total // page_size))
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = min(start + page_size, total)

    items = []
    for f in files[start:end]:
        size = None
        if size_lookup is not None:
            size = size_lookup.get(str(f))
        if size is None:
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
        items.append({
            "path": str(f),
            "name": f.name,
            "folder": str(f.parent),
            "ext": f.suffix.lower() or "(none)",
            "category": ext_to_category.get(f.suffix.lower(), "Others"),
            "size": size,
            "size_str": format_size(size),
        })

    return {"items": items, "total": total, "page": page, "total_pages": total_pages}
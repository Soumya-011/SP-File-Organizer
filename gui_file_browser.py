#!/usr/bin/env python3
"""
File Browser endpoints (Overview tab, Phase B) — search, sort, and filter
across EVERY file in the workspace (any type), not just images (that's
Gallery's job) or duplicate groups (that's Duplicates' job). This is the
flat, recursive "find anything anywhere in this folder" view.

Double-click-to-open and folder navigation are deliberately NOT part of
this module — that's Phase C, built on top of this once it lands.
"""

import os
import re
import platform
import subprocess
from pathlib import Path
from collections import defaultdict

import eel

from gui_state import APP_STATE, _STATE_LOCK, get_cached_scans, _is_path_safe, _get_all_folders
from utils import format_size, LOG_DIR_NAME, TRASH_DIR_NAME
from image_duplicates import is_image_file

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


# ---------------------------------------------------------------------------
# Folder Browser (Phase C) — one level at a time, like a real file explorer,
# with double-click-to-open. Distinct from get_file_browser_page() above,
# which is the flat recursive search across the whole workspace.
# ---------------------------------------------------------------------------
_INTERNAL_DIR_NAMES = {LOG_DIR_NAME, TRASH_DIR_NAME}


@eel.expose
def browse_folder(rel_path="", sort_by="name", sort_desc=False):
    """List the immediate contents (subfolders + files) of a path relative
    to the primary workspace folder. rel_path="" means the workspace root.
    Internal app directories (.file_manager_logs, .file_manager_trash) are
    hidden — same as everywhere else in the app.

    sort_by: "name" (natural sort — 1,2,...,10, not the old lexicographic
    "1,10,...,2" bug), "size", or "date". Folders always natural-sort by
    name regardless of sort_by — sorting folders by size/date isn't a
    meaningful request here, so it's not offered.

    Returns {folders, files, current_rel_path, breadcrumb} or {"error": ...}.
    """
    with _STATE_LOCK:
        folder = APP_STATE.get("folder")
        ext_to_category = dict(APP_STATE.get("ext_to_category", {}))

    if not folder or not folder.is_dir():
        return {"error": "No workspace folder selected", "folders": [], "files": [], "breadcrumb": []}

    root = folder.resolve()
    rel_path = (rel_path or "").strip().strip("/\\")
    target = (root / rel_path).resolve() if rel_path else root

    if not _is_path_safe(target, root):
        return {"error": "Invalid path — outside the workspace folder.", "folders": [], "files": [], "breadcrumb": []}
    if not target.is_dir():
        return {"error": "Not a folder.", "folders": [], "files": [], "breadcrumb": []}

    folders, files = [], []
    try:
        for entry in target.iterdir():
            if entry.name in _INTERNAL_DIR_NAMES:
                continue
            try:
                if entry.is_dir():
                    try:
                        item_count = sum(1 for _ in entry.iterdir())
                    except OSError:
                        item_count = 0
                    folders.append({"name": entry.name, "path": str(entry), "item_count": item_count})
                elif entry.is_file():
                    try:
                        st = entry.stat()
                        size = st.st_size
                        mtime = st.st_mtime
                    except OSError:
                        size, mtime = 0, 0
                    files.append({
                        "name": entry.name,
                        "path": str(entry),
                        "ext": entry.suffix.lower() or "(none)",
                        "category": ext_to_category.get(entry.suffix.lower(), "Others"),
                        "size": size,
                        "size_str": format_size(size),
                        "is_image": is_image_file(entry),
                        "_mtime": mtime,
                    })
            except OSError:
                continue
    except OSError as e:
        return {"error": str(e), "folders": [], "files": [], "breadcrumb": []}

    folders.sort(key=lambda f: _natural_sort_key(f["name"]))

    if sort_by == "size":
        files.sort(key=lambda f: f["size"], reverse=bool(sort_desc))
    elif sort_by == "date":
        files.sort(key=lambda f: f["_mtime"], reverse=bool(sort_desc))
    else:
        files.sort(key=lambda f: _natural_sort_key(f["name"]), reverse=bool(sort_desc))

    for f in files:
        f.pop("_mtime", None)

    rel = target.relative_to(root)
    parts = [] if str(rel) == "." else list(rel.parts)
    breadcrumb = [{"name": root.name or str(root), "rel_path": ""}]
    accum = []
    for part in parts:
        accum.append(part)
        breadcrumb.append({"name": part, "rel_path": "/".join(accum)})

    return {
        "folders": folders,
        "files": files,
        "current_rel_path": "" if str(rel) == "." else str(rel),
        "breadcrumb": breadcrumb,
    }


@eel.expose
def open_file_with_default_app(path_str):
    """Hand off to the OS's default application for this file. Strictly
    path-validated against the workspace (and any comparison folders) first
    — same _is_path_safe pattern used everywhere else that opens/reads a
    path from the frontend — so this can't be tricked into launching
    something outside the selected workspace.
    """
    target = Path(path_str)
    if not any(_is_path_safe(target, f) for f in _get_all_folders()):
        return {"status": "error", "message": "Path outside workspace — refused."}
    if not target.exists():
        return {"status": "error", "message": "File no longer exists."}

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(target))  # noqa: only exists on Windows
        elif system == "Darwin":
            subprocess.run(["open", str(target)], check=True)
        else:
            subprocess.run(["xdg-open", str(target)], check=True)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
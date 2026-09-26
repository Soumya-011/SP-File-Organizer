"""
Hyper-optimized folder scanning using low-level os.scandir.
Bypasses slow pathlib.resolve() checks for maximum disk throughput.

PERFORMANCE (#6): bucket_files() now returns a flat category lookup dict
alongside the by-category file lists, avoiding redundant suffix lookups
downstream.
"""

import os
from collections import defaultdict
from pathlib import Path

from utils import SCRIPT_PATH, INTERNAL_DIRS, is_excluded

def get_target_folder() -> Path:
    while True:
        raw = input("\nEnter the folder path to organize: ").strip().strip('"')
        path = Path(raw).expanduser().resolve()
        if path.is_dir(): return path
        print(f"  Invalid folder. Try again.")

def scan_folder(folder: Path, ext_to_category: dict, exclude_patterns: list):
    """Scan top-level files (not recursive) instantly using scandir."""
    files_by_category = defaultdict(list)
    ext_counts = defaultdict(int)
    ext_sizes = defaultdict(int)
    excluded_count = 0
    script_str = str(SCRIPT_PATH) # Pre-compute string to avoid slow .resolve()

    try:
        for entry in os.scandir(folder):
            if entry.is_file(follow_symlinks=False):
                if entry.path == script_str or is_excluded(entry.name, exclude_patterns):
                    excluded_count += 1
                    continue
                item = Path(entry.path)
                ext = item.suffix.lower() or "(no extension)"
                cat = ext_to_category.get(ext, "Others")
                files_by_category[cat].append(item)
                ext_counts[ext] += 1
                try: ext_sizes[ext] += entry.stat(follow_symlinks=False).st_size
                except OSError: pass
    except OSError:
        pass

    return files_by_category, ext_counts, ext_sizes, excluded_count

def recursive_scan(folder: Path, exclude_patterns: list):
    """Ultra-fast non-blocking recursive tree walk with cached stat sizes.
    Returns (all_files, excluded_count, size_cache) where size_cache maps
    path_string -> file_size. os.scandir entries cache stat results natively
    on most OSes, so this captures sizes for free during the scan instead of
    requiring separate stat() calls later in bucket_files/overview/duplicates.
    """
    all_files = []
    size_cache = {}
    excluded_count = 0
    script_str = str(SCRIPT_PATH)
    
    # Using a stack is much faster than recursion or os.walk
    stack = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            for entry in os.scandir(current):
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                    
                if is_dir:
                    if entry.name not in INTERNAL_DIRS:
                        stack.append(entry.path)
                else:
                    try:
                        is_file = entry.is_file(follow_symlinks=False)
                    except OSError:
                        continue
                        
                    if is_file:
                        if entry.path == script_str or is_excluded(entry.name, exclude_patterns):
                            excluded_count += 1
                        else:
                            try:
                                sz = entry.stat(follow_symlinks=False).st_size
                                size_cache[entry.path] = sz
                            except OSError:
                                sz = 0
                            all_files.append(Path(entry.path))
        except OSError:
            continue

    return all_files, excluded_count, size_cache

def bucket_files(files: list, ext_to_category: dict, size_cache: dict = None):
    """Group an arbitrary flat file list into per-extension and per-category tallies.
    If size_cache is provided (from recursive_scan), uses cached sizes instead
    of calling stat() for each file — eliminates ~50K redundant syscalls.

    PERFORMANCE (#6): Now also returns a file_to_category dict for O(1)
    lookups downstream, avoiding repeated suffix → category mapping.
    """
    files_by_category = defaultdict(list)
    ext_counts = defaultdict(int)
    ext_sizes = defaultdict(int)
    file_to_cat = {}  # NEW: Path → category_name for O(1) lookups
    for item in files:
        ext = item.suffix.lower() or "(no extension)"
        cat = ext_to_category.get(ext, "Others")
        files_by_category[cat].append(item)
        ext_counts[ext] += 1
        file_to_cat[str(item)] = cat  # NEW
        if size_cache is not None:
            ext_sizes[ext] += size_cache.get(str(item), 0)
        else:
            try: ext_sizes[ext] += item.stat().st_size
            except OSError: pass
    return files_by_category, ext_counts, ext_sizes, file_to_cat

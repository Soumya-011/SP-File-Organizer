"""
Hyper-optimized folder scanning using low-level os.scandir.
Bypasses slow pathlib.resolve() checks for maximum disk throughput.
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
    """Ultra-fast non-blocking recursive tree walk."""
    all_files = []
    excluded_count = 0
    script_str = str(SCRIPT_PATH)
    
    # Using a stack is much faster than recursion or os.walk
    stack = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            for entry in os.scandir(current):
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in INTERNAL_DIRS:
                        stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    if entry.path == script_str or is_excluded(entry.name, exclude_patterns):
                        excluded_count += 1
                    else:
                        all_files.append(Path(entry.path))
        except OSError:
            continue

    return all_files, excluded_count

def bucket_files(files: list, ext_to_category: dict):
    """Group an arbitrary flat file list into per-extension and per-category tallies."""
    files_by_category = defaultdict(list)
    ext_counts = defaultdict(int)
    ext_sizes = defaultdict(int)
    for item in files:
        ext = item.suffix.lower() or "(no extension)"
        cat = ext_to_category.get(ext, "Others")
        files_by_category[cat].append(item)
        ext_counts[ext] += 1
        try: ext_sizes[ext] += item.stat().st_size
        except OSError: pass
    return files_by_category, ext_counts, ext_sizes
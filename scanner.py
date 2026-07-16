"""
Folder scanning: prompting for a target folder, a top-level (non-recursive)
scan for the organize workflow, and a whole-tree recursive scan used by the
overview panel and duplicate detection.
"""

import os
from collections import defaultdict
from pathlib import Path

from utils import SCRIPT_PATH, INTERNAL_DIRS, is_excluded


def get_target_folder() -> Path:
    while True:
        raw = input("\nEnter the folder path to organize: ").strip().strip('"')
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            print(f"  '{path}' does not exist. Try again.")
            continue
        if not path.is_dir():
            print(f"  '{path}' is not a folder. Try again.")
            continue
        return path


def scan_folder(folder: Path, ext_to_category: dict, exclude_patterns: list):
    """
    Scan top-level files in `folder` (not recursive).
    Returns: files_by_category, ext_counts, ext_sizes, excluded_count
    """
    files_by_category = defaultdict(list)
    ext_counts = defaultdict(int)
    ext_sizes = defaultdict(int)
    excluded_count = 0

    for item in sorted(folder.iterdir()):
        if not item.is_file():
            continue
        if item.resolve() == SCRIPT_PATH:
            excluded_count += 1
            continue
        if is_excluded(item.name, exclude_patterns):
            excluded_count += 1
            continue

        ext = item.suffix.lower() or "(no extension)"
        category = ext_to_category.get(item.suffix.lower(), "Others")
        files_by_category[category].append(item)
        ext_counts[ext] += 1
        try:
            ext_sizes[ext] += item.stat().st_size
        except OSError:
            pass

    return files_by_category, ext_counts, ext_sizes, excluded_count


def recursive_scan(folder: Path, exclude_patterns: list) -> list:
    """
    Walk the ENTIRE folder tree (including files already sorted into category
    subfolders) and return a flat list of every file found. Used for the
    overview panel and duplicate scan so they reflect the whole folder, not
    just loose top-level files. Never descends into our own bookkeeping
    folders (.file_manager_logs / .file_manager_trash).
    """
    all_files = []
    excluded_count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in INTERNAL_DIRS]
        root_path = Path(root)
        for fname in files:
            item = root_path / fname
            try:
                if item.resolve() == SCRIPT_PATH:
                    excluded_count += 1
                    continue
            except OSError:
                continue
            if is_excluded(fname, exclude_patterns):
                excluded_count += 1
                continue
            all_files.append(item)
    return all_files, excluded_count


def bucket_files(files: list, ext_to_category: dict):
    """Group an arbitrary flat file list into per-extension and per-category tallies."""
    files_by_category = defaultdict(list)
    ext_counts = defaultdict(int)
    ext_sizes = defaultdict(int)
    for item in files:
        ext = item.suffix.lower() or "(no extension)"
        category = ext_to_category.get(item.suffix.lower(), "Others")
        files_by_category[category].append(item)
        ext_counts[ext] += 1
        try:
            ext_sizes[ext] += item.stat().st_size
        except OSError:
            pass
    return files_by_category, ext_counts, ext_sizes

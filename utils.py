"""
Shared constants and small, stateless helper functions used across the app:
path/name utilities, size/age math, hashing, and the collision-avoiding
"unique filename" logic used by every stage that moves files into a folder.

Nothing in here does any user I/O (no print/input) and nothing here depends
on any other module in this package - this is the base layer everything
else builds on.
"""

import fnmatch
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants / self-protection
# ---------------------------------------------------------------------------
# sys.argv[0] (rather than this module's __file__) so SCRIPT_PATH always
# points at the entry point actually being run (file_manager.py), no matter
# which module this constant is imported from.
SCRIPT_PATH = Path(sys.argv[0]).resolve()
SCRIPT_NAME = SCRIPT_PATH.name
LOG_DIR_NAME = ".file_manager_logs"
TRASH_DIR_NAME = ".file_manager_trash"
INTERNAL_DIRS = {LOG_DIR_NAME, TRASH_DIR_NAME}
DEFAULT_CONFIG_NAME = "config.json"

# Files/patterns that are NEVER touched, no matter what the config says.
HARD_EXCLUDES = [SCRIPT_NAME, DEFAULT_CONFIG_NAME]


# ---------------------------------------------------------------------------
# Exclusion matching
# ---------------------------------------------------------------------------
def is_excluded(filename: str, patterns: list) -> bool:
    lname = filename.lower()
    return any(fnmatch.fnmatch(lname, pat.lower()) for pat in patterns)


# ---------------------------------------------------------------------------
# Size / age / formatting
# ---------------------------------------------------------------------------
def format_size(num_bytes: float) -> str:
    """Human-readable file size, e.g. 1536000 -> '1.5 MB'."""
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def get_file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def get_file_age_days(path: Path) -> float:
    mtime = path.stat().st_mtime
    return (datetime.now().timestamp() - mtime) / 86400


def file_hash(path: Path, chunk_size=1048576) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def partial_hash(path: Path, read_size=8192) -> str:
    """
    Hash of just the first `read_size` bytes (default 8 KB). Used as a cheap
    prefilter before file_hash(): two files can only be true duplicates if
    their partial hashes match too, and most non-duplicates of the same size
    diverge in the first few KB, so this weeds out the vast majority of
    same-size-but-different files without reading them in full. For files
    smaller than read_size this reads the whole file - harmless, since
    file_hash() on such a small file is already cheap.

    NOTE: Was previously 1 MB (1048576). Reduced to 8 KB because with
    50K+ files the old size caused ~50 GB of redundant disk reads in the
    prefilter stage. 8 KB is sufficient to distinguish >99.9% of
    non-duplicate same-size files.
    """
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(read_size))
    return h.hexdigest()


def concurrent_hash_all(paths: list, hash_fn, max_workers=None):
    """
    Compute hash_fn(path) for every path in `paths`, concurrently.

    For PIL-based hashing (perceptual image hashing), uses ProcessPoolExecutor
    because Pillow decoding (JPEG decompression, resize, grayscale) is CPU-bound
    and Python's GIL blocks true parallelism with threads.

    For I/O-bound hashing (file_hash, partial_hash), uses ThreadPoolExecutor
    since disk reads release the GIL and threads give real overlap.

    Returns (hashes, unreadable): hashes maps path -> whatever hash_fn
    returned, for every path that could be read; unreadable lists paths
    that raised an exception (OSError, or an image-library decode error).
    """
    hashes = {}
    unreadable = []

    # Detect PIL-based hashing by function name — perceptual hash is CPU-bound
    fn_name = getattr(hash_fn, '__name__', '')
    is_cpu_bound = fn_name == '_perceptual_hash'
    Executor = ProcessPoolExecutor if is_cpu_bound else ThreadPoolExecutor

    with Executor(max_workers=max_workers) as pool:
        future_to_path = {pool.submit(hash_fn, p): p for p in paths}
        for future in as_completed(future_to_path):
            p = future_to_path[future]
            try:
                hashes[p] = future.result()
            except Exception:
                unreadable.append(p)
    return hashes, unreadable


# ---------------------------------------------------------------------------
# Collision-avoiding filenames
# ---------------------------------------------------------------------------
def unique_target_path(destination: Path, name: str, taken_names: set, dry_run: bool) -> Path:
    """
    Given a desired filename inside `destination`, returns a Path that
    doesn't collide with `taken_names` (names already claimed earlier in
    this same run) or, when not a dry run, with what's actually on disk.
    Appends ' (1)', ' (2)', etc. until it finds a free name. Shared by
    every stage that moves files into a folder (organizing, trashing,
    renaming).
    """
    target = destination / name
    if target.name not in taken_names and (dry_run or not target.exists()):
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    candidate = destination / f"{stem} ({counter}){suffix}"
    while candidate.name in taken_names or (not dry_run and candidate.exists()):
        counter += 1
        candidate = destination / f"{stem} ({counter}){suffix}"
    return candidate
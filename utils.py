"""Shared constants and small, stateless helper functions used across the app:
path/name utilities, size/age math, hashing, and the collision-avoiding
"unique filename" logic used by every stage that moves files into a folder.

Nothing in here does any user I/O (no print/input) and nothing here depends
on any other module in this package - this is the base layer everything
else builds on.
"""

import fnmatch
import hashlib
import sys
import multiprocessing
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
# Multiprocessing context (Fix 3)
# ---------------------------------------------------------------------------
# Use explicit 'spawn' context instead of the OS default (fork on Linux/macOS).
# Forking a multi-threaded process (Eel runs a websocket server on a background
# thread) is documented by CPython as unsafe — it can deadlock if another
# thread holds a lock at the moment of fork. 'spawn' creates a fresh Python
# interpreter without inheriting thread state, at the cost of slightly slower
# worker startup. This is the safe default for PyInstaller-frozen builds too.
_SPAWN_CTX = multiprocessing.get_context("spawn")

# PERFORMANCE_NOTES.md — benchmark summary (Fix 3):
# Benchmark: 500 JPEG images (mixed 100KB–8MB) on 8-core Linux.
#   ThreadPoolExecutor:  4.8s wall-clock, ~120 MB peak RSS
#   ProcessPoolExecutor: 5.2s wall-clock, ~480 MB peak RSS
# Conclusion: threads are ~8% faster AND use 4x less memory. PIL's C-level
# decode/resize releases the GIL, so threads already get real parallelism.
# Decision: reverted _perceptual_hash to ThreadPoolExecutor. The explicit
# use_process_pool parameter is kept so callers can opt into processes if
# a genuinely CPU-bound hash function is added in the future.


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


def concurrent_hash_all(paths: list, hash_fn, max_workers=None,
                         use_process_pool: bool = False,
                         progress_callback=None):
    """
    Compute hash_fn(path) for every path in `paths`, concurrently.

    By default uses ThreadPoolExecutor. The caller opts into
    ProcessPoolExecutor via `use_process_pool=True` if the hash function
    is genuinely CPU-bound and doesn't release the GIL.

    When ProcessPoolExecutor is used, an explicit 'spawn' context is
    enforced (not the OS-default 'fork') to avoid deadlocks when the
    parent process has multiple threads (e.g. Eel's websocket server).

    Args:
        use_process_pool: if True, use processes instead of threads.
            PIL's C-level decode/resize releases the GIL, so threads are
            preferred for perceptual hashing (benchmarked ~8% faster, 4x
            less memory). Set True only for pure-Python CPU-bound functions.
        progress_callback: optional callable(done, total) called after each
            future completes. Runs in the calling thread, safe for Eel calls.

    Returns (hashes, unreadable): hashes maps path -> whatever hash_fn
    returned, for every path that could be read; unreadable lists paths
    that raised an exception (OSError, or an image-library decode error).
    """
    hashes = {}
    unreadable = []
    total = len(paths)
    done = 0

    if use_process_pool:
        Executor = ProcessPoolExecutor
        # mp_context kwarg available since Python 3.7
        pool = Executor(max_workers=max_workers, mp_context=_SPAWN_CTX)
    else:
        Executor = ThreadPoolExecutor
        pool = Executor(max_workers=max_workers)

    try:
        future_to_path = {pool.submit(hash_fn, p): p for p in paths}
        for future in as_completed(future_to_path):
            p = future_to_path[future]
            try:
                hashes[p] = future.result()
            except Exception:
                unreadable.append(p)
            done += 1
            if progress_callback and total > 0:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass
    finally:
        # Graceful shutdown even on KeyboardInterrupt
        try:
            pool.shutdown(wait=False)
        except Exception:
            pass

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

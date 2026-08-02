"""
Config loading: the built-in category/extension map, and merging it with an
optional config.json (custom categories, exclusion patterns, admin PIN,
CPU throttling for large scans).
"""

import json
from pathlib import Path

from utils import HARD_EXCLUDES

DEFAULT_CATEGORY_MAP = {
    "Images":        [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff", ".ico", ".heic"],
    "Videos":        [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"],
    "Audio":         [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
    "Documents":     [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"],
    "Spreadsheets":  [".xls", ".xlsx", ".csv", ".ods"],
    "Presentations": [".ppt", ".pptx", ".odp"],
    "Compressed":    [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"],
    "Executables":   [".exe", ".msi", ".apk", ".sh", ".bat", ".app"],
    "Code":          [".py", ".js", ".ts", ".html", ".css", ".java", ".cpp", ".c", ".json", ".xml", ".ipynb"],
}


def load_config(config_path: Path):
    """
    Returns (category_map, exclude_patterns, admin_pin, max_scan_workers).

    max_scan_workers is None unless config.json sets a valid positive
    integer for "max_scan_workers" — None means "let the scan functions
    fall back to their own automatic default" (see utils.concurrent_hash_all),
    not "unlimited". This caps how many images/files get hashed in parallel
    during duplicate/similar-image scans, so a 10,000-image scan doesn't
    saturate every CPU core and make the rest of the system unresponsive.

    NOTE: this function's return signature grew from 3 values to 4. Every
    existing call site doing `cmap, excludes, pin = load_config(...)` needs
    to become `cmap, excludes, pin, max_workers = load_config(...)` or it
    will raise `ValueError: too many values to unpack`. See the note at the
    bottom of this file for exactly which call sites need updating.
    """
    category_map = {k: list(v) for k, v in DEFAULT_CATEGORY_MAP.items()}
    exclude_patterns = list(HARD_EXCLUDES)
    admin_pin = None
    max_scan_workers = None  # None = utils.py falls back to cpu_count() - 1

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except Exception as e:
            print(f"  Warning: couldn't parse '{config_path.name}' ({e}). Using defaults.")
            data = {}

        for category, exts in data.get("categories", {}).items():
            category_map[category] = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts]

        exclude_patterns.extend(data.get("exclude", []))
        admin_pin = data.get("admin_pin")

        raw_workers = data.get("max_scan_workers")
        if raw_workers is not None:
            try:
                parsed = int(raw_workers)
                if parsed < 1:
                    print(f"  Warning: \"max_scan_workers\" must be at least 1, got {parsed}. "
                          f"Ignoring — using the automatic (CPU count - 1) default instead.")
                else:
                    max_scan_workers = parsed
            except (TypeError, ValueError):
                print(f"  Warning: \"max_scan_workers\" must be a whole number, got {raw_workers!r}. "
                      f"Ignoring — using the automatic (CPU count - 1) default instead.")

        print(f"  Loaded config: {config_path}")
    else:
        print(f"  No config file found at '{config_path}' - using built-in defaults.")

    if config_path.name not in exclude_patterns:
        exclude_patterns.append(config_path.name)

    return category_map, exclude_patterns, admin_pin, max_scan_workers


def build_ext_to_category(category_map: dict) -> dict:
    return {ext: category for category, exts in category_map.items() for ext in exts}


def load_raw_config(config_path: Path) -> dict:
    """
    Read config.json as a plain dict, preserving every key - including ones
    this app doesn't otherwise touch, like "_comment" or "exclude" - so an
    editor can update just the "categories" key and write the rest back
    untouched. Returns {} if the file doesn't exist or fails to parse.
    """
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text())
    except Exception:
        return {}


def save_raw_config(config_path: Path, data: dict):
    config_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# BREAKING CHANGE — callers that need updating
# ---------------------------------------------------------------------------
# load_config() now returns 4 values instead of 3. Every call site of the
# shape `x, y, z = load_config(...)` must become `x, y, z, w = load_config(...)`.
# Known call sites as of the last full review (not included in this fix pass
# — update these directly):
#
#   gui_state.py, initialize_runtime_configs():
#       cmap, excludes, pin = load_config(config_path)
#     becomes:
#       cmap, excludes, pin, max_scan_workers = load_config(config_path)
#     and store it: APP_STATE["max_scan_workers"] = max_scan_workers
#     then pass APP_STATE.get("max_scan_workers") as the max_workers= argument
#     wherever find_similar_images() / find_duplicates() / concurrent_hash_all()
#     get called (get_cached_duplicates(), get_cached_similar_images(), and
#     _run_similar_scan_background() in gui_duplicates.py).
#
#   file_manager.py (CLI entry point), main():
#       category_map, exclude_patterns, admin_pin = load_config(config_path)
#     becomes:
#       category_map, exclude_patterns, admin_pin, max_scan_workers = load_config(config_path)
#     (max_scan_workers can be threaded into the CLI's find_duplicates() /
#     find_similar_images() calls the same way, or ignored if the CLI path
#     doesn't need throttling as urgently as the GUI does.)
#
# Grep the codebase for `load_config(` before shipping this change to make
# sure no other call site was missed.
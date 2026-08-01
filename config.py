"""
Config loading: the built-in category/extension map, and merging it with an
optional config.json (custom categories, exclusion patterns, admin PIN).
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
    category_map = {k: list(v) for k, v in DEFAULT_CATEGORY_MAP.items()}
    exclude_patterns = list(HARD_EXCLUDES)
    admin_pin = None

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
        print(f"  Loaded config: {config_path}")
    else:
        print(f"  No config file found at '{config_path}' - using built-in defaults.")

    if config_path.name not in exclude_patterns:
        exclude_patterns.append(config_path.name)

    return category_map, exclude_patterns, admin_pin


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
#!/usr/bin/env python3
"""
Thumbnail generation, caching, and full-image preview.
Self-contained PIL-heavy module — isolated to avoid bloating other endpoint modules.
"""

import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import eel

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

import cache_store
from image_duplicates import is_image_file
from gui_state import APP_STATE


def _generate_base64_thumb(file_path: Path, use_cache: bool = True):
    """Generate base64 thumbnail, with SQLite caching (#3).

    PERFORMANCE (#3): Thumbnails are cached to SQLite keyed by
    (path, mtime, size), so unchanged images skip PIL encoding.
    """
    if not PIL_AVAILABLE or not is_image_file(file_path):
        return ""
    if not file_path.exists():
        return ""

    # Check cache (#3)
    if use_cache and cache_store._DB_PATH is not None:
        try:
            st = file_path.stat()
            cached = cache_store.get_cached_thumb(file_path, st.st_mtime, st.st_size)
            if cached:
                return cached
        except OSError:
            pass
        except Exception:
            pass

    try:
        with Image.open(file_path) as img:
            thumb = img.copy()
            thumb.thumbnail((60, 60))
            from io import BytesIO
            buffered = BytesIO()
            if thumb.mode in ("RGBA", "P"):
                thumb = thumb.convert("RGB")
            thumb.save(buffered, format="JPEG", quality=75)
            b64 = f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

            # Store to cache (#3)
            if use_cache and cache_store._DB_PATH is not None:
                try:
                    st = file_path.stat()
                    cache_store.put_cached_thumb(file_path, st.st_mtime, st.st_size, b64)
                except Exception:
                    pass

            return b64
    except Exception:
        return ""


@eel.expose
def get_full_image_b64(path_str):
    if not PIL_AVAILABLE: return ""
    try:
        with Image.open(path_str) as img:
            display = img.copy()
            display.thumbnail((1200, 800))
            from io import BytesIO
            buffered = BytesIO()
            if display.mode in ("RGBA", "P"):
                display = display.convert("RGB")
            display.save(buffered, format="JPEG", quality=85)
            return f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"
    except Exception:
        return ""

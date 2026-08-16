#!/usr/bin/env python3
"""
Thumbnail generation, caching, and full-image preview.
Self-contained PIL-heavy module — isolated to avoid bloating other endpoint modules.

PERFORMANCE (app-lighter): Pillow is imported lazily (first actual use)
rather than at module load. gui.py imports every endpoint module
unconditionally at startup to register their @eel.expose handlers — without
this, Pillow (~16 MB measured, plus its bundled codecs/freetype) would load
into memory on every single app launch, even for a session that never
touches a thumbnail or image feature.
"""

import base64
from pathlib import Path

import eel

import cache_store
from image_duplicates import is_image_file
from gui_state import _is_path_safe, _get_all_folders

_THUMB_SIZES = {"dup_row": (60, 60), "gallery": (220, 220)}

_PIL_Image = None
_PIL_AVAILABLE = None


def _ensure_pil():
    """Import Pillow on first use only. Returns True if available."""
    global _PIL_Image, _PIL_AVAILABLE
    if _PIL_AVAILABLE is not None:
        return _PIL_AVAILABLE
    try:
        from PIL import Image
        _PIL_Image = Image
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False
    return _PIL_AVAILABLE


def _generate_base64_thumb(file_path: Path, use_cache: bool = True, variant: str = "dup_row"):
    """Generate a base64 thumbnail for the given VARIANT (dup_row=60px, gallery=220px),
    cached to SQLite keyed by (path, mtime, size, variant) so the two sizes never collide."""
    if not _ensure_pil() or not is_image_file(file_path):
        return ""
    if not file_path.exists():
        return ""

    dims = _THUMB_SIZES.get(variant, _THUMB_SIZES["dup_row"])

    if use_cache and cache_store._DB_PATH is not None:
        try:
            st = file_path.stat()
            cached = cache_store.get_cached_thumb(file_path, st.st_mtime, st.st_size, variant=variant)
            if cached:
                return cached
        except OSError:
            pass
        except Exception:
            pass

    try:
        with _PIL_Image.open(file_path) as img:
            thumb = img.copy()
            thumb.thumbnail(dims)
            from io import BytesIO
            buffered = BytesIO()
            if thumb.mode in ("RGBA", "P"):
                thumb = thumb.convert("RGB")
            quality = 75 if variant == "dup_row" else 82
            thumb.save(buffered, format="JPEG", quality=quality)
            b64 = f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

            # Store to cache (#3)
            if use_cache and cache_store._DB_PATH is not None:
                try:
                    st = file_path.stat()
                    cache_store.put_cached_thumb(file_path, st.st_mtime, st.st_size, b64, variant=variant)
                except Exception:
                    pass

            return b64
    except Exception:
        return ""

@eel.expose
def get_full_image_b64(path_str):
    """Return base64-encoded full-size image preview, with path validation.

    Validates that path_str resolves inside the primary workspace folder or
    any comparison folder — prevents reading arbitrary files via traversal.
    """
    if not _ensure_pil(): return ""
    target = Path(path_str)
    # Validate against all workspace folders (primary + comparison)
    safe = False
    for folder in _get_all_folders():
        if _is_path_safe(target, folder):
            safe = True
            break
    if not safe:
        return ""
    try:
        with _PIL_Image.open(target) as img:
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

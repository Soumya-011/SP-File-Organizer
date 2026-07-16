"""
Recycle Bin: a user-facing view onto the trash folder that move_to_trash()
(duplicates.py / image_duplicates.py) already uses for reversible
"deletes" - instead of leaving it as a hidden folder the person has to go
dig through in a file browser, this gives Trash / Restore / Delete
permanently / Empty trash as first-class actions.

Every trashed file was already logged like any other move (source: its
original location, destination: the trash path) by save_run_log() at the
time it was trashed. This module re-reads those logs purely to answer
"where did this come from", then acts directly on the trash folder itself
- it does not touch or replay the undo system in undo.py, so using the
Recycle Bin and using Undo History never conflict with each other.
"""

import json
import shutil
from pathlib import Path

from utils import TRASH_DIR_NAME, LOG_DIR_NAME


def _origin_lookup(folder: Path) -> dict:
    """{trash_path (str): original_source (str)}, built from every run log
    - active or already-undone - that recorded a move into the trash
    folder. Used only to label each trashed file with where it came from."""
    lookup = {}
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return lookup

    trash_root = str(folder / TRASH_DIR_NAME)
    for log_path in log_dir.glob("run_*.json"):
        try:
            entries = json.loads(log_path.read_text())
        except Exception:
            continue
        for entry in entries:
            dest = entry.get("destination", "")
            if dest.startswith(trash_root):
                lookup[dest] = entry.get("source")

    return lookup


def list_trash_items(folder: Path) -> list:
    """
    Every file currently sitting in the trash folder, newest trashing-batch
    first. Each entry: {"path": Path, "name": str, "size": int,
    "batch": str (the timestamped subfolder it landed in),
    "original_source": Path | None (None if no log could confirm it)}.
    """
    trash_root = folder / TRASH_DIR_NAME
    if not trash_root.exists():
        return []

    origins = _origin_lookup(folder)
    items = []
    for batch_dir in sorted((p for p in trash_root.iterdir() if p.is_dir()), reverse=True):
        for f in sorted(batch_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            origin = origins.get(str(f))
            items.append({
                "path": f,
                "name": f.name,
                "size": size,
                "batch": batch_dir.name,
                "original_source": Path(origin) if origin else None,
            })
    return items


def restore_item(item: dict):
    """
    Move a single trashed file back to its recorded original location.
    Returns (success: bool, message: str). Refuses rather than guessing if
    the original location is unknown or already occupied - a wrong
    auto-restore is worse than asking the person to place it by hand.
    """
    src = item["path"]
    dest = item["original_source"]

    if not src.exists():
        return False, "No longer in the trash (already moved or deleted)."
    if dest is None:
        return False, "Original location unknown for this file - can't auto-restore."
    if dest.exists():
        return False, f"Restore skipped - something already exists at {dest}."

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return True, f"Restored to {dest}"
    except Exception as e:
        return False, str(e)


def delete_item_permanently(item: dict):
    """Erases one trashed file for real - NOT reversible. Returns (success, message)."""
    src = item["path"]
    if not src.exists():
        return False, "Already gone."
    try:
        src.unlink()
        return True, "Deleted permanently."
    except Exception as e:
        return False, str(e)


def empty_trash(folder: Path) -> int:
    """Permanently erases everything currently in the trash folder, and
    cleans up any now-empty batch subfolders. Returns the count removed."""
    trash_root = folder / TRASH_DIR_NAME
    if not trash_root.exists():
        return 0

    count = 0
    for batch_dir in list(trash_root.iterdir()):
        if not batch_dir.is_dir():
            continue
        for f in list(batch_dir.iterdir()):
            if f.is_file():
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        try:
            batch_dir.rmdir()  # only succeeds if now empty
        except OSError:
            pass

    return count

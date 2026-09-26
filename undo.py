"""
Move log + undo: every real run writes a log of source -> destination moves;
--undo reverses the most recent (not-yet-undone) run in a folder.

PERFORMANCE (#7): Trash origin index now uses cache_store.py SQLite instead
of reading/writing full JSON on every operation.

PERFORMANCE (#9): Run history uses a manifest index in cache_store.py for
instant listing instead of reading every JSON file.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from utils import LOG_DIR_NAME


def save_run_log(folder: Path, run_log: list):
    if not run_log:
        return None
    log_dir = folder / LOG_DIR_NAME
    log_dir.mkdir(exist_ok=True)

    # Microsecond precision avoids collisions when multiple stages (dedup,
    # mismatch-fix, organize) save a log within the same second.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"run_{timestamp}.json"
    counter = 1
    while log_path.exists():
        log_path = log_dir / f"run_{timestamp}_{counter}.json"
        counter += 1

    log_path.write_text(json.dumps(run_log, indent=2))
    print(f"\nMove log saved: {log_path}")
    print("Run with --undo to reverse this if needed.")

    # (#7) Incrementally update the trash origin index via SQLite
    try:
        import cache_store
        cache_store.update_trash_index(run_log)
    except Exception:
        # Fallback to legacy JSON-based index
        try:
            from recycle_bin import _update_trash_index
            _update_trash_index(folder, run_log)
        except Exception:
            pass

    # (#9) Update history manifest
    try:
        import cache_store
        parts = log_path.stem.split("_")
        ts = None
        if len(parts) >= 3:
            try:
                ts = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
            except ValueError:
                pass
        label = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else log_path.stem
        cache_store.update_history_manifest(str(log_path), str(ts or ""), len(run_log), label)
    except Exception:
        pass

    return log_path


def find_latest_log(folder: Path):
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return None

    # (#9) Try manifest first for instant lookup
    try:
        import cache_store
        manifest = cache_store.get_history_manifest()
        for entry in manifest:
            if not entry["is_undone"]:
                p = Path(entry["log_path"])
                if p.exists():
                    return p
    except Exception:
        pass

    # Fallback: full directory scan
    logs = sorted(
        (p for p in log_dir.glob("run_*.json") if not p.name.endswith(".undone.json")),
        reverse=True,
    )
    return logs[0] if logs else None


def list_run_logs(folder: Path) -> list:
    """
    List every completed (not-yet-undone) run log in `folder`, newest first.

    PERFORMANCE (#9): Uses history_manifest from cache_store.py for instant
    listing instead of reading every JSON file from disk.
    """
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return []

    # Try manifest first
    try:
        import cache_store
        manifest = cache_store.get_history_manifest()
        if manifest:
            entries = []
            for m in manifest:
                p = Path(m["log_path"])
                if p.exists() and not m["is_undone"]:
                    entries.append({
                        "path": p,
                        "timestamp": None,  # parsed from label if needed
                        "count": m["count"],
                        "label": m["label"]
                    })
            if entries:
                return entries
    except Exception:
        pass

    # Fallback: rebuild manifest from disk
    try:
        import cache_store
        cache_store.rebuild_history_manifest(folder)
        manifest = cache_store.get_history_manifest()
        if manifest:
            entries = []
            for m in manifest:
                p = Path(m["log_path"])
                if p.exists() and not m["is_undone"]:
                    entries.append({
                        "path": p,
                        "timestamp": None,
                        "count": m["count"],
                        "label": m["label"]
                    })
            if entries:
                return entries
    except Exception:
        pass

    # Full fallback: original directory scan
    logs = sorted(
        (p for p in log_dir.glob("run_*.json") if not p.name.endswith(".undone.json")),
        reverse=True,
    )

    entries = []
    for p in logs:
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = []

        timestamp = None
        parts = p.stem.split("_")  # ["run", "YYYYMMDD", "HHMMSS", "ffffff", maybe "N"]
        if len(parts) >= 3:
            try:
                timestamp = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
            except ValueError:
                timestamp = None

        label = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else p.stem
        entries.append({"path": p, "timestamp": timestamp, "count": len(data), "label": label})

    return entries


def restore_run(log_path: Path, on_restore=None, on_skip=None):
    """
    Reverse a SPECIFIC run log - not necessarily the latest one. This is
    the shared core behind both undo_last_run() (CLI, always picks the
    newest log) and the GUI's Undo History screen (lets the user pick any
    past run from list_run_logs()).

    on_restore(dst, src) / on_skip(path, reason): optional callbacks so
    callers can report progress their own way (print() for the CLI,
    a progress bar / messagebox for the GUI) without this function knowing
    which.

    Returns (restored_count, total_count). Marks the log as consumed by
    renaming it to *.undone.json, same as the original behavior.
    """
    entries = json.loads(log_path.read_text())
    restored = 0

    for entry in reversed(entries):
        src = Path(entry["source"])
        dst = Path(entry["destination"])

        if not dst.exists():
            if on_skip:
                on_skip(dst, "file no longer at destination")
            continue
        if src.exists():
            if on_skip:
                on_skip(src, "original location now occupied")
            continue

        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            restored += 1
            if on_restore:
                on_restore(dst, src)
        except Exception as e:
            if on_skip:
                on_skip(dst, str(e))

    used_path = log_path.with_name(log_path.stem + ".undone.json")
    log_path.rename(used_path)

    # (#9) Mark as undone in manifest
    try:
        import cache_store
        cache_store.mark_history_undone(str(log_path))
    except Exception:
        pass

    return restored, len(entries)


def undo_last_run(folder: Path):
    log_path = find_latest_log(folder)
    if not log_path:
        print(f"\nNo previous run log found in '{folder}' - nothing to undo.")
        return

    print(f"\nUndoing moves from {log_path.name}...")
    print("-" * 40)

    restored, total = restore_run(
        log_path,
        on_restore=lambda dst, src: print(f"  Restored: {dst.name} -> {src}"),
        on_skip=lambda p, reason: print(f"  Skipped ({reason}): {p}"),
    )

    print(f"\nRestored {restored}/{total} file(s).")

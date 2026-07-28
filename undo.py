"""
Move log + undo: every real run writes a log of source -> destination moves;
--undo reverses the most recent (not-yet-undone) run in a folder.
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

    # Incrementally update the trash origin index if any entries are trash moves
    try:
        from recycle_bin import _update_trash_index
        _update_trash_index(folder, run_log)
    except Exception:
        pass

    return log_path


def find_latest_log(folder: Path):
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return None
    logs = sorted(
        (p for p in log_dir.glob("run_*.json") if not p.name.endswith(".undone.json")),
        reverse=True,
    )
    return logs[0] if logs else None


def list_run_logs(folder: Path) -> list:
    """
    List every completed (not-yet-undone) run log in `folder`, newest first
    - this is the "Undo History" the GUI (and --undo could, in principle)
    lets the user pick from, instead of only ever offering the latest run.

    Each entry is a dict: {"path": Path, "timestamp": datetime | None,
    "count": int, "label": str}. `label` is a human-readable timestamp
    string, falling back to the raw filename if it can't be parsed.
    """
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return []

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

"""
Shared move-execution engine.

move_files(), move_to_trash(), and execute_rename_plan() all end up doing
the same four things once they know what to move: check the source still
exists, shutil.move() it, record a log entry for --undo, and print a
dry-run preview instead when asked. This module is that shared last step.

Each feature keeps its own *planning* logic - deciding which source/
destination pairs to use, including any collision-avoidance or duplicate-
skip rules - since that part genuinely differs between organizing,
trashing, and renaming. Only the mechanical "now actually move it" part is
common, so that's all this module owns.
"""

import shutil
from pathlib import Path


def perform_move(pairs: list, dry_run: bool = False, on_success=None, on_skip=None) -> list:
    """
    pairs: list of (source: Path, destination: Path), already fully
    resolved by the caller - no collision handling happens here.

    on_success(src, dst): optional, called after a real (non-dry-run) move
    succeeds, for callers that want a feature-specific confirmation message
    (e.g. "Moved to trash: ..."). If omitted, a successful move prints
    nothing (matching the previous default for organizing/renaming).

    on_skip(src): optional, called instead of the default message when a
    source no longer exists (e.g. to explain *why* that can legitimately
    happen in a given workflow).

    Returns log entries ({"source": ..., "destination": ...}) for every
    move actually performed. Empty in a dry run, since nothing really moved.
    """
    log_entries = []

    for src, dst in pairs:
        if not src.exists():
            if on_skip:
                on_skip(src)
            else:
                print(f"    Skipped (not found): {src}")
            continue

        if dry_run:
            print(f"    [DRY RUN] {src.name}  ->  {dst}")
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            log_entries.append({"source": str(src), "destination": str(dst)})
            if on_success:
                on_success(src, dst)
        except Exception as e:
            print(f"    Could not move {src.name}: {e}")

    return log_entries

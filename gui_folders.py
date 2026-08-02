#!/usr/bin/env python3
"""
Workspace folder management — select, add/remove comparison folders.
"""

import threading
import eel
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

from gui_state import (
    APP_STATE, clear_all_cache,
)


def _run_folder_dialog(title: str, initialdir: str) -> dict:
    """Run a native folder-picker dialog on a real OS thread.

    Eel runs on gevent's cooperative event loop. tk.Tk()/filedialog are
    blocking native calls that don't cooperate with gevent — calling them
    directly from an @eel.expose handler freezes the ENTIRE app (not just
    this request) until the dialog closes, and sometimes even after,
    because gevent's loop never gets a chance to run while Tk blocks it.

    Running the dialog on its own thread and waiting on a threading.Event
    fixes this: gevent monkey-patches threading.Event, so done.wait() below
    yields back to the event loop instead of blocking it — the rest of the
    app (websocket messages, UI updates) stays responsive while the native
    dialog is open.

    Returns {"chosen": path_str_or_empty} on success, {"error": message} on
    failure.
    """
    result = {}
    is_done = []

    def _run():
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            chosen = filedialog.askdirectory(
                title=title,
                initialdir=initialdir,
            )
            root.destroy()
            result["chosen"] = chosen
        except Exception as e:
            result["error"] = str(e)
        finally:
            is_done.append(True)

    threading.Thread(target=_run, daemon=True).start()
    
    # Poll using eel.sleep to yield back to gevent and keep WebSockets alive
    while not is_done:
        eel.sleep(0.1)
        
    return result


@eel.expose
def select_folder_native():
    result = _run_folder_dialog(
        title="Select Folder to Organize",
        initialdir=str(APP_STATE["folder"] or ""),
    )
    if "error" in result:
        return {"status": "error", "message": result["error"]}

    chosen = result.get("chosen")
    if chosen:
        APP_STATE["folder"] = Path(chosen)
        clear_all_cache()
        return {"status": "success", "path": chosen}
    return {"status": "cancelled"}


@eel.expose
def add_comparison_folder():
    """Open a folder picker and add to the comparison folder list."""
    result = _run_folder_dialog(
        title="Select Comparison Folder",
        initialdir=str(APP_STATE["folder"] or ""),
    )
    if "error" in result:
        return {"status": "error", "message": result["error"]}

    chosen = result.get("chosen")
    if not chosen:
        return {"status": "cancelled"}

    chosen_path = Path(chosen)
    all_existing = [APP_STATE["folder"].resolve()] + [f.resolve() for f in APP_STATE.get("comparison_folders", [])]
    if chosen_path.resolve() in all_existing:
        return {"status": "error", "message": "This folder is already in the workspace."}
    APP_STATE.setdefault("comparison_folders", []).append(chosen_path)
    clear_all_cache()
    return {"status": "success", "path": chosen}


@eel.expose
def remove_comparison_folder(folder_path_str):
    """Remove a specific comparison folder by its path."""
    target = Path(folder_path_str).resolve()
    APP_STATE["comparison_folders"] = [f for f in APP_STATE.get("comparison_folders", []) if f.resolve() != target]
    clear_all_cache()
    return {"status": "success"}


@eel.expose
def get_comparison_folders():
    """Return the current list of comparison folder paths."""
    return [str(f) for f in APP_STATE.get("comparison_folders", [])]
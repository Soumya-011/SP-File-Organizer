#!/usr/bin/env python3
"""
Workspace folder management — select, add/remove comparison folders.
"""

import eel
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

from gui_state import (
    APP_STATE, clear_all_cache,
)


@eel.expose
def select_folder_native():
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    chosen = filedialog.askdirectory(title="Select Folder to Organize", initialdir=str(APP_STATE["folder"] or ""))
    root.destroy()
    if chosen:
        APP_STATE["folder"] = Path(chosen)
        clear_all_cache()
        return {"status": "success", "path": chosen}
    return {"status": "cancelled"}


@eel.expose
def add_comparison_folder():
    """Open a folder picker and add to the comparison folder list."""
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    chosen = filedialog.askdirectory(title="Select Comparison Folder", initialdir=str(APP_STATE["folder"] or ""))
    root.destroy()
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
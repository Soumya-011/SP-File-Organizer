#!/usr/bin/env python3
"""
Eel Web-Desktop GUI — Thin Launcher Module.

All business logic has been split into focused modules:
  gui_state.py      — APP_STATE, thread-safe accessors, cache management
  gui_thumbnails.py — Thumbnail generation and caching (PIL-heavy)
  gui_dashboard.py  — Dashboard telemetry, categories, batch endpoint
  gui_organize.py   — Bulk/separate/size/age organization, mismatch fix
  gui_duplicates.py — Exact & perceptual dedup, paginated groups, thumbnails
  gui_history.py    — Undo, restore, trash bin
  gui_admin.py      — PIN auth, category CRUD, bulk rename
  gui_folders.py    — Folder selection, comparison folders

This file initializes Eel, imports all endpoint modules (which
register their @eel.expose handlers), and launches the window.
"""

import eel

# Initialize Eel web directory BEFORE importing endpoint modules,
# so @eel.expose decorators can register properly.
eel.init('web')

# Import all endpoint modules — each registers its @eel.expose handlers.
# The import order doesn't matter since expose() uses a global registry.
import gui_state       # APP_STATE, cache, progress bridge
import gui_thumbnails  # get_full_image_b64
import gui_dashboard   # get_dashboard_batch, get_system_metadata, etc.
import gui_organize    # trigger_bulk_organization, etc.
import gui_duplicates  # get_duplicate_groups_data, start_similar_scan, etc.
import gui_history     # restore_from_bin, empty_trash_completely, etc.
import gui_admin       # verify_admin_pin, update_category, etc.
import gui_folders     # select_folder_native, add_comparison_folder, etc.

def launch_gui(config_path, initial_folder=None):
    gui_state.initialize_runtime_configs(config_path, initial_folder)
    try:
        eel.start('index.html', size=(1120, 820), mode='chrome')
    except (SystemExit, MemoryError, KeyboardInterrupt):
        pass

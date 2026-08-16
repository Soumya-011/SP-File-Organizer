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
import platform

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
import gui_gallery     # get_gallery_folders, get_gallery_page, similarity map
import gui_history     # restore_from_bin, empty_trash_completely, etc.
import gui_admin       # verify_admin_pin, update_category, etc.
import gui_folders     # select_folder_native, add_comparison_folder, etc.

def launch_gui(config_path, initial_folder=None):
    gui_state.initialize_runtime_configs(config_path, initial_folder)

    # PERFORMANCE (app-lighter, tier 3): on Windows, prefer 'edge' mode,
    # which reuses the OS's built-in WebView2 runtime instead of spawning a
    # separate Chrome process — meaningfully lower memory footprint on
    # machines that have it (virtually all Windows 10/11 installs by
    # default; WebView2 has shipped inbox since the 2022 Windows 11 update
    # and as a Windows Update component on Windows 10 since 2022). Falls
    # back to 'chrome' automatically and silently if Edge/WebView2 isn't
    # available, and is skipped entirely on non-Windows platforms where
    # 'edge' mode doesn't apply. This cannot be verified from this
    # environment — see the accompanying testing notes for how to confirm
    # it on an actual Windows machine.
    preferred_mode = 'edge' if platform.system() == 'Windows' else 'chrome'

    try:
        eel.start('index.html', size=(1120, 820), mode=preferred_mode)
    except (SystemExit, MemoryError, KeyboardInterrupt):
        pass
    except Exception as e:
        if preferred_mode != 'chrome':
            print(f"  Could not start in '{preferred_mode}' mode ({e}). Falling back to Chrome.")
            try:
                eel.start('index.html', size=(1120, 820), mode='chrome')
            except (SystemExit, MemoryError, KeyboardInterrupt):
                pass
        else:
            raise

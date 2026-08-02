📂 Sp Enterprise File Organizer (v1.02)

A high-performance, web-native desktop utility that automatically cleans, sorts, and manages massive file directories. Built with a decoupled Python core and an interactive Chromium-based frontend using Eel.

✨ Features

🌍 Multi-Workspace Merger: Compare, deduplicate, and organize files across multiple hard drives or separate directories simultaneously.

📊 Deep Storage Telemetry: Instantly scan directories to visualize storage allocation, file counts, and space usage via a dynamic donut-chart dashboard.

🗂️ Advanced Sorting: Organize loose files by Extension/Category, File Size (e.g., separate >100MB files), or File Age.

🔍 Multi-Staged Duplicate Detection:

Exact Duplicates: Uses a highly efficient staged pipeline (Size grouping ➔ 4KB partial hash ➔ blake2b full-content hash) to find byte-for-byte copies safely.

Similar Images: Utilizes perceptual difference hashing (dhash) to group photos that look visually identical but have different resolutions, compressions, or formats. Includes an interactive fullscreen visualizer.

🧹 Workspace Vacuum & Mismatch Fixing: Automatically hunts down empty leftover folders or files that were placed in the wrong category and safely resolves them.

🛡️ Reversible Safety Nets: Every major disk operation generates a microsecond-precise JSON log. Features a robust Undo History and a native Recycle Bin to prevent accidental data loss.

🔒 Secure Admin UI: Protect bulk-renaming and category configurations using a securely hashed authentication PIN.

🚀 Installation & Setup

Prerequisites: Make sure you have Python 3.10+ installed.

Clone the repository: Download or clone this project to your local machine.

Install Dependencies: Open your terminal and install the required libraries:

pip install eel pillow pyinstaller



Run the Application:

python file_manager.py



(Note: To launch the classic text-based CLI instead of the GUI, use python file_manager.py --cli)

🔐 Configuring the Admin PIN (Security)

To prevent accidental changes to your configuration or mass file renaming, you can lock these features behind an Admin PIN.

For security, the application requires the PIN to be stored as a SHA-256 Hash in your config.json, NOT as plain text.

To generate your hashed PIN, run this quick script in your Python terminal (replace "1234" with your desired PIN):

import hashlib
print(hashlib.sha256(b"1234").hexdigest())



Copy the long string it outputs and place it in your config.json in the root directory:

{
  "admin_pin": "03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4"
}



📦 Building a Standalone Executable

You can compile this project into a standalone .exe (Windows) or .app (Mac) that requires zero installation for the end user. Make sure your virtual environment is active, then run:

Windows:

python -m pyinstaller --onefile --noconsole --name "FileOrganizer" --icon="web/icon.ico" --add-data "web;web" file_manager.py



Mac/Linux:

python3 -m pyinstaller --onefile --noconsole --name "FileOrganizer" --icon="web/icon.icns" --add-data "web:web" file_manager.py



Note: Your compiled executable will be located in the dist/ folder. Ensure you place a copy of config.json in the same directory as the executable before running it!

🏗️ Architecture Layout

The codebase maintains a strict decoupling between the logical backend engines, the CLI, and the web UI front-end.

Component

Responsibility

file_manager.py

Main entry point. Routes CLI arguments and launches either the GUI or the text-based app.

gui_*.py

Eel WebSocket bridge modules (gui_state, gui_dashboard, gui_duplicates, etc.) handling async UI execution.

organizer.py

Handles move-plan building, category sorting, and resolving file mismatches.

duplicates.py & image_duplicates.py

The multi-threaded hashing (exact match) and visual similarity (dhash) engines.

rename.py

Admin-only Regex bulk renaming logic.

undo.py & recycle_bin.py

Transactional logging, run history, and the recovery ecosystem.

scanner.py & mover.py

File system traversal and the shared safe move-execution engine (handles collision-avoiding filenames).

web/

Contains the pure HTML/CSS/JS interface injected into the Chromium window.

utils.py

Stateless core helpers (size formatting, global multiprocess configurations).
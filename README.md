# 📂 Sp Enterprise File Organizer (v0.8)

A high-performance, web-native desktop utility that automatically cleans, sorts, and manages massive file directories. Built with a decoupled Python core and an interactive Chromium-based frontend using **Eel**.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Eel](https://img.shields.io/badge/Eel-Web_GUI-green.svg)
![License](https://img.shields.io/badge/License-MIT-orange.svg)

## ✨ Features

* **📊 Deep Storage Telemetry:** Instantly scan directories to visualize storage allocation, file counts, and space usage via a dynamic donut-chart dashboard.
* **🗂️ Advanced Sorting:** Organize loose files by Extension/Category, File Size, or File Age. 
* **🔍 Multi-Staged Duplicate Detection:**
  * *Exact Duplicates:* Uses a highly efficient staged pipeline (Size grouping ➔ 4KB partial hash ➔ blake2b full-content hash) to find exact byte-for-byte copies safely.
  * *Similar Images:* Utilizes perceptual difference hashing (dhash) to group photos that look visually identical but have different resolutions, compressions, or formats. Includes an interactive fullscreen visualizer.
* **🧹 Workspace Vacuum & Mismatch Fixing:** Automatically hunts down empty leftover folders or files that were placed in the wrong category and safely resolves them.
* **🛡️ Reversible Safety Nets:** Every major disk operation generates a microsecond-precise JSON log. Features a robust Undo History and a native Recycle Bin to prevent accidental data loss.
* **🔐 Admin Security Lock:** Bulk text-renaming and systemic category modifications are locked behind an Admin PIN to prevent unauthorized directory structural changes.

## 🚀 Installation & Setup (For Developers)

To run the source code directly, you will need Python installed on your system along with Google Chrome or Microsoft Edge.

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Soumya-011/SP-File-Organizer.git](https://github.com/Soumya-011/SP-File-Organizer.git)
   cd File-Organizer

Create a Virtual Environment:

Bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
Install Dependencies:

Bash
pip install eel pillow pyinstaller
Run the Application:

Bash
python file_manager.py
📦 Building the Standalone Executable
You can compile this project into a standalone .exe (Windows) or .app (Mac) that requires zero installation for the end user.

Make sure your virtual environment is active, then run:

Bash
# Windows
python -m pyinstaller --onefile --noconsole --name "FileOrganizer" --icon="web/icon.ico" --add-data "web;web" file_manager.py

# Mac/Linux
python3 -m pyinstaller --onefile --noconsole --name "FileOrganizer" --icon="web/icon.icns" --add-data "web:web" file_manager.py
Note: Your compiled executable will be located in the dist/ folder. Ensure you place a copy of config.json in the same directory as the executable before running it!

🏗️ Architecture
The codebase maintains a strict decoupling between the logical backend engines and the web UI front-end.

gui.py: The Eel WebSocket bridge and asynchronous execution controller.

organizer.py / rename.py: Handles move-plan building, category sorting, and regex renaming.

duplicates.py / image_duplicates.py: The multi-threaded hashing and visual similarity engines.

undo.py / recycle_bin.py: The transactional logging and recovery ecosystem.

web/: Contains the pure HTML/CSS/JS interface injected into the Chromium app window.

📄 License
This project is licensed under the GPL License - see the LICENSE file for details.   
# 📂 Enterprise File Organizer (v0.8)

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
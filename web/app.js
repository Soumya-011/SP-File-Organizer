/*
 * Asynchronous front-end application controller core module.
 * Bridges active dynamic user interactions directly over backend logic routines.
 */

// HTML-escape helper — prevents XSS when inserting user/file data into innerHTML.
// Must be used for ALL file names, paths, category names, and error messages
// that originate from the Python backend (file system data is untrusted input).
function _esc(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// Escape for insertion into HTML attribute values (double-quoted).
// Used in onclick= handlers to prevent attribute injection.
function _attrEsc(str) {
    return _esc(str).replace(/"/g, "&quot;");
}

// Escape for insertion into a JS string literal that is INSIDE an HTML attribute.
// Fixes the Windows backslash path corruption in onclick="triggerUndo('${_jsInlineEsc(path)}')"
function _jsInlineEsc(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/\\/g, "\\\\")  // Escape backslashes for JS engine
        .replace(/'/g, "\\'")    // Escape single quotes for JS engine
        .replace(/"/g, "&quot;") // Escape double quotes for HTML parser
        .replace(/</g, "&lt;")   // HTML safety
        .replace(/>/g, "&gt;");  // HTML safety
}

let currentCategoriesMap = [];
let activeScanType = "exact";
let currentRenameCategory = null; 
let renamePreviewOp = null, renamePreviewArg1 = "", renamePreviewArg2 = "";
let renameCurrentPage = 0, renameTotalPages = 1;

// Multi-folder comparison state (unlimited folders)
let comparisonFolders = []; // array of {path, label}
let organizeFolderData = null; // cached from get_organize_view_data() 

// Global State Caching for Clean Array Pathing & Modal Navigations
window.currentDuplicateGroups = [];
window.currentTrashItems = [];
window.currentMismatches = [];
window.currentPreviewGidx = 0;
window.currentPreviewFidx = 0;

// Pagination state for duplicates panel
let dupCurrentPage = 0;
let dupTotalPages = 1;
let dupTotalGroups = 0;
let dupNameFilter = "";
let dupSmartSelectActive = false;

// Global Loader Wrappers
window.showLoader = function(msg = "Processing...") {
    document.getElementById("loader-text").innerText = msg;
    document.getElementById("loader-progress-bar-wrap").style.display = "none";
    document.getElementById("loader-counter").style.display = "none";
    document.getElementById("loader-progress-bar").style.width = "0%";
    document.getElementById("global-loader").style.display = "flex";
};

window.hideLoader = function() {
    document.getElementById("global-loader").style.display = "none";
    document.getElementById("loader-progress-bar-wrap").style.display = "none";
    document.getElementById("loader-counter").style.display = "none";
    document.getElementById("loader-progress-bar").style.width = "0%";
};

window.updateLoaderProgress = function(msg, current, total) {
    // Update the loader text with real-time progress from Python
    const textEl = document.getElementById("loader-text");
    const barWrap = document.getElementById("loader-progress-bar-wrap");
    const bar = document.getElementById("loader-progress-bar");
    const counter = document.getElementById("loader-counter");
    if (textEl) textEl.innerText = msg;
    if (total > 0 && current >= 0) {
        barWrap.style.display = "block";
        counter.style.display = "block";
        const pct = Math.min(100, Math.round((current / total) * 100));
        bar.style.width = pct + "%";
        counter.innerText = current + " / " + total + " (" + pct + "%)";
    } else {
        barWrap.style.display = "none";
        counter.style.display = "none";
    }
};

// Eel calls this from Python to push real-time progress
// Python calls: eel._on_python_progress(message, current, total)()
if (typeof eel !== "undefined") {
    eel.expose(_on_python_progress);
    eel.expose(_on_similar_scan_complete);
    eel.expose(_on_similar_scan_progress);
    eel.expose(_on_exact_scan_complete);
    eel.expose(_on_exact_scan_progress);
}
function _on_python_progress(message, current, total) {
    // Only update if loader is visible (operation is in progress)
    if (document.getElementById("global-loader").style.display !== "none") {
        updateLoaderProgress(message, current, total);
    }
}

// Called from Python when background similar-image scan completes
function _on_similar_scan_complete(result) {
    const progressEl = document.getElementById("gallery-scan-progress");
    if (progressEl) progressEl.style.display = "none";

    const galleryPanel = document.getElementById("gallery-panel");
    if (!galleryPanel || !galleryPanel.classList.contains("active-view")) return;

    if (result.error) {
        showToast("Similar-image scan failed: " + result.error, "error");
        return;
    }
    _refreshGallerySimilarityMap();
    showToast(`Found ${result.total_groups} similar-image group(s).`, "success");
}

// Called from Python with progress updates during similar-image scan
function _on_similar_scan_progress(data) {
    if (data.message) console.log("[similar-scan]", data.message);
    const bar = document.getElementById("gallery-scan-bar");
    const msg = document.getElementById("gallery-scan-msg");
    const counter = document.getElementById("gallery-scan-counter");
    if (!bar) return;
    document.getElementById("gallery-scan-progress").style.display = "block";
    if (data.pct !== undefined) bar.style.width = Math.min(100, data.pct) + "%";
    if (data.message) msg.innerText = data.message;
    if (counter && data.done !== undefined && data.total > 0) {
        counter.innerText = data.done + " / " + data.total;
    }
}

// Called from Python when the background EXACT-duplicate scan completes.
// Mirrors _on_similar_scan_complete — same non-blocking pattern, different tab.
function _on_exact_scan_complete(result) {
    const progressBar = document.getElementById("exact-scan-progress");
    if (progressBar) progressBar.style.display = "none";

    const dupPanel = document.getElementById("duplicates-panel");
    if (!dupPanel || !dupPanel.classList.contains("active-view")) return;

    if (result.error) {
        const dupContainer = document.getElementById("duplicates-render-container");
        if (dupContainer) {
            dupContainer.innerHTML = `<div class="banner-error">Error during scan: ${_esc(result.error)}</div>`;
        }
        return;
    }

    dupCurrentPage = 0;
    refreshDashboardTelemetryMetrics();
}

// Called from Python with progress updates during the background exact-duplicate scan.
function _on_exact_scan_progress(data) {
    if (data.message) console.log("[exact-scan]", data.message);
    const bar = document.getElementById("exact-scan-bar");
    const msg = document.getElementById("exact-scan-msg");
    const counter = document.getElementById("exact-scan-counter");
    if (!bar) return;
    document.getElementById("exact-scan-progress").style.display = "block";
    if (data.pct !== undefined) bar.style.width = Math.min(100, data.pct) + "%";
    if (data.message) msg.innerText = data.message;
    if (counter && data.done !== undefined && data.total > 0) {
        counter.innerText = data.done + " / " + data.total;
    } else if (counter && data.pct !== undefined) {
        counter.innerText = data.pct + "%";
    }
}

// Toast Notification System (replaces browser alert())
window.showToast = function(message, type = "success", duration = 3500) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const colors = {
        success: { bg: "#065F46", border: "#059669", icon: "\u2713" },
        error:   { bg: "#7F1D1D", border: "#DC2626", icon: "\u2717" },
        info:    { bg: "#1E3A5F", border: "#2563EB", icon: "\u2139" },
        warning: { bg: "#78350F", border: "#D97706", icon: "\u26A0" }
    };
    const c = colors[type] || colors.info;

    const toast = document.createElement("div");
    toast.style.cssText = `
        pointer-events: auto; display:flex; align-items:center; gap:10px;
        padding:12px 18px; border-radius:10px; font-size:14px; font-weight:500;
        color:#fff; background:${c.bg}; border-left:4px solid ${c.border};
        box-shadow:0 8px 24px rgba(0,0,0,0.18); min-width:280px; max-width:440px;
        transform:translateX(120%); transition:transform 0.35s cubic-bezier(0.22,1,0.36,1), opacity 0.3s;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    `;
    toast.innerHTML = `
        <span style="font-size:16px; font-weight:700; opacity:0.9;">${c.icon}</span>
        <span style="flex:1; line-height:1.4;">${message}</span>
    `;

    // Auto-close on click
    toast.style.cursor = "pointer";
    toast.onclick = () => dismissToast(toast);

    container.appendChild(toast);

    // Slide in
    requestAnimationFrame(() => {
        toast.style.transform = "translateX(0)";
    });

    // Auto dismiss
    const timer = setTimeout(() => dismissToast(toast), duration);
    toast._dismissTimer = timer;
};

function dismissToast(toast) {
    if (toast._dismissed) return;
    toast._dismissed = true;
    clearTimeout(toast._dismissTimer);
    toast.style.transform = "translateX(120%)";
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 400);
}

document.addEventListener("DOMContentLoaded", () => {
    initViewPanelNavigation();
    initApplicationContextData();
    initOrganizeSubTabHandlers();
    initGalleryHandlers();
    initAdminAndRenameHandlers();
    initInteractivityHandlers();
    initCategoryHandlers();
    initSearchHandlers();
    initPerformanceHandlers();
    initIdleAutoScan();
    initSidebarToggle();
    initFileBrowserHandlers();
    initThemeToggle();
    initActivityFeed();
    initCustomContextMenu();
    initGalleryViewDensity();
    initFolderBrowserViewDensity();
    initFolderBrowserSortHandlers();
});

// ---------------------------------------------------------------------------
// Retractable sidebar — state persisted in localStorage (a plain desktop
// Chrome window via Eel, not the claude.ai artifact sandbox that restricts
// browser storage, so this is fine here).
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Remember window size across restarts. Saves on a DEBOUNCED resize event
// (not just on close) since Eel's underlying browser subprocess can
// terminate before an async call from a 'beforeunload' handler reliably
// finishes — catching every resize as it settles is more robust than
// betting on the final close event landing in time.
// ---------------------------------------------------------------------------
let _resizeSaveTimer = null;
window.addEventListener("resize", () => {
    clearTimeout(_resizeSaveTimer);
    _resizeSaveTimer = setTimeout(() => {
        if (typeof eel !== "undefined" && eel.save_window_size) {
            eel.save_window_size(window.outerWidth, window.outerHeight)();
        }
    }, 600);
});

// Best-effort extra save right before the window closes, in case the very
// last resize happened less than 600ms before close (so the debounce above
// never fired for it). Not relied upon as the primary mechanism.
window.addEventListener("beforeunload", () => {
    if (typeof eel !== "undefined" && eel.save_window_size) {
        try { eel.save_window_size(window.outerWidth, window.outerHeight)(); } catch (e) { /* non-fatal */ }
    }
});

// ---------------------------------------------------------------------------
// File Browser (Overview tab, Phase B) — search/sort/filter across every
// file type, recursively. Double-click-to-open and folder navigation are
// deliberately not part of this yet — that's Phase C, built on top later.
// ---------------------------------------------------------------------------
let fbCurrentPage = 0;
let fbTotalPages = 1;
let fbSortBy = "name";
let fbSortDesc = false;
let fbNameFilter = "";
let fbCategoryFilter = "";
let fbExtFilter = "";

async function initFileSearch() {
    await _loadFileBrowserFacets();
    await loadFileBrowserPage(0);
}

async function initOverviewFolderBrowser() {
    await loadFolderBrowser("");
}

async function _loadFileBrowserFacets() {
    const facets = await eel.get_file_browser_facets()();

    const chipRow = document.getElementById("fb-category-chips");
    chipRow.innerHTML = "";
    const allChip = document.createElement("button");
    allChip.className = "gallery-folder-chip" + (fbCategoryFilter === "" ? " active" : "");
    allChip.innerText = `All (${facets.total})`;
    allChip.addEventListener("click", () => { fbCategoryFilter = ""; _renderFbChipsActive(); loadFileBrowserPage(0); });
    chipRow.appendChild(allChip);

    facets.categories.forEach(c => {
        const chip = document.createElement("button");
        chip.className = "gallery-folder-chip" + (fbCategoryFilter === c.name ? " active" : "");
        chip.setAttribute("data-cat", c.name);
        chip.innerText = `${c.name} (${c.count})`;
        chip.addEventListener("click", () => { fbCategoryFilter = c.name; _renderFbChipsActive(); loadFileBrowserPage(0); });
        chipRow.appendChild(chip);
    });

    const extSelect = document.getElementById("fb-ext-select");
    const currentExtValue = extSelect.value;
    extSelect.innerHTML = '<option value="">All extensions</option>';
    facets.extensions.forEach(e => {
        const opt = document.createElement("option");
        opt.value = e.ext;
        opt.innerText = `${e.ext} (${e.count})`;
        extSelect.appendChild(opt);
    });
    extSelect.value = currentExtValue;
}

function _renderFbChipsActive() {
    document.querySelectorAll("#fb-category-chips .gallery-folder-chip").forEach(chip => {
        const isAll = chip.innerText.startsWith("All (");
        const cat = chip.getAttribute("data-cat");
        chip.classList.toggle("active", isAll ? fbCategoryFilter === "" : cat === fbCategoryFilter);
    });
}

async function loadFileBrowserPage(page) {
    const res = await eel.get_file_browser_page(
        page, 60, fbSortBy, fbSortDesc, fbNameFilter, fbCategoryFilter, fbExtFilter
    )();

    fbCurrentPage = res.page;
    fbTotalPages = res.total_pages;

    document.getElementById("fb-result-count").innerText = `${res.total.toLocaleString()} file(s)`;

    const bar = document.getElementById("fb-pagination-bar");
    if (fbTotalPages > 1) {
        bar.style.display = "flex";
        document.getElementById("fb-page-info").innerText = `Page ${fbCurrentPage + 1} of ${fbTotalPages}`;
        document.getElementById("fb-prev-btn").disabled = (fbCurrentPage <= 0);
        document.getElementById("fb-next-btn").disabled = (fbCurrentPage >= fbTotalPages - 1);
    } else {
        bar.style.display = "none";
    }

    const tbody = document.getElementById("fb-table-body");
    tbody.innerHTML = "";
    if (res.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center-cell">No files match this search/filter.</td></tr>';
        return;
    }
    res.items.forEach(item => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td class="tbl-cell" style="font-weight:500;" title="${_attrEsc(item.path)}">${_esc(item.name)}</td>
            <td class="tbl-cell"><span class="org-preview-tag">${_esc(item.category)}</span></td>
            <td class="tbl-cell-mono" style="font-size:11.5px; color:var(--text-secondary);">${_esc(item.ext)}</td>
            <td class="tbl-cell" style="text-align:right; color:var(--text-secondary);">${_esc(item.size_str)}</td>
            <td class="tbl-cell-mono" style="font-size:11.5px; color:var(--text-secondary);" title="${_attrEsc(item.folder)}">${_esc(item.folder)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function initFileBrowserHandlers() {
    const debouncedSearch = _debounce((value) => {
        fbNameFilter = value;
        loadFileBrowserPage(0);
    }, 350);
    document.getElementById("fb-search-input").addEventListener("input", (e) => {
        debouncedSearch(e.target.value);
    });

    document.getElementById("fb-ext-select").addEventListener("change", (e) => {
        fbExtFilter = e.target.value;
        loadFileBrowserPage(0);
    });

    document.getElementById("fb-sort-select").addEventListener("change", (e) => {
        fbSortBy = e.target.value;
        loadFileBrowserPage(0);
    });

    document.getElementById("fb-sort-dir-btn").addEventListener("click", (e) => {
        fbSortDesc = !fbSortDesc;
        e.target.innerHTML = fbSortDesc ? "&darr; Desc" : "&uarr; Asc";
        loadFileBrowserPage(0);
    });

    document.getElementById("fb-prev-btn").addEventListener("click", () => {
        if (fbCurrentPage > 0) loadFileBrowserPage(fbCurrentPage - 1);
    });
    document.getElementById("fb-next-btn").addEventListener("click", () => {
        if (fbCurrentPage < fbTotalPages - 1) loadFileBrowserPage(fbCurrentPage + 1);
    });
}

// ---------------------------------------------------------------------------
// Folder Browser (Phase C) — one level at a time, like a real file explorer,
// with double-click-to-open. Distinct from the File Browser above, which is
// the flat recursive search across the whole workspace.
// ---------------------------------------------------------------------------
let fbBrowsePath = "";
let _lastFolderBrowserFiles = [];
let fbBrowseSortBy = "name";
let fbBrowseSortDesc = false;

async function loadFolderBrowser(relPath) {
    const res = await eel.browse_folder(relPath, fbBrowseSortBy, fbBrowseSortDesc)();
    if (res.error) {
        showToast(res.error, "error");
        return;
    }
    fbBrowsePath = res.current_rel_path;
    _lastFolderBrowserFiles = res.files;
    _renderFolderBreadcrumb(res.breadcrumb);
    _renderFolderBrowserGrid(res.folders, res.files);
}

function _renderFolderBreadcrumb(breadcrumb) {
    const container = document.getElementById("folder-browser-breadcrumb");
    container.innerHTML = "";
    breadcrumb.forEach((crumb, idx) => {
        const isLast = idx === breadcrumb.length - 1;
        const btn = document.createElement("button");
        btn.className = "breadcrumb-item" + (isLast ? " active" : "");
        btn.innerText = crumb.name;
        btn.disabled = isLast;
        btn.addEventListener("click", () => loadFolderBrowser(crumb.rel_path));
        container.appendChild(btn);
        if (!isLast) {
            const sep = document.createElement("span");
            sep.className = "breadcrumb-sep";
            sep.innerText = "/";
            container.appendChild(sep);
        }
    });
}

function _renderFolderBrowserGrid(folders, files) {
    const grid = document.getElementById("folder-browser-grid");
    grid.className = "gallery-grid view-" + folderBrowserViewMode;
    grid.innerHTML = "";

    if (folders.length === 0 && files.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">This folder is empty.</p>';
        return;
    }

    folders.forEach(f => {
        const tile = document.createElement("div");
        tile.className = "gallery-tile folder-tile";
        tile.title = "Double-click to open";
        tile.setAttribute("data-path", f.path);
        tile.setAttribute("data-name", f.name);
        tile.innerHTML = `
            <div class="gallery-tile-inner">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:32px; height:32px;"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
            </div>
            <div class="gallery-tile-name" title="${_attrEsc(f.name)}"><span>${_esc(f.name)}</span><span class="tile-meta">${f.item_count} item(s)</span></div>
        `;
        tile.addEventListener("dblclick", () => {
            const newPath = fbBrowsePath ? `${fbBrowsePath}/${f.name}` : f.name;
            loadFolderBrowser(newPath);
        });
        grid.appendChild(tile);
    });

    const imageFiles = [];
    files.forEach(f => {
        const tile = document.createElement("div");
        tile.className = "gallery-tile file-tile" + (f.is_image ? " thumb-placeholder" : "");
        tile.title = f.is_image ? "Double-click to preview" : "Double-click to open with default app";
        tile.setAttribute("data-path", f.path);
        tile.setAttribute("data-name", f.name);
        tile.setAttribute("data-is-image", f.is_image ? "true" : "false");
        const metaHtml = f.size_str ? `<span class="tile-meta">${_esc(f.size_str)}</span>` : "";
        tile.innerHTML = `
            <div class="gallery-tile-inner">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="gallery-tile-icon"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
            </div>
            <div class="gallery-tile-name" title="${_attrEsc(f.name)}"><span>${_esc(f.name)}</span>${metaHtml}</div>
        `;
        tile.addEventListener("dblclick", () => _openBrowsedFile(f));
        grid.appendChild(tile);
        if (f.is_image) imageFiles.push(f);
    });

    // FIX: image files were being collected into imageFiles but the actual
    // thumbnail fetch was never called — every image tile stayed a generic
    // file icon regardless of type. Batch-fetch at the density-appropriate
    // variant, same pattern as Gallery's _loadGalleryThumbnails.
    if (imageFiles.length > 0) {
        const paths = imageFiles.map(f => f.path);
        const variant = _variantForViewMode(folderBrowserViewMode);
        eel.get_gallery_thumbnails(paths, variant)().then(thumbsMap => {
            imageFiles.forEach(f => {
                const b64 = thumbsMap[f.path];
                if (!b64) return;
                const tile = document.querySelector(`#folder-browser-grid .gallery-tile[data-path="${CSS.escape(f.path)}"]`);
                if (!tile) return;
                tile.classList.remove("thumb-placeholder");
                const inner = tile.querySelector(".gallery-tile-inner");
                if (inner) inner.innerHTML = `<img src="${b64}" class="gallery-tile-img">`;
            });
        });
    }
}

async function _openBrowsedFile(f) {
    if (f.is_image) {
        // FIX: previously opened with a single-item array, so Left/Right had
        // nothing to cycle to — (0+1)%1 always resolves back to index 0.
        // Build the full image list from everything in the current folder
        // instead, matching how Gallery's own lightbox navigation works.
        const imagesInFolder = _lastFolderBrowserFiles.filter(x => x.is_image);
        const idx = imagesInFolder.findIndex(x => x.path === f.path);
        window.currentGalleryPageItems = imagesInFolder.map(x => ({ path: x.path, name: x.name, folder: fbBrowsePath }));
        window.galleryPreviewSource = "folder-browser";
        window.openGalleryImagePreview(idx >= 0 ? idx : 0);
        return;
    }
    const res = await eel.open_file_with_default_app(f.path)();
    if (res.status !== "success") {
        showToast(res.message || "Could not open file.", "error");
    }
}

// ---------------------------------------------------------------------------
// Dark mode — toggled via a data-theme attribute on <html>, persisted in
// localStorage. The CSS variables in style.css do the actual work for
// everything class/variable-based (cards, tables, inputs, sidebar); a
// handful of static modals and JS-generated confirm dialogs that hardcode
// inline colors are patched separately in style.css's [data-theme="dark"]
// overrides — flagging that as a known, bounded gap rather than a silent
// claim of pixel-perfect coverage.
// ---------------------------------------------------------------------------
const _SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
const _MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

function initThemeToggle() {
    const btn = document.getElementById("theme-toggle-btn");
    if (!btn) return;

    let isDark = false;
    try {
        isDark = localStorage.getItem("theme") === "dark";
    } catch (e) { /* fail open to light mode */ }
    _applyTheme(isDark);

    btn.addEventListener("click", () => {
        isDark = !isDark;
        _applyTheme(isDark);
        try { localStorage.setItem("theme", isDark ? "dark" : "light"); } catch (e) { /* non-fatal */ }
    });
}

function _applyTheme(isDark) {
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) {
        btn.innerHTML = isDark ? _SUN_ICON : _MOON_ICON;
        btn.title = isDark ? "Switch to light mode" : "Switch to dark mode";
    }
}

// ---------------------------------------------------------------------------
// Activity Feed — recent app actions (scans, moves, restores), backed by
// cache_store.log_activity() on the Python side. Groundwork for surfacing
// background work, especially anything that happens without the user
// directly watching (idle scans now; a future Watchdog would need this
// even more if it's ever revisited).
// ---------------------------------------------------------------------------
async function _loadActivityFeed() {
    const res = await eel.get_recent_activity(50)();
    const list = document.getElementById("activity-feed-list");
    list.innerHTML = "";
    if (!res.entries || res.entries.length === 0) {
        list.innerHTML = '<div class="activity-feed-empty">No activity yet.</div>';
        return;
    }
    res.entries.forEach(e => {
        const item = document.createElement("div");
        item.className = "activity-feed-item" + (e.level === "warning" ? " level-warning" : "");
        item.innerHTML = `${_esc(e.message)}<span class="activity-feed-time">${_esc(_formatActivityTime(e.timestamp))}</span>`;
        list.appendChild(item);
    });
}

function _formatActivityTime(isoString) {
    try {
        return new Date(isoString).toLocaleString();
    } catch (e) {
        return isoString;
    }
}

function initActivityFeed() {
    const btn = document.getElementById("activity-feed-btn");
    const panel = document.getElementById("activity-feed-panel");
    const closeBtn = document.getElementById("activity-feed-close");
    if (!btn || !panel) return;

    btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const isOpen = panel.style.display === "flex";
        if (isOpen) {
            panel.style.display = "none";
        } else {
            panel.style.display = "flex";
            await _loadActivityFeed();
        }
    });
    closeBtn.addEventListener("click", () => { panel.style.display = "none"; });

    document.addEventListener("click", (e) => {
        if (panel.style.display === "flex" && !panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
            panel.style.display = "none";
        }
    });
}

// ---------------------------------------------------------------------------
// Custom context menu — replaces Chrome's native right-click menu (which is
// meaningless in a packaged desktop app, and whose "Reload" entry can
// silently blow away in-progress state). Purpose-built per target:
//   - Gallery tile: Open / Copy Path / Delete
//   - Folder Browser folder: Open / Copy Path
//   - Folder Browser file: Open / Copy Path
// Anywhere else on the page, the native menu is suppressed with nothing
// shown in its place. Text inputs/textareas are explicitly excluded so
// normal copy/paste keeps working in search boxes and the PIN field.
// ---------------------------------------------------------------------------
let _ctxMenuEl = null;

function _buildContextMenu(items, x, y) {
    _closeContextMenu();
    const menu = document.createElement("div");
    menu.className = "custom-context-menu";
    items.forEach(item => {
        if (item.separator) {
            const sep = document.createElement("div");
            sep.className = "context-menu-separator";
            menu.appendChild(sep);
            return;
        }
        const el = document.createElement("button");
        el.className = "context-menu-item" + (item.danger ? " danger" : "");
        el.innerText = item.label;
        el.addEventListener("click", () => {
            _closeContextMenu();
            item.action();
        });
        menu.appendChild(el);
    });
    document.body.appendChild(menu);

    // Clamp to viewport so the menu never renders partly off-screen near an edge.
    const rect = menu.getBoundingClientRect();
    const maxX = window.innerWidth - rect.width - 8;
    const maxY = window.innerHeight - rect.height - 8;
    menu.style.left = Math.min(x, Math.max(8, maxX)) + "px";
    menu.style.top = Math.min(y, Math.max(8, maxY)) + "px";

    _ctxMenuEl = menu;
}

function _closeContextMenu() {
    if (_ctxMenuEl) {
        _ctxMenuEl.remove();
        _ctxMenuEl = null;
    }
}

async function _copyPathToClipboard(path) {
    try {
        await navigator.clipboard.writeText(path);
        showToast("Path copied to clipboard.", "success");
    } catch (e) {
        showToast("Could not copy path (clipboard access unavailable).", "error");
    }
}

function initCustomContextMenu() {
    document.addEventListener("contextmenu", (e) => {
        const tag = e.target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return; // native menu for text fields (copy/paste)

        const galleryTile = e.target.closest("#gallery-grid .gallery-tile");
        const folderTile = e.target.closest("#folder-browser-grid .folder-tile");
        const fileTile = e.target.closest("#folder-browser-grid .file-tile");

        if (galleryTile) {
            e.preventDefault();
            const path = galleryTile.getAttribute("data-path");
            const idx = window.currentGalleryPageItems.findIndex(i => i.path === path);
            if (idx === -1) return;
            _buildContextMenu([
                { label: "Open", action: () => { window.galleryPreviewSource = "gallery"; window.openGalleryImagePreview(idx); } },
                { label: "Copy Path", action: () => _copyPathToClipboard(path) },
                { separator: true },
                { label: "Delete", danger: true, action: () => _deleteGalleryPaths([path]) },
            ], e.clientX, e.clientY);
            return;
        }

        if (folderTile) {
            e.preventDefault();
            const name = folderTile.getAttribute("data-name");
            const path = folderTile.getAttribute("data-path");
            _buildContextMenu([
                {
                    label: "Open", action: () => {
                        const newPath = fbBrowsePath ? `${fbBrowsePath}/${name}` : name;
                        loadFolderBrowser(newPath);
                    }
                },
                { label: "Copy Path", action: () => _copyPathToClipboard(path) },
            ], e.clientX, e.clientY);
            return;
        }

        if (fileTile) {
            e.preventDefault();
            const path = fileTile.getAttribute("data-path");
            const name = fileTile.getAttribute("data-name");
            const isImage = fileTile.getAttribute("data-is-image") === "true";
            _buildContextMenu([
                {
                    label: "Open", action: () => _openBrowsedFile({ path, name, is_image: isImage })
                },
                { label: "Copy Path", action: () => _copyPathToClipboard(path) },
            ], e.clientX, e.clientY);
            return;
        }

        // Anywhere else — suppress the native menu, show nothing.
        e.preventDefault();
    });

    document.addEventListener("click", () => _closeContextMenu());
    document.addEventListener("scroll", () => _closeContextMenu(), true);
    window.addEventListener("blur", () => _closeContextMenu());
}

// ---------------------------------------------------------------------------
// View density (List/Small/Medium/Large) — shared between Gallery and
// Folder Browser, since both render into the same .gallery-grid/.gallery-tile
// markup. Preference persists per-component in localStorage.
// ---------------------------------------------------------------------------
function _variantForViewMode(mode) {
    if (mode === "list") return "dup_row";   // 60px
    if (mode === "small") return "small";    // 100px
    return "gallery";                        // medium/large — 220px
}

function _markActiveDensityBtn(group, mode) {
    group.querySelectorAll(".view-density-btn").forEach(b => {
        b.classList.toggle("active", b.getAttribute("data-mode") === mode);
    });
}

function _initViewDensityGroup(groupId, storageKey, setMode, onChange) {
    const group = document.getElementById(groupId);
    if (!group) return;

    let mode = "medium";
    try {
        const saved = localStorage.getItem(storageKey);
        if (saved) mode = saved;
    } catch (e) { /* fail open to medium */ }
    setMode(mode);
    _markActiveDensityBtn(group, mode);

    group.querySelectorAll(".view-density-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const newMode = btn.getAttribute("data-mode");
            setMode(newMode);
            _markActiveDensityBtn(group, newMode);
            try { localStorage.setItem(storageKey, newMode); } catch (e) { /* non-fatal */ }
            onChange(newMode);
        });
    });
}

function initGalleryViewDensity() {
    _initViewDensityGroup(
        "gallery-view-density", "galleryViewMode",
        (m) => { galleryViewMode = m; },
        () => {
            // Re-render from the currently loaded page's data at the new
            // density — cheap (server-side thumbnails are cache-backed per
            // variant) and guarantees the right-resolution image for the
            // new tile size instead of a stretched/blurry old one.
            if (galleryOnlySimilarActive) {
                _renderOnlySimilarPage();
            } else {
                renderGalleryGrid(window.currentGalleryPageItems || []);
            }
        }
    );
}

function initFolderBrowserViewDensity() {
    _initViewDensityGroup(
        "folder-browser-view-density", "folderBrowserViewMode",
        (m) => { folderBrowserViewMode = m; },
        () => loadFolderBrowser(fbBrowsePath)
    );
}

function initFolderBrowserSortHandlers() {
    document.getElementById("folder-browser-sort-select").addEventListener("change", (e) => {
        fbBrowseSortBy = e.target.value;
        loadFolderBrowser(fbBrowsePath);
    });
    document.getElementById("folder-browser-sort-dir-btn").addEventListener("click", (e) => {
        fbBrowseSortDesc = !fbBrowseSortDesc;
        e.target.innerHTML = fbBrowseSortDesc ? "&darr; Desc" : "&uarr; Asc";
        loadFolderBrowser(fbBrowsePath);
    });
}

function initSidebarToggle() {
    const sidebar = document.getElementById("app-sidebar");
    const btn = document.getElementById("sidebar-toggle-btn");
    if (!sidebar || !btn) return;

    let collapsed = false;
    try {
        collapsed = localStorage.getItem("sidebarCollapsed") === "true";
    } catch (e) {
        // localStorage can throw in rare sandboxed contexts — fail open
        // (sidebar stays expanded) rather than break the toggle entirely.
    }
    if (collapsed) {
        sidebar.classList.add("collapsed");
        btn.classList.add("collapsed");
        btn.title = "Expand sidebar";
    }

    btn.addEventListener("click", () => {
        const isCollapsed = sidebar.classList.toggle("collapsed");
        btn.classList.toggle("collapsed", isCollapsed);
        btn.title = isCollapsed ? "Expand sidebar" : "Collapse sidebar";
        try {
            localStorage.setItem("sidebarCollapsed", isCollapsed ? "true" : "false");
        } catch (e) { /* non-fatal */ }
    });
}

function initViewPanelNavigation() {
    const navButtons = document.querySelectorAll(".nav-btn");
    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active-view"));

            btn.classList.add("active");
            const target = btn.getAttribute("data-target");
            document.getElementById(target).classList.add("active-view");

            if (target === "gallery-panel") {
                loadGalleryData();
            } else if (target === "overview-panel") {
                refreshDashboardTelemetryMetrics();
                initOverviewFolderBrowser();
            } else if (target === "search-panel") {
                initFileSearch();
            } else {
                refreshDashboardTelemetryMetrics();
            }
        });
    });
}

function initOrganizeSubTabHandlers() {
    const orgTabs = document.querySelectorAll(".org-tab");
    orgTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            orgTabs.forEach(t => {
                t.classList.remove("active-tab");
                t.style.borderBottom = "2px solid transparent";
                t.style.fontWeight = "normal";
            });
            tab.classList.add("active-tab");
            tab.style.borderBottom = "2px solid #3B82F6";
            tab.style.fontWeight = "bold";
            
            document.querySelectorAll(".org-sub-panel").forEach(p => p.style.display = "none");
            const targetId = tab.getAttribute("data-sub");
            document.getElementById(targetId).style.display = "block";
            triggerRuleLivePreviews();
        });
    });
}


function initAdminAndRenameHandlers() {
    const handleAuth = async (inputId) => {
        const val = document.getElementById(inputId).value;
        const res = await eel.verify_admin_pin(val)();
        if (res.status === "success") {
            unlockAdminUI();
        } else {
            showToast(res.message, "error");
        }
    };

    document.getElementById("submit-pin-btn").addEventListener("click", () => handleAuth("admin-pin-input"));
    document.getElementById("admin-pin-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleAuth("admin-pin-input");
    });

    const catBtn = document.getElementById("cat-submit-pin-btn");
    if (catBtn) catBtn.addEventListener("click", () => handleAuth("cat-admin-pin-input"));
    const catPinInput = document.getElementById("cat-admin-pin-input");
    if (catPinInput) {
        catPinInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") handleAuth("cat-admin-pin-input");
        });
    }

    document.getElementById("rename-op-select").addEventListener("change", (e) => {
        const op = e.target.value;
        const c2 = document.getElementById("rename-arg2-container");
        const l1 = document.getElementById("rename-arg1-label");
        c2.style.display = (op === "replace") ? "block" : "none";
        
        if (op === "remove") l1.innerText = "Text to remove";
        if (op === "replace") l1.innerText = "Text to find";
        if (op === "prefix") l1.innerText = "Text to add as prefix";
        if (op === "suffix") l1.innerText = "Text to add as suffix";
    });

    document.getElementById("rename-category-select").addEventListener("change", async (e) => {
        currentRenameCategory = e.target.value; 
        document.getElementById("rename-preview-list").innerHTML = "<li>Category loaded. Select parameters and click preview...</li>";
        document.getElementById("rename-pagination-bar").style.display = "none";
        document.getElementById("apply-rename-btn").disabled = true;
    });

    document.getElementById("preview-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        if (!arg1 && op !== "replace") return showToast("Please enter text argument", "warning");
        if (!currentRenameCategory) return showToast("No category selected", "warning");

        renamePreviewOp = op;
        renamePreviewArg1 = arg1;
        renamePreviewArg2 = arg2;
        await loadRenamePreviewPage(0);
    });

    document.getElementById("rename-prev-btn").addEventListener("click", () => {
        if (renameCurrentPage > 0) loadRenamePreviewPage(renameCurrentPage - 1);
    });
    document.getElementById("rename-next-btn").addEventListener("click", () => {
        if (renameCurrentPage < renameTotalPages - 1) loadRenamePreviewPage(renameCurrentPage + 1);
    });

    document.getElementById("apply-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        const count = await eel.execute_rename(currentRenameCategory, op, arg1, arg2)();
        
        document.getElementById("rename-preview-list").innerHTML = "<li>Select parameters and click preview...</li>";
        document.getElementById("rename-pagination-bar").style.display = "none";
        document.getElementById("apply-rename-btn").disabled = true;
        
        await populateRenameCategories();
        await refreshDashboardTelemetryMetrics();
        showToast(`Successfully renamed ${count} files.`, "success");
    });
}

// Wraps the character ranges in `spans` (from preview_rename's old_spans /
// new_spans — [[start,end], ...], non-overlapping, ascending) in `cssClass`;
// everything else is plain escaped text. No diffing here — the backend
// already knows exactly what it changed and just tells us where.
function _renderSpannedName(name, spans, cssClass) {
    if (!spans || spans.length === 0) return _esc(name);
    let out = "";
    let pos = 0;
    spans.forEach(([s, e]) => {
        if (s > pos) out += _esc(name.slice(pos, s));
        out += `<span class="${cssClass}">${_esc(name.slice(s, e))}</span>`;
        pos = e;
    });
    if (pos < name.length) out += _esc(name.slice(pos));
    return out;
}

async function loadRenamePreviewPage(page) {
    const res = await eel.preview_rename(
        currentRenameCategory, renamePreviewOp, renamePreviewArg1, renamePreviewArg2, page, 50
    )();

    renameCurrentPage = res.page;
    renameTotalPages = res.total_pages;

    const list = document.getElementById("rename-preview-list");
    const bar = document.getElementById("rename-pagination-bar");
    list.innerHTML = "";

    if (res.total === 0) {
        list.innerHTML = "<li>No files would be changed with these parameters.</li>";
        bar.style.display = "none";
        document.getElementById("apply-rename-btn").disabled = true;
        return;
    }

    res.items.forEach(item => {
        const oldHtml = _renderSpannedName(item.old, item.old_spans, "rename-diff-removed");
        const newHtml = _renderSpannedName(item.new, item.new_spans, "rename-diff-added");
        list.innerHTML += `<li>${oldHtml} &nbsp;&rarr;&nbsp; <b style="color:var(--text-primary)">${newHtml}</b></li>`;
    });

    if (renameTotalPages > 1) {
        bar.style.display = "flex";
        const startItem = renameCurrentPage * 50 + 1;
        const endItem = Math.min((renameCurrentPage + 1) * 50, res.total);
        document.getElementById("rename-page-info").innerText =
            `Showing ${startItem}\u2013${endItem} of ${res.total.toLocaleString()} file(s)`;
        document.getElementById("rename-prev-btn").disabled = (renameCurrentPage <= 0);
        document.getElementById("rename-next-btn").disabled = (renameCurrentPage >= renameTotalPages - 1);
    } else {
        bar.style.display = "none";
    }

    document.getElementById("apply-rename-btn").disabled = false;
}

function unlockAdminUI() {
    const catAuth = document.getElementById("categories-auth-section");
    const catWork = document.getElementById("categories-workspace-section");
    if(catAuth) catAuth.style.display = "none";
    if(catWork) catWork.style.display = "block";

    const renAuth = document.getElementById("rename-auth-section");
    const renWork = document.getElementById("rename-workspace-section");
    if(renAuth) renAuth.style.display = "none";
    if(renWork) renWork.style.display = "block";
    
    populateRenameCategories();
    populatePerformanceSettings();
}

async function populateRenameCategories() {
    const cats = await eel.get_rename_categories()();
    const sel = document.getElementById("rename-category-select");
    sel.innerHTML = "";
    if (cats.length === 0) {
        sel.innerHTML = "<option>No categories available</option>";
        currentRenameCategory = null; 
    } else {
        cats.forEach((c, idx) => {
            const opt = document.createElement("option");
            opt.value = c.name;
            opt.innerText = `${c.name} (${c.count} files)`;
            sel.appendChild(opt);
            if (idx === 0) currentRenameCategory = c.name; 
        });
    }
}

// ---------------------------------------------------------------------------
// Admin — Performance settings (Low/Medium/High scan worker threads)
// ---------------------------------------------------------------------------
async function populatePerformanceSettings() {
    const res = await eel.get_worker_settings()();
    document.getElementById("perf-cpu-count").innerText = res.cpu_count;
    document.querySelectorAll(".worker-tier-count").forEach(el => {
        const t = el.getAttribute("data-tier");
        el.innerText = `(${res.tiers[t]})`;
    });
    document.querySelectorAll(".worker-tier-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tier") === res.current_tier);
    });
    const label = document.getElementById("perf-current-label");
    if (res.current_tier) {
        label.innerText = `Current: ${res.current_tier} (${res.current_max_workers} thread(s))`;
    } else if (res.current_max_workers) {
        label.innerText = `Current: custom (${res.current_max_workers} thread(s))`;
    } else {
        label.innerText = `Current: auto (${Math.max(1, res.cpu_count - 1)} thread(s))`;
    }
}

function initPerformanceHandlers() {
    document.querySelectorAll(".worker-tier-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const tier = btn.getAttribute("data-tier");
            const res = await eel.set_worker_tier(tier)();
            if (res.status === "success") {
                showToast(`Scan workers set to ${tier} (${res.max_workers} thread(s), ${res.cpu_count} cores detected).`, "success");
                populatePerformanceSettings();
            } else {
                showToast(res.message || "Failed to update worker setting.", "error");
            }
        });
    });
}

function initCategoryHandlers() {
    document.getElementById("add-category-btn").addEventListener("click", () => {
        document.getElementById("cat-modal-title").innerText = "Add New Category";
        document.getElementById("cat-name-input").value = "";
        document.getElementById("cat-name-input").readOnly = false;
        document.getElementById("cat-exts-input").value = "";
        document.getElementById("category-modal").style.display = "flex";
    });

    document.getElementById("save-category-btn").addEventListener("click", async () => {
        const name = document.getElementById("cat-name-input").value.trim();
        const exts = document.getElementById("cat-exts-input").value.trim();
        if(!name || !exts) return showToast("Category Name and Extensions are required.", "warning");
        
        const res = await eel.update_category(name, exts)();
        if(res.status === "success") {
            document.getElementById("category-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
        } else {
            showToast(res.message, "error");
        }
    });
}

window.editCategory = function(name, exts) {
    document.getElementById("cat-modal-title").innerText = "Edit Category";
    document.getElementById("cat-name-input").value = name;
    document.getElementById("cat-name-input").readOnly = true; 
    document.getElementById("cat-exts-input").value = exts;
    document.getElementById("category-modal").style.display = "flex";
};

window.deleteCategory = async function(name) {
    const proceed = await _showSimpleConfirmModal(
        `Remove custom config overrides for '${name}'?`,
        "This will restore the default extensions for this category.",
        "#D97706"
    );
    if (proceed) {
        await eel.remove_category(name)();
        await refreshDashboardTelemetryMetrics();
    }
};

async function initApplicationContextData() {
    if (typeof eel === "undefined") return;
    const metadata = await eel.get_system_metadata()();
    document.getElementById("current-path-display").innerText = metadata.folder || "No working folder selected.";

    // Comparison folder state
    comparisonFolders = (metadata.comparison_folders || []).map(p => ({path: p, label: p.split(/[\\/]/).pop() || p}));
    _renderComparisonBar();

    if (!metadata.has_pin) {
        document.getElementById("rename-auth-msg").innerText = "Admin PIN not configured. Add \"admin_pin\" to config.json.";
        document.getElementById("submit-pin-btn").disabled = true;
        
        const catAuthMsg = document.getElementById("categories-auth-msg");
        if(catAuthMsg) catAuthMsg.innerText = "Admin PIN not configured. Add \"admin_pin\" to config.json.";
        const catSubBtn = document.getElementById("cat-submit-pin-btn");
        if(catSubBtn) catSubBtn.disabled = true;
    }
    
    if (metadata.admin_mode) {
        unlockAdminUI();
    }
    
    if (metadata.folder) {
        window.showLoader("Scanning workspace, please wait...");
        await refreshDashboardTelemetryMetrics();
        await initOverviewFolderBrowser();
        window.hideLoader();
    }
}

async function triggerRuleLivePreviews() {
    if (typeof eel === "undefined") return;
    const sizeVal = document.getElementById("size-input-value").value;
    const sizeRes = await eel.get_rule_preview_metrics("size", sizeVal)();
    document.getElementById("size-preview-tally").innerText = `${sizeRes.count} files currently match (${sizeRes.size_str})`;

    const ageVal = document.getElementById("age-input-value").value;
    const ageRes = await eel.get_rule_preview_metrics("age", ageVal)();
    document.getElementById("age-preview-tally").innerText = `${ageRes.count} files currently match (${ageRes.size_str})`;
}

async function refreshDashboardTelemetryMetrics() {
    if (typeof eel === "undefined") return;
    
    // --- TELEMETRY AND CATEGORY RENDERING ---
    // Fetch rule preview values to batch them
    const sizeVal = document.getElementById("size-input-value") ? document.getElementById("size-input-value").value : null;
    const ageVal = document.getElementById("age-input-value") ? document.getElementById("age-input-value").value : null;

    // Use the batched endpoint to replace the 6 separated calls
    const batch = await eel.get_dashboard_batch(sizeVal, ageVal)();
    if (batch.error) return;

    // 1. Storage Telemetry
    const data = batch.storage;
    document.getElementById("count-total-files").innerText = (data.total_files || 0).toLocaleString();
    document.getElementById("count-trash-items").innerText = (data.trash_count || 0).toLocaleString();
    document.getElementById("total-storage-tally").innerText = data.total_size_str || "0 B";

    // 2. Duplicate count from batch
    document.getElementById("count-dup-sets").innerText = (batch.duplicate_count || 0).toLocaleString();

    const chartRing = document.getElementById("donut-render-target");
    const legendList = document.getElementById("legend-render-target");
    if (chartRing && legendList && data.categories && data.categories.length > 0) {
        legendList.innerHTML = "";
        let cumulativePct = 0;
        let gradients = [];
        const palette = ['#3B82F6', '#7A5AF8', '#12B76A', '#F79009', '#F04438', '#98A2B3'];

        data.categories.forEach((cat, idx) => {
            const nextPct = cumulativePct + cat.percentage;
            const color = palette[idx % palette.length];
            gradients.push(`${color} ${cumulativePct}% ${nextPct}%`);
            
            const li = document.createElement("li");
            li.innerHTML = `<span class="dot" style="background:${color}"></span>${_esc(cat.name)}<span class="pct">${cat.percentage}% · ${cat.size_str}</span>`;
            legendList.appendChild(li);
            cumulativePct = nextPct;
        });
        chartRing.style.background = `conic-gradient(${gradients.join(',')})`;
    }

    // 3. Categories Data
    const catData = batch.categories;
    const grid = document.getElementById("categories-grid");
    if (grid) {
        grid.innerHTML = "";
        grid.style.display = "grid";
        grid.style.gridTemplateColumns = "repeat(auto-fill, minmax(280px, 1fr))";
        grid.style.gap = "16px";

        catData.forEach(c => {
            const card = document.createElement("div");
            card.className = "ui-card";
            card.style.padding = "16px";
            card.style.border = "1px solid var(--stroke-color)";
            
            let chipsHtml = c.extensions.map(ext => `<span class="tag" style="background:var(--space-bg); border:1px solid var(--stroke-color); border-radius:6px; padding:4px 8px; font-size:12px; display:inline-block; margin:2px;">${ext}</span>`).join('');
            let badge = c.is_custom ? `<span style="background:#EEF6FF; color:#2563EB; font-size:10px; padding:2px 6px; border-radius:12px; margin-left:8px; vertical-align:middle;">Custom</span>` : '';

            card.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="font-weight:600; font-size:14px;">${_esc(c.name)}${badge}</div>
                    <div style="display:flex; gap:6px;">
                        <button class="ui-btn secondary" style="padding:4px 8px; font-size:11px;" onclick="editCategory('${_jsInlineEsc(c.name)}', '${_jsInlineEsc(c.extensions.join(', '))}')">Edit</button>
                        ${c.is_custom ? `<button class="ui-btn danger" style="padding:4px 8px; font-size:11px;" onclick="deleteCategory('${_jsInlineEsc(c.name)}')">Del</button>` : ''}
                    </div>
                </div>
                <div>${chipsHtml}</div>
            `;
            grid.appendChild(card);
        });
    }

    // 4. Mismatches
    const mismatches = batch.mismatches;
    const misCard = document.getElementById("mismatch-warning-card");
    if (misCard) {
        if(mismatches && mismatches.length > 0) {
            misCard.style.display = "block";
            document.getElementById("mismatch-text").innerText = `${mismatches.length} file(s) are sitting inside category folders they don't belong to.`;
            window.currentMismatches = mismatches;
        } else {
            misCard.style.display = "none";
            window.currentMismatches = [];
        }
    }

    // 5. Organize View Data
    const organizeData = batch.organize_view;
    organizeFolderData = organizeData; // cache for the modal
    const checklistContainer = document.getElementById("organize-checklist-container");
    if (checklistContainer) {
        checklistContainer.innerHTML = "";
        currentCategoriesMap = [];
        
        document.getElementById("org-select-all").checked = true;

        // Build combined category list with total counts across all folders
        const catMap = organizeData.categories || {};
        const allCats = Object.keys(catMap);

        if (allCats.length === 0) {
            checklistContainer.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">No loose files to sort currently.</p>';
        } else {
            allCats.forEach((cat, index) => {
                currentCategoriesMap.push(cat);
                const totalCount = Object.values(catMap[cat]).reduce((a, b) => a + b, 0);
                const row = document.createElement("div");
                row.style.margin = "8px 0";
                row.innerHTML = `
                    <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                        <input type="checkbox" id="cat-checkbox-${index}" class="org-cat-checkbox" checked style="width:16px; height:16px;">
                        <span>${cat} <b style="color:var(--text-secondary); font-weight:500;">(${totalCount} files)</b></span>
                    </label>
                `;
                checklistContainer.appendChild(row);
            });
        }
        _applyOrganizeSearchFilter();
    }

    // 6. Rule Previews
    if (batch.rule_previews) {
        if (batch.rule_previews.size) {
            document.getElementById("size-preview-tally").innerText = `${batch.rule_previews.size.count} files currently match (${batch.rule_previews.size.size_str})`;
        }
        if (batch.rule_previews.age) {
            document.getElementById("age-preview-tally").innerText = `${batch.rule_previews.age.count} files currently match (${batch.rule_previews.age.size_str})`;
        }
    }

    // 7. History and Trash
    const historyData = batch.history;
    window.currentTrashItems = batch.trash;

    const historyBody = document.getElementById("history-table-body");
    if (historyBody) {
        historyBody.innerHTML = "";
        if (historyData.length === 0) {
            historyBody.innerHTML = '<tr><td colspan="3" style="color:var(--text-secondary); text-align:center; padding:12px;">No historical action logs found.</td></tr>';
        } else {
            historyData.forEach(run => {
                const tr = document.createElement("tr");
                // CRITICAL FIX: Properly escape Windows backslashes for JS execution in inline onclick handlers!
                tr.innerHTML = `
                    <td style="padding:10px;"><b>${_esc(run.label)}</b></td>
                    <td style="padding:10px;">Moved <b>${run.count}</b> files/folders</td>
                    <td style="padding:10px;">
                        <button class="ui-btn secondary" onclick="triggerUndoSequence('${_jsInlineEsc(run.path)}', '${_jsInlineEsc(run.label)}', ${run.count})" style="padding:4px 10px; font-size:11.5px;">Undo Action</button>
                    </td>`;
                historyBody.appendChild(tr);
            });
        }
        _applyHistorySearchFilter();
    }

    const trashBody = document.getElementById("trash-table-body");
    if (trashBody) {
        trashBody.innerHTML = "";
        if (batch.trash.length === 0) {
            trashBody.innerHTML = '<tr><td colspan="4" style="color:var(--text-secondary); text-align:center; padding:12px;">Recycle bin empty.</td></tr>';
        } else {
            batch.trash.forEach((item, idx) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td style="padding:10px;"><input type="checkbox" class="bin-item-checkbox" data-index="${idx}"></td>
                    <td style="padding:10px; font-family:monospace; word-break:break-all;">${_esc(item.name)}</td>
                    <td style="padding:10px;"><b>${item.size}</b></td>
                    <td style="padding:10px; color:var(--text-secondary); font-size:11.5px;">${_esc(item.batch)}</td>
                `;
                trashBody.appendChild(tr);
            });
        }
        _applyBinSearchFilter();
    }

    // --- DUPLICATES (PAGINATED + LAZY THUMBNAILS) ---
    // Only load duplicates if the user is actually looking at the Duplicate tab!
    const activePanel = document.querySelector(".view-panel.active-view");
    if (activePanel && activePanel.id === "duplicates-panel" && !dupSmartSelectActive) {

        const thresholdVal = 10; // similar-image scanning moved to Gallery; Duplicates is exact-only now

        // NON-BLOCKING (fix): get_duplicate_groups_data() now only reads
        // cache/scan-state — it never runs find_duplicates() itself, so this
        // call returns almost instantly and never freezes the app. If a scan
        // is actually needed, it's kicked off as a background thread via
        // start_exact_scan() below, exactly like similar-image scanning
        // already works — the rest of the UI stays fully interactive while
        // it runs, with real progress on the inline bar instead of a
        // full-screen loader.
        const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage, 25, dupNameFilter)();
        const isCached = dupResponse.from_cache === true;

        if (dupResponse.needs_scan === true) {
            dupCurrentPage = 0;
            document.getElementById("exact-scan-progress").style.display = "block";
            await eel.start_exact_scan()();
            // Progress arrives via _on_exact_scan_progress; completion via
            // _on_exact_scan_complete, which re-calls this function.
            const dupContainer = document.getElementById("duplicates-render-container");
            if (dupContainer) dupContainer.innerHTML = "";
            return;
        }

        // A scan may already be running in the background (e.g. user switched
        // tabs away and back) — just make sure the progress bar is visible;
        // updates keep arriving via the same push handlers.
        if (dupResponse.total_groups === 0 && !isCached) {
            const scanStatus = await eel.get_exact_scan_status()();
            if (scanStatus.scanning) {
                const progressEl = document.getElementById("exact-scan-progress");
                if (progressEl) progressEl.style.display = "block";
                const dupContainer = document.getElementById("duplicates-render-container");
                if (dupContainer) dupContainer.innerHTML = "";
                return;
            }
        }

        document.getElementById("exact-scan-progress").style.display = "none";

        const dupGroups = dupResponse.displayed_groups || [];
        window.currentDuplicateGroups = dupGroups;
        dupTotalGroups = dupResponse.total_groups || 0;
        dupTotalPages = dupResponse.total_pages || 1;
        dupCurrentPage = dupResponse.page || 0;
        
        const dupSelectAllBtn = document.getElementById("dup-select-all");
        if(dupSelectAllBtn) dupSelectAllBtn.checked = false;

        // Pagination bar visibility
        const paginationBar = document.getElementById("dup-pagination-bar");
        if (paginationBar) {
            if (dupTotalPages > 1) {
                paginationBar.style.display = "flex";
                const startItem = dupCurrentPage * 25 + 1;
                const endItem = Math.min((dupCurrentPage + 1) * 25, dupTotalGroups);
                document.getElementById("dup-page-info").innerText = 
                    `Showing ${startItem}\u2013${endItem} of ${dupTotalGroups.toLocaleString()} sets`;
                document.getElementById("dup-prev-btn").disabled = (dupCurrentPage <= 0);
                document.getElementById("dup-next-btn").disabled = (dupCurrentPage >= dupTotalPages - 1);
            } else {
                paginationBar.style.display = "none";
            }
        }

        const dupContainer = document.getElementById("duplicates-render-container");
        if (dupContainer) {
            dupContainer.innerHTML = "";

            if (dupResponse.error) {
                dupContainer.innerHTML += `
                    <div style="background: #FEF2F2; color: #B91C1C; padding: 12px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #FECACA; font-size: 13px;">
                        <b>Similar-image scan unavailable:</b> ${dupResponse.error}
                    </div>
                `;
                window.hideLoader();
                return;
            }

            if (dupTotalGroups > dupGroups.length) {
                const showingCount = (dupCurrentPage + 1) * 25;
                const shown = Math.min(showingCount, dupTotalGroups);
                dupContainer.innerHTML += `
                    <div style="background: #FFFBEB; color: #B45309; padding: 12px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #FDE68A; font-size: 13px;">
                        <b>High Volume:</b> ${dupTotalGroups.toLocaleString()} duplicate sets found. Showing ${shown} of ${dupTotalGroups.toLocaleString()}. Use pagination to browse all sets.
                    </div>
                `;
            }

            if (dupResponse.unreadable_count > 0) {
                dupContainer.innerHTML += `
                    <div style="background: #FFFBEB; color: #B45309; padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #FDE68A; font-size: 12.5px;">
                        Note: ${dupResponse.unreadable_count} image(s) could not be read (corrupt, locked, or an unsupported format like HEIC without the pillow-heif plugin) and were skipped.
                    </div>
                `;
            }

            if (dupGroups.length === 0) {
                dupContainer.innerHTML += '<p style="color:var(--text-secondary); font-size:13.5px;">No duplicate elements detected.</p>';
            } else {
                dupGroups.forEach((group, gIdx) => {
                    const groupWrapper = document.createElement("div");
                    groupWrapper.style.padding = "16px";
                    groupWrapper.style.border = "1px solid var(--stroke-color)";
                    groupWrapper.style.borderRadius = "8px";
                    groupWrapper.style.marginBottom = "16px";
                    groupWrapper.style.backgroundColor = "#fff";
                    
                    let itemsListHtml = "";
                    group.files.forEach((file, fIdx) => {
                        const autoChecked = (activeScanType === "exact" && fIdx > 0) ? "checked" : "";
                        
                        // Lazy thumbnail: placeholder first, loaded async after render
                        let mediaThumbnailHtml = `
                            <div class="thumb-placeholder" data-gidx="${gIdx}" data-fidx="${fIdx}" data-gid="${group.id}" style="width:38px; height:38px; border-radius:8px; background:#F3F5FA; border:1px solid var(--stroke-color); display:flex; align-items:center; justify-content:center; flex-shrink:0; color:#98A2B3; cursor:pointer;" title="Loading...">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px; height:16px;"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                            </div>`;
                            
                        if (file.thumb_b64) {
                            mediaThumbnailHtml = `<img src="${file.thumb_b64}" onclick="openImagePreview(${gIdx}, ${fIdx})" style="cursor:pointer; width:38px; height:38px; border-radius:8px; object-fit:cover; border:1px solid var(--stroke-color); flex-shrink:0;" title="Click for full preview" />`;
                        }

                        itemsListHtml += `
                            <div class="dup-row" style="display:flex; align-items:center; gap:12px; padding:12px 6px; border-bottom:1px solid var(--stroke-color);">
                                <input type="checkbox" class="dup-file-purge-checkbox" data-gidx="${gIdx}" data-fidx="${fIdx}" ${autoChecked} style="width:16px; height:16px;">
                                ${mediaThumbnailHtml}
                                <div class="dup-info">
                                    <b style="font-size:13px; display:block; color:var(--text-primary); word-break:break-all;">${_esc(file.name)}</b>
                                    <span style="font-size:11.5px; color:var(--text-secondary); word-break:break-all;">${_esc(file.path)}</span>
                                </div>
                            </div>
                        `;
                    });

                    groupWrapper.innerHTML = `
                        <div style="font-size:11px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">Set Match Collection — ${group.size_str} copies each</div>
                        <div style="display:flex; flex-direction:column;">${itemsListHtml}</div>
                    `;
                    dupContainer.appendChild(groupWrapper);
                });

                // Lazy-load thumbnails for visible groups after DOM render
                _loadVisibleThumbnails(activeScanType);
            }
        }
        window.hideLoader();
    }
}

// ---------------------------------------------------------------------------
// Search bars (Phase 3) — every tab except the admin ones (Categories, Rename).
// Overview has no listable content (just a chart + stat cards) so it's
// intentionally skipped. Organize/History/Bin filter client-side over
// already-rendered rows; Duplicates/Gallery go through the backend since
// those are server-paginated and a client-side filter would only ever see
// the current page.
// ---------------------------------------------------------------------------
let organizeSearchQuery = "";
let historySearchQuery = "";
let binSearchQuery = "";

function _debounce(fn, delayMs) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delayMs);
    };
}

function _applyOrganizeSearchFilter() {
    const q = organizeSearchQuery.trim().toLowerCase();
    document.querySelectorAll("#organize-checklist-container > div").forEach(row => {
        row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? "" : "none";
    });
}

function _applyHistorySearchFilter() {
    const q = historySearchQuery.trim().toLowerCase();
    document.querySelectorAll("#history-table-body tr").forEach(row => {
        row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? "" : "none";
    });
}

function _applyBinSearchFilter() {
    const q = binSearchQuery.trim().toLowerCase();
    document.querySelectorAll("#trash-table-body tr").forEach(row => {
        row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? "" : "none";
    });
}

function initSearchHandlers() {
    document.getElementById("organize-search-input").addEventListener("input", (e) => {
        organizeSearchQuery = e.target.value;
        _applyOrganizeSearchFilter();
    });

    document.getElementById("history-search-input").addEventListener("input", (e) => {
        historySearchQuery = e.target.value;
        _applyHistorySearchFilter();
    });

    document.getElementById("bin-search-input").addEventListener("input", (e) => {
        binSearchQuery = e.target.value;
        _applyBinSearchFilter();
    });

    // Duplicates and Gallery are server-paginated — debounce so we don't fire
    // a request on every keystroke, and reset to page 0 since the filtered
    // result set is a different size than the unfiltered one.
    const debouncedDupSearch = _debounce(async (value) => {
        dupNameFilter = value;
        dupCurrentPage = 0;
        await refreshDashboardTelemetryMetrics();
    }, 350);
    document.getElementById("duplicates-search-input").addEventListener("input", (e) => {
        debouncedDupSearch(e.target.value);
    });

    const debouncedGallerySearch = _debounce(async (value) => {
        galleryNameFilter = value;
        if (galleryOnlySimilarActive) {
            _renderOnlySimilarView();
        } else {
            await loadGalleryPage(0);
        }
    }, 350);
    document.getElementById("gallery-search-input").addEventListener("input", (e) => {
        debouncedGallerySearch(e.target.value);
    });
}

function initInteractivityHandlers() {
    document.getElementById("metric-total-files").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="organize-panel"]').click();
    });
    document.getElementById("metric-duplicates").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="duplicates-panel"]').click();
    });
    document.getElementById("metric-trash").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="bin-panel"]').click();
    });

    document.getElementById("fix-mismatch-btn").addEventListener("click", () => {
        const targets = new Set(window.currentMismatches.map(m => m.correct));
        const container = document.getElementById("mismatch-checklist");
        container.innerHTML = "";
        targets.forEach((cat) => {
            const count = window.currentMismatches.filter(m => m.correct === cat).length;
            container.innerHTML += `
                <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer; margin-bottom:8px;">
                    <input type="checkbox" class="mismatch-cat-checkbox" value="${cat}" checked style="width:16px; height:16px;">
                    <span>${cat} <b style="color:var(--text-secondary); font-weight:500;">(${count} files)</b></span>
                </label>
            `;
        });
        document.getElementById("mismatch-modal").style.display = "flex";
    });

    document.getElementById("execute-mismatch-btn").addEventListener("click", async () => {
        const selected = Array.from(document.querySelectorAll(".mismatch-cat-checkbox:checked")).map(cb => cb.value);
        if(selected.length === 0) return showToast("Select at least one category to fix.", "warning");

        const count = await eel.fix_mismatched_files(selected)();
        document.getElementById("mismatch-modal").style.display = "none";
        await refreshDashboardTelemetryMetrics();
        showToast(`Fixed ${count} misplaced files inside target directories.`, "success");
    });

    document.getElementById("vacuum-btn").addEventListener("click", async () => {
        const emptyFolders = await eel.get_empty_folders_data()();
        if (emptyFolders.length === 0) return showToast("No empty folders found in the workspace.", "info");
        
        const container = document.getElementById("vacuum-checklist");
        container.innerHTML = "";
        document.getElementById("vacuum-count-label").innerText = `${emptyFolders.length} folder(s) found`;
        document.getElementById("vacuum-select-all").checked = true;
        
        emptyFolders.forEach((f) => {
            container.innerHTML += `
                <label style="display:flex; align-items:center; gap:10px; font-size:13px; cursor:pointer; margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid var(--stroke-color);">
                    <input type="checkbox" class="vacuum-folder-checkbox" value="${_attrEsc(f.path)}" checked style="width:16px; height:16px; flex-shrink:0;">
                    <span style="word-break: break-all;">
                        <b style="color:var(--text-primary); display:block; font-size:13px;">${_esc(f.name)}</b>
                        <span style="color:var(--text-secondary); font-size:11px; font-family:monospace;">${_esc(f.rel_path)}</span>
                    </span>
                </label>
            `;
        });
        
        document.getElementById("vacuum-modal").style.display = "flex";
    });

    document.getElementById("vacuum-select-all").addEventListener("change", (e) => {
        document.querySelectorAll(".vacuum-folder-checkbox").forEach(cb => cb.checked = e.target.checked);
    });

    document.getElementById("execute-vacuum-btn").addEventListener("click", async () => {
        const selected = Array.from(document.querySelectorAll(".vacuum-folder-checkbox:checked")).map(cb => cb.value);
        if(selected.length === 0) return showToast("Select at least one empty folder to clean.", "warning");

        const permanentDelete = document.getElementById("vacuum-permanent-delete").checked;

        if (permanentDelete) {
            const res = await eel.delete_empty_folders_permanently(selected)();
            if(res.status === "success") {
                document.getElementById("vacuum-modal").style.display = "none";
                await refreshDashboardTelemetryMetrics();
                let msg = `Permanently deleted ${res.deleted} empty folder(s).`;
                if (res.failed > 0) msg += ` ${res.failed} folder(s) skipped (no longer empty).`;
                showToast(msg, "success");
            } else {
                showToast(res.message || "Error deleting folders.", "error");
            }
        } else {
            const res = await eel.purge_selected_empty_folders(selected)();
            if(res.status === "success") {
                document.getElementById("vacuum-modal").style.display = "none";
                await refreshDashboardTelemetryMetrics();
                showToast(`Workspace Vacuum complete! Cleaned up and moved ${res.purged} empty folder(s) to the Recycle Bin safely.`, "success");
            } else {
                showToast(res.message || "Error cleaning folders.", "error");
            }
        }

        // Reset the permanent delete checkbox after action
        document.getElementById("vacuum-permanent-delete").checked = false;
    });

    document.getElementById("change-workspace-btn").addEventListener("click", async () => {
        try {
            const res = await eel.select_folder_native()();
            if (res.status === "success") {
                document.getElementById("current-path-display").innerText = res.path;
                _galleryEverLoaded = false; // new workspace — Gallery needs a real first load again
                window.showLoader("Scanning new workspace, please wait...");
                await refreshDashboardTelemetryMetrics();
                await initOverviewFolderBrowser();
                window.hideLoader();
                if (document.getElementById("rename-workspace-section").style.display === "block") populateRenameCategories();
            } else if (res.status === "error") {
                showToast("Folder dialog error: " + (res.message || "Unknown error"), "error");
            }
        } catch (e) {
            showToast("Failed to open folder dialog. Check console for details.", "error");
            console.error("select_folder_native error:", e);
        }
    });

    document.getElementById("preview-size-btn").addEventListener("click", triggerRuleLivePreviews);
    document.getElementById("preview-age-btn").addEventListener("click", triggerRuleLivePreviews);

    document.getElementById("org-select-all").addEventListener("change", (e) => {
        document.querySelectorAll(".org-cat-checkbox").forEach(cb => cb.checked = e.target.checked);
    });

    document.getElementById("execute-organize-btn").addEventListener("click", () => {
        let targets = [];
        currentCategoriesMap.forEach((name, idx) => {
            const cb = document.getElementById(`cat-checkbox-${idx}`);
            if (cb && cb.checked) targets.push(name);
        });
        if (targets.length === 0) return showToast("Select at least one category checkbox.", "warning");

        // If comparison folder is active, show destination modal
        if (comparisonFolders.length > 0 && organizeFolderData && organizeFolderData.folders.length > 1) {
            _showOrganizeDestModal(targets);
        } else {
            // Single folder — organize directly
            _executeDirectOrganize(targets);
        }
    });

    document.getElementById("add-comparison-btn").addEventListener("click", async () => {
        const res = await eel.add_comparison_folder()();
        if (res.status === "success") {
            comparisonFolders.push({path: res.path, label: res.path.split(/[\\/]/).pop() || res.path});
            _renderComparisonBar();
            window.showLoader("Scanning all folders, please wait...");
            await refreshDashboardTelemetryMetrics();
            window.hideLoader();
        } else if (res.status === "error") {
            showToast(res.message, "error");
        }
    });

    document.getElementById("execute-separate-org-btn").addEventListener("click", async () => {
        if (!organizeFolderData) return;
        const folders = organizeFolderData.folders;
        const folderCatsMap = {};

        // Query all checked boxes once to avoid CSS querySelector slash/escape bugs
        const allCheckedBoxes = Array.from(document.querySelectorAll(".sep-cat-cb:checked"));

        folders.forEach(f => {
            const selected = [];
            allCheckedBoxes.forEach(cb => {
                if (cb.getAttribute("data-folder") === f.path) {
                    selected.push(cb.value);
                }
            });
            if (selected.length > 0) folderCatsMap[f.path] = selected;
        });

        if (Object.keys(folderCatsMap).length === 0) return showToast("Select at least one category for at least one folder.", "warning");

        const proceed = await _showSimpleConfirmModal(
            "Organize separately into each folder's own subfolders?",
            "Each folder's loose files will be moved into categorized subfolders within that same folder.",
            "#2563EB"
        );
        if (!proceed) return;

        window.showLoader("Organizing separately...");
        const res = await eel.trigger_separate_organization(folderCatsMap)();
        window.hideLoader();
        if (res.status === "success") {
            document.getElementById("organize-dest-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
            showToast(`Separate organization complete. Moved ${res.moved} items.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

    document.getElementById("execute-size-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("size-input-value").value;
        const timing = document.querySelector('input[name="size-timing"]:checked').value;
        
        window.showLoader("Organizing by size...");
        const res = await eel.trigger_separation_organization("size", timing, val)();
        window.hideLoader();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            showToast(`Size organization resolved. Isolated ${res.moved} files.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

    document.getElementById("execute-age-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("age-input-value").value;
        const timing = document.querySelector('input[name="age-timing"]:checked').value;
        
        window.showLoader("Organizing by age...");
        const res = await eel.trigger_separation_organization("age", timing, val)();
        window.hideLoader();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            showToast(`Age organization resolved. Isolated ${res.moved} files.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

    document.getElementById("dup-select-all").addEventListener("change", (e) => {
        const isChecked = e.target.checked;
        document.querySelectorAll(".dup-file-purge-checkbox").forEach(cb => {
            const fIdx = parseInt(cb.getAttribute("data-fidx"), 10);
            if(fIdx > 0) {
                cb.checked = isChecked;
            }
        });
    });

    document.getElementById("purge-duplicates-btn").addEventListener("click", async () => {
        let targets = [];
        document.querySelectorAll(".dup-file-purge-checkbox").forEach(cb => {
            if (cb.checked) {
                const gIdx = cb.getAttribute("data-gidx");
                const fIdx = cb.getAttribute("data-fidx");
                targets.push(window.currentDuplicateGroups[gIdx].files[fIdx].path);
            }
        });
        if (targets.length === 0) return showToast("No items selected for cleanup.", "warning");

        // Show confirmation via toast-style approach
        const proceed = await _showPurgeConfirmModal(targets.length);
        if (!proceed) return;

        const res = await eel.purge_selected_duplicates(targets)();
        if (res.status === "success") {
            dupSmartSelectActive = false;
            dupCurrentPage = 0;
            await refreshDashboardTelemetryMetrics();
            showToast(`Moved ${res.purged} duplicate file(s) to the Recycle Bin.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

    // Smart Select — computes which file to keep per set (newest), then
    // switches into a REVIEW view showing exactly those affected sets with
    // the to-be-deleted files pre-checked. Nothing is deleted here — the
    // person reviews/unchecks, then clicks the existing "Purge Checked
    // Copies" button above (which already knows how to read whatever's
    // checked, regardless of whether it got there via Smart Select or
    // manual clicking).
    document.getElementById("dup-smart-delete-btn").addEventListener("click", async () => {
        const preview = await eel.get_smart_select_preview("exact", "newest")();
        if (!preview.groups || preview.groups.length === 0) {
            showToast("Nothing to smart-select — run a scan first, or every set already has just one copy.", "info");
            return;
        }
        dupSmartSelectActive = true;
        window.currentDuplicateGroups = preview.groups;
        document.getElementById("dup-pagination-bar").style.display = "none";
        _renderDupSmartSelectReview(preview.groups, preview.total_to_delete);
    });

    function _renderDupSmartSelectReview(groups, totalToDelete) {
        const dupContainer = document.getElementById("duplicates-render-container");
        dupContainer.innerHTML = "";

        const banner = document.createElement("div");
        banner.className = "banner-warning";
        banner.style.display = "flex";
        banner.style.justifyContent = "space-between";
        banner.style.alignItems = "center";
        banner.style.flexWrap = "wrap";
        banner.style.gap = "10px";
        banner.innerHTML = `
            <span><b>Reviewing Smart Select:</b> ${totalToDelete} file(s) across ${groups.length} set(s) pre-checked (keeping the newest copy in each). Uncheck any you want to keep, then click "Purge Checked Copies" above.</span>
            <button class="ui-btn secondary" id="dup-smart-select-cancel-btn" style="padding:5px 12px; font-size:12px; flex-shrink:0;">Cancel</button>
        `;
        dupContainer.appendChild(banner);

        groups.forEach((group, gIdx) => {
            const groupWrapper = document.createElement("div");
            groupWrapper.className = "dup-group-card";

            let itemsListHtml = "";
            group.files.forEach((file, fIdx) => {
                const checkedAttr = file.preselected ? "checked" : "";
                const mediaThumbnailHtml = `
                    <div class="thumb-placeholder" data-gidx="${gIdx}" data-fidx="${fIdx}" data-gid="${group.id}" style="width:38px; height:38px; border-radius:8px; background:#F3F5FA; border:1px solid var(--stroke-color); display:flex; align-items:center; justify-content:center; flex-shrink:0; color:#98A2B3; cursor:pointer;" title="Loading...">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px; height:16px;"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                    </div>`;
                itemsListHtml += `
                    <div class="dup-row" style="display:flex; align-items:center; gap:12px; padding:12px 6px; border-bottom:1px solid var(--stroke-color);">
                        <input type="checkbox" class="dup-file-purge-checkbox" data-gidx="${gIdx}" data-fidx="${fIdx}" ${checkedAttr} style="width:16px; height:16px;">
                        ${mediaThumbnailHtml}
                        <div class="dup-info">
                            <b style="font-size:13px; display:block; color:var(--text-primary); word-break:break-all;">${_esc(file.name)}</b>
                            <span style="font-size:11.5px; color:var(--text-secondary); word-break:break-all;">${_esc(file.path)}</span>
                        </div>
                    </div>
                `;
            });

            groupWrapper.innerHTML = `
                <div class="dup-group-label">Set — ${group.size_str} copies each</div>
                <div style="display:flex; flex-direction:column;">${itemsListHtml}</div>
            `;
            dupContainer.appendChild(groupWrapper);
        });

        document.getElementById("dup-smart-select-cancel-btn").addEventListener("click", () => {
            dupSmartSelectActive = false;
            dupCurrentPage = 0;
            refreshDashboardTelemetryMetrics();
        });

        _loadVisibleThumbnails("exact");
    }

    // Re-scan duplicates button
    document.getElementById("dup-rescan-btn").addEventListener("click", async () => {
        await eel.force_refresh_duplicates()();
        dupCurrentPage = 0;
        showToast("Re-scanning duplicates...", "info");
        await refreshDashboardTelemetryMetrics();
    });

    document.getElementById("restore-bin-btn").addEventListener("click", async () => {
        let targets = [];
        document.querySelectorAll(".bin-item-checkbox").forEach(cb => {
            if (cb.checked) {
                const idx = cb.getAttribute("data-index");
                targets.push(window.currentTrashItems[idx].path);
            }
        });
        if (targets.length === 0) return showToast("Select files using the checkboxes first.", "warning");
        
        const res = await eel.restore_from_bin(targets)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            showToast(`Restored ${res.restored} item(s) back to original locations.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

    document.getElementById("empty-bin-btn").addEventListener("click", async () => {
        document.getElementById("empty-trash-confirm-modal").style.display = "flex";
    });

    document.getElementById("empty-trash-cancel-btn").addEventListener("click", () => {
        document.getElementById("empty-trash-confirm-modal").style.display = "none";
    });

    document.getElementById("empty-trash-confirm-btn").addEventListener("click", async () => {
        document.getElementById("empty-trash-confirm-modal").style.display = "none";
        const res = await eel.empty_trash_completely()();
        await refreshDashboardTelemetryMetrics();
        showToast(`Trash cleared. Purged ${res.flushed} files permanently.`, "success");
    });
}

window.openImagePreview = async function(gIdx, fIdx) {
    window.galleryPreviewActive = false;
    window.currentPreviewGidx = gIdx;
    window.currentPreviewFidx = fIdx;
    
    const targetFile = window.currentDuplicateGroups[gIdx].files[fIdx];
    const modal = document.getElementById("image-preview-modal");
    const imgEl = document.getElementById("preview-modal-img");
    const loader = document.getElementById("preview-loading");
    const infoEl = document.getElementById("preview-file-info");
    
    modal.style.display = "flex";
    loader.style.display = "block";
    imgEl.style.display = "none";
    imgEl.src = "";
    infoEl.innerText = "";
    
    const b64Data = await eel.get_full_image_b64(targetFile.path)();
    if(b64Data) {
        loader.style.display = "none";
        imgEl.src = b64Data;
        imgEl.style.display = "block";
        infoEl.innerText = `${targetFile.name}   —   ${targetFile.path}`;
    } else {
        loader.innerText = "Error loading high resolution image data.";
    }
};

document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("image-preview-modal");
    if (modal.style.display === "flex") {
        if (window.galleryPreviewActive) {
            const items = window.currentGalleryPageItems;
            if (e.key === "ArrowRight") {
                window.openGalleryImagePreview((window.galleryPreviewIndex + 1) % items.length);
            } else if (e.key === "ArrowLeft") {
                window.openGalleryImagePreview((window.galleryPreviewIndex - 1 + items.length) % items.length);
            } else if (e.key === "Delete") {
                e.preventDefault();
                _deleteCurrentGalleryPreviewImage();
            } else if (e.key === "Escape") {
                modal.style.display = "none";
                window.galleryPreviewActive = false;
            }
        } else {
            const groupFiles = window.currentDuplicateGroups[window.currentPreviewGidx].files;
            if (e.key === "ArrowRight") {
                let next = (window.currentPreviewFidx + 1) % groupFiles.length;
                window.openImagePreview(window.currentPreviewGidx, next);
            } else if (e.key === "ArrowLeft") {
                let prev = (window.currentPreviewFidx - 1 + groupFiles.length) % groupFiles.length;
                window.openImagePreview(window.currentPreviewGidx, prev);
            } else if (e.key === "Delete") {
                e.preventDefault();
                _deleteCurrentDuplicatePreviewImage();
            } else if (e.key === "Escape") {
                modal.style.display = "none";
            }
        }
    }

    // Close organize destination modal on Escape
    const orgModal = document.getElementById("organize-dest-modal");
    if (orgModal && orgModal.style.display === "flex" && e.key === "Escape") {
        orgModal.style.display = "none";
    }

    // Close undo confirm modal on Escape
    const undoModal = document.getElementById("undo-confirm-modal");
    if (undoModal && undoModal.style.display === "flex" && e.key === "Escape") {
        undoModal.style.display = "none";
        _pendingUndoLogPath = null;
    }

    // Close organize preview modal on Escape
    const orgPreviewModal = document.getElementById("organize-preview-modal");
    if (orgPreviewModal && orgPreviewModal.style.display === "flex" && e.key === "Escape") {
        orgPreviewModal.style.display = "none";
    }
});

// --- Undo Confirmation Modal Logic ---
let _pendingUndoLogPath = null;

function _shortPath(p, maxLen) {
    if (p.length <= maxLen) return p;
    const parts = p.replace(/\\/g, "/").split("/");
    if (parts.length <= 2) return p;
    return ".../" + parts.slice(-2).join("/");
}

// ---------------------------------------------------------------------------
// Generic Simple Confirm Modal (replaces all confirm() calls)
// ---------------------------------------------------------------------------
function _showSimpleConfirmModal(title, message, accentColor) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.id = "simple-confirm-overlay";
        overlay.style.cssText = "position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:99998; align-items:center; justify-content:center; backdrop-filter:blur(2px); display:flex;";

        const color = accentColor || "#2563EB";

        overlay.innerHTML = `
            <div style="background:#fff; border-radius:14px; width:400px; max-width:92vw; box-shadow:0 20px 60px rgba(0,0,0,0.25);">
                <div style="display:flex; align-items:center; gap:10px; padding:20px 24px; border-bottom:1px solid var(--stroke-color);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" style="width:22px; height:22px; flex-shrink:0;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
                    <h3 style="margin:0; font-size:15px; font-weight:700; color:#111827;">${_esc(title)}</h3>
                </div>
                <div style="padding:18px 24px;">
                    <p style="margin:0; font-size:13.5px; color:#6B7280;">${_esc(message)}</p>
                </div>
                <div style="padding:14px 24px; border-top:1px solid var(--stroke-color); display:flex; justify-content:flex-end; gap:10px;">
                    <button class="sc-cancel-btn ui-btn secondary">Cancel</button>
                    <button class="sc-confirm-btn ui-btn primary" style="background:${color}; border-color:${color}; color:#fff;">Confirm</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        overlay.querySelector(".sc-cancel-btn").onclick = () => { overlay.remove(); resolve(false); };
        overlay.querySelector(".sc-confirm-btn").onclick = () => { overlay.remove(); resolve(true); };
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) { overlay.remove(); resolve(false); }
        });
    });
}

// ---------------------------------------------------------------------------
// Purge Duplicates Confirmation Modal (replaces confirm() for duplicate cleanup)
// ---------------------------------------------------------------------------
function _showPurgeConfirmModal(count) {
    return new Promise((resolve) => {
        const modal = document.getElementById("empty-trash-confirm-modal");
        if (!modal) { resolve(true); return; }

        // Reuse the modal pattern — show inline confirmation
        const overlay = document.createElement("div");
        overlay.id = "purge-confirm-overlay";
        overlay.style.cssText = "position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:99998; align-items:center; justify-content:center; backdrop-filter:blur(2px); display:flex;";

        overlay.innerHTML = `
            <div style="background:#fff; border-radius:14px; width:420px; max-width:92vw; box-shadow:0 20px 60px rgba(0,0,0,0.25);">
                <div style="display:flex; align-items:center; gap:10px; padding:20px 24px; border-bottom:1px solid var(--stroke-color);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="#D97706" stroke-width="2" style="width:22px; height:22px; flex-shrink:0;"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2 2h4a2 2 0 0 1 2 2v2"/></svg>
                    <h3 style="margin:0; font-size:16px; font-weight:700; color:#111827;">Purge Duplicate Files</h3>
                </div>
                <div style="padding:20px 24px;">
                    <div style="padding:12px 16px; background:#FFFBEB; border:1px solid #FDE68A; border-radius:8px; font-size:13.5px; color:#92400E; margin-bottom:14px;">
                        Move <b>${count}</b> selected file(s) to the Recycle Bin? You can restore them later from the History tab.
                    </div>
                </div>
                <div style="padding:14px 24px; border-top:1px solid var(--stroke-color); display:flex; justify-content:flex-end; gap:10px;">
                    <button id="purge-modal-cancel" class="ui-btn secondary">Cancel</button>
                    <button id="purge-modal-confirm" class="ui-btn primary" style="background:#D97706; border-color:#D97706; color:#fff;">Yes, Move to Recycle Bin</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        document.getElementById("purge-modal-cancel").onclick = () => {
            overlay.remove();
            resolve(false);
        };
        document.getElementById("purge-modal-confirm").onclick = () => {
            overlay.remove();
            resolve(true);
        };
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) { overlay.remove(); resolve(false); }
        });
    });
}

function _showUndoConfirmModal(logPath, label, fileCount) {
    _pendingUndoLogPath = logPath;
    const modal = document.getElementById("undo-confirm-modal");
    document.getElementById("undo-log-label").innerText = label;
    document.getElementById("undo-file-count").innerText = " \u2014 " + fileCount + " file(s) will be restored";

    // Show loader while fetching details
    const tbody = document.getElementById("undo-preview-tbody");
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:20px; color:#6B7280;">Loading file details...</td></tr>';
    modal.style.display = "flex";

    // Fetch details async
    eel.get_undo_log_details(logPath)().then(res => {
        if (res.status === "success" && res.entries.length > 0) {
            const limit = 100;
            const entries = res.entries.slice(0, limit);
            tbody.innerHTML = "";
            entries.forEach(e => {
                const tr = document.createElement("tr");
                tr.style.borderBottom = "1px solid var(--stroke-color)";
                tr.innerHTML = `
                    <td style="padding:6px 12px; font-weight:500; color:#111827; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${_attrEsc(e.file_name)}">${_esc(e.file_name)}</td>
                    <td style="padding:6px 12px; color:#6B7280; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${_attrEsc(e.destination)}">${_esc(_shortPath(e.destination, 40))}</td>
                    <td style="padding:6px 12px; color:#059669; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${_attrEsc(e.source)}">${_esc(_shortPath(e.source, 40))}</td>
                `;
                tbody.appendChild(tr);
            });
            if (res.entries.length > limit) {
                document.getElementById("undo-preview-too-many").style.display = "block";
                document.getElementById("undo-total-count").innerText = res.entries.length;
            } else {
                document.getElementById("undo-preview-too-many").style.display = "none";
            }
        } else {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:20px; color:#6B7280;">No file details available.</td></tr>';
        }
    }).catch(() => {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:20px; color:#DC2626;">Failed to load file details.</td></tr>';
    });
}

// Wire up undo modal buttons
document.addEventListener("DOMContentLoaded", () => {
    const undoModal = document.getElementById("undo-confirm-modal");
    document.getElementById("undo-modal-close-btn").addEventListener("click", () => { undoModal.style.display = "none"; _pendingUndoLogPath = null; });
    document.getElementById("undo-cancel-btn").addEventListener("click", () => { undoModal.style.display = "none"; _pendingUndoLogPath = null; });
    undoModal.addEventListener("click", (e) => { if (e.target === undoModal) { undoModal.style.display = "none"; _pendingUndoLogPath = null; } });

    document.getElementById("undo-confirm-btn").addEventListener("click", async () => {
        if (!_pendingUndoLogPath) return;
        const logPath = _pendingUndoLogPath;
        undoModal.style.display = "none";
        window.showLoader("Undoing file moves...");
        const res = await eel.execute_undo_operation(logPath)();
        window.hideLoader();
        await refreshDashboardTelemetryMetrics();
        showToast(`Undo complete. Restored ${res.restored} items.`, "success");
        _pendingUndoLogPath = null;
    });
});

window.triggerUndoSequence = async function(logPathString, label, count) {
    _showUndoConfirmModal(logPathString, label || "Undo", count || 0);
};

// ---------------------------------------------------------------------------
// Comparison Bar — Renders folder chips with individual remove buttons
// ---------------------------------------------------------------------------
function _renderComparisonBar() {
    const compBar = document.getElementById("comparison-bar");
    const chipsContainer = document.getElementById("comparison-folder-chips");
    if (!compBar || !chipsContainer) return;

    if (comparisonFolders.length === 0) {
        compBar.style.display = "none";
        return;
    }

    compBar.style.display = "block";
    chipsContainer.innerHTML = "";

    comparisonFolders.forEach((f, idx) => {
        const shortLabel = f.label.length > 45 ? f.label.substring(0, 42) + "..." : f.label;
        const chip = document.createElement("div");
        chip.className = "comparison-chip";
        chip.innerHTML = `
            <span title="${_attrEsc(f.path)}">${_esc(shortLabel)}</span>
            <button class="comparison-chip-remove" data-idx="${idx}" title="Remove this folder">&times;</button>
        `;
        chipsContainer.appendChild(chip);
    });

    // Attach remove handlers
    chipsContainer.querySelectorAll(".comparison-chip-remove").forEach(btn => {
        btn.addEventListener("click", async () => {
            const idx = parseInt(btn.getAttribute("data-idx"), 10);
            const folderToRemove = comparisonFolders[idx];
            comparisonFolders.splice(idx, 1);
            await eel.remove_comparison_folder(folderToRemove.path)();
            _renderComparisonBar();
            window.showLoader("Re-scanning...");
            await refreshDashboardTelemetryMetrics();
            window.hideLoader();
        });
    });
}

// ---------------------------------------------------------------------------
// Multi-Folder Organize Destination Modal
// ---------------------------------------------------------------------------
function _showOrganizeDestModal(selectedCategories) {
    if (!organizeFolderData) return;
    const folders = organizeFolderData.folders;
    const optionsDiv = document.getElementById("org-dest-options");
    const separateSection = document.getElementById("org-dest-separate-section");
    const separateBody = document.getElementById("org-dest-separate-body");
    const cancelRow = document.getElementById("org-dest-cancel-row");

    // Build folder option buttons
    optionsDiv.innerHTML = "";
    folders.forEach(f => {
        const shortLabel = f.label.length > 40 ? f.label.substring(0, 37) + "..." : f.label;
        const btn = document.createElement("button");
        btn.className = "org-dest-option-btn";
        btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:18px; height:18px; flex-shrink:0;"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
            <div>
                <div style="font-weight:600; font-size:13px;">Organize into ${_esc(shortLabel)}</div>
                <div style="font-size:11px; color:var(--text-secondary);">All files from all folders merged into this location</div>
            </div>
        `;
        btn.addEventListener("click", () => {
            document.getElementById("organize-dest-modal").style.display = "none";
            _executeDirectOrganize(selectedCategories, f.path);
        });
        optionsDiv.appendChild(btn);
    });

    // Build "Separate for each" section
    separateBody.innerHTML = "";
    const catMap = organizeFolderData.categories;
    folders.forEach(f => {
        // Safe attribute-escaped path string for Data Attributes
        const escapedPathForAttr = _attrEsc(f.path);
        const folderBlock = document.createElement("div");
        folderBlock.style.marginBottom = "14px";
        folderBlock.innerHTML = `
            <div style="font-size:13px; font-weight:600; margin-bottom:8px; color:var(--text-primary);">${_esc(f.label)}</div>
            <div style="display:flex; flex-wrap:wrap; gap:6px;"></div>
        `;
        const catContainer = folderBlock.querySelector("div:last-child");
        Object.keys(catMap).forEach(cat => {
            const count = catMap[cat][f.path] || 0;
            const label = document.createElement("label");
            label.style.cssText = "display:flex; align-items:center; gap:5px; font-size:12px; cursor:pointer; padding:4px 8px; background:var(--space-bg); border:1px solid var(--stroke-color); border-radius:6px;";
            label.innerHTML = `<input type="checkbox" class="sep-cat-cb" data-folder="${escapedPathForAttr}" value="${_attrEsc(cat)}" checked style="width:13px; height:13px;"> ${_esc(cat)} (${count})`;
            catContainer.appendChild(label);
        });
        separateBody.appendChild(folderBlock);
    });

    // Show separate section + cancel row (hidden if only single folder)
    if (folders.length > 1) {
        separateSection.style.display = "block";
    } else {
        separateSection.style.display = "none";
    }
    cancelRow.style.display = "flex";
    cancelRow.style.justifyContent = "flex-end";
    const modalEl = document.getElementById("organize-dest-modal");

    // Close on backdrop click (outside the card)
    modalEl.onclick = function(e) {
        if (e.target === modalEl) modalEl.style.display = "none";
    };
    modalEl.style.display = "flex";
}

// --- Organize Preview Modal Logic ---
let _pendingOrgCategories = null;
let _pendingOrgDestPath = null;

function _showOrganizePreviewModal(selectedCategories, destPath) {
    _pendingOrgCategories = selectedCategories;
    _pendingOrgDestPath = destPath;
    const modal = document.getElementById("organize-preview-modal");
    const tbody = document.getElementById("org-preview-tbody");
    const summaryEl = document.getElementById("org-preview-summary");

    summaryEl.innerText = "Loading preview...";
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:#6B7280;">Scanning files for preview...</td></tr>';
    modal.style.display = "flex";

    eel.get_organize_preview(selectedCategories)().then(res => {
        if (res.status === "success") {
            // Build per-category summary
            const catCounts = {};
            res.entries.forEach(e => { catCounts[e.category] = (catCounts[e.category] || 0) + 1; });
            const catParts = Object.entries(catCounts).map(([c, n]) => n + " " + c).join(", ");
            summaryEl.innerText = res.total + " file(s) will be moved: " + catParts;

            const limit = 150;
            const entries = res.entries.slice(0, limit);
            tbody.innerHTML = "";
            entries.forEach(e => {
                const tr = document.createElement("tr");
                tr.style.borderBottom = "1px solid var(--stroke-color)";
                tr.innerHTML = `
                    <td style="padding:6px 12px; font-weight:500; color:#111827; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${_attrEsc(e.file_name)}">${_esc(e.file_name)}</td>
                    <td style="padding:6px 12px; color:#6B7280; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${_attrEsc(e.source_folder)}">${_esc(_shortPath(e.source_folder, 35))}</td>
                    <td style="padding:6px 12px;"><span class="tag" style="background:#EFF6FF; color:#2563EB; border:1px solid #BFDBFE; border-radius:6px; padding:2px 8px; font-size:11px; font-weight:600;">${_esc(e.category)}</span></td>
                    <td style="padding:6px 12px; text-align:right; color:#6B7280; font-size:11.5px;">${_esc(e.file_size)}</td>
                `;
                tbody.appendChild(tr);
            });
            if (res.entries.length > limit) {
                document.getElementById("org-preview-too-many").style.display = "block";
                document.getElementById("org-preview-total-count").innerText = res.entries.length;
            } else {
                document.getElementById("org-preview-too-many").style.display = "none";
            }
        } else {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:#DC2626;">Failed to load preview.</td></tr>';
        }
    }).catch(() => {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:#DC2626;">Failed to load preview.</td></tr>';
    });
}

// Wire up organize preview modal buttons
document.addEventListener("DOMContentLoaded", () => {
    const orgPreviewModal = document.getElementById("organize-preview-modal");
    document.getElementById("org-preview-close-btn").addEventListener("click", () => { orgPreviewModal.style.display = "none"; });
    document.getElementById("org-preview-cancel-btn").addEventListener("click", () => { orgPreviewModal.style.display = "none"; });
    orgPreviewModal.addEventListener("click", (e) => { if (e.target === orgPreviewModal) orgPreviewModal.style.display = "none"; });

    document.getElementById("org-preview-confirm-btn").addEventListener("click", async () => {
        if (!_pendingOrgCategories) return;
        const cats = _pendingOrgCategories;
        const dest = _pendingOrgDestPath;
        orgPreviewModal.style.display = "none";
        // Execute the actual organize
        window.showLoader("Organizing files...");
        const res = await eel.trigger_bulk_organization(cats, dest)();
        window.hideLoader();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            showToast(`Reorganized ${res.moved} items into folders.`, "success");
        } else {
            showToast(res.message, "error");
        }
        _pendingOrgCategories = null;
        _pendingOrgDestPath = null;
    });
});

function _executeDirectOrganize(selectedCategories, destPath) {
    // Show preview modal instead of executing directly
    _showOrganizePreviewModal(selectedCategories, destPath);
}

// ---------------------------------------------------------------------------
// Lazy Thumbnail Loading — fetches thumbnails one group at a time on demand
// ---------------------------------------------------------------------------
window._loadVisibleThumbnails = async function(scanType) {
    const placeholders = document.querySelectorAll(".thumb-placeholder");
    const globalIds = new Set();
    placeholders.forEach(el => {
        const gid = el.getAttribute("data-gid");
        if (gid !== null) globalIds.add(parseInt(gid, 10));
    });

    // (#8) Batch thumbnail loading — single API call for all visible groups
    const gidList = Array.from(globalIds);
    if (gidList.length === 0) return;

    try {
        const thumbsMap = await eel.get_thumbnails_for_page(gidList, scanType)();
        // thumbsMap = {group_id: [{name, path, thumb_b64}, ...], ...}
        for (const gid of gidList) {
            const thumbs = thumbsMap[gid];
            if (!thumbs) continue;
            document.querySelectorAll(`.thumb-placeholder[data-gid="${gid}"]`).forEach(el => {
                const gIdx = parseInt(el.getAttribute("data-gidx"), 10);
                const fIdx = parseInt(el.getAttribute("data-fidx"), 10);
                const match = thumbs.find(t => t.path === window.currentDuplicateGroups[gIdx].files[fIdx].path);
                if (match && match.thumb_b64) {
                    const img = document.createElement("img");
                    img.src = match.thumb_b64;
                    img.style.cssText = "cursor:pointer; width:38px; height:38px; border-radius:8px; object-fit:cover; border:1px solid var(--stroke-color); flex-shrink:0;";
                    img.title = "Click for full preview";
                    img.onclick = () => openImagePreview(gIdx, fIdx);
                    el.replaceWith(img);
                }
            });
        }
    } catch(e) {
        // Silently skip if backend is busy
    }
};

// ---------------------------------------------------------------------------
// Pagination Handlers for Duplicates Panel
// ---------------------------------------------------------------------------
document.getElementById("dup-prev-btn").addEventListener("click", async () => {
    if (dupCurrentPage > 0) {
        dupCurrentPage--;
        window.showLoader("Loading previous page...");
        await _loadDuplicatePage();
        window.hideLoader();
    }
});

document.getElementById("dup-next-btn").addEventListener("click", async () => {
    if (dupCurrentPage < dupTotalPages - 1) {
        dupCurrentPage++;
        window.showLoader("Loading next page...");
        await _loadDuplicatePage();
        window.hideLoader();
    }
});

document.getElementById("dup-jump-btn").addEventListener("click", async () => {
    const input = document.getElementById("dup-jump-input");
    const target = parseInt(input.value, 10);
    if (isNaN(target) || target < 1 || target > dupTotalPages) {
        input.value = "";
        return;
    }
    dupCurrentPage = target - 1;
    input.value = "";
    window.showLoader(`Loading page ${dupCurrentPage + 1}...`);
    await _loadDuplicatePage();
    window.hideLoader();
});

document.getElementById("dup-jump-input").addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
        document.getElementById("dup-jump-btn").click();
    }
});

window._loadDuplicatePage = async function() {
    const thresholdVal = 10; // similar-image scanning moved to Gallery; Duplicates is exact-only now
    const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage, 25, dupNameFilter)();
    
    const dupGroups = dupResponse.displayed_groups || [];
    window.currentDuplicateGroups = dupGroups;
    dupTotalGroups = dupResponse.total_groups || 0;
    dupTotalPages = dupResponse.total_pages || 1;
    dupCurrentPage = dupResponse.page || 0;

    const dupSelectAllBtn = document.getElementById("dup-select-all");
    if(dupSelectAllBtn) dupSelectAllBtn.checked = false;

    const paginationBar = document.getElementById("dup-pagination-bar");
    if (paginationBar) {
        if (dupTotalPages > 1) {
            paginationBar.style.display = "flex";
            const startItem = dupCurrentPage * 25 + 1;
            const endItem = Math.min((dupCurrentPage + 1) * 25, dupTotalGroups);
            document.getElementById("dup-page-info").innerText = 
                `Showing ${startItem}\u2013${endItem} of ${dupTotalGroups.toLocaleString()} sets`;
            document.getElementById("dup-prev-btn").disabled = (dupCurrentPage <= 0);
            document.getElementById("dup-next-btn").disabled = (dupCurrentPage >= dupTotalPages - 1);
        } else {
            paginationBar.style.display = "none";
        }
    }

    const dupContainer = document.getElementById("duplicates-render-container");
    if (!dupContainer) return;
    dupContainer.innerHTML = "";

    if (dupGroups.length === 0) {
        dupContainer.innerHTML += '<p style="color:var(--text-secondary); font-size:13.5px;">No duplicate elements on this page.</p>';
        return;
    }

    dupGroups.forEach((group, gIdx) => {
        const groupWrapper = document.createElement("div");
        groupWrapper.style.padding = "16px";
        groupWrapper.style.border = "1px solid var(--stroke-color)";
        groupWrapper.style.borderRadius = "8px";
        groupWrapper.style.marginBottom = "16px";
        groupWrapper.style.backgroundColor = "#fff";
        
        let itemsListHtml = "";
        group.files.forEach((file, fIdx) => {
            const autoChecked = (activeScanType === "exact" && fIdx > 0) ? "checked" : "";
            let mediaThumbnailHtml = `
                <div class="thumb-placeholder" data-gidx="${gIdx}" data-fidx="${fIdx}" data-gid="${group.id}" style="width:38px; height:38px; border-radius:8px; background:#F3F5FA; border:1px solid var(--stroke-color); display:flex; align-items:center; justify-content:center; flex-shrink:0; color:#98A2B3; cursor:pointer;" title="Loading...">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px; height:16px;"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                </div>`;
            if (file.thumb_b64) {
                mediaThumbnailHtml = `<img src="${file.thumb_b64}" onclick="openImagePreview(${gIdx}, ${fIdx})" style="cursor:pointer; width:38px; height:38px; border-radius:8px; object-fit:cover; border:1px solid var(--stroke-color); flex-shrink:0;" title="Click for full preview" />`;
            }
            itemsListHtml += `
                <div class="dup-row" style="display:flex; align-items:center; gap:12px; padding:12px 6px; border-bottom:1px solid var(--stroke-color);">
                    <input type="checkbox" class="dup-file-purge-checkbox" data-gidx="${gIdx}" data-fidx="${fIdx}" ${autoChecked} style="width:16px; height:16px;">
                    ${mediaThumbnailHtml}
                    <div class="dup-info">
                        <b style="font-size:13px; display:block; color:var(--text-primary); word-break:break-all;">${_esc(file.name)}</b>
                        <span style="font-size:11.5px; color:var(--text-secondary); word-break:break-all;">${_esc(file.path)}</span>
                    </div>
                </div>
            `;
        });
        groupWrapper.innerHTML = `
            <div style="font-size:11px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">Set Match Collection — ${group.size_str} copies each</div>
            <div style="display:flex; flex-direction:column;">${itemsListHtml}</div>
        `;
        dupContainer.appendChild(groupWrapper);
    });

    _loadVisibleThumbnails(activeScanType);
};
// ---------------------------------------------------------------------------
// Gallery
// ---------------------------------------------------------------------------
let galleryFolders = [];
let galleryCurrentFolder = null;
let galleryCurrentPage = 0;
let galleryTotalPages = 1;
let gallerySimilarityMap = {};
let gallerySimilarityReady = false;
let gallerySortBy = "name";
let gallerySortDesc = false;
let galleryOnlySimilarActive = false;
let galleryOnlySimilarAllItems = [];
let galleryOnlySimilarPage = 0;
const GALLERY_PAGE_SIZE = 60;
let gallerySelectedPaths = new Set();
let galleryNameFilter = "";
let galleryViewMode = "medium";
let folderBrowserViewMode = "medium";

window.currentGalleryPageItems = [];
window.galleryPreviewIndex = 0;
window.galleryPreviewActive = false;
window.galleryPreviewSource = "gallery"; // "gallery" | "folder-browser" — which grid to refresh on delete

let _galleryEverLoaded = false;

async function loadGalleryData() {
    // FIX: previously always showed the full-screen loader on every visit,
    // even though get_gallery_folders/get_gallery_page/similarity-map are
    // all cache-backed and return near-instantly after the first real scan.
    // Only the very first visit (or right after switching workspaces, which
    // resets this flag below) needs the visible loading state.
    const showFullLoader = !_galleryEverLoaded;
    if (showFullLoader) window.showLoader("Loading gallery...");
    galleryFolders = await eel.get_gallery_folders()();
    _renderGalleryFolderChips();
    await loadGalleryPage(0);
    await _refreshGallerySimilarityMap();
    if (showFullLoader) window.hideLoader();
    _galleryEverLoaded = true;
}

function _renderGalleryFolderChips() {
    const container = document.getElementById("gallery-folder-chips");
    if (!container) return;
    container.innerHTML = "";

    const allChip = document.createElement("button");
    allChip.className = "gallery-folder-chip" + (galleryCurrentFolder === null ? " active" : "");
    allChip.innerText = `All (${galleryFolders.reduce((a, f) => a + f.count, 0)})`;
    allChip.addEventListener("click", () => { galleryCurrentFolder = null; loadGalleryPage(0); _renderGalleryFolderChips(); });
    container.appendChild(allChip);

    galleryFolders.forEach(f => {
        const chip = document.createElement("button");
        chip.className = "gallery-folder-chip" + (galleryCurrentFolder === f.path ? " active" : "");
        chip.title = f.path;
        chip.innerText = `${f.name} (${f.count})`;
        chip.addEventListener("click", () => { galleryCurrentFolder = f.path; loadGalleryPage(0); _renderGalleryFolderChips(); });
        container.appendChild(chip);
    });
}

async function loadGalleryPage(page) {
    document.getElementById("gallery-filter-banner").style.display = "none";

    // "Show Only Similar" is a client-side filter over the similarity map, not
    // a normal paginated fetch — route there instead if it's active.
    if (galleryOnlySimilarActive) {
        _renderOnlySimilarView();
        return;
    }

    const res = await eel.get_gallery_page(galleryCurrentFolder, page, GALLERY_PAGE_SIZE, gallerySortBy, gallerySortDesc, galleryNameFilter)();
    galleryCurrentPage = res.page;
    galleryTotalPages = res.total_pages;
    _updateGalleryPagination(res.total, res.total_pages, res.page);
    renderGalleryGrid(res.items);
}

function _updateGalleryPagination(total, totalPages, currentPage) {
    const bar = document.getElementById("gallery-pagination-bar");
    if (!bar) return;
    const tp = totalPages !== undefined ? totalPages : galleryTotalPages;
    const cp = currentPage !== undefined ? currentPage : galleryCurrentPage;
    if (tp > 1) {
        bar.style.display = "flex";
        document.getElementById("gallery-page-info").innerText =
            `Page ${cp + 1} of ${tp} (${total.toLocaleString()} images)`;
        document.getElementById("gallery-prev-btn").disabled = (cp <= 0);
        document.getElementById("gallery-next-btn").disabled = (cp >= tp - 1);
    } else {
        bar.style.display = "none";
    }
}

function renderGalleryGrid(items) {
    window.currentGalleryPageItems = items;
    gallerySelectedPaths.clear();
    _updateGallerySelectionUI();

    const grid = document.getElementById("gallery-grid");
    grid.className = "gallery-grid view-" + galleryViewMode;
    grid.innerHTML = "";
    if (items.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">No images in this view.</p>';
        return;
    }
    items.forEach((item, idx) => {
        const tile = document.createElement("div");
        tile.className = "gallery-tile thumb-placeholder";
        tile.setAttribute("data-path", item.path);
        const metaHtml = item.size_str ? `<span class="tile-meta">${_esc(item.size_str)}</span>` : "";
        tile.innerHTML = `
            <input type="checkbox" class="gallery-tile-checkbox" title="Select for deletion">
            <div class="gallery-tile-inner">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="gallery-tile-icon"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
            </div>
            <div class="gallery-tile-name" title="${_attrEsc(item.name)}"><span>${_esc(item.name)}</span>${metaHtml}</div>
        `;
        const checkbox = tile.querySelector(".gallery-tile-checkbox");
        checkbox.addEventListener("click", (e) => e.stopPropagation());
        checkbox.addEventListener("change", (e) => {
            if (e.target.checked) gallerySelectedPaths.add(item.path);
            else gallerySelectedPaths.delete(item.path);
            tile.classList.toggle("selected", e.target.checked);
            _updateGallerySelectionUI();
        });
        tile.addEventListener("click", () => { window.galleryPreviewSource = "gallery"; window.openGalleryImagePreview(idx); });
        grid.appendChild(tile);
    });
    _applyGallerySimilarityBadges();
    _loadGalleryThumbnails(items);
}

function _updateGallerySelectionUI() {
    const btn = document.getElementById("gallery-delete-selected-btn");
    const countEl = document.getElementById("gallery-selection-count");
    const count = gallerySelectedPaths.size;
    if (btn) {
        btn.disabled = count === 0;
        btn.innerText = count > 0 ? `Delete Selected (${count})` : "Delete Selected";
    }
    if (countEl) countEl.innerText = count > 0 ? `${count} selected` : "";
}

async function _deleteGalleryPaths(paths) {
    if (!paths || paths.length === 0) return;
    const res = await eel.purge_selected_duplicates(paths)();
    if (res.status !== "success") {
        showToast(res.message || "Failed to delete selected image(s).", "error");
        return;
    }
    showToast(`Moved ${res.purged} image(s) to the Recycle Bin.`, "success");
    gallerySelectedPaths.clear();
    await _afterGalleryDeletion();
}

async function _afterGalleryDeletion() {
    // Deleting changes the file population, so any cached similarity grouping
    // is now stale (the backend already invalidated it — see
    // invalidate_duplicate_cache() in purge_selected_duplicates()). Drop back
    // to the normal grid rather than show a stale or empty "only similar"
    // view; the user re-runs Find Similar Images if they want fresh groups.
    galleryOnlySimilarActive = false;
    const onlySimilarBtn = document.getElementById("gallery-only-similar-btn");
    if (onlySimilarBtn) onlySimilarBtn.classList.remove("active");
    gallerySimilarityReady = false;
    gallerySimilarityMap = {};
    document.getElementById("gallery-similar-count").innerText = "Not scanned yet.";
    document.getElementById("gallery-filter-banner").style.display = "none";

    galleryFolders = await eel.get_gallery_folders()();
    _renderGalleryFolderChips();
    await loadGalleryPage(galleryCurrentPage);
}

async function _loadGalleryThumbnails(items) {
    const paths = items.map(i => i.path);
    if (paths.length === 0) return;
    const thumbsMap = await eel.get_gallery_thumbnails(paths, _variantForViewMode(galleryViewMode))();
    items.forEach(item => {
        const b64 = thumbsMap[item.path];
        if (!b64) return;
        const tile = document.querySelector(`#gallery-grid .gallery-tile[data-path="${CSS.escape(item.path)}"]`);
        if (!tile) return;
        tile.classList.remove("thumb-placeholder");
        const inner = tile.querySelector(".gallery-tile-inner");
        inner.innerHTML = `<img src="${b64}" class="gallery-tile-img">`;
    });
}

function _galleryBadgeColor(gid) {
    const palette = ['#2563EB', '#7A5AF8', '#12B76A', '#F79009', '#F04438', '#0EA5E9', '#DB2777'];
    return palette[gid % palette.length];
}

function _applyGallerySimilarityBadges() {
    if (!gallerySimilarityReady) return;
    document.querySelectorAll("#gallery-grid .gallery-tile").forEach(tile => {
        const path = tile.getAttribute("data-path");
        const info = gallerySimilarityMap[path];
        const gid = info ? info.group : undefined;
        const existing = tile.querySelector(".gallery-badge");
        if (existing) existing.remove();
        if (gid !== undefined) {
            const badge = document.createElement("div");
            badge.className = "gallery-badge";
            const distLabel = (info.distance !== undefined) ? ` — closeness \u0394${info.distance}` : "";
            badge.title = `Similar-image group #${gid + 1}${distLabel} — click to filter`;
            badge.innerText = gid + 1;
            badge.style.background = _galleryBadgeColor(gid);
            badge.addEventListener("click", (e) => { e.stopPropagation(); _filterGalleryByGroup(gid); });
            tile.appendChild(badge);
        }
    });
}

function _sortGalleryItemsByGroupThenName(items) {
    // "Only Similar" and single-group views are built from the similarity
    // map, not a fresh backend page, so sorting happens client-side. Group
    // first (badge #1's images together, then #2's, ...) so scrolling walks
    // one cluster at a time instead of interleaving groups by filename;
    // name (numeric-aware, same as the main grid's natural sort) breaks
    // ties within a group.
    const sorted = [...items];
    sorted.sort((a, b) => {
        const infoA = gallerySimilarityMap[a.path];
        const infoB = gallerySimilarityMap[b.path];
        const ga = infoA ? infoA.group : Number.MAX_SAFE_INTEGER;
        const gb = infoB ? infoB.group : Number.MAX_SAFE_INTEGER;
        if (ga !== gb) return ga - gb;
        return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
    });
    return sorted;
}

function _renderOnlySimilarView() {
    if (!gallerySimilarityReady) {
        showToast('Run "Find Similar Images" first.', "info");
        galleryOnlySimilarActive = false;
        const btn = document.getElementById("gallery-only-similar-btn");
        if (btn) btn.classList.remove("active");
        return;
    }
    const allMatchPaths = Object.keys(gallerySimilarityMap);
    let items = allMatchPaths.map(p => {
        const known = window.currentGalleryPageItems.find(i => i.path === p);
        return known || { path: p, name: p.split(/[\\/]/).pop(), folder: "" };
    });
    if (galleryNameFilter.trim()) {
        const nf = galleryNameFilter.trim().toLowerCase();
        items = items.filter(i => i.name.toLowerCase().includes(nf));
    }
    items = _sortGalleryItemsByGroupThenName(items);
    galleryOnlySimilarAllItems = items;
    galleryOnlySimilarPage = 0;
    _renderOnlySimilarPage();
}

function _renderOnlySimilarPage() {
    const items = galleryOnlySimilarAllItems;
    const totalPages = Math.max(1, Math.ceil(items.length / GALLERY_PAGE_SIZE));
    galleryOnlySimilarPage = Math.max(0, Math.min(galleryOnlySimilarPage, totalPages - 1));
    const start = galleryOnlySimilarPage * GALLERY_PAGE_SIZE;
    const pageItems = items.slice(start, start + GALLERY_PAGE_SIZE);

    const groupCount = new Set(Object.values(gallerySimilarityMap).map(v => v.group)).size;
    document.getElementById("gallery-filter-banner").style.display = "flex";
    document.getElementById("gallery-filter-label").innerText =
        `Showing only similar images (${items.length} image(s) across ${groupCount} group(s))`;

    _updateGalleryPagination(items.length, totalPages, galleryOnlySimilarPage);
    renderGalleryGrid(pageItems);
}

function _filterGalleryByGroup(gid) {
    const allMatchPaths = Object.keys(gallerySimilarityMap).filter(p => gallerySimilarityMap[p].group === gid);
    const items = allMatchPaths.map(p => {
        const known = window.currentGalleryPageItems.find(i => i.path === p);
        return known || { path: p, name: p.split(/[\\/]/).pop(), folder: "" };
    });
    document.getElementById("gallery-filter-banner").style.display = "flex";
    document.getElementById("gallery-filter-label").innerText = `Showing similarity group #${gid + 1} (${items.length} image(s))`;
    document.getElementById("gallery-pagination-bar").style.display = "none";
    renderGalleryGrid(items);
}

async function _refreshGallerySimilarityMap() {
    const thresholdVal = document.getElementById("gallery-similarity-select").value;
    const res = await eel.get_gallery_similarity_map(thresholdVal)();
    gallerySimilarityReady = res.ready;
    gallerySimilarityMap = res.map || {};
    document.getElementById("gallery-similar-count").innerText = res.ready
        ? `${res.group_count} similar-image group(s) found.`
        : (res.scanning ? "Scanning..." : "Not scanned yet.");

    if (galleryOnlySimilarActive) {
        _renderOnlySimilarView();
    } else {
        _applyGallerySimilarityBadges();
    }
}

// ---------------------------------------------------------------------------
// Idle-triggered background similar-image scan (Phase 3, #5)
//
// This is an IN-APP idle proxy, not true OS-level idle detection — it tracks
// mouse/keyboard/scroll activity within this window only. That's a
// deliberate, honest trade-off: real system idle detection needs a native
// dependency this app doesn't otherwise require. If the person is idle in
// this app but actively doing something else on their machine, this can
// still fire — the CPU cap (2 cores, see start_idle_similar_scan in
// gui_duplicates.py) and the "don't fire while anything else is running"
// checks below exist specifically to keep that acceptable.
// ---------------------------------------------------------------------------
let _lastActivityTime = Date.now();
let _idleScanFired = false;
let idleAutoScanEnabled = true;
const IDLE_THRESHOLD_MS = 60 * 1000;   // consider "idle" after 60s of no input
const IDLE_CHECK_INTERVAL_MS = 15 * 1000;
const IDLE_SCAN_THRESHOLDS = [5, 10, 16]; // Strict, Normal, Loose — see SIMILARITY_PRESETS

["mousemove", "mousedown", "keydown", "scroll", "click", "touchstart"].forEach(evt => {
    document.addEventListener(evt, () => {
        _lastActivityTime = Date.now();
        _idleScanFired = false; // a fresh idle period can trigger again later
    }, { passive: true });
});

function initIdleAutoScan() {
    const toggle = document.getElementById("gallery-idle-autoscan-toggle");
    if (toggle) {
        idleAutoScanEnabled = toggle.checked;
        toggle.addEventListener("change", (e) => {
            idleAutoScanEnabled = e.target.checked;
        });
    }

    setInterval(async () => {
        if (!idleAutoScanEnabled || _idleScanFired) return;
        if (typeof eel === "undefined") return;
        if (Date.now() - _lastActivityTime < IDLE_THRESHOLD_MS) return;

        // Don't compete with anything already using the CPU or the UI thread.
        const loaderVisible = document.getElementById("global-loader").style.display !== "none";
        if (loaderVisible) return;

        const metadata = await eel.get_system_metadata()();
        if (!metadata.folder) return;

        const status = await eel.get_similar_scan_status()();
        if (status.scanning) return; // a foreground/manual scan is already running

        _idleScanFired = true; // don't re-trigger the cycle again this same idle stretch
        await _runIdleScanCycle();
    }, IDLE_CHECK_INTERVAL_MS);
}

// Mirrors a message to both the browser DevTools console AND the Python
// terminal (via gui_state.log_to_terminal). Eel's chrome/edge app-mode
// window doesn't expose DevTools by default, so background activity like
// the idle-scan cycle would otherwise be invisible unless it happens to
// touch something already rendered on screen.
function _logBoth(msg) {
    console.log(msg);
    if (typeof eel !== "undefined" && eel.log_to_terminal) {
        eel.log_to_terminal(msg)();
    }
}

// Cycles Strict/Normal/Loose sequentially — the backend only runs one
// background scan thread at a time, so firing all three at once would just
// make start_idle_similar_scan() no-op on the 2nd and 3rd with
// {"status": "scanning"}. Each already-disk-cached threshold (see Phase 2's
// scan_results table) resolves in milliseconds via find_similar_images()'s
// own internal signature check, so only genuinely uncached thresholds take
// real time. Bails immediately, mid-cycle, the moment the user comes back.
async function _runIdleScanCycle() {
    for (const threshold of IDLE_SCAN_THRESHOLDS) {
        if (Date.now() - _lastActivityTime < IDLE_THRESHOLD_MS) {
            _logBoth("[idle-scan] Activity detected — stopping idle cycle.");
            return;
        }
        if (!idleAutoScanEnabled) return;

        const status = await eel.get_similar_scan_status()();
        if (status.scanning) return; // don't pile onto a scan started elsewhere

        _logBoth(`[idle-scan] Warming threshold ${threshold} (capped to 2 cores)...`);
        const res = await eel.start_idle_similar_scan(threshold)();

        if (res.status === "started") {
            // Wait for this threshold to actually finish before moving on.
            for (let i = 0; i < 300; i++) { // ~2.5 min safety cap per threshold
                if (Date.now() - _lastActivityTime < IDLE_THRESHOLD_MS) return;
                const s = await eel.get_similar_scan_status()();
                if (!s.scanning) break;
                await new Promise(r => setTimeout(r, 500));
            }
        }
        // "cached": already warm on disk — start_idle_similar_scan() returned
        // instantly, nothing to wait for; move straight to the next threshold.
    }
    _logBoth("[idle-scan] Idle cycle complete — Strict/Normal/Loose all pre-cached.");
    // If the person is sitting on the Gallery tab right now, refresh the
    // badge overlay so the newly-warmed thresholds are reflected without
    // needing to touch anything.
    if (document.getElementById("gallery-panel").classList.contains("active-view")) {
        await _refreshGallerySimilarityMap();
    }
}

function initGalleryHandlers() {
    document.getElementById("gallery-find-similar-btn").addEventListener("click", async () => {
        const thresholdVal = document.getElementById("gallery-similarity-select").value;
        document.getElementById("gallery-scan-progress").style.display = "block";
        const status = await eel.start_similar_scan(thresholdVal)();
        if (status.status === "cached") {
            document.getElementById("gallery-scan-progress").style.display = "none";
            await _refreshGallerySimilarityMap();
        }
        // "started": wait for _on_similar_scan_progress / _on_similar_scan_complete
    });

    document.getElementById("gallery-prev-btn").addEventListener("click", () => {
        if (galleryOnlySimilarActive) {
            if (galleryOnlySimilarPage > 0) {
                galleryOnlySimilarPage--;
                _renderOnlySimilarPage();
            }
        } else if (galleryCurrentPage > 0) {
            loadGalleryPage(galleryCurrentPage - 1);
        }
    });
    document.getElementById("gallery-next-btn").addEventListener("click", () => {
        if (galleryOnlySimilarActive) {
            const totalPages = Math.max(1, Math.ceil(galleryOnlySimilarAllItems.length / GALLERY_PAGE_SIZE));
            if (galleryOnlySimilarPage < totalPages - 1) {
                galleryOnlySimilarPage++;
                _renderOnlySimilarPage();
            }
        } else if (galleryCurrentPage < galleryTotalPages - 1) {
            loadGalleryPage(galleryCurrentPage + 1);
        }
    });
    document.getElementById("gallery-clear-filter-btn").addEventListener("click", () => {
        document.getElementById("gallery-filter-banner").style.display = "none";
        loadGalleryPage(galleryCurrentPage);
    });

    document.getElementById("gallery-sort-select").addEventListener("change", (e) => {
        gallerySortBy = e.target.value;
        loadGalleryPage(0);
    });

    document.getElementById("gallery-sort-dir-btn").addEventListener("click", (e) => {
        gallerySortDesc = !gallerySortDesc;
        e.target.innerHTML = gallerySortDesc ? "&darr; Desc" : "&uarr; Asc";
        loadGalleryPage(0);
    });

    document.getElementById("gallery-only-similar-btn").addEventListener("click", (e) => {
        galleryOnlySimilarActive = !galleryOnlySimilarActive;
        e.target.classList.toggle("active", galleryOnlySimilarActive);
        if (galleryOnlySimilarActive) {
            _renderOnlySimilarView();
        } else {
            document.getElementById("gallery-filter-banner").style.display = "none";
            loadGalleryPage(0);
        }
    });

    document.getElementById("gallery-select-all-btn").addEventListener("click", () => {
        const checkboxes = document.querySelectorAll(".gallery-tile-checkbox");
        const allSelected = gallerySelectedPaths.size === checkboxes.length && checkboxes.length > 0;
        checkboxes.forEach(cb => {
            cb.checked = !allSelected;
            cb.dispatchEvent(new Event("change"));
        });
    });

    document.getElementById("gallery-delete-selected-btn").addEventListener("click", async () => {
        if (gallerySelectedPaths.size === 0) return;
        const proceed = await _showSimpleConfirmModal(
            "Move to Recycle Bin",
            `Move ${gallerySelectedPaths.size} selected image(s) to the Recycle Bin? You can restore them later from Undo History.`,
            "#D97706"
        );
        if (!proceed) return;
        await _deleteGalleryPaths(Array.from(gallerySelectedPaths));
    });

    document.getElementById("gallery-smart-delete-btn").addEventListener("click", async () => {
        if (!gallerySimilarityReady) {
            showToast('Run "Find Similar Images" first.', "info");
            return;
        }
        const strategy = document.getElementById("gallery-smart-strategy-select").value;
        await _enterGallerySmartSelectReview(strategy);
    });
}

async function _enterGallerySmartSelectReview(strategy) {
    const preview = await eel.get_smart_select_preview("similar", strategy)();
    if (!preview.groups || preview.groups.length === 0) {
        showToast("Nothing to smart-select — every group already has just one image.", "info");
        return;
    }

    // Flatten groups into a single item list (same idea as _renderOnlySimilarView
    // flattening gallerySimilarityMap), remembering which paths to pre-check.
    const preselectedPaths = new Set();
    const items = [];
    preview.groups.forEach(g => {
        g.files.forEach(f => {
            items.push({ path: f.path, name: f.name, folder: "" });
            if (f.preselected) preselectedPaths.add(f.path);
        });
    });

    galleryOnlySimilarActive = false;
    const onlySimilarBtn = document.getElementById("gallery-only-similar-btn");
    if (onlySimilarBtn) onlySimilarBtn.classList.remove("active");
    document.getElementById("gallery-pagination-bar").style.display = "none";

    const strategyLabel = strategy === "largest" ? "largest" : "most recently modified";
    document.getElementById("gallery-filter-banner").style.display = "flex";
    document.getElementById("gallery-filter-label").innerText =
        `Reviewing Smart Select: ${preview.total_to_delete} file(s) across ${preview.groups.length} group(s) pre-checked (keeping the ${strategyLabel} image in each). Uncheck any you want to keep, then click "Delete Selected".`;

    renderGalleryGrid(items);

    // Pre-check the flagged tiles — has to happen AFTER renderGalleryGrid,
    // since that function clears selection state on every render by design.
    document.querySelectorAll(".gallery-tile-checkbox").forEach(cb => {
        const tile = cb.closest(".gallery-tile");
        const path = tile ? tile.getAttribute("data-path") : null;
        if (path && preselectedPaths.has(path)) {
            cb.checked = true;
            gallerySelectedPaths.add(path);
            tile.classList.add("selected");
        }
    });
    _updateGallerySelectionUI();
}

window.openGalleryImagePreview = async function (idx) {
    window.galleryPreviewActive = true;
    window.galleryPreviewIndex = idx;
    const target = window.currentGalleryPageItems[idx];
    const modal = document.getElementById("image-preview-modal");
    const imgEl = document.getElementById("preview-modal-img");
    const loader = document.getElementById("preview-loading");
    const infoEl = document.getElementById("preview-file-info");

    modal.style.display = "flex";
    loader.style.display = "block";
    imgEl.style.display = "none";
    imgEl.src = "";
    infoEl.innerText = "";

    const b64Data = await eel.get_full_image_b64(target.path)();
    if (b64Data) {
        loader.style.display = "none";
        imgEl.src = b64Data;
        imgEl.style.display = "block";
        infoEl.innerText = `${target.name}   —   ${target.folder}`;
    } else {
        loader.innerText = "Error loading high resolution image data.";
    }
};

// Delete-key shortcut in the Gallery lightbox: moves the currently open
// image to the Recycle Bin, then shows the next one (or closes if that was
// the last image on this page).
async function _deleteCurrentGalleryPreviewImage() {
    const items = window.currentGalleryPageItems;
    if (!items || items.length === 0) return;
    const target = items[window.galleryPreviewIndex];
    if (!target) return;

    const res = await eel.purge_selected_duplicates([target.path])();
    if (res.status !== "success") {
        showToast(res.message || "Failed to delete image.", "error");
        return;
    }
    showToast(`Moved "${target.name}" to the Recycle Bin.`, "success");

    // Similarity grouping is now stale (file population changed) — same
    // reasoning as the bulk-delete toolbar.
    galleryOnlySimilarActive = false;
    const onlySimilarBtn = document.getElementById("gallery-only-similar-btn");
    if (onlySimilarBtn) onlySimilarBtn.classList.remove("active");
    gallerySimilarityReady = false;
    gallerySimilarityMap = {};
    document.getElementById("gallery-similar-count").innerText = "Not scanned yet.";
    document.getElementById("gallery-filter-banner").style.display = "none";

    const removedIndex = window.galleryPreviewIndex;
    items.splice(removedIndex, 1);

    galleryFolders = await eel.get_gallery_folders()();
    _renderGalleryFolderChips();

    if (window.galleryPreviewSource === "folder-browser") {
        // Folder Browser doesn't support in-place splice+re-render the way
        // Gallery's grid does — a full reload of the current folder is
        // simpler and always correct, at the cost of closing the lightbox
        // instead of advancing to the next image in place.
        document.getElementById("image-preview-modal").style.display = "none";
        window.galleryPreviewActive = false;
        await loadFolderBrowser(fbBrowsePath);
        return;
    }

    if (items.length === 0) {
        document.getElementById("image-preview-modal").style.display = "none";
        window.galleryPreviewActive = false;
        await loadGalleryPage(galleryCurrentPage);
        return;
    }

    // Full re-render rebinds every tile's click handler to its new (shifted)
    // index — splicing the array alone would leave stale onclick indices on
    // every tile after the deleted one.
    renderGalleryGrid(items);
    const nextIndex = Math.min(removedIndex, items.length - 1);
    window.openGalleryImagePreview(nextIndex);
}

// Same shortcut for the Duplicates-panel lightbox. Always closes and does a
// full panel refresh (rather than advancing in place like the gallery does)
// because each row's thumbnail has its file index baked into an inline
// onclick handler at render time — splicing the in-memory array without a
// full re-render would leave those handlers pointing at the wrong file.
async function _deleteCurrentDuplicatePreviewImage() {
    const group = window.currentDuplicateGroups[window.currentPreviewGidx];
    if (!group) return;
    const target = group.files[window.currentPreviewFidx];
    if (!target) return;

    const res = await eel.purge_selected_duplicates([target.path])();
    if (res.status !== "success") {
        showToast(res.message || "Failed to delete image.", "error");
        return;
    }
    showToast(`Moved "${target.name}" to the Recycle Bin.`, "success");

    document.getElementById("image-preview-modal").style.display = "none";
    await refreshDashboardTelemetryMetrics();
}
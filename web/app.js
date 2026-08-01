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
function _attrEsc(str) {
    return _esc(str).replace(/"/g, "&quot;");
}

let currentCategoriesMap = [];
let activeScanType = "exact";
let currentRenameCategory = null;

// Multi-folder comparison state (unlimited folders)
let comparisonFolders = [];
let organizeFolderData = null;

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

// Global Loader Wrappers
window.showLoader = function(msg = "Processing...") {
    document.getElementById("loader-text").innerText = msg;
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

// Eel exposes for Python→JS callbacks
if (typeof eel !== "undefined") {
    eel.expose(_on_python_progress);
    eel.expose(_on_similar_scan_complete);
    eel.expose(_on_similar_scan_progress);
}
function _on_python_progress(message, current, total) {
    if (document.getElementById("global-loader").style.display !== "none") {
        updateLoaderProgress(message, current, total);
    }
}

function _on_similar_scan_complete(result) {
    const progressBar = document.getElementById("similar-scan-progress");
    if (progressBar) progressBar.style.display = "none";

    const exactTab = document.getElementById("tab-exact");
    const similarTab = document.getElementById("tab-similar");
    if (!exactTab || !similarTab || !similarTab.classList.contains("active-tab")) return;

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

function _on_similar_scan_progress(data) {
    const progressBar = document.getElementById("similar-scan-progress");
    const bar = document.getElementById("similar-scan-bar");
    const msg = document.getElementById("similar-scan-msg");
    const counter = document.getElementById("similar-scan-counter");
    if (!progressBar || !bar) return;

    progressBar.style.display = "block";
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
    toast.className = "toast";
    toast.style.backgroundColor = c.bg;
    toast.style.borderLeft = "4px solid " + c.border;
    toast.innerHTML = `
        <span class="toast-icon">${c.icon}</span>
        <span class="toast-msg">${message}</span>
    `;

    toast.style.cursor = "pointer";
    toast.onclick = () => dismissToast(toast);

    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.style.transform = "translateX(0)";
    });

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
    initDuplicateViewTabHandlers();
    initAdminAndRenameHandlers();
    initInteractivityHandlers();
    initCategoryHandlers();
});

function initViewPanelNavigation() {
    const navButtons = document.querySelectorAll(".nav-btn");
    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active-view"));
            btn.classList.add("active");
            const target = btn.getAttribute("data-target");
            document.getElementById(target).classList.add("active-view");
            refreshDashboardTelemetryMetrics();
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

function initDuplicateViewTabHandlers() {
    const subTabs = document.querySelectorAll(".sub-tab");
    subTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            subTabs.forEach(t => {
                t.classList.remove("active-tab");
                t.style.borderBottom = "2px solid transparent";
                t.style.fontWeight = "normal";
            });
            tab.classList.add("active-tab");
            tab.style.borderBottom = "2px solid #3B82F6";
            tab.style.fontWeight = "bold";

            activeScanType = tab.getAttribute("data-scan");
            document.getElementById("similarity-threshold-pane").style.display = (activeScanType === "similar") ? "block" : "none";
            dupCurrentPage = 0;
            refreshDashboardTelemetryMetrics();
        });
    });
}

function initAdminAndRenameHandlers() {
    const pinBtn = document.getElementById("submit-pin-btn");
    if (pinBtn) {
        pinBtn.addEventListener("click", async () => {
            const val = document.getElementById("admin-pin-input").value;
            if (!val) return showToast("Please enter the admin PIN.", "warning");
            const res = await eel.verify_admin_pin(val)();
            if (res.status === "success") {
                showToast("Admin access granted.", "success");
                unlockAdminUI();
                document.getElementById("pin-auth-section").style.display = "none";
            } else {
                showToast(res.message || "Incorrect PIN.", "error");
            }
        });
    }

    // Rename handlers
    document.getElementById("rename-category-select").addEventListener("change", (e) => {
        currentRenameCategory = e.target.value;
        _clearRenamePreview();
    });
    document.getElementById("rename-preview-btn").addEventListener("click", async () => {
        if (!currentRenameCategory) return showToast("Select a category first.", "warning");
        const op = document.getElementById("rename-operation-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        if (!arg1 && op !== "case") return showToast("Enter a value for the operation.", "warning");

        const changed = await eel.preview_rename(currentRenameCategory, op, arg1, arg2)();
        const previewBody = document.getElementById("rename-preview-body");
        previewBody.innerHTML = "";
        if (changed.length === 0) {
            previewBody.innerHTML = '<tr><td colspan="2" class="text-center-cell">No filenames would change.</td></tr>';
        } else {
            changed.forEach(r => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td class="tbl-cell">${_esc(r.old)}</td><td class="tbl-cell">${_esc(r.new)}</td>`;
                previewBody.appendChild(tr);
            });
        }
    });
    document.getElementById("rename-execute-btn").addEventListener("click", async () => {
        if (!currentRenameCategory) return showToast("Select a category first.", "warning");
        const op = document.getElementById("rename-operation-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        if (!arg1 && op !== "case") return showToast("Enter a value for the operation.", "warning");
        window.showLoader("Renaming files...");
        const count = await eel.execute_rename(currentRenameCategory, op, arg1, arg2)();
        window.hideLoader();
        if (count > 0) {
            showToast(`Renamed ${count} file(s) successfully.`, "success");
            _clearRenamePreview();
        } else {
            showToast("No files were renamed.", "info");
        }
    });
}

function unlockAdminUI() {
    document.getElementById("rename-auth-section").style.display = "none";
    document.getElementById("rename-workspace-section").style.display = "block";
    const catAuthMsg = document.getElementById("categories-auth-msg");
    if (catAuthMsg) catAuthMsg.style.display = "none";
    const catAuthSection = document.getElementById("categories-auth-section");
    if (catAuthSection) catAuthSection.style.display = "none";
    const catContent = document.getElementById("categories-content-section");
    if (catContent) catContent.style.display = "block";
    populateRenameCategories();
}

async function populateRenameCategories() {
    const cats = await eel.get_rename_categories()();
    const sel = document.getElementById("rename-category-select");
    sel.innerHTML = '<option value="">-- Select Category --</option>';
    cats.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = c.name + " (" + c.count + " files)";
        sel.appendChild(opt);
    });
}

function _clearRenamePreview() {
    document.getElementById("rename-preview-body").innerHTML = '';
}

async function initApplicationContextData() {
    if (typeof eel === "undefined") return;
    const metadata = await eel.get_system_metadata()();
    document.getElementById("current-path-display").innerText = metadata.folder || "No working folder selected.";

    comparisonFolders = (metadata.comparison_folders || []).map(p => ({path: p, label: p.split(/[\\/]/).pop() || p}));
    _renderComparisonBar();

    if (!metadata.has_pin) {
        document.getElementById("rename-auth-msg").innerText = "Admin PIN not configured. Add \"admin_pin\" to config.json.";
        document.getElementById("submit-pin-btn").disabled = true;
        const catAuthMsg = document.getElementById("categories-auth-msg");
        if (catAuthMsg) catAuthMsg.innerText = "Admin PIN not configured. Add \"admin_pin\" to config.json.";
        const catSubBtn = document.getElementById("cat-submit-pin-btn");
        if (catSubBtn) catSubBtn.disabled = true;
    }

    if (metadata.admin_mode) unlockAdminUI();

    if (metadata.folder) {
        window.showLoader("Scanning workspace, please wait...");
        await refreshDashboardTelemetryMetrics();
        window.hideLoader();
    }
}

function initCategoryHandlers() {
    const catPinBtn = document.getElementById("cat-submit-pin-btn");
    if (catPinBtn) {
        catPinBtn.addEventListener("click", async () => {
            const val = document.getElementById("cat-admin-pin-input").value;
            if (!val) return;
            const res = await eel.verify_admin_pin(val)();
            if (res.status === "success") {
                showToast("Admin access granted.", "success");
                unlockAdminUI();
            } else {
                showToast(res.message || "Incorrect PIN.", "error");
            }
        });
    }
}

async function triggerRuleLivePreviews() {
    if (typeof eel === "undefined") return;
    const sizeInput = document.getElementById("size-input-value");
    const ageInput = document.getElementById("age-input-value");
    if (!sizeInput || !ageInput) return;
    const sizeVal = sizeInput.value;
    const ageVal = ageInput.value;
    const sizeRes = await eel.get_rule_preview_metrics("size", sizeVal)();
    document.getElementById("size-preview-tally").innerText = sizeRes.count + " files currently match (" + sizeRes.size_str + ")";
    const ageRes = await eel.get_rule_preview_metrics("age", ageVal)();
    document.getElementById("age-preview-tally").innerText = ageRes.count + " files currently match (" + ageRes.size_str + ")";
}

// ---------------------------------------------------------------------------
// MAIN DASHBOARD REFRESH — now uses get_dashboard_batch() (1 call vs 6+)
// ---------------------------------------------------------------------------
async function refreshDashboardTelemetryMetrics() {
    if (typeof eel === "undefined") return;

    // --- BATCH TELEMETRY (Phase 2) ---
    // Single round-trip replaces: execute_storage_telemetry, get_duplicate_count,
    // get_categories_data, get_mismatched_data, get_organize_view_data,
    // get_history_and_trash_logs (6 sequential calls → 1).
    // Optionally includes rule previews if input values are available.
    const sizeInput = document.getElementById("size-input-value");
    const ageInput = document.getElementById("age-input-value");
    const sizeVal = (sizeInput && sizeInput.value) || null;
    const ageVal = (ageInput && ageInput.value) || null;

    const data = await eel.get_dashboard_batch(sizeVal, ageVal)();
    if (data.error) return;

    // 1. Storage telemetry metrics
    const storage = data.storage;
    document.getElementById("count-total-files").innerText = (storage.total_files || 0).toLocaleString();
    document.getElementById("count-trash-items").innerText = (storage.trash_count || 0).toLocaleString();
    document.getElementById("total-storage-tally").innerText = storage.total_size_str || "0 B";

    // 2. Duplicate count
    document.getElementById("count-dup-sets").innerText = (data.duplicate_count || 0).toLocaleString();

    // 3. Donut chart + legend
    const chartRing = document.getElementById("donut-render-target");
    const legendList = document.getElementById("legend-render-target");
    if (chartRing && legendList && storage.categories && storage.categories.length > 0) {
        legendList.innerHTML = "";
        let cumulativePct = 0;
        let gradients = [];
        const palette = ['#3B82F6', '#7A5AF8', '#12B76A', '#F79009', '#F04438', '#98A2B3'];

        storage.categories.forEach((cat, idx) => {
            const nextPct = cumulativePct + cat.percentage;
            const color = palette[idx % palette.length];
            gradients.push(color + " " + cumulativePct + "% " + nextPct + "%");
            const li = document.createElement("li");
            li.innerHTML = `<span class="dot" style="background:${color}"></span>${_esc(cat.name)}<span class="pct">${cat.percentage}% · ${cat.size_str}</span>`;
            legendList.appendChild(li);
            cumulativePct = nextPct;
        });
        chartRing.style.background = "conic-gradient(" + gradients.join(',') + ")";
    }

    // 4. Categories grid
    const catData = data.categories;
    const grid = document.getElementById("categories-grid");
    if (grid) {
        grid.innerHTML = "";
        grid.style.display = "grid";
        grid.style.gridTemplateColumns = "repeat(auto-fill, minmax(280px, 1fr))";
        grid.style.gap = "16px";

        catData.forEach(c => {
            const card = document.createElement("div");
            card.className = "ui-card cat-grid-card";
            let chipsHtml = c.extensions.map(ext => `<span class="ext-tag">${ext}</span>`).join('');
            let badge = c.is_custom ? '<span class="custom-badge">Custom</span>' : '';
            card.innerHTML = `
                <div class="cat-header">
                    <div class="cat-name">${_esc(c.name)}${badge}</div>
                    <div class="cat-actions">
                        <button class="ui-btn secondary" onclick="editCategory('${_attrEsc(c.name)}', '${_attrEsc(c.extensions.join(', '))}')">Edit</button>
                        ${c.is_custom ? `<button class="ui-btn danger" onclick="deleteCategory('${_attrEsc(c.name)}')">Del</button>` : ''}
                    </div>
                </div>
                <div>${chipsHtml}</div>
            `;
            grid.appendChild(card);
        });
    }

    // 5. Mismatched files
    const mismatches = data.mismatches;
    const misCard = document.getElementById("mismatch-warning-card");
    if (misCard) {
        if (mismatches && mismatches.length > 0) {
            misCard.style.display = "block";
            document.getElementById("mismatch-text").innerText = mismatches.length + " file(s) are sitting inside category folders they don't belong to.";
            window.currentMismatches = mismatches;
        } else {
            misCard.style.display = "none";
            window.currentMismatches = [];
        }
    }

    // 6. Organize view
    organizeFolderData = data.organize_view;
    const checklistContainer = document.getElementById("organize-checklist-container");
    if (checklistContainer) {
        checklistContainer.innerHTML = "";
        currentCategoriesMap = [];
        document.getElementById("org-select-all").checked = true;

        const catMap = (data.organize_view && data.organize_view.categories) || {};
        const allCats = Object.keys(catMap);

        if (allCats.length === 0) {
            checklistContainer.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">No loose files to sort currently.</p>';
        } else {
            allCats.forEach((cat, index) => {
                currentCategoriesMap.push(cat);
                const totalCount = Object.values(catMap[cat]).reduce((a, b) => a + b, 0);
                const row = document.createElement("div");
                row.className = "org-check-row";
                row.innerHTML = `
                    <label class="check-label">
                        <input type="checkbox" id="cat-checkbox-${index}" class="org-cat-checkbox" checked>
                        <span>${_esc(cat)} <b style="color:var(--text-secondary); font-weight:500;">(${totalCount} files)</b></span>
                    </label>
                `;
                checklistContainer.appendChild(row);
            });
        }
    }

    // 7. Rule previews (from batch — no extra round-trips)
    if (data.rule_previews) {
        if (data.rule_previews.size) {
            const sp = data.rule_previews.size;
            document.getElementById("size-preview-tally").innerText = sp.count + " files currently match (" + sp.size_str + ")";
        }
        if (data.rule_previews.age) {
            const ap = data.rule_previews.age;
            document.getElementById("age-preview-tally").innerText = ap.count + " files currently match (" + ap.size_str + ")";
        }
    }

    // --- DUPLICATES (PAGINATED + LAZY THUMBNAILS) ---
    const activePanel = document.querySelector(".view-panel.active-view");
    if (activePanel && activePanel.id === "duplicates-panel") {

        const thresholdVal = document.getElementById("similarity-select").value;
        const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage)();
        const isCached = dupResponse.from_cache === true;

        if (dupResponse.needs_scan === true && activeScanType === "similar") {
            dupCurrentPage = 0;
            await eel.start_similar_scan(thresholdVal)();
            const dupContainer = document.getElementById("duplicates-render-container");
            if (dupContainer) dupContainer.innerHTML = "";
            return;
        }

        if (activeScanType === "similar" && dupResponse.total_groups === 0 && dupResponse.needs_scan !== true) {
            const scanStatus = await eel.get_similar_scan_status()();
            if (scanStatus.scanning) {
                const progressEl = document.getElementById("similar-scan-progress");
                if (progressEl) progressEl.style.display = "block";
                const dupContainer = document.getElementById("duplicates-render-container");
                if (dupContainer) dupContainer.innerHTML = "";
                return;
            }
        }

        if (!isCached && activeScanType === "exact") {
            window.showLoader("Scanning exact duplicates...");
        }

        const dupGroups = dupResponse.displayed_groups || [];
        window.currentDuplicateGroups = dupGroups;
        dupTotalGroups = dupResponse.total_groups || 0;
        dupTotalPages = dupResponse.total_pages || 1;
        dupCurrentPage = dupResponse.page || 0;

        const dupSelectAllBtn = document.getElementById("dup-select-all");
        if (dupSelectAllBtn) dupSelectAllBtn.checked = false;

        const paginationBar = document.getElementById("dup-pagination-bar");
        if (paginationBar) {
            if (dupTotalPages > 1) {
                paginationBar.style.display = "flex";
                const startItem = dupCurrentPage * 25 + 1;
                const endItem = Math.min((dupCurrentPage + 1) * 25, dupTotalGroups);
                document.getElementById("dup-page-info").innerText =
                    "Showing " + startItem + "\u2013" + endItem + " of " + dupTotalGroups.toLocaleString() + " sets";
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
                dupContainer.innerHTML += `<div class="banner-error"><b>Similar-image scan unavailable:</b> ${_esc(dupResponse.error)}</div>`;
                window.hideLoader();
                return;
            }

            if (dupTotalGroups > dupGroups.length) {
                const showingCount = (dupCurrentPage + 1) * 25;
                const shown = Math.min(showingCount, dupTotalGroups);
                dupContainer.innerHTML += `<div class="banner-warning"><b>High Volume:</b> ${dupTotalGroups.toLocaleString()} duplicate sets found. Showing ${shown} of ${dupTotalGroups.toLocaleString()}. Use pagination to browse all sets.</div>`;
            }

            if (dupResponse.unreadable_count > 0) {
                dupContainer.innerHTML += `<div class="banner-warning banner-sm">Note: ${dupResponse.unreadable_count} image(s) could not be read (corrupt, locked, or an unsupported format like HEIC without the pillow-heif plugin) and were skipped.</div>`;
            }

            if (dupGroups.length === 0) {
                dupContainer.innerHTML += '<p style="color:var(--text-secondary); font-size:13.5px;">No duplicate elements detected.</p>';
            } else {
                dupGroups.forEach((group, gIdx) => {
                    const groupWrapper = document.createElement("div");
                    groupWrapper.className = "dup-group-card";

                    let itemsListHtml = "";
                    group.files.forEach((file, fIdx) => {
                        const autoChecked = (activeScanType === "exact" && fIdx > 0) ? "checked" : "";

                        let mediaThumbnailHtml = `
                            <div class="thumb-placeholder" data-gidx="${gIdx}" data-fidx="${fIdx}" data-gid="${group.id}" title="Loading...">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                            </div>`;

                        if (file.thumb_b64) {
                            mediaThumbnailHtml = `<img class="thumb-img" src="${file.thumb_b64}" onclick="openImagePreview(${gIdx}, ${fIdx})" title="Click for full preview" />`;
                        }

                        itemsListHtml += `
                            <div class="dup-row">
                                <input type="checkbox" class="dup-file-purge-checkbox" data-gidx="${gIdx}" data-fidx="${fIdx}" ${autoChecked}>
                                ${mediaThumbnailHtml}
                                <div class="dup-info">
                                    <b>${_esc(file.name)}</b>
                                    <span>${_esc(file.path)}</span>
                                </div>
                            </div>
                        `;
                    });

                    groupWrapper.innerHTML = `
                        <div class="dup-group-label">Set Match Collection — ${group.size_str} copies each</div>
                        <div style="display:flex; flex-direction:column;">${itemsListHtml}</div>
                    `;
                    dupContainer.appendChild(groupWrapper);
                });

                _loadVisibleThumbnails(activeScanType);
            }
        }
        window.hideLoader();
    }

    // --- HISTORY AND RECOVERY BIN (from batch data) ---
    window.currentTrashItems = data.trash || [];

    const historyBody = document.getElementById("history-table-body");
    if (historyBody) {
        historyBody.innerHTML = "";
        const history = data.history || [];
        if (history.length === 0) {
            historyBody.innerHTML = '<tr><td colspan="3" class="text-center-cell">No historical action logs found.</td></tr>';
        } else {
            history.forEach(run => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td class="tbl-cell"><b>${_esc(run.label)}</b></td><td class="tbl-cell">Moved <b>${run.count}</b> files/folders</td><td class="tbl-cell"><button class="ui-btn secondary" onclick="triggerUndoSequence('${_attrEsc(run.path)}', '${_attrEsc(run.label)}', ${run.count})" style="padding:4px 10px; font-size:11.5px;">Undo Action</button></td>`;
                historyBody.appendChild(tr);
            });
        }
    }

    const trashBody = document.getElementById("trash-table-body");
    if (trashBody) {
        trashBody.innerHTML = "";
        if (window.currentTrashItems.length === 0) {
            trashBody.innerHTML = '<tr><td colspan="4" class="text-center-cell">Recycle bin empty.</td></tr>';
        } else {
            window.currentTrashItems.forEach((item, idx) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td class="tbl-cell"><input type="checkbox" class="bin-item-checkbox" data-index="${idx}"></td>
                    <td class="tbl-cell-mono">${_esc(item.name)}</td>
                    <td class="tbl-cell"><b>${item.size}</b></td>
                    <td class="tbl-cell-sm" style="color:var(--text-secondary);">${_esc(item.batch)}</td>
                `;
                trashBody.appendChild(tr);
            });
        }
    }
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
                <label class="check-label" style="margin-bottom:8px;">
                    <input type="checkbox" class="mismatch-cat-checkbox" value="${cat}" checked>
                    <span>${cat} <b style="color:var(--text-secondary); font-weight:500;">(${count} files)</b></span>
                </label>
            `;
        });
        document.getElementById("mismatch-modal").style.display = "flex";
    });

    document.getElementById("execute-mismatch-btn").addEventListener("click", async () => {
        const selected = Array.from(document.querySelectorAll(".mismatch-cat-checkbox:checked")).map(cb => cb.value);
        if (selected.length === 0) return showToast("Select at least one category to fix.", "warning");
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
        document.getElementById("vacuum-count-label").innerText = emptyFolders.length + " folder(s) found";
        document.getElementById("vacuum-select-all").checked = true;

        emptyFolders.forEach((f) => {
            container.innerHTML += `
                <label class="vacuum-label">
                    <input type="checkbox" class="vacuum-folder-checkbox" value="${f.path.replace(/\\/g, '\\\\')}" checked>
                    <span style="word-break: break-all;">
                        <b style="color:var(--text-primary); display:block; font-size:13px;">${_esc(f.name)}</b>
                        <span style="color:var(--text-secondary); font-size:11px; font-family:monospace;">${f.rel_path}</span>
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
        if (selected.length === 0) return showToast("Select at least one empty folder to clean.", "warning");

        const permanentDelete = document.getElementById("vacuum-permanent-delete").checked;

        if (permanentDelete) {
            const res = await eel.delete_empty_folders_permanently(selected)();
            if (res.status === "success") {
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
            if (res.status === "success") {
                document.getElementById("vacuum-modal").style.display = "none";
                await refreshDashboardTelemetryMetrics();
                showToast(`Workspace Vacuum complete! Cleaned up and moved ${res.purged} empty folder(s) to the Recycle Bin safely.`, "success");
            } else {
                showToast(res.message || "Error cleaning folders.", "error");
            }
        }

        document.getElementById("vacuum-permanent-delete").checked = false;
    });

    document.getElementById("change-workspace-btn").addEventListener("click", async () => {
        const res = await eel.select_folder_native()();
        if (res.status === "success") {
            document.getElementById("current-path-display").innerText = res.path;
            window.showLoader("Scanning new workspace, please wait...");
            await refreshDashboardTelemetryMetrics();
            window.hideLoader();
            if (document.getElementById("rename-workspace-section").style.display === "block") populateRenameCategories();
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
        if (comparisonFolders.length > 0 && organizeFolderData && organizeFolderData.folders.length > 1) {
            _showOrganizeDestModal(targets);
        } else {
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
        const catMap = organizeFolderData.categories;
        const folderCatsMap = {};

        folders.forEach(f => {
            const escapedPath = f.path.replace(/'/g, "\\'").replace(/"/g, '&quot;');
            const selected = [];
            document.querySelectorAll(`.sep-cat-cb[data-folder="${escapedPath}"]:checked`).forEach(cb => {
                selected.push(cb.value);
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
            if (fIdx > 0) cb.checked = isChecked;
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
        const proceed = await _showPurgeConfirmModal(targets.length);
        if (!proceed) return;
        const res = await eel.purge_selected_duplicates(targets)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            showToast(`Moved ${res.purged} duplicate file(s) to the Recycle Bin.`, "success");
        } else {
            showToast(res.message, "error");
        }
    });

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

    document.getElementById("empty-bin-btn").addEventListener("click", () => {
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

// ---------------------------------------------------------------------------
// Image Preview
// ---------------------------------------------------------------------------
window.openImagePreview = async function(gIdx, fIdx) {
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
    if (b64Data) {
        loader.style.display = "none";
        imgEl.src = b64Data;
        imgEl.style.display = "block";
        infoEl.innerText = targetFile.name + "   —   " + targetFile.path;
    } else {
        loader.innerText = "Error loading high resolution image data.";
    }
};

document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("image-preview-modal");
    if (modal.style.display === "flex") {
        const groupFiles = window.currentDuplicateGroups[window.currentPreviewGidx].files;
        if (e.key === "ArrowRight") {
            let next = (window.currentPreviewFidx + 1) % groupFiles.length;
            window.openImagePreview(window.currentPreviewGidx, next);
        } else if (e.key === "ArrowLeft") {
            let prev = (window.currentPreviewFidx - 1 + groupFiles.length) % groupFiles.length;
            window.openImagePreview(window.currentPreviewGidx, prev);
        } else if (e.key === "Escape") {
            modal.style.display = "none";
        }
    }

    const orgModal = document.getElementById("organize-dest-modal");
    if (orgModal && orgModal.style.display === "flex" && e.key === "Escape") orgModal.style.display = "none";

    const undoModal = document.getElementById("undo-confirm-modal");
    if (undoModal && undoModal.style.display === "flex" && e.key === "Escape") {
        undoModal.style.display = "none";
        _pendingUndoLogPath = null;
    }

    const orgPreviewModal = document.getElementById("organize-preview-modal");
    if (orgPreviewModal && orgPreviewModal.style.display === "flex" && e.key === "Escape") orgPreviewModal.style.display = "none";
});

// ---------------------------------------------------------------------------
// Generic Simple Confirm Modal (replaces all confirm() calls)
// ---------------------------------------------------------------------------
function _showSimpleConfirmModal(title, message, accentColor) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.className = "modal-overlay";
        const color = accentColor || "#2563EB";
        overlay.innerHTML = `
            <div class="modal-card modal-card-sm">
                <div class="modal-header">
                    <svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
                    <h3>${title}</h3>
                </div>
                <div class="modal-body"><p>${message}</p></div>
                <div class="modal-footer">
                    <button class="sc-cancel-btn ui-btn secondary">Cancel</button>
                    <button class="sc-confirm-btn ui-btn primary" style="background:${color}; border-color:${color}; color:#fff;">Confirm</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector(".sc-cancel-btn").onclick = () => { overlay.remove(); resolve(false); };
        overlay.querySelector(".sc-confirm-btn").onclick = () => { overlay.remove(); resolve(true); };
        overlay.addEventListener("click", (e) => { if (e.target === overlay) { overlay.remove(); resolve(false); } });
    });
}

// ---------------------------------------------------------------------------
// Purge Duplicates Confirmation Modal
// ---------------------------------------------------------------------------
function _showPurgeConfirmModal(count) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.className = "modal-overlay";
        overlay.innerHTML = `
            <div class="modal-card">
                <div class="modal-header">
                    <svg viewBox="0 0 24 24" fill="none" stroke="#D97706" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    <h3>Purge Duplicate Files</h3>
                </div>
                <div class="modal-body">
                    <div class="purge-warning-box">Move <b>${count}</b> selected file(s) to the Recycle Bin? You can restore them later from the History tab.</div>
                </div>
                <div class="modal-footer">
                    <button id="purge-modal-cancel" class="ui-btn secondary">Cancel</button>
                    <button id="purge-modal-confirm" class="ui-btn primary" style="background:#D97706; border-color:#D97706; color:#fff;">Yes, Move to Recycle Bin</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        document.getElementById("purge-modal-cancel").onclick = () => { overlay.remove(); resolve(false); };
        document.getElementById("purge-modal-confirm").onclick = () => { overlay.remove(); resolve(true); };
        overlay.addEventListener("click", (e) => { if (e.target === overlay) { overlay.remove(); resolve(false); } });
    });
}

// ---------------------------------------------------------------------------
// Undo Confirmation Modal
// ---------------------------------------------------------------------------
function _showUndoConfirmModal(logPath, label, fileCount) {
    _pendingUndoLogPath = logPath;
    const modal = document.getElementById("undo-confirm-modal");
    document.getElementById("undo-log-label").innerText = label;
    document.getElementById("undo-file-count").innerText = " \u2014 " + fileCount + " file(s) will be restored";

    const tbody = document.getElementById("undo-preview-tbody");
    tbody.innerHTML = '<tr><td colspan="3" class="text-center-cell-loading">Loading file details...</td></tr>';
    modal.style.display = "flex";

    eel.get_undo_log_details(logPath)().then(res => {
        if (res.status === "success" && res.entries.length > 0) {
            const limit = 100;
            const entries = res.entries.slice(0, limit);
            tbody.innerHTML = "";
            entries.forEach(e => {
                const tr = document.createElement("tr");
                tr.className = "undo-preview-row";
                tr.innerHTML = `
                    <td class="undo-cell" title="${_esc(e.file_name)}">${_esc(e.file_name)}</td>
                    <td class="undo-cell-secondary" title="${_esc(e.destination)}">${_shortPath(e.destination, 40)}</td>
                    <td class="undo-cell-green" title="${_esc(e.source)}">${_shortPath(e.source, 40)}</td>
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
            tbody.innerHTML = '<tr><td colspan="3" class="text-center-cell-loading">No file details available.</td></tr>';
        }
    }).catch(() => {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center-cell" style="color:#DC2626;">Failed to load file details.</td></tr>';
    });
}

let _pendingUndoLogPath = null;

function _shortPath(p, maxLen) {
    if (p.length <= maxLen) return p;
    const parts = p.replace(/\\/g, "/").split("/");
    if (parts.length <= 2) return p;
    return ".../" + parts.slice(-2).join("/");
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
// Comparison Bar
// ---------------------------------------------------------------------------
function _renderComparisonBar() {
    const compBar = document.getElementById("comparison-bar");
    const chipsContainer = document.getElementById("comparison-folder-chips");
    if (!compBar || !chipsContainer) return;

    if (comparisonFolders.length === 0) { compBar.style.display = "none"; return; }

    compBar.style.display = "block";
    chipsContainer.innerHTML = "";

    comparisonFolders.forEach((f, idx) => {
        const shortLabel = f.label.length > 45 ? f.label.substring(0, 42) + "..." : f.label;
        const chip = document.createElement("div");
        chip.className = "comparison-chip";
        chip.innerHTML = `
            <span title="${f.path}">${shortLabel}</span>
            <button class="comparison-chip-remove" data-idx="${idx}" title="Remove this folder">&times;</button>
        `;
        chipsContainer.appendChild(chip);
    });

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

    optionsDiv.innerHTML = "";
    folders.forEach(f => {
        const shortLabel = f.label.length > 40 ? f.label.substring(0, 37) + "..." : f.label;
        const btn = document.createElement("button");
        btn.className = "org-dest-option-btn";
        btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:18px; height:18px; flex-shrink:0;"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
            <div>
                <div style="font-weight:600; font-size:13px;">Organize into ${shortLabel}</div>
                <div style="font-size:11px; color:var(--text-secondary);">All files from all folders merged into this location</div>
            </div>
        `;
        btn.addEventListener("click", () => {
            document.getElementById("organize-dest-modal").style.display = "none";
            _executeDirectOrganize(selectedCategories, f.path);
        });
        optionsDiv.appendChild(btn);
    });

    separateBody.innerHTML = "";
    const catMap = organizeFolderData.categories;
    folders.forEach(f => {
        const escapedPath = f.path.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const folderBlock = document.createElement("div");
        folderBlock.style.marginBottom = "14px";
        folderBlock.innerHTML = `
            <div class="org-dest-section-label">${f.label}</div>
            <div style="display:flex; flex-wrap:wrap; gap:6px;"></div>
        `;
        const catContainer = folderBlock.querySelector("div:last-child");
        Object.keys(catMap).forEach(cat => {
            const count = catMap[cat][f.path] || 0;
            const label = document.createElement("label");
            label.className = "check-label check-label-sm";
            label.innerHTML = `<input type="checkbox" class="sep-cat-cb" data-folder="${escapedPath}" value="${cat}" checked> ${cat} (${count})`;
            catContainer.appendChild(label);
        });
        separateBody.appendChild(folderBlock);
    });

    if (folders.length > 1) { separateSection.style.display = "block"; } else { separateSection.style.display = "none"; }
    cancelRow.style.display = "flex";
    cancelRow.style.justifyContent = "flex-end";
    const modalEl = document.getElementById("organize-dest-modal");
    modalEl.onclick = function(e) { if (e.target === modalEl) modalEl.style.display = "none"; };
    modalEl.style.display = "flex";
}

// ---------------------------------------------------------------------------
// Organize Preview Modal
// ---------------------------------------------------------------------------
let _pendingOrgCategories = null;
let _pendingOrgDestPath = null;

function _showOrganizePreviewModal(selectedCategories, destPath) {
    _pendingOrgCategories = selectedCategories;
    _pendingOrgDestPath = destPath;
    const modal = document.getElementById("organize-preview-modal");
    const tbody = document.getElementById("org-preview-tbody");
    const summaryEl = document.getElementById("org-preview-summary");

    summaryEl.innerText = "Loading preview...";
    tbody.innerHTML = '<tr><td colspan="4" class="text-center-cell-loading">Scanning files for preview...</td></tr>';
    modal.style.display = "flex";

    eel.get_organize_preview(selectedCategories)().then(res => {
        if (res.status === "success") {
            const catCounts = {};
            res.entries.forEach(e => { catCounts[e.category] = (catCounts[e.category] || 0) + 1; });
            const catParts = Object.entries(catCounts).map(([c, n]) => n + " " + c).join(", ");
            summaryEl.innerText = res.total + " file(s) will be moved: " + catParts;

            const limit = 150;
            const entries = res.entries.slice(0, limit);
            tbody.innerHTML = "";
            entries.forEach(e => {
                const tr = document.createElement("tr");
                tr.className = "undo-preview-row";
                tr.innerHTML = `
                    <td class="undo-cell" title="${_esc(e.file_name)}">${_esc(e.file_name)}</td>
                    <td class="undo-cell-secondary" title="${_esc(e.source_folder)}">${_shortPath(e.source_folder, 35)}</td>
                    <td class="tbl-cell-sm"><span class="org-preview-tag">${_esc(e.category)}</span></td>
                    <td class="tbl-cell-sm" style="text-align:right; color:var(--text-secondary);">${e.file_size}</td>
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
            tbody.innerHTML = '<tr><td colspan="4" class="text-center-cell" style="color:#DC2626;">Failed to load preview.</td></tr>';
        }
    }).catch(() => {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center-cell" style="color:#DC2626;">Failed to load preview.</td></tr>';
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
    _showOrganizePreviewModal(selectedCategories, destPath);
}

// ---------------------------------------------------------------------------
// Lazy Thumbnail Loading
// ---------------------------------------------------------------------------
window._loadVisibleThumbnails = async function(scanType) {
    const placeholders = document.querySelectorAll(".thumb-placeholder");
    const globalIds = new Set();
    placeholders.forEach(el => {
        const gid = el.getAttribute("data-gid");
        if (gid !== null) globalIds.add(parseInt(gid, 10));
    });

    const gidList = Array.from(globalIds);
    if (gidList.length === 0) return;

    try {
        const thumbsMap = await eel.get_thumbnails_for_page(gidList, scanType)();
        for (const gid of gidList) {
            const thumbs = thumbsMap[gid];
            if (!thumbs) continue;
            document.querySelectorAll(`.thumb-placeholder[data-gid="${gid}"]`).forEach(el => {
                const gIdx = parseInt(el.getAttribute("data-gidx"), 10);
                const fIdx = parseInt(el.getAttribute("data-fidx"), 10);
                const match = thumbs.find(t => t.path === window.currentDuplicateGroups[gIdx].files[fIdx].path);
                if (match && match.thumb_b64) {
                    const img = document.createElement("img");
                    img.className = "thumb-img";
                    img.src = match.thumb_b64;
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
    if (isNaN(target) || target < 1 || target > dupTotalPages) { input.value = ""; return; }
    dupCurrentPage = target - 1;
    input.value = "";
    window.showLoader(`Loading page ${dupCurrentPage + 1}...`);
    await _loadDuplicatePage();
    window.hideLoader();
});

document.getElementById("dup-jump-input").addEventListener("keydown", async (e) => {
    if (e.key === "Enter") document.getElementById("dup-jump-btn").click();
});

window._loadDuplicatePage = async function() {
    const thresholdVal = document.getElementById("similarity-select").value;
    const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage)();

    const dupGroups = dupResponse.displayed_groups || [];
    window.currentDuplicateGroups = dupGroups;
    dupTotalGroups = dupResponse.total_groups || 0;
    dupTotalPages = dupResponse.total_pages || 1;
    dupCurrentPage = dupResponse.page || 0;

    const dupSelectAllBtn = document.getElementById("dup-select-all");
    if (dupSelectAllBtn) dupSelectAllBtn.checked = false;

    const paginationBar = document.getElementById("dup-pagination-bar");
    if (paginationBar) {
        if (dupTotalPages > 1) {
            paginationBar.style.display = "flex";
            const startItem = dupCurrentPage * 25 + 1;
            const endItem = Math.min((dupCurrentPage + 1) * 25, dupTotalGroups);
            document.getElementById("dup-page-info").innerText =
                "Showing " + startItem + "\u2013" + endItem + " of " + dupTotalGroups.toLocaleString() + " sets";
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
        groupWrapper.className = "dup-group-card";

        let itemsListHtml = "";
        group.files.forEach((file, fIdx) => {
            const autoChecked = (activeScanType === "exact" && fIdx > 0) ? "checked" : "";
            let mediaThumbnailHtml = `
                <div class="thumb-placeholder" data-gidx="${gIdx}" data-fidx="${fIdx}" data-gid="${group.id}" title="Loading...">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                </div>`;
            if (file.thumb_b64) {
                mediaThumbnailHtml = `<img class="thumb-img" src="${file.thumb_b64}" onclick="openImagePreview(${gIdx}, ${fIdx})" title="Click for full preview" />`;
            }
            itemsListHtml += `
                <div class="dup-row">
                    <input type="checkbox" class="dup-file-purge-checkbox" data-gidx="${gIdx}" data-fidx="${fIdx}" ${autoChecked}>
                    ${mediaThumbnailHtml}
                    <div class="dup-info">
                        <b>${_esc(file.name)}</b>
                        <span>${_esc(file.path)}</span>
                    </div>
                </div>
            `;
        });
        groupWrapper.innerHTML = `
            <div class="dup-group-label">Set Match Collection — ${group.size_str} copies each</div>
            <div style="display:flex; flex-direction:column;">${itemsListHtml}</div>
        `;
        dupContainer.appendChild(groupWrapper);
    });

    _loadVisibleThumbnails(activeScanType);
};

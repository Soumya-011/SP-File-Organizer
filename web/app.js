/**
 * Asynchronous front-end application controller core module.
 * Bridges active dynamic user interactions directly over backend logic routines.
 */

let currentCategoriesMap = [];
let activeScanType = "exact";
let currentRenameCategory = null; 

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

// Global Loader Wrappers
window.showLoader = function(msg = "Processing...") {
    document.getElementById("loader-text").innerText = msg;
    document.getElementById("global-loader").style.display = "flex";
};

window.hideLoader = function() {
    document.getElementById("global-loader").style.display = "none";
};

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

    document.getElementById("similarity-select").addEventListener("change", () => {
        dupCurrentPage = 0;
        refreshDashboardTelemetryMetrics();
    });
}

function initAdminAndRenameHandlers() {
    const handleAuth = async (inputId) => {
        const val = document.getElementById(inputId).value;
        const res = await eel.verify_admin_pin(val)();
        if (res.status === "success") {
            unlockAdminUI();
        } else {
            alert(res.message);
        }
    };

    document.getElementById("submit-pin-btn").addEventListener("click", () => handleAuth("admin-pin-input"));
    
    const catBtn = document.getElementById("cat-submit-pin-btn");
    if(catBtn) catBtn.addEventListener("click", () => handleAuth("cat-admin-pin-input"));

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
        document.getElementById("apply-rename-btn").disabled = true;
    });

    document.getElementById("preview-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        if (!arg1 && op !== "replace") return alert("Please enter text argument");
        if (!currentRenameCategory) return alert("No category selected");
        
        const changed = await eel.preview_rename(currentRenameCategory, op, arg1, arg2)();

        const list = document.getElementById("rename-preview-list");
        list.innerHTML = "";
        if (changed.length === 0) {
            list.innerHTML = "<li>No files would be changed with these parameters.</li>";
            document.getElementById("apply-rename-btn").disabled = true;
        } else {
            changed.forEach(item => {
                list.innerHTML += `<li>${item.old} &nbsp;&rarr;&nbsp; <b style="color:var(--text-primary)">${item.new}</b></li>`;
            });
            if (changed.length === 50) {
                list.innerHTML += `<li style="color: var(--status-alert); margin-top: 8px;">...preview limited to 50 items to optimize performance.</li>`;
            }
            document.getElementById("apply-rename-btn").disabled = false;
        }
    });

    document.getElementById("apply-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        const count = await eel.execute_rename(currentRenameCategory, op, arg1, arg2)();
        
        document.getElementById("rename-preview-list").innerHTML = "<li>Select parameters and click preview...</li>";
        document.getElementById("apply-rename-btn").disabled = true;
        
        await populateRenameCategories();
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Successfully renamed ${count} files.`), 10);
    });
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
        if(!name || !exts) return alert("Category Name and Extensions are required fields.");
        
        const res = await eel.update_category(name, exts)();
        if(res.status === "success") {
            document.getElementById("category-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
        } else {
            alert(res.message);
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
    if(confirm(`Are you sure you want to remove custom config overrides for '${name}'?`)) {
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
    const data = await eel.execute_storage_telemetry()();
    if (data.error) return;

    document.getElementById("count-total-files").innerText = (data.total_files || 0).toLocaleString();
    document.getElementById("count-dup-sets").innerText = (data.duplicate_sets || 0).toLocaleString();
    document.getElementById("count-trash-items").innerText = (data.trash_count || 0).toLocaleString();
    document.getElementById("total-storage-tally").innerText = data.total_size_str || "0 B";

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
            li.innerHTML = `<span class="dot" style="background:${color}"></span>${cat.name}<span class="pct">${cat.percentage}% · ${cat.size_str}</span>`;
            legendList.appendChild(li);
            cumulativePct = nextPct;
        });
        chartRing.style.background = `conic-gradient(${gradients.join(',')})`;
    }

    const catData = await eel.get_categories_data()();
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
                    <div style="font-weight:600; font-size:14px;">${c.name}${badge}</div>
                    <div style="display:flex; gap:6px;">
                        <button class="ui-btn secondary" style="padding:4px 8px; font-size:11px;" onclick="editCategory('${c.name}', '${c.extensions.join(', ')}')">Edit</button>
                        ${c.is_custom ? `<button class="ui-btn danger" style="padding:4px 8px; font-size:11px;" onclick="deleteCategory('${c.name}')">Del</button>` : ''}
                    </div>
                </div>
                <div>${chipsHtml}</div>
            `;
            grid.appendChild(card);
        });
    }

    const mismatches = await eel.get_mismatched_data()();
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

    const organizeData = await eel.get_organize_view_data()();
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
    }
    triggerRuleLivePreviews();

    // --- DUPLICATES (PAGINATED + LAZY THUMBNAILS) ---
    // Only load duplicates if the user is actually looking at the Duplicate tab!
    const activePanel = document.querySelector(".view-panel.active-view");
    if (activePanel && activePanel.id === "duplicates-panel") {
        
        if (activeScanType === "similar") {
            window.showLoader("Analyzing images for visual matches. This may take a moment for large folders...");
        } else {
            window.showLoader("Scanning exact duplicates...");
        }

        const thresholdVal = document.getElementById("similarity-select").value;
        const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage)();
        
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
                                    <b style="font-size:13px; display:block; color:var(--text-primary); word-break:break-all;">${file.name}</b>
                                    <span style="font-size:11.5px; color:var(--text-secondary); word-break:break-all;">${file.path}</span>
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

    // --- HISTORY AND RECOVERY BIN ---
    const historyData = await eel.get_history_and_trash_logs()();
    window.currentTrashItems = historyData.trash;

    const historyBody = document.getElementById("history-table-body");
    if (historyBody) {
        historyBody.innerHTML = "";
        if (historyData.history.length === 0) {
            historyBody.innerHTML = '<tr><td colspan="3" style="color:var(--text-secondary); text-align:center; padding:12px;">No historical action logs found.</td></tr>';
        } else {
            historyData.history.forEach(run => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td style="padding:10px;"><b>${run.label}</b></td><td style="padding:10px;">Moved <b>${run.count}</b> files/folders</td><td style="padding:10px;"><button class="ui-btn secondary" onclick="triggerUndoSequence('${run.path.replace(/\\/g, '\\\\')}')" style="padding:4px 10px; font-size:11.5px;">Undo Action</button></td>`;
                historyBody.appendChild(tr);
            });
        }
    }

    const trashBody = document.getElementById("trash-table-body");
    if (trashBody) {
        trashBody.innerHTML = "";
        if (historyData.trash.length === 0) {
            trashBody.innerHTML = '<tr><td colspan="4" style="color:var(--text-secondary); text-align:center; padding:12px;">Recycle bin empty.</td></tr>';
        } else {
            historyData.trash.forEach((item, idx) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td style="padding:10px;"><input type="checkbox" class="bin-item-checkbox" data-index="${idx}"></td>
                    <td style="padding:10px; font-family:monospace; word-break:break-all;">${item.name}</td>
                    <td style="padding:10px;"><b>${item.size}</b></td>
                    <td style="padding:10px; color:var(--text-secondary); font-size:11.5px;">${item.batch}</td>
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
        if(selected.length === 0) return alert("Select at least one category to fix.");

        const count = await eel.fix_mismatched_files(selected)();
        document.getElementById("mismatch-modal").style.display = "none";
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Fixed ${count} misplaced files inside target directories.`), 10);
    });

    document.getElementById("vacuum-btn").addEventListener("click", async () => {
        const emptyFolders = await eel.get_empty_folders_data()();
        if (emptyFolders.length === 0) return alert("No empty folders found natively inside the current workspace.");
        
        const container = document.getElementById("vacuum-checklist");
        container.innerHTML = "";
        document.getElementById("vacuum-count-label").innerText = `${emptyFolders.length} folder(s) found`;
        document.getElementById("vacuum-select-all").checked = true;
        
        emptyFolders.forEach((f) => {
            container.innerHTML += `
                <label style="display:flex; align-items:center; gap:10px; font-size:13px; cursor:pointer; margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid var(--stroke-color);">
                    <input type="checkbox" class="vacuum-folder-checkbox" value="${f.path.replace(/\\/g, '\\\\')}" checked style="width:16px; height:16px; flex-shrink:0;">
                    <span style="word-break: break-all;">
                        <b style="color:var(--text-primary); display:block; font-size:13px;">${f.name}</b>
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
        if(selected.length === 0) return alert("Select at least one empty folder to clean.");

        const res = await eel.purge_selected_empty_folders(selected)();
        if(res.status === "success") {
            document.getElementById("vacuum-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Workspace Vacuum complete! Cleaned up and moved ${res.purged} empty folder(s) to the Recycle Bin safely.`), 10);
        } else {
            alert(res.message || "Error cleaning folders.");
        }
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
        if (targets.length === 0) return alert("Select at least one category checkbox.");

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
            alert(res.message);
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

        if (Object.keys(folderCatsMap).length === 0) return alert("Select at least one category for at least one folder.");

        if (!confirm(`Organize separately into each folder's own subfolders?`)) return;

        window.showLoader("Organizing separately...");
        const res = await eel.trigger_separate_organization(folderCatsMap)();
        window.hideLoader();
        if (res.status === "success") {
            document.getElementById("organize-dest-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Separate organization complete. Moved ${res.moved} items.`), 10);
        } else {
            alert(res.message);
        }
    });

    document.getElementById("execute-size-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("size-input-value").value;
        const timing = document.querySelector('input[name="size-timing"]:checked').value;
        
        const res = await eel.trigger_separation_organization("size", timing, val)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Size organization sequence resolved. Isolated ${res.moved} files.`), 10);
        } else {
            alert(res.message);
        }
    });

    document.getElementById("execute-age-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("age-input-value").value;
        const timing = document.querySelector('input[name="age-timing"]:checked').value;
        
        const res = await eel.trigger_separation_organization("age", timing, val)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Age organization sequence resolved. Isolated ${res.moved} files.`), 10);
        } else {
            alert(res.message);
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
        if (targets.length === 0) return alert("No items selected for cleanup.");
        if (confirm(`Move these ${targets.length} duplicate file(s) into the recovery bin?`)) {
            const res = await eel.purge_selected_duplicates(targets)();
            if (res.status === "success") {
                await refreshDashboardTelemetryMetrics();
                setTimeout(() => alert(`Asset cleanups processed successfully.`), 10);
            } else {
                alert(res.message);
            }
        }
    });

    document.getElementById("restore-bin-btn").addEventListener("click", async () => {
        let targets = [];
        document.querySelectorAll(".bin-item-checkbox").forEach(cb => {
            if (cb.checked) {
                const idx = cb.getAttribute("data-index");
                targets.push(window.currentTrashItems[idx].path);
            }
        });
        if (targets.length === 0) return alert("Select files using the checkboxes first.");
        
        const res = await eel.restore_from_bin(targets)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Successfully restored ${res.restored} item(s) back to original locations.`), 10);
        } else {
            alert(res.message);
        }
    });

    document.getElementById("empty-bin-btn").addEventListener("click", async () => {
        if (confirm("Attention! This action completely flushes your hidden recovery files permanently from disk memory. Proceed?")) {
            const res = await eel.empty_trash_completely()();
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Trash directory cleared completely. Purged ${res.flushed} system files permanently.`), 10);
        }
    });
}

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
    // Close image preview on Escape
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

    // Close organize destination modal on Escape
    const orgModal = document.getElementById("organize-dest-modal");
    if (orgModal && orgModal.style.display === "flex" && e.key === "Escape") {
        orgModal.style.display = "none";
    }
});

window.triggerUndoSequence = async function(logPathString) {
    if (confirm("Reverse adjustments logged inside this execution batch?")) {
        const res = await eel.execute_undo_operation(logPathString)();
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Filesystem adjustments reversed safely. Restored ${res.restored} items.`), 10);
    }
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
            <span title="${f.path}">${shortLabel}</span>
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

    // Build "Separate for each" section
    separateBody.innerHTML = "";
    const catMap = organizeFolderData.categories;
    folders.forEach(f => {
        const escapedPath = f.path.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const folderBlock = document.createElement("div");
        folderBlock.style.marginBottom = "14px";
        folderBlock.innerHTML = `
            <div style="font-size:13px; font-weight:600; margin-bottom:8px; color:var(--text-primary);">${f.label}</div>
            <div style="display:flex; flex-wrap:wrap; gap:6px;"></div>
        `;
        const catContainer = folderBlock.querySelector("div:last-child");
        Object.keys(catMap).forEach(cat => {
            const count = catMap[cat][f.path] || 0;
            const label = document.createElement("label");
            label.style.cssText = "display:flex; align-items:center; gap:5px; font-size:12px; cursor:pointer; padding:4px 8px; background:var(--space-bg); border:1px solid var(--stroke-color); border-radius:6px;";
            label.innerHTML = `<input type="checkbox" class="sep-cat-cb" data-folder="${escapedPath}" value="${cat}" checked style="width:13px; height:13px;"> ${cat} (${count})`;
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

async function _executeDirectOrganize(selectedCategories, destPath = null) {
    window.showLoader("Organizing files...");
    const res = await eel.trigger_bulk_organization(selectedCategories, destPath)();
    window.hideLoader();
    if (res.status === "success") {
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Execution complete. Reorganized ${res.moved} items into folders.`), 10);
    } else {
        alert(res.message);
    }
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

    for (const gid of globalIds) {
        try {
            const thumbs = await eel.get_thumbnails_for_group(gid, scanType)();
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
        } catch(e) {
            // Silently skip if backend is busy
        }
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
    const thresholdVal = document.getElementById("similarity-select").value;
    const dupResponse = await eel.get_duplicate_groups_data(activeScanType, thresholdVal, dupCurrentPage)();
    
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
                        <b style="font-size:13px; display:block; color:var(--text-primary); word-break:break-all;">${file.name}</b>
                        <span style="font-size:11.5px; color:var(--text-secondary); word-break:break-all;">${file.path}</span>
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
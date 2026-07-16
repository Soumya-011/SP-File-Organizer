/**
 * Asynchronous front-end application controller core module.
 * Bridges active dynamic user interactions directly over backend logic routines.
 */

let currentCategoriesMap = [];
let activeScanType = "exact";
let currentRenameFiles = [];

// Global State Caching for Clean Array Pathing & Modal Navigations
window.currentDuplicateGroups = [];
window.currentTrashItems = [];
window.currentMismatches = [];
window.currentPreviewGidx = 0;
window.currentPreviewFidx = 0;

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
            
            refreshDashboardTelemetryMetrics();
        });
    });

    document.getElementById("similarity-select").addEventListener("change", () => {
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
    
    // Bind the auth check to the new Categories panel input
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
        const cats = await eel.get_rename_categories()();
        const found = cats.find(c => c.name === e.target.value);
        currentRenameFiles = found ? found.files : [];
        document.getElementById("rename-preview-list").innerHTML = "<li>Files loaded. Select parameters and click preview...</li>";
        document.getElementById("apply-rename-btn").disabled = true;
    });

    document.getElementById("preview-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        if (!arg1 && op !== "replace") return alert("Please enter text argument");
        if (currentRenameFiles.length === 0) return alert("No files in category");

        window.showLoader("Generating text preview...");
        const changed = await eel.preview_rename(currentRenameFiles, op, arg1, arg2)();
        window.hideLoader();

        const list = document.getElementById("rename-preview-list");
        list.innerHTML = "";
        if (changed.length === 0) {
            list.innerHTML = "<li>No files would be changed with these parameters.</li>";
            document.getElementById("apply-rename-btn").disabled = true;
        } else {
            changed.forEach(item => {
                list.innerHTML += `<li>${item.old} &nbsp;&rarr;&nbsp; <b style="color:var(--text-primary)">${item.new}</b></li>`;
            });
            document.getElementById("apply-rename-btn").disabled = false;
        }
    });

    document.getElementById("apply-rename-btn").addEventListener("click", async () => {
        const op = document.getElementById("rename-op-select").value;
        const arg1 = document.getElementById("rename-arg1").value;
        const arg2 = document.getElementById("rename-arg2").value;
        
        window.showLoader("Applying bulk rename...");
        const count = await eel.execute_rename(currentRenameFiles, op, arg1, arg2)();
        
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
        currentRenameFiles = [];
    } else {
        cats.forEach((c, idx) => {
            const opt = document.createElement("option");
            opt.value = c.name;
            opt.innerText = `${c.name} (${c.files.length} files)`;
            sel.appendChild(opt);
            if (idx === 0) currentRenameFiles = c.files;
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
        
        window.showLoader("Saving mapping data...");
        const res = await eel.update_category(name, exts)();
        if(res.status === "success") {
            document.getElementById("category-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
        } else {
            window.hideLoader();
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
        window.showLoader("Removing custom configurations...");
        await eel.remove_category(name)();
        await refreshDashboardTelemetryMetrics();
    }
};

async function initApplicationContextData() {
    if (typeof eel === "undefined") return;
    const metadata = await eel.get_system_metadata()();
    document.getElementById("current-path-display").innerText = metadata.folder || "No working folder selected.";
    
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
        refreshDashboardTelemetryMetrics();
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
    
    window.showLoader("Scanning workspace, please wait...");

    // --- TELEMETRY AND CATEGORY RENDERING ---
    const data = await eel.execute_storage_telemetry()();
    if (data.error) {
        window.hideLoader();
        return;
    }

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

    // -- POPULATE CATEGORY MATRIX TAB --
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

    // --- LOOSE ORGANIZE & MISMATCHES ---
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
    const checklistContainer = document.getElementById("organize-checklist-container");
    if (checklistContainer) {
        checklistContainer.innerHTML = "";
        currentCategoriesMap = [];
        
        document.getElementById("org-select-all").checked = true;

        if (organizeData.length === 0) {
            checklistContainer.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">No loose files to sort currently.</p>';
        } else {
            organizeData.forEach((cat, index) => {
                currentCategoriesMap.push(cat.name);
                const row = document.createElement("div");
                row.style.margin = "8px 0";
                row.innerHTML = `
                    <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                        <input type="checkbox" id="cat-checkbox-${index}" class="org-cat-checkbox" checked style="width:16px; height:16px;">
                        <span>${cat.name} <b style="color:var(--text-secondary); font-weight:500;">(${cat.count} files)</b></span>
                    </label>
                `;
                checklistContainer.appendChild(row);
            });
        }
    }
    triggerRuleLivePreviews();

    // --- DUPLICATES ---
    const thresholdVal = document.getElementById("similarity-select").value;
    const dupGroups = await eel.get_duplicate_groups_data(activeScanType, thresholdVal)();
    window.currentDuplicateGroups = dupGroups; 
    
    const dupSelectAllBtn = document.getElementById("dup-select-all");
    if(dupSelectAllBtn) dupSelectAllBtn.checked = false;

    const dupContainer = document.getElementById("duplicates-render-container");
    if (dupContainer) {
        dupContainer.innerHTML = "";
        if (dupGroups.length === 0) {
            dupContainer.innerHTML = '<p style="color:var(--text-secondary); font-size:13.5px;">No duplicate elements detected.</p>';
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
                    
                    let mediaThumbnailHtml = `
                        <div style="width:38px; height:38px; border-radius:8px; background:#F3F5FA; border:1px solid var(--stroke-color); display:flex; align-items:center; justify-content:center; flex-shrink:0; color:#98A2B3;">
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
        }
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
    
    // Scan Complete, UI built. Drop the loader.
    window.hideLoader();
}

function initInteractivityHandlers() {
    // Nav Click Routings For Dashboard Tiles
    document.getElementById("metric-total-files").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="organize-panel"]').click();
    });
    document.getElementById("metric-duplicates").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="duplicates-panel"]').click();
    });
    document.getElementById("metric-trash").addEventListener("click", () => {
        document.querySelector('.nav-btn[data-target="bin-panel"]').click();
    });

    // Mismatch File Modals Trigger Logic
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

        window.showLoader("Moving files...");
        const count = await eel.fix_mismatched_files(selected)();
        
        document.getElementById("mismatch-modal").style.display = "none";
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Fixed ${count} misplaced files inside target directories.`), 10);
    });

    // WORKSPACE VACUUM TRIGGER
    document.getElementById("vacuum-btn").addEventListener("click", async () => {
        window.showLoader("Looking for empty directories...");
        const emptyFolders = await eel.get_empty_folders_data()();
        window.hideLoader();
        
        if (emptyFolders.length === 0) {
            alert("No empty folders found natively inside the current workspace.");
            return;
        }
        
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

        window.showLoader("Sweeping empty folders...");
        const res = await eel.purge_selected_empty_folders(selected)();
        if(res.status === "success") {
            document.getElementById("vacuum-modal").style.display = "none";
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Workspace Vacuum complete! Cleaned up and moved ${res.purged} empty folder(s) to the Recycle Bin safely.`), 10);
        } else {
            window.hideLoader();
            alert(res.message || "Error cleaning folders.");
        }
    });

    document.getElementById("change-workspace-btn").addEventListener("click", async () => {
        const res = await eel.select_folder_native()();
        if (res.status === "success") {
            document.getElementById("current-path-display").innerText = res.path;
            refreshDashboardTelemetryMetrics();
            if (document.getElementById("rename-workspace-section").style.display === "block") populateRenameCategories();
        }
    });

    document.getElementById("preview-size-btn").addEventListener("click", triggerRuleLivePreviews);
    document.getElementById("preview-age-btn").addEventListener("click", triggerRuleLivePreviews);

    document.getElementById("org-select-all").addEventListener("change", (e) => {
        document.querySelectorAll(".org-cat-checkbox").forEach(cb => cb.checked = e.target.checked);
    });

    document.getElementById("execute-organize-btn").addEventListener("click", async () => {
        let targets = [];
        currentCategoriesMap.forEach((name, idx) => {
            const cb = document.getElementById(`cat-checkbox-${idx}`);
            if (cb && cb.checked) targets.push(name);
        });
        if (targets.length === 0) return alert("Select at least one category checkbox.");
        
        window.showLoader("Organizing files into structures...");
        const res = await eel.trigger_bulk_organization(targets)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Execution complete. Reorganized ${res.moved} items into folders.`), 10);
        } else {
            window.hideLoader();
            alert(res.message);
        }
    });

    document.getElementById("execute-size-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("size-input-value").value;
        const timing = document.querySelector('input[name="size-timing"]:checked').value;
        
        window.showLoader("Processing large files...");
        const res = await eel.trigger_separation_organization("size", timing, val)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Size organization sequence resolved. Isolated ${res.moved} files.`), 10);
        } else {
            window.hideLoader();
            alert(res.message);
        }
    });

    document.getElementById("execute-age-organize-btn").addEventListener("click", async () => {
        const val = document.getElementById("age-input-value").value;
        const timing = document.querySelector('input[name="age-timing"]:checked').value;
        
        window.showLoader("Processing historical files...");
        const res = await eel.trigger_separation_organization("age", timing, val)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Age organization sequence resolved. Isolated ${res.moved} files.`), 10);
        } else {
            window.hideLoader();
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
            window.showLoader("Moving duplicates to Recovery Bin...");
            const res = await eel.purge_selected_duplicates(targets)();
            if (res.status === "success") {
                await refreshDashboardTelemetryMetrics();
                setTimeout(() => alert(`Asset cleanups processed successfully.`), 10);
            } else {
                window.hideLoader();
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
        
        window.showLoader("Restoring files to original destinations...");
        const res = await eel.restore_from_bin(targets)();
        if (res.status === "success") {
            await refreshDashboardTelemetryMetrics();
            setTimeout(() => alert(`Successfully restored ${res.restored} item(s) back to original locations.`), 10);
        } else {
            window.hideLoader();
            alert(res.message);
        }
    });

    document.getElementById("empty-bin-btn").addEventListener("click", async () => {
        if (confirm("Attention! This action completely flushes your hidden recovery files permanently from disk memory. Proceed?")) {
            window.showLoader("Securely deleting trash logs...");
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
});

window.triggerUndoSequence = async function(logPathString) {
    if (confirm("Reverse adjustments logged inside this execution batch?")) {
        window.showLoader("Reversing operations...");
        const res = await eel.execute_undo_operation(logPathString)();
        await refreshDashboardTelemetryMetrics();
        setTimeout(() => alert(`Filesystem adjustments reversed safely. Restored ${res.restored} items.`), 10);
    }
};
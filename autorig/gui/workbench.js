// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: main desktop-class workbench controller (gui/workbench.js).
// Coordinates the top application menu bar, 3D viewport modes (Rig & Fix vs Render & Test),
// slide-over drawers (Rig Inspector, Console Log), and native dialog modals.

import { parseJobProgressLine, formatJobHeader } from "./viewer_logic.js";

const TOKEN = window.AUTORIG_TOKEN;
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fileUrl = (u) => u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);

const CHECKS = {
  bleed_pct: "Bleed %",
  combined_tears: "Tears, combined pose",
  bend_tears: "Tears, single bends",
  head_pct: "Head share %",
  max_influences: "Influences per vertex"
};

const STEP_DESCRIPTIONS = {
  survey: "Survey source mesh, detect armature kind, and render 4 facing views to find forward direction.",
  rig: "Build standard armature from rig.json, skin mesh with bone heat & voxel proxy, and render QA bend test.",
  trim: "Decimate geometry to triangle budget, clamp to max 4 influences per vertex, and normalize weights.",
  audit: "Run automated QA checks: test for tears in extreme poses, weight bleed, head share, and influence limits.",
  clips: "Author procedural animation clips (walk, idle, attack, etc.) for the model's archetype.",
  publish: "Write engine model card (model.json) with bounding measurements and index collection.",
  preview: "Compile preview.glb with skinned mesh and baked clips for interactive 3D results viewer.",
  all: "Execute entire pipeline in sequence: Survey -> Rig -> Trim -> Audit -> Clips -> Publish -> Preview."
};

const gradeClass = (g) => ({ PASS: "pass", CHECK: "check", FAIL: "fail", HEALTHY: "pass", WARN: "check" }[g] || "");

let state = null;
let currentModel = "";
let currentDetails = null;
let activeJob = null;
let currentJobWorkingModel = "";
let currentJobSubCount = null;
let currentJobPrevResult = null;
let currentJobLastResult = null;
let currentJobCounts = { passed: 0, failed: 0 };
let eventSource = null;
let activeMenu = null;

async function api(path, body) {
  const opt = { headers: { "X-Autorig-Token": TOKEN } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------------------------------------------------------------------------------------------------------------
// Initialization & State Synchronization
// ---------------------------------------------------------------------------------------------------------------

async function init() {
  setupMenus();
  setupDrawers();
  setupViewbar();
  setupShortcuts();
  setupModals();
  setupDragAndDrop();
  setupModelManagement();

  try {
    await refreshState();
  } catch (e) {
    console.error("Workbench state init error:", e);
  }

  // Ensure 3D spec editor module has finished initializing
  if (window.specEditorReady) {
    try {
      await window.specEditorReady;
    } catch (e) {}
  }

  // Determine initial model
  const hashModel = new URLSearchParams(location.hash.slice(1)).get("model");
  const queryModel = new URLSearchParams(location.search).get("model");
  const initial = hashModel || queryModel || (state && state.models && state.models[0]?.name) || "";

  if (initial) {
    await selectModel(initial);
  }

  // Periodic poll for background jobs if active
  setInterval(() => {
    if (state && state.running) refreshState().catch(() => {});
  }, 4000);
}

async function refreshState() {
  state = await api("/api/state");
  renderModelSelector();
  updateTopJobPill();
  updateJobSelect();

  const busy = state.running;
  if (busy) {
    if (!activeJob || activeJob.id !== busy.id) {
      followJob(busy);
    }
  } else if (!activeJob && state.jobs && state.jobs.length > 0) {
    loadJobLog(state.jobs[0].id);
  }
}

let currentModelFilter = "all";
let currentGroupFilter = "all";
let currentModelSearch = "";
let selectedModelNames = new Set();
let mgrCurrentSort = "name_asc";
let mgrStatusFilter = "all";

function matchesFilter(m, filter, group = currentGroupFilter, search = currentModelSearch) {
  // 1. Group filter
  if (group && group !== "all") {
    const mg = m.group || "";
    if (group === "(root)" ? mg !== "" : mg !== group) return false;
  }

  // 2. Text search
  if (search) {
    const term = search.toLowerCase();
    const matchName = (m.name || "").toLowerCase().includes(term);
    const matchGroup = (m.group || "").toLowerCase().includes(term);
    const matchArch = (m.clips_spec || "").toLowerCase().includes(term);
    const matchExt = (m.source_ext || "").toLowerCase().includes(term);
    if (!matchName && !matchGroup && !matchArch && !matchExt) return false;
  }

  // 3. Status filter
  if (!filter || filter === "all") return true;
  const audit = (m.audit || "").toUpperCase();
  if (filter === "pass") return audit === "PASS";
  if (filter === "check") return audit === "CHECK";
  if (filter === "fail") return audit === "FAIL";
  if (filter === "rigged") return Boolean(m.rigged);
  if (filter === "unrigged") return !m.rigged;
  if (filter === "nospec") return !m.spec;
  return true;
}

function setModelFilter(filter) {
  currentModelFilter = filter;
  applyModelFilters();
}

// After the status, group or search filter changes: redraw the list, and open the first model that still shows
// when the open one no longer does.
function applyModelFilters() {
  renderModelSelector();
  const allModels = (state && state.models) || [];
  const filtered = allModels.filter((m) => matchesFilter(m, currentModelFilter, currentGroupFilter, currentModelSearch));
  if (filtered.length > 0 && !filtered.some((m) => m.name === currentModel)) {
    selectModel(filtered[0].name);
  }
  if ($("#modelManagerModal")?.open) renderModelManager();
}

function renderRootBadge() {
  const rootBtn = $("#btnSwitchRoot");
  const rootNameEl = $("#activeRootName");
  if (!rootNameEl || !state) return;
  rootNameEl.textContent = state.root_name || "Root";
  if (rootBtn) {
    rootBtn.title = `Working folder (models root):\n${state.root || ""}\n\nWork folder:\n${state.work || ""}\n\nClick to choose another`;
  }
}

function renderModelSelector() {
  const sel = $("#modelSelect");
  if (!sel) return;

  renderRootBadge();

  const allModels = (state && state.models) || [];

  // Update counts on filter dropdown items
  const counts = {
    all: allModels.length,
    pass: allModels.filter((m) => (m.audit || "").toUpperCase() === "PASS").length,
    check: allModels.filter((m) => (m.audit || "").toUpperCase() === "CHECK").length,
    fail: allModels.filter((m) => (m.audit || "").toUpperCase() === "FAIL").length,
    rigged: allModels.filter((m) => Boolean(m.rigged)).length,
    unrigged: allModels.filter((m) => !m.rigged).length,
    nospec: allModels.filter((m) => !m.spec).length,
  };

  const setCnt = (id, n) => { const el = $(id); if (el) el.textContent = `(${n})`; };
  setCnt("#filterCountAll", counts.all);
  setCnt("#filterCountPass", counts.pass);
  setCnt("#filterCountCheck", counts.check);
  setCnt("#filterCountFail", counts.fail);
  setCnt("#filterCountRigged", counts.rigged);
  setCnt("#filterCountUnrigged", counts.unrigged);
  setCnt("#filterCountNoSpec", counts.nospec);

  // Update filter button label & styling
  const filterBtn = $("#modelFilterBtn");
  if (filterBtn) {
    const labels = {
      all: "⚡ All",
      pass: `⚡ PASS (${counts.pass})`,
      check: `⚡ CHECK (${counts.check})`,
      fail: `⚡ FAIL (${counts.fail})`,
      rigged: `⚡ Rigged (${counts.rigged})`,
      unrigged: `⚡ Unrigged (${counts.unrigged})`,
      nospec: `⚡ No Spec (${counts.nospec})`,
    };
    filterBtn.innerHTML = `<span>${esc(labels[currentModelFilter] || "⚡ Filter")}</span> ▾`;
    filterBtn.classList.toggle("on", currentModelFilter !== "all");
  }

  // Update group select dropdown
  const grpSel = $("#groupSelect");
  if (grpSel) {
    const groupCounts = {};
    for (const m of allModels) {
      const g = m.group || "";
      groupCounts[g] = (groupCounts[g] || 0) + 1;
    }
    const prevGrp = currentGroupFilter;
    let grpHtml = `<option value="all">📂 All Groups (${allModels.length})</option>`;
    for (const g of Object.keys(groupCounts).sort()) {
      const label = g ? g : "Root / Direct";
      const val = g ? g : "(root)";
      grpHtml += `<option value="${esc(val)}" ${val === prevGrp ? "selected" : ""}>📁 ${esc(label)} (${groupCounts[g]})</option>`;
    }
    grpSel.innerHTML = grpHtml;
    const shown = currentGroupFilter === "(root)" ? "" : currentGroupFilter;
    grpSel.value = currentGroupFilter !== "all" && groupCounts[shown] ? currentGroupFilter : "all";
    currentGroupFilter = grpSel.value;
  }

  const searchInput = $("#modelSearchInput");
  if (searchInput && searchInput.value.trim() !== currentModelSearch) searchInput.value = currentModelSearch;
  const clearBtn = $("#clearModelSearch");
  if (clearBtn) clearBtn.style.display = currentModelSearch ? "block" : "none";

  // Filter models according to active criteria
  const filteredModels = allModels.filter((m) => matchesFilter(m, currentModelFilter, currentGroupFilter, currentModelSearch));

  const prev = currentModel;
  const byGroup = {};
  for (const m of filteredModels) {
    (byGroup[m.group || "Root / Direct"] ||= []).push(m);
  }

  let html = "";
  for (const group of Object.keys(byGroup).sort()) {
    html += `<optgroup label="${esc(group)} (${byGroup[group].length})">`;
    for (const m of byGroup[group]) {
      const badge = m.audit ? ` [${m.audit}]` : (m.rigged ? " [Rigged]" : (m.spec ? " [Spec]" : ""));
      html += `<option value="${esc(m.name)}" ${m.name === prev ? "selected" : ""}>${esc(m.name)}${badge}</option>`;
    }
    html += `</optgroup>`;
  }

  sel.innerHTML = html || `<option value="">(No models match filter)</option>`;
  sel.onchange = (e) => {
    if (e.target.value) selectModel(e.target.value);
  };
  updateModelBadge();
}

function updateModelBadge() {
  const m = state && state.models && state.models.find((x) => x.name === currentModel);
  const badgeEl = $("#modelBadge");
  const infoEl = $("#hudModelInfo");
  if (!m) {
    if (badgeEl) badgeEl.style.display = "none";
    if (infoEl) infoEl.innerHTML = `<span class="empty">No model loaded</span>`;
    return;
  }

  if (badgeEl) {
    badgeEl.style.display = "inline-block";
    const g = m.audit || (m.rigged ? "RIGGED" : (m.spec ? "SPEC" : "NEW"));
    badgeEl.textContent = g;
    badgeEl.className = "chip " + gradeClass(m.audit || "");
  }

  if (infoEl) {
    infoEl.innerHTML = `<div class="model-name">${esc(m.name)}</div>` +
      `<div class="meta">${esc(m.group || "Root")} · ${esc(m.kind || "No spec")}${m.budget ? " · " + esc(m.budget) + " tris" : ""}` +
      (m.audit ? ` · <span class="chip ${gradeClass(m.audit)}">${esc(m.audit)}</span>` : "") +
      `</div>`;
  }
}

async function deleteSpecificModel(name) {
  if (!name) return;
  const m = state && state.models && state.models.find((x) => x.name === name);
  const where = m && m.group ? m.group + "/" + name : name;
  const running = state && state.running;
  if (running && (running.model === name || running.current_model === name)) {
    alert("A step is running on " + name + ". Cancel it before deleting the model.");
    return;
  }
  const ok = confirm(
    'Delete "' + name + '"?\n\nThis removes the model folder (' + where +
    ") and the work files for it: the source, the spec, the rig, audits and clips. It cannot be undone."
  );
  if (!ok) return;
  try {
    await api("/api/model/delete", { model: name });
  } catch (e) {
    alert(e.message);
    return;
  }
  await refreshState();
  if (currentModel === name) {
    const rest = ((state && state.models) || []).map((x) => x.name).filter((n) => n !== name);
    if (rest.length) {
      await selectModel(rest[0], { force: true });
    } else {
      currentModel = "";
      try { history.replaceState(null, "", location.pathname); } catch (e) {}
      updateModelBadge();
    }
  }
  if ($("#modelManagerModal") && $("#modelManagerModal").open) {
    renderModelManager();
  }
}

async function deleteCurrentModel() {
  if (!currentModel) {
    alert("No model selected.");
    return;
  }
  await deleteSpecificModel(currentModel);
}

async function selectModel(name, opts = {}) {
  if (!name) return false;
  const se = window.specEditor;
  if (!opts.force && name !== currentModel && se && typeof se.hasUnsavedEdits === "function" && se.hasUnsavedEdits()) {
    // unsaved spec edits are never dropped by something the user did not ask for (a job's log naming another
    // model); a switch they did ask for asks first
    const sel = $("#modelSelect");
    if (opts.auto || !confirm(`Discard your unsaved spec edits to ${currentModel}?`)) {
      if (sel) sel.value = currentModel;
      return false;
    }
  }
  currentModel = name;
  const sel = $("#modelSelect");
  if (sel && sel.value !== name) sel.value = name;

  updateModelBadge();
  try {
    history.replaceState(null, "", "#model=" + encodeURIComponent(name));
  } catch (e) {}

  // Synchronize 3D viewer & spec editor
  if (window.specEditor && typeof window.specEditor.switchModel === "function") {
    try {
      await window.specEditor.switchModel(name);
    } catch (e) {
      console.warn("specEditor.switchModel error:", e);
    }
  }

  // Update mode buttons based on model rigged status
  const m = state && state.models && state.models.find((x) => x.name === name);
  const curViewMode = (window.specEditor && typeof window.specEditor.viewMode === "function") ? window.specEditor.viewMode() : "source";
  const modeRig = $("#modeRig");
  const modeTest = $("#modeTest");
  if (modeRig) modeRig.classList.toggle("on", curViewMode === "source");
  if (modeTest) {
    modeTest.classList.toggle("on", curViewMode === "rigged");
    modeTest.disabled = m ? !m.rigged : false;
    modeTest.title = (m && m.rigged) ? "Rigged mesh & clips: test animations and tears" : "Rig the model first to test animations and clips";
  }

  // Load details in background for modals and quick views
  try {
    currentDetails = await api("/api/model?name=" + encodeURIComponent(name));
  } catch (e) {
    console.warn("Could not load model details:", e);
  }
  return true;
}

// ---------------------------------------------------------------------------------------------------------------
// Top Menu Bar & Dropdowns
// ---------------------------------------------------------------------------------------------------------------

function setupMenus() {
  const menuButtons = $$(".menu-item > button");

  menuButtons.forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const parent = btn.parentElement;
      if (activeMenu === parent) {
        closeMenus();
      } else {
        closeMenus();
        parent.classList.add("open");
        activeMenu = parent;
      }
    };

    btn.onmouseenter = () => {
      if (activeMenu && activeMenu !== btn.parentElement) {
        closeMenus();
        btn.parentElement.classList.add("open");
        activeMenu = btn.parentElement;
      }
    };
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-item")) {
      closeMenus();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenus();
    }
  });

  // Wire menu actions
  $$("[data-action]").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      closeMenus();
      handleAction(el.dataset.action);
    };
  });

  // Wire model filter menu items
  $$("[data-filter]").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      closeMenus();
      setModelFilter(el.dataset.filter);
    };
  });

  // Mode buttons
  const modeRig = $("#modeRig");
  const modeTest = $("#modeTest");
  if (modeRig && modeTest) {
    modeRig.onclick = () => setWorkbenchMode("source");
    modeTest.onclick = () => setWorkbenchMode("rigged");
  }

  // Quick Action Primary Button
  const btnQuickAction = $("#btnQuickAction");
  if (btnQuickAction) {
    btnQuickAction.onclick = () => {
      const mode = window.specEditor ? window.specEditor.viewMode() : "source";
      if (mode === "source") {
        if (window.specEditor) {
          if (typeof window.specEditor.rerig === "function") window.specEditor.rerig();
          else if (typeof window.specEditor.save === "function") window.specEditor.save(true);
        }
      } else {
        runStep("all");
      }
    };
  }

  // Rebake clips buttons (viewport clipBar & inspector drawer)
  const btnRebakeClips = $("#btnRebakeClips");
  if (btnRebakeClips) {
    btnRebakeClips.onclick = () => rebakeCurrentModelClips();
  }
  const bRebakeDrawer = $("#bRebakeClips");
  if (bRebakeDrawer) {
    bRebakeDrawer.onclick = () => rebakeCurrentModelClips();
  }
}

function closeMenus() {
  if (activeMenu) {
    activeMenu.classList.remove("open");
    activeMenu = null;
  }
}

function setWorkbenchMode(mode) {
  if (!window.specEditor) return;

  const m = state && state.models && state.models.find((x) => x.name === currentModel);
  if (mode === "rigged" && m && !m.rigged) {
    alert(`Model "${currentModel}" has not been rigged yet.\n\nClick "🚀 Save & Re-Rig" or run Step 2 (Rig & Skin) to generate the rigged skeleton and animation preview.`);
    return;
  }

  window.specEditor.setViewMode(mode);

  const modeRig = $("#modeRig");
  const modeTest = $("#modeTest");
  const btnQuickAction = $("#btnQuickAction");

  if (modeRig) modeRig.classList.toggle("on", mode === "source");
  if (modeTest) modeTest.classList.toggle("on", mode === "rigged");

  if (btnQuickAction) {
    if (mode === "source") {
      btnQuickAction.innerHTML = `<span>🚀 Save &amp; Re-Rig</span>`;
      btnQuickAction.title = "Save spec changes and re-rig model (runs full QA pipeline)";
    } else {
      btnQuickAction.innerHTML = `<span>⚡ Run All Steps</span>`;
      btnQuickAction.title = "Run entire pipeline (Survey -> Rig -> Trim -> Audit -> Clips -> Preview)";
    }
  }
}

async function handleAction(action) {
  switch (action) {
    case "ai-fix-rig":
      if (!currentModel) alert("Select a model first.");
      else if (window.aiAssist) window.aiAssist.openFixRig(currentModel);
      break;
    case "ai-settings":
      if (window.aiAssist) window.aiAssist.openSettings();
      break;
    case "model-studio":
      window.open("./studio.html?t=" + encodeURIComponent(TOKEN) + (currentModel ? "&model=" + encodeURIComponent(currentModel) : ""), "_blank");
      break;
    // File
    case "switch-root":
      openRootModal();
      break;
    case "model-manager":
      openModelManagerModal();
      break;
    case "open-root":
      api("/api/open-root", {}).catch((e) => alert(e.message));
      break;
    case "git-commit":
      openGitCommit();
      break;
    case "clean-generated":
      openCleanGenerated();
      break;
    case "import-model":
      openModal("#importModal");
      break;
    case "samples-gallery":
      openSamplesGallery();
      break;
    case "open-output":
      if (currentModel) {
        api("/api/open", { model: currentModel }).catch((e) => alert(e.message));
      }
      break;
    case "refresh":
      await refreshState();
      // re-read the current model in place (keeps unsaved edits; switchModel would start it over)
      if (currentModel && window.specEditor && window.specEditor.refreshCurrentModel) {
        await window.specEditor.refreshCurrentModel();
      }
      break;

    // Model
    case "move-model":
      if (currentModel) promptMoveModel(currentModel);
      else alert("Select a model first.");
      break;
    case "rename-model":
      if (currentModel) promptRenameModel(currentModel);
      else alert("Select a model first.");
      break;
    case "duplicate-model":
      if (currentModel) promptDuplicateModel(currentModel);
      else alert("Select a model first.");
      break;
    case "new-group":
      promptNewGroup();
      break;
    case "model-details":
      openModelDetails();
      break;
    case "mesh-doctor":
      openMeshDoctor();
      break;
    case "facing-views":
      openFacingViews();
      break;
    case "delete-model":
      deleteCurrentModel();
      break;

    // Rig & Fix
    case "suggest-skeleton":
      if (window.specEditor && window.specEditor.suggest) window.specEditor.suggest();
      break;
    case "auto-tune":
      if (window.specEditor && window.specEditor.autoTune) window.specEditor.autoTune();
      break;
    case "toggle-inspector":
      toggleDrawer("#inspectorDrawer");
      break;
    case "undo":
      if (window.specEditor && window.specEditor.undo) window.specEditor.undo();
      break;
    case "revert":
      if (window.specEditor && window.specEditor.revert) window.specEditor.revert();
      break;
    case "save-spec":
      if (window.specEditor && window.specEditor.save) window.specEditor.save(false);
      break;
    case "save-rerig":
      if (window.specEditor) {
        if (typeof window.specEditor.rerig === "function") window.specEditor.rerig();
        else if (typeof window.specEditor.save === "function") window.specEditor.save(true);
      }
      break;

    // Pipeline
    case "rebake-model-clips":
    case "pipe-rebake-clips":
      rebakeCurrentModelClips();
      break;
    case "pipe-prepare":
    case "pipe-survey":
    case "pipe-rig":
    case "pipe-trim":
    case "pipe-audit":
    case "pipe-clips":
    case "pipe-publish":
    case "pipe-preview":
    case "pipe-all": {
      const step = action.replace("pipe-", "");
      runStep(step);
      break;
    }
    case "batch-source-all":
      try {
        const j = await api("/api/source-all", { missing_only: false });
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;
    case "batch-source-missing":
      try {
        const j = await api("/api/source-all", { missing_only: true });
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;
    case "batch-rig-all":
      try {
        const j = await api("/api/rig-all", {});
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;
    case "batch-audit-all":
      try {
        const j = await api("/api/audit-all", {});
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;
    case "batch-audit-failed":
      try {
        const j = await api("/api/audit-failed", {});
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;
    case "batch-rebake-clips":
      try {
        const j = await api("/api/clips-all", {});
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
      } catch (e) { alert(e.message); }
      break;

    // QA & Test
    case "run-audit":
      runStep("audit");
      break;
    case "view-audit":
      openAuditModal();
      break;
    case "collection-table":
      openCollectionTable();
      break;
    case "toggle-bend-test": {
      const chk = $("#poseTestToggle");
      if (chk) { chk.checked = !chk.checked; chk.dispatchEvent(new Event("change")); }
      break;
    }
    case "toggle-tears": {
      const chk = $("#showSpots");
      if (chk) { chk.checked = !chk.checked; chk.dispatchEvent(new Event("change")); }
      break;
    }

    // Export
    case "export-unreal":
    case "export-unity":
    case "export-godot":
    case "export-web":
    case "export-all":
      openExportModal(action.replace("export-", ""));
      break;

    // View
    case "view-mode-rig":
      setWorkbenchMode("source");
      break;
    case "view-mode-test":
      setWorkbenchMode("rigged");
      break;
    case "view-front":
      if (window.specEditor && window.specEditor.frameView) window.specEditor.frameView([0, -1, 0]);
      break;
    case "view-side":
      if (window.specEditor && window.specEditor.frameView) window.specEditor.frameView([1, 0, 0]);
      break;
    case "view-top":
      if (window.specEditor && window.specEditor.frameView) window.specEditor.frameView([0, 0, 1]);
      break;
    case "view-reset":
      if (window.specEditor && window.specEditor.frameView) window.specEditor.frameView([0.35, -1, 0.3]);
      break;
    case "toggle-log":
      toggleDrawer("#logDrawer");
      break;

    // Help
    case "help-guide":
      window.open(fileUrl("/help.html"), "_blank");
      break;
    case "help-shortcuts":
      openModal("#shortcutsModal");
      break;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Drawers (Inspector & Log Console) & Viewbar Controls
// ---------------------------------------------------------------------------------------------------------------

let logHeight = parseInt(localStorage.getItem("autorig_log_height") || "250", 10);
if (isNaN(logHeight) || logHeight < 100) logHeight = 250;

function applyLogHeight(h) {
  logHeight = h;
  document.documentElement.style.setProperty("--log-drawer-height", `${h}px`);
  const logDrawer = $("#logDrawer");
  if (logDrawer && logDrawer.classList.contains("open")) {
    document.documentElement.style.setProperty("--log-drawer-offset", `${h + 2}px`);
  }
}

function setupDrawers() {
  const btnToggleInspector = $("#toggleInspector");
  const btnToggleLog = $("#toggleLog");
  const btnToggleAudit = $("#toggleAudit");

  const inspector = $("#inspectorDrawer");
  const logDrawer = $("#logDrawer");
  const logToggleHeader = $("#logToggleHeader");
  const resizeHandle = $("#logResizeHandle");
  const collapseLog = $("#collapseLog");
  const clearLogBtn = $("#clearLogBtn");

  applyLogHeight(logHeight);

  if (btnToggleInspector && inspector) {
    btnToggleInspector.onclick = () => toggleDrawer("#inspectorDrawer");
  }
  if (btnToggleLog && logDrawer) {
    btnToggleLog.onclick = () => toggleDrawer("#logDrawer");
  }
  if (btnToggleAudit) {
    btnToggleAudit.onclick = () => openAuditModal();
  }

  const closeInspector = $("#closeInspector");
  if (closeInspector && inspector) {
    closeInspector.onclick = () => setDrawerOpen(inspector, false);
  }

  let lastDragTime = 0;
  if (resizeHandle && logDrawer) {
    let isDragging = false;
    let startY = 0;
    let startH = 0;
    let dragDistance = 0;

    const onPointerMove = (e) => {
      if (!isDragging) return;
      const deltaY = startY - e.clientY;
      dragDistance += Math.abs(deltaY);
      const minH = 100;
      const maxH = Math.max(minH, window.innerHeight - 80);
      const newH = Math.round(Math.min(maxH, Math.max(minH, startH + deltaY)));
      applyLogHeight(newH);
    };

    const finishDrag = () => {
      if (!isDragging) return;
      isDragging = false;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", finishDrag);
      resizeHandle.classList.remove("active");
      document.body.classList.remove("resizing");
      if (dragDistance > 3) {
        lastDragTime = Date.now();
      }
      try {
        localStorage.setItem("autorig_log_height", String(logHeight));
      } catch (_) {}
      if (window.specEditor && typeof window.specEditor.resize === "function") {
        try { window.specEditor.resize(); } catch (_) {}
      }
    };

    resizeHandle.onpointerdown = (e) => {
      e.preventDefault();
      e.stopPropagation();
      isDragging = true;
      dragDistance = 0;
      startY = e.clientY;
      startH = logDrawer.getBoundingClientRect().height;
      resizeHandle.classList.add("active");
      document.body.classList.add("resizing");
      if (!logDrawer.classList.contains("open")) {
        setDrawerOpen(logDrawer, true);
      }
      window.addEventListener("pointermove", onPointerMove, { passive: true });
      window.addEventListener("pointerup", finishDrag);
      window.addEventListener("pointercancel", finishDrag);
    };

    resizeHandle.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
    };
  }

  if (logToggleHeader && logDrawer) {
    logToggleHeader.onclick = (e) => {
      if (e.target.closest("button, select, input")) return;
      if (Date.now() - lastDragTime < 300) return;
      toggleDrawer("#logDrawer");
    };
  }

  if (collapseLog && logDrawer) {
    collapseLog.onclick = (e) => {
      e.stopPropagation();
      setDrawerOpen(logDrawer, false);
    };
  }

  if (clearLogBtn) {
    clearLogBtn.onclick = (e) => {
      e.stopPropagation();
      setConsoleContent("");
      currentJobSubCount = null;
      currentJobPrevResult = null;
      currentJobLastResult = null;
      currentJobWorkingModel = "";
      currentJobCounts = { passed: 0, failed: 0 };
      const isRunning = activeJob && (activeJob.state === "running" || activeJob.state === "queued");
      if (!isRunning) {
        activeJob = null;
      }
      updateJobUI();
    };
  }

  // Model badge click opens audit
  const badgeEl = $("#modelBadge");
  if (badgeEl) {
    badgeEl.onclick = () => openAuditModal();
  }

  // Initialize CSS offsets and body classes for HUDs
  const inspectorOpen = !!(inspector && inspector.classList.contains("open"));
  const logOpen = !!(logDrawer && logDrawer.classList.contains("open"));
  document.body.classList.toggle("inspector-open", inspectorOpen);
  document.body.classList.toggle("log-open", logOpen);
  document.documentElement.style.setProperty("--inspector-offset", inspectorOpen ? "440px" : "0px");
  document.documentElement.style.setProperty("--log-drawer-offset", logOpen ? `${logHeight + 2}px` : "34px");
}

function toggleDrawer(sel) {
  const el = $(sel);
  if (!el) return;
  const isOpen = el.classList.contains("open");
  setDrawerOpen(el, !isOpen);
}

function setDrawerOpen(el, open) {
  el.classList.toggle("open", open);
  if (el.id === "inspectorDrawer") {
    const btn = $("#toggleInspector");
    if (btn) btn.classList.toggle("on", open);
    document.body.classList.toggle("inspector-open", open);
    document.documentElement.style.setProperty("--inspector-offset", open ? "440px" : "0px");
  } else if (el.id === "logDrawer") {
    const btn = $("#toggleLog");
    if (btn) btn.classList.toggle("on", open);
    document.body.classList.toggle("log-open", open);
    document.documentElement.style.setProperty("--log-drawer-offset", open ? `${logHeight + 2}px` : "34px");
  }
  if (window.specEditor && typeof window.specEditor.resize === "function") {
    setTimeout(() => window.specEditor.resize(), 230);
  }
}

function setupViewbar() {
  const viewbar = $("#viewbar");
  const btnToggle = $("#btnToggleViewbar");
  if (!viewbar || !btnToggle) return;

  const savedCollapsed = localStorage.getItem("autorig_viewbar_collapsed") === "true";
  setViewbarCollapsed(savedCollapsed);

  btnToggle.onclick = (e) => {
    e.stopPropagation();
    toggleViewbar();
  };
}

function toggleViewbar() {
  const viewbar = $("#viewbar");
  if (!viewbar) return;
  const isCollapsed = viewbar.classList.contains("collapsed");
  setViewbarCollapsed(!isCollapsed);
}

function setViewbarCollapsed(collapsed) {
  const viewbar = $("#viewbar");
  const btnToggle = $("#btnToggleViewbar");
  if (!viewbar) return;
  viewbar.classList.toggle("collapsed", collapsed);
  if (btnToggle) {
    btnToggle.title = collapsed ? "Expand toolbox options (Ctrl+U)" : "Collapse toolbox options (Ctrl+U)";
    const chevron = $("#viewbarChevron");
    if (chevron) chevron.textContent = collapsed ? "▼" : "▲";
  }
  localStorage.setItem("autorig_viewbar_collapsed", collapsed ? "true" : "false");
}

// ---------------------------------------------------------------------------------------------------------------
// Pipeline Execution & Live Job Follower
// ---------------------------------------------------------------------------------------------------------------

function getConsoleElements() {
  const els = [];
  const c = $("#consoleLog");
  if (c) els.push(c);
  const p = $("#paneLog");
  if (p && !els.includes(p)) els.push(p);
  const legacy = $("#log");
  if (legacy && !els.includes(legacy)) els.push(legacy);
  return els;
}

function setConsoleContent(text) {
  const els = getConsoleElements();
  for (const el of els) {
    el.textContent = text;
    el.scrollTop = el.scrollHeight;
  }
}

function appendConsoleLine(line) {
  const els = getConsoleElements();
  for (const el of els) {
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    el.textContent += line + "\n";
    if (stick) el.scrollTop = el.scrollHeight;
  }
}

// A live log line naming the model a job is working on follows it in the viewer. Replayed logs (an old job picked
// in the dropdown, a reconnect, the final re-read) only update the counters: they never change the model.
function detectAndSwitchJobModel(line, live = false) {
  if (!line || typeof line !== "string") return;
  const p = parseJobProgressLine(line);
  if (p) {
    if (p.current && p.total) {
      currentJobSubCount = { current: p.current, total: p.total };
    }
    if (p.prevResult) {
      currentJobPrevResult = p.prevResult;
    }
    if (p.lastResult) {
      currentJobLastResult = p.lastResult;
      if (p.lastResult.status === "FAILED") {
        currentJobCounts.failed++;
      } else {
        currentJobCounts.passed++;
      }
    }
    if (p.model) {
      currentJobWorkingModel = p.model;
      const isBulkJob = Boolean(
        (currentJobSubCount && currentJobSubCount.total > 1) ||
        (activeJob && (activeJob.total > 1 || (activeJob.model && activeJob.model.startsWith("("))))
      );
      if (live && !isBulkJob && state && state.models && state.models.some((m) => m.name === p.model)) {
        if (currentModel !== p.model) {
          selectModel(p.model, { auto: true });
        }
      }
    }
    updateJobUI();
    return;
  }

  let detected = "";
  const mActive = line.match(/^(?:==|::)\s*MODEL_ACTIVE\s+([A-Za-z0-9_\-]+)/);
  if (mActive) {
    detected = mActive[1];
  } else {
    const mStep = line.match(/^==\s*(?:\d+\/\d+:|source view \d+\/\d+:|audit \d+\/\d+:|make clips \d+\/\d+:|preview \d+\/\d+:)\s*([A-Za-z0-9_\-]+)/);
    if (mStep) {
      detected = mStep[1];
    } else {
      const mCli = line.match(/^(?:AUDIT_ALL|BATCH_MODEL)\s+\d+\/\d+\s+([A-Za-z0-9_\-]+)/);
      if (mCli) detected = mCli[1];
    }
  }
  if (detected && state && state.models && state.models.some((m) => m.name === detected)) {
    if (currentJobWorkingModel !== detected) {
      currentJobWorkingModel = detected;
      updateJobUI();
      const isBulkJob = Boolean(
        (currentJobSubCount && currentJobSubCount.total > 1) ||
        (activeJob && (activeJob.total > 1 || (activeJob.model && activeJob.model.startsWith("("))))
      );
      if (live && !isBulkJob && currentModel !== detected) {
        selectModel(detected, { auto: true });
      }
    }
  }
}

async function loadJobLog(jobId) {
  try {
    const detail = await api(`/api/jobs/${jobId}`);
    if (detail) {
      activeJob = detail;
      currentJobWorkingModel = (detail.model && !detail.model.startsWith("(")) ? detail.model : "";
      currentJobSubCount = (detail.total && detail.total > 1) ? { current: detail.current || detail.total, total: detail.total } : null;
      currentJobPrevResult = detail.prev_result || null;
      currentJobLastResult = detail.last_result || null;
      currentJobCounts = { passed: detail.passed || 0, failed: detail.failed || 0 };
      if (Array.isArray(detail.log)) {
        for (const l of detail.log) {
          detectAndSwitchJobModel(l);
        }
        setConsoleContent(detail.log.join("\n") + (detail.log.length ? "\n" : ""));
      }
      updateJobUI();
      updateJobSelect();
    }
  } catch (e) {
    console.warn("Could not load job log:", e);
  }
}

function updateJobSelect() {
  const sel = $("#logJobSelect");
  if (!sel) return;
  const jobs = (state && state.jobs) || [];
  if (jobs.length === 0) {
    sel.style.display = "none";
    return;
  }
  sel.style.display = "inline-block";
  let html = "";
  for (const j of jobs) {
    const isCur = activeJob && activeJob.id === j.id;
    html += `<option value="${j.id}" ${isCur ? "selected" : ""}>Job ${j.id}: ${esc(j.step)} (${esc(j.state)})</option>`;
  }
  sel.innerHTML = html;
  sel.onchange = (e) => {
    const id = parseInt(e.target.value, 10);
    if (id) loadJobLog(id);
  };
}

async function rebakeCurrentModelClips() {
  if (!currentModel) {
    alert("Please select or add a model first.");
    return;
  }
  if (window.specEditor && typeof window.specEditor.rebakeClips === "function" && window.specEditor.getModel() === currentModel) {
    try {
      await window.specEditor.rebakeClips();
      setDrawerOpen($("#logDrawer"), true);
      return;
    } catch (e) {
      alert("Re-bake failed: " + e.message);
      return;
    }
  }
  runStep("rebake-clips");
}

async function runStep(step) {
  if (!currentModel) {
    alert("Please select or add a model first.");
    return;
  }
  try {
    const j = await api("/api/run", { model: currentModel, step });
    followJob(j);
    setDrawerOpen($("#logDrawer"), true);
    await refreshState();
  } catch (e) {
    alert(e.message);
  }
}

async function followJob(j) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  activeJob = j;
  currentJobWorkingModel = (j.model && !j.model.startsWith("(")) ? j.model : "";
  currentJobSubCount = (j.total && j.total > 1) ? { current: j.current || 1, total: j.total } : null;
  currentJobPrevResult = j.prev_result || null;
  currentJobLastResult = j.last_result || null;
  currentJobCounts = { passed: j.passed || 0, failed: j.failed || 0 };
  setDrawerOpen($("#logDrawer"), true);
  setConsoleContent("");
  updateJobUI();
  updateJobSelect();

  // 1. Immediately fetch existing buffered log lines so nothing is missed even if fast
  let startFrom = 0;
  try {
    const detail = await api(`/api/jobs/${j.id}`);
    if (detail && Array.isArray(detail.log) && detail.log.length > 0) {
      for (const l of detail.log) {
        detectAndSwitchJobModel(l);
      }
      setConsoleContent(detail.log.join("\n") + "\n");
      startFrom = detail.log.length;
      if (detail.state !== "running" && detail.state !== "queued") {
        activeJob = detail;
        if (detail.total && detail.total > 1) {
          currentJobSubCount = { current: detail.current || detail.total, total: detail.total };
        }
        if (detail.prev_result) {
          currentJobPrevResult = detail.prev_result;
        }
        if (detail.last_result) {
          currentJobLastResult = detail.last_result;
        }
        updateJobUI();
        updateJobSelect();
        return;
      }
    }
  } catch (e) {
    console.warn("Could not pre-fetch job:", e);
  }

  // 2. Stream events from current offset
  eventSource = new EventSource(fileUrl(`/api/jobs/${j.id}/events?from=${startFrom}`));

  eventSource.onmessage = (ev) => {
    let line = "";
    try {
      line = JSON.parse(ev.data);
    } catch (_) {
      line = ev.data;
    }
    detectAndSwitchJobModel(line, true);
    appendConsoleLine(line);
  };

  eventSource.addEventListener("end", async (ev) => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    try {
      activeJob = JSON.parse(ev.data);
      if (activeJob) {
        if (activeJob.total && activeJob.total > 1) {
          currentJobSubCount = { current: activeJob.current || activeJob.total, total: activeJob.total };
        }
        if (activeJob.prev_result) {
          currentJobPrevResult = activeJob.prev_result;
        }
        if (activeJob.last_result) {
          currentJobLastResult = activeJob.last_result;
        }
        if (activeJob.passed !== undefined) {
          currentJobCounts = { passed: activeJob.passed, failed: activeJob.failed };
        }
      }
    } catch (_) {}
    updateJobUI();
    updateJobSelect();
    await refreshState();

    // Final sync fetch to guarantee full log is rendered
    try {
      if (activeJob && activeJob.id) {
        const detail = await api(`/api/jobs/${activeJob.id}`);
        if (detail && Array.isArray(detail.log)) {
          for (const l of detail.log) {
            detectAndSwitchJobModel(l);
          }
          setConsoleContent(detail.log.join("\n") + (detail.log.length ? "\n" : ""));
        }
      }
    } catch (_) {}

    // Refresh model in viewer when finished
    const targetModel = currentJobWorkingModel || (activeJob && activeJob.model);
    if (targetModel === currentModel && window.specEditor && window.specEditor.refreshCurrentModel) {
      window.specEditor.refreshCurrentModel();
    }
  });

  eventSource.onerror = async () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (activeJob && activeJob.id) {
      try {
        const detail = await api(`/api/jobs/${activeJob.id}`);
        if (detail) {
          activeJob = detail;
          if (detail.total && detail.total > 1) {
            currentJobSubCount = { current: detail.current || detail.total, total: detail.total };
          }
          if (detail.prev_result) {
            currentJobPrevResult = detail.prev_result;
          }
          if (detail.last_result) {
            currentJobLastResult = detail.last_result;
          }
          if (Array.isArray(detail.log)) {
            for (const l of detail.log) {
              detectAndSwitchJobModel(l);
            }
            setConsoleContent(detail.log.join("\n") + (detail.log.length ? "\n" : ""));
          }
          updateJobUI();
          updateJobSelect();
        }
      } catch (_) {}
    }
  };
}

function updateJobUI() {
  const pill = $("#topJobInfo");
  const logHeader = $("#logJobInfo");
  const cancelBtn = $("#cancelJob");

  if (!activeJob) {
    if (pill) pill.style.display = "none";
    if (logHeader) {
      logHeader.textContent = "Idle";
      logHeader.removeAttribute("title");
    }
    if (cancelBtn) cancelBtn.disabled = true;
    return;
  }

  const st = activeJob.state;
  const isRunning = st === "running" || st === "queued";
  const displayModel = currentJobWorkingModel || (activeJob.model && !activeJob.model.startsWith("(") ? activeJob.model : "");

  const subCount = currentJobSubCount || (activeJob.total && activeJob.total > 1 ? { current: activeJob.current || activeJob.total, total: activeJob.total } : null);
  const prevResult = currentJobPrevResult || activeJob.prev_result || null;
  const lastResult = currentJobLastResult || activeJob.last_result || null;

  const headerFmt = formatJobHeader(activeJob, subCount, prevResult, lastResult, displayModel);

  if (logHeader) {
    logHeader.innerHTML = headerFmt.html;
    logHeader.title = headerFmt.text;
  }

  if (pill) {
    pill.style.display = isRunning ? "inline-flex" : "none";
    let countBadge = (subCount && subCount.total > 1) ? ` (${subCount.current}/${subCount.total})` : "";
    let prevText = (prevResult && prevResult.model && prevResult.status && prevResult.model !== displayModel)
      ? ` · Prev: ${prevResult.model} ${prevResult.status}`
      : "";
    pill.innerHTML = `<span class="spinner"></span><b>${esc(activeJob.step)}</b>${countBadge}` +
      (displayModel ? ` on <b>${esc(displayModel)}</b>` : (activeJob.model ? ` on ${esc(activeJob.model)}` : "")) +
      (prevText ? `<span style="opacity:0.85; margin-left:4px">${esc(prevText)}</span>` : "");
  }

  if (cancelBtn) {
    cancelBtn.disabled = !isRunning;
    cancelBtn.onclick = async (e) => {
      e.stopPropagation();
      try {
        await api("/api/cancel", { job: activeJob.id });
      } catch (err) {
        alert(err.message);
      }
    };
  }
}

function updateTopJobPill() {
  const busy = state && state.running;
  if (!busy && !activeJob) {
    const pill = $("#topJobInfo");
    if (pill) pill.style.display = "none";
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Native Dialog Modals
// ---------------------------------------------------------------------------------------------------------------

function setupModals() {
  $$("dialog").forEach((dlg) => {
    // Backdrop click dismisses
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg) dlg.close();
    });
    // Wire close buttons inside
    dlg.querySelectorAll(".close-dialog").forEach((btn) => {
      btn.onclick = () => dlg.close();
    });
  });
}

function openModal(sel) {
  const dlg = $(sel);
  if (!dlg || typeof dlg.showModal !== "function") return;
  // The menu click is still in flight. showModal during that click lands on the
  // backdrop, and the dialog's own click listener closes it before it paints.
  setTimeout(() => {
    if (!dlg.open) dlg.showModal();
  }, 0);
}

// ---- Sample Models Gallery Modal
async function openSamplesGallery() {
  openModal("#samplesModal");
  const body = $("#samplesModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading built-in sample models…</div>`;

  try {
    const data = await api("/api/samples");
    let html = `<div class="sample-grid">`;
    for (const s of data.samples) {
      const isInst = state && state.models.some((m) => m.name === s.name);
      html += `<div class="sample-card">`;
      if (s.thumbnail) {
        html += `<img class="thumb" src="${esc(fileUrl(s.thumbnail))}" alt="${esc(s.title)}" loading="lazy">`;
      }
      html += `<div class="row" style="margin-top:8px"><h3 style="margin:0;font-size:15px">${esc(s.title)}</h3>` +
        `<span class="chip">${esc(s.category)}</span><span class="chip on">${esc(s.archetype)}</span>` +
        (isInst ? `<span class="chip pass">Loaded</span>` : "") +
        `</div>`;
      html += `<div class="why" style="margin:6px 0 8px">${esc(s.description)}</div>`;
      html += `<div class="meta" style="font-size:12px;margin-bottom:8px">Kind: <b>${esc(s.kind)}</b> · Budget: <b>${esc(s.budget)} tris</b></div>`;
      html += `<div class="actions">`;
      if (isInst) {
        html += `<button class="primary" data-open-sample="${esc(s.name)}">Open in Workbench</button>`;
      } else {
        html += `<button class="primary" data-load-sample="${esc(s.name)}">Load into Workbench</button>`;
        html += `<button data-load-rig-sample="${esc(s.name)}">Load &amp; Rig All</button>`;
      }
      html += `</div></div>`;
    }
    html += `</div>`;
    body.innerHTML = html;

    body.querySelectorAll("button[data-open-sample]").forEach((b) => {
      b.onclick = () => {
        $("#samplesModal").close();
        selectModel(b.dataset.openSample);
      };
    });

    body.querySelectorAll("button[data-load-sample]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true; b.textContent = "Loading…";
        try {
          await api("/api/samples/load", { name: b.dataset.loadSample });
          await refreshState();
          $("#samplesModal").close();
          await selectModel(b.dataset.loadSample);
        } catch (e) {
          alert(e.message);
          b.disabled = false; b.textContent = "Load into Workbench";
        }
      };
    });

    body.querySelectorAll("button[data-load-rig-sample]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true; b.textContent = "Loading…";
        try {
          await api("/api/samples/load", { name: b.dataset.loadRigSample });
          await refreshState();
          $("#samplesModal").close();
          // only on the sample itself: kept edits to another model (the discard prompt cancelled) stop it here
          if (await selectModel(b.dataset.loadRigSample)) runStep("all");
        } catch (e) {
          alert(e.message);
          b.disabled = false; b.textContent = "Load & Rig All";
        }
      };
    });
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---- Audit QA Report Modal
async function openAuditModal() {
  if (!currentModel) return;
  openModal("#auditModal");
  const body = $("#auditModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading audit results for ${esc(currentModel)}…</div>`;

  try {
    const d = await api("/api/model?name=" + encodeURIComponent(currentModel));
    const a = d.audit;
    if (!a) {
      body.innerHTML = `<div class="empty">No audit yet for <b>${esc(currentModel)}</b>. Rig the model, then run Audit.</div>` +
        `<div style="margin-top:12px;text-align:center"><button class="primary" id="btnRunAuditNow">Run Rig &amp; Audit Now</button></div>`;
      const btn = $("#btnRunAuditNow");
      if (btn) btn.onclick = () => { $("#auditModal").close(); runStep("all"); };
      return;
    }

    const g = a.grade || (a.pass ? "PASS" : "FAIL");
    let html = `<div class="row" style="margin-bottom:12px"><span class="verdict ${gradeClass(g)}">${esc(g)}</span>` +
      `<span class="meta">${esc(a.fbx_rel || a.fbx || "")}</span></div>`;

    html += `<div class="scroll"><table><tr><th>Check</th><th>Value</th><th>Threshold</th><th>Allowance</th><th>Result</th></tr>`;
    for (const c of a.checks) {
      const higher = c.check === "head_pct";
      const cg = c.grade || (c.ok ? "PASS" : "FAIL");
      html += `<tr><td>${esc(CHECKS[c.check] || c.check)}</td>` +
        `<td class="num">${esc(c.value)}</td>` +
        `<td class="num">${higher ? "≥ " : "≤ "}${esc(c.threshold)}</td>` +
        `<td>${c.allowance !== undefined && c.allowance !== null ? (higher ? "≥ " : "≤ ") + esc(c.allowance) + (c.reason ? `<div class="why">${esc(c.reason)}</div>` : "") : "—"}</td>` +
        `<td><span class="chip ${gradeClass(cg)}">${esc(cg)}</span></td></tr>`;
    }
    html += `</table></div>`;

    const t = a.tears;
    if (t && t.by_bone && t.by_bone.length) {
      html += `<h3 style="margin:16px 0 6px">Tears by bone</h3>` +
        `<div class="meta" style="margin-bottom:8px">Each joint bent 40° and twisted 60°. Gaps are marked in the 3D viewport.</div>` +
        `<div class="scroll"><table><tr><th>Bone</th><th>Bend tears</th><th>Gap %</th><th>Twist tears</th><th>Gap %</th></tr>` +
        t.by_bone.slice(0, 10).map((b) => `<tr><td>${esc(b.bone)}</td><td class="num">${esc(b.bend)}</td><td class="num">${esc(b.bend_gap_pct)}</td><td class="num">${esc(b.twist)}</td><td class="num">${esc(b.twist_gap_pct)}</td></tr>`).join("") +
        `</table></div>`;
    }

    if (a.warnings && a.warnings.length) {
      html += `<div class="warns" style="margin-top:12px"><b>Warnings:</b> ${a.warnings.map(esc).join("; ")}</div>`;
    }

    if (a.skin || a.bend) {
      html += `<h3 style="margin:16px 0 6px">QA Diagnostic Sheets</h3><div class="pics">` +
        (a.skin ? `<figure><img src="${esc(fileUrl(a.skin))}" alt="Skin weights map"><figcaption>Skin assignment</figcaption></figure>` : "") +
        (a.bend ? `<figure><img src="${esc(fileUrl(a.bend))}" alt="Bend test result"><figcaption>Bend test</figcaption></figure>` : "") +
        `</div>`;
    }

    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---- Collection Table Modal
async function openCollectionTable() {
  openModal("#collectionModal");
  const body = $("#collectionModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading collection audits…</div>`;

  try {
    const d = await api("/api/audits");
    const f = (v) => v === null || v === undefined ? "–" : esc(v);
    let html = `<div class="row" style="margin-bottom:10px; gap:8px">` +
      `<button class="small" id="colRigAll">Rig all</button>` +
      `<button class="small" id="colAuditAll">Audit all</button>` +
      `<button class="small" id="colAuditFailed">Audit failed only</button>` +
      `</div>`;

    if (!d.models || !d.models.length) {
      html += `<div class="empty">No models in workspace.</div>`;
    } else {
      html += `<div class="scroll"><table><tr><th>Model</th><th>Grade</th><th>Tears comb</th><th>Tears bend</th><th>Worst gap</th><th>Bleed %</th><th>Worst bone</th></tr>`;
      for (const r of d.models) {
        html += `<tr class="pick" data-pick-model="${esc(r.model)}">` +
          `<td><b>${esc(r.model)}</b><div class="why">${esc(r.group || "")}</div></td>` +
          `<td>${r.grade ? `<span class="chip ${gradeClass(r.grade)}">${esc(r.grade)}</span>` : `<span class="chip">${esc(r.status || "not audited")}</span>`}</td>` +
          `<td class="num">${f(r.combined_tears)}</td><td class="num">${f(r.bend_tears)}</td>` +
          `<td class="num">${f(r.worst_gap_pct)}</td><td class="num">${f(r.bleed_pct)}</td>` +
          `<td>${f(r.worst_bone)}</td></tr>`;
      }
      html += `</table></div>`;
    }

    body.innerHTML = html;
    body.querySelectorAll("tr[data-pick-model]").forEach((tr) => {
      tr.onclick = () => {
        $("#collectionModal").close();
        selectModel(tr.dataset.pickModel);
      };
    });

    const bRigAll = $("#colRigAll");
    if (bRigAll) bRigAll.onclick = () => { $("#collectionModal").close(); handleAction("batch-rig-all"); };
    const bAudAll = $("#colAuditAll");
    if (bAudAll) bAudAll.onclick = () => { $("#collectionModal").close(); handleAction("batch-audit-all"); };
    const bAudFail = $("#colAuditFailed");
    if (bAudFail) bAudFail.onclick = () => { $("#collectionModal").close(); handleAction("batch-audit-failed"); };
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---- Engine Export Presets Modal
function openExportModal(targetPreset = "all") {
  if (!currentModel) {
    alert("Select a model first to export.");
    return;
  }
  openModal("#exportModal");
  const titleEl = $("#exportModalTitle");
  if (titleEl) titleEl.textContent = `Export Game Engine Packages: ${currentModel}`;

  const body = $("#exportModalBody");
  if (!body) return;

  const presets = [
    { id: "unreal", name: "Unreal Engine 4 / 5", ext: "FBX", desc: "Budgeted skeletal mesh and clips FBX (Convert Scene turns it Z-up on import), UE Mannequin bone mapping, import settings notes and guide." },
    { id: "unity", name: "Unity (Mecanim)", ext: "FBX (Y-up)", desc: "Budgeted mesh and clips FBX written so the roots import with rotation 0 and scale 1, Humanoid bone-slot notes, and import walkthrough." },
    { id: "godot", name: "Godot 4.x", ext: "FBX (4.3+)", desc: "Budgeted mesh and clips FBX, Godot .import settings, and sample GDScript character controller." },
    { id: "web", name: "Web / glTF", ext: "GLB (HTML5)", desc: "The viewer's GLB (full resolution, every clip; gltfpack-compressed and checked by the glTF-Validator when they are installed), web manifest, and interactive HTML 3D previewer." },
  ];

  let html = `<div class="sample-grid">`;
  for (const p of presets) {
    html += `<div class="sample-card"><div class="row"><h3 style="margin:0">${esc(p.name)}</h3><span class="chip on">${esc(p.ext)}</span></div>` +
      `<div class="why" style="margin:8px 0">${esc(p.desc)}</div>` +
      `<div class="actions"><button class="primary" data-dl-export="${p.id}">Download ${esc(p.name.split(" ")[0])} Pack (.zip)</button></div></div>`;
  }
  html += `</div>` +
    `<div class="row" style="margin-top:16px; justify-content:space-between; align-items:center; background:var(--panel2); padding:10px 14px; border-radius:8px">` +
    `<div><b>All Engine Presets Bundle</b><div class="why">Includes all 4 target engine configurations in one archive</div></div>` +
    `<button class="primary" data-dl-export="all">Download All Bundles (.zip)</button>` +
    `</div>` +
    `<div id="exportStatusMsg" class="msg" style="margin-top:12px"></div>`;

  body.innerHTML = html;

  body.querySelectorAll("button[data-dl-export]").forEach((btn) => {
    btn.onclick = async () => {
      const target = btn.dataset.dlExport;
      btn.disabled = true; const origText = btn.textContent; btn.textContent = "Packaging…";
      const statusEl = $("#exportStatusMsg");
      if (statusEl) { statusEl.className = "msg"; statusEl.textContent = `Packaging ${target} export…`; }

      try {
        const res = await api("/api/export", { model: currentModel, target });
        btn.disabled = false; btn.textContent = origText;
        if (statusEl) {
          statusEl.className = "msg ok";
          statusEl.innerHTML = `Exported <b>${esc(res.preset_name || target)}</b> package (${Math.round(res.zip_size / 1024)} KB). ` +
            `<a href="${esc(fileUrl('/files/work/' + res.zip_rel))}" download style="color:var(--pass);font-weight:bold;text-decoration:underline">Download again if not started automatically ↗</a>` +
            exportPassesHtml(res.passes || []);
        }
        const a = document.createElement("a");
        a.href = fileUrl("/files/work/" + res.zip_rel);
        a.download = res.zip_rel.split("/").pop();
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (e) {
        btn.disabled = false; btn.textContent = origText;
        if (statusEl) { statusEl.className = "msg err"; statusEl.textContent = e.message; }
        else alert(e.message);
      }
    };
  });
}

// The export passes (core/export_passes.py): Unity FBX axes, gltfpack, glTF-Validator; a skipped one says why
function exportPassesHtml(passes) {
  if (!passes.length) return "";
  const rows = passes.map((p) => `<li><b>${esc(p.pass)}</b> ${esc(p.status)}: ` +
    `${esc((p.target ? p.target + "/" : "") + p.file)}${p.detail ? " (" + esc(p.detail) + ")" : ""}</li>`).join("");
  return `<ul class="meta" style="margin:8px 0 0 18px;padding:0">${rows}</ul>`;
}

// ---- File > Clean Up Generated Files… (core/clean_generated.py): delete what the tool can rebuild
const fmtBytes = (n) => (n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB");

async function openCleanGenerated() {
  openModal("#cleanModal");
  const body = $("#cleanModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Measuring the generated files…</div>`;
  let scan;
  try {
    scan = await api("/api/clean/scan");
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  const rows = scan.kinds.map((k) => `
    <tr>
      <td style="width:1.5em"><input type="checkbox" data-kind="${esc(k.key)}" ${k.default && k.files ? "checked" : ""} ${k.files ? "" : "disabled"}></td>
      <td><b>${esc(k.label)}</b><div class="meta">${esc(k.note)}</div></td>
      <td class="num" style="white-space:nowrap">${k.files ? fmtBytes(k.bytes) : "—"}</td>
      <td class="num" style="white-space:nowrap">${k.files} file${k.files === 1 ? "" : "s"}</td>
    </tr>`).join("");
  body.innerHTML = `
    <div class="meta" style="margin-bottom:10px">Work folder <code>${esc(scan.work)}</code>. Models, textures, rig.json, cards, rigs and clips are never touched.</div>
    <table style="margin-bottom:12px">${rows}</table>
    <div class="row" style="justify-content:flex-end;gap:8px">
      <span id="cleanResult" class="meta" style="margin-right:auto"></span>
      <button type="button" class="small close-clean">Cancel</button>
      <button type="button" class="small primary" id="cleanGo">Delete</button>
    </div>`;
  const go = $("#cleanGo");
  const result = $("#cleanResult");
  const update = () => {
    const picked = scan.kinds.filter((k) => body.querySelector(`input[data-kind="${k.key}"]`).checked);
    const total = picked.reduce((a, k) => a + k.bytes, 0);
    go.disabled = !picked.length;
    go.textContent = picked.length ? `Delete ${fmtBytes(total)}` : "Delete";
  };
  body.querySelectorAll("input[data-kind]").forEach((c) => (c.onchange = update));
  update();
  body.querySelector(".close-clean").onclick = () => $("#cleanModal").close();
  go.onclick = async () => {
    const kinds = [...body.querySelectorAll("input[data-kind]:checked")].map((c) => c.dataset.kind);
    if (!kinds.length) return;
    go.disabled = true;
    result.textContent = "Deleting…";
    try {
      const r = await api("/api/clean", { kinds });
      const files = Object.values(r.deleted).reduce((a, d) => a + d.files, 0);
      const bytes = Object.values(r.deleted).reduce((a, d) => a + d.bytes, 0);
      result.textContent = `Deleted ${files} file${files === 1 ? "" : "s"}, ${fmtBytes(bytes)} freed.` +
        (r.failed.length ? ` ${r.failed.length} couldn't be deleted: ${r.failed[0]}` : "");
      go.textContent = "Done";
      await refreshState();
    } catch (e) {
      result.textContent = e.message;
      go.disabled = false;
    }
  };
}

// ---- File > Commit to Git Repo… (core/git_commit.py): commit the models folder, push, open a pull request
async function openGitCommit() {
  openModal("#gitModal");
  const body = $("#gitModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Looking at the models folder's git repo…</div>`;
  let st;
  try {
    st = await api("/api/git/status");
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  if (!st.repo) {
    body.innerHTML = `<div class="empty">${esc(st.error || "The models folder is not in a git repo.")}</div>`;
    return;
  }
  const where = st.restart
    ? `a new branch from the latest <b>${esc(st.base)}</b> (the pull request for <b>${esc(st.branch)}</b> is ${esc(String(st.pr.state).toLowerCase())})`
    : st.new_branch
      ? `a new branch from <b>${esc(st.branch || "HEAD")}</b>, then a pull request into <b>${esc(st.base)}</b>`
      : `branch <b>${esc(st.branch)}</b>` + (st.pr && st.pr.state === "OPEN"
        ? `, whose open <a href="${esc(st.pr.url)}" target="_blank" rel="noopener">pull request</a> picks it up`
        : `, then a pull request into <b>${esc(st.base)}</b>`);
  const models = st.models || [];
  const named = models.slice(0, 3).join(", ") + (models.length > 3 ? ` and ${models.length - 3} more` : "");
  const suggested = models.length ? `Update ${named}` : (st.count ? `Update ${st.count} file${st.count === 1 ? "" : "s"}` : "");
  const files = (st.changes || []).slice(0, 40).map((c) =>
    `<tr><td class="num" style="width:2.5em"><code>${esc(c.code)}</code></td><td style="word-break:break-all">${esc(c.path)}</td></tr>`).join("");
  const more = st.count > 40 ? `<div class="meta">…and ${st.count - 40} more</div>` : "";
  const canRun = st.count > 0 || (!st.new_branch && st.unpushed > 0);
  body.innerHTML = `
    <div class="meta" style="margin-bottom:6px">Repo <code>${esc(st.repo)}</code>${st.github ? ` (${esc(st.github)})` : ""}</div>
    <div class="meta" style="margin-bottom:10px">Commits everything changed under <code>${esc(st.root)}</code> to ${where}.
      ${st.gh ? "" : "The GitHub CLI (gh) isn't installed here, so you'll get a link to open the pull request yourself."}</div>
    ${st.count
      ? `<div style="margin-bottom:6px"><b>${st.count}</b> changed file${st.count === 1 ? "" : "s"}${models.length ? ` in ${models.length} model${models.length === 1 ? "" : "s"}` : ""}</div>
         <div style="max-height:220px;overflow:auto;border:1px solid var(--line);border-radius:6px;margin-bottom:12px"><table>${files}</table></div>${more}`
      : `<div class="empty" style="margin-bottom:12px">Nothing has changed under the models folder${st.unpushed ? `, but ${st.unpushed} commit${st.unpushed === 1 ? " isn't" : "s aren't"} pushed yet` : ""}.</div>`}
    <label class="meta" for="gitMessage">Commit message (also the pull request's title)</label>
    <input id="gitMessage" type="text" style="width:100%;margin:4px 0 12px" value="${esc(suggested)}">
    <div class="row" style="justify-content:flex-end;gap:8px">
      <span id="gitResult" class="meta" style="margin-right:auto"></span>
      <button type="button" class="small close-git">Cancel</button>
      <button type="button" class="small primary" id="gitGo" ${canRun ? "" : "disabled"}>Commit, push &amp; open PR</button>
    </div>`;
  body.querySelector(".close-git").onclick = () => $("#gitModal").close();
  const go = $("#gitGo");
  go.onclick = async () => {
    const message = $("#gitMessage").value.trim();
    if (!message) { $("#gitMessage").focus(); return; }
    go.disabled = true;
    const result = $("#gitResult");
    result.textContent = "Committing and pushing… (the log shows each git step)";
    try {
      const j = await api("/api/git/commit", { message });
      followJob(j);
      watchGitJob(j.id, result);
    } catch (e) {
      result.textContent = e.message;
      go.disabled = false;
    }
  };
}

async function watchGitJob(id, result) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    let d;
    try { d = await api(`/api/jobs/${id}`); } catch (e) { continue; }
    if (d.state === "queued" || d.state === "running") {
      result.textContent = d.state === "queued" ? "Waiting for the running step to finish…" : "Committing and pushing…";
      continue;
    }
    const log = d.log || [];
    const pr = log.map((l) => (l.match(/^PR: (\S+)/) || [])[1]).filter(Boolean).pop();
    const nothing = log.some((l) => l.startsWith("Nothing to commit"));
    if (d.state === "done" && pr) {
      result.innerHTML = `Done: <a href="${esc(pr)}" target="_blank" rel="noopener">${esc(pr)}</a>`;
    } else if (d.state === "done") {
      result.textContent = nothing ? "Nothing to commit." : "Done.";
    } else {
      result.textContent = `It ${d.state === "cancelled" ? "was cancelled" : "failed"}: the log says why.`;
    }
    return;
  }
}

// ---- Mesh Doctor Modal
async function openMeshDoctor() {
  openModal("#doctorModal");
  const body = $("#doctorModalBody");
  if (!body) return;
  if (!currentModel) {
    body.innerHTML = `<div class="empty">Select a model first. Mesh Doctor reports on that model's source file and does not change it.</div>`;
    return;
  }
  body.innerHTML = `<div class="empty">Checking ${esc(currentModel)}… An OBJ is read here. FBX and glTF open in Blender, which can take a minute.</div>`;

  try {
    const diag = await api("/api/doctor?model=" + encodeURIComponent(currentModel));
    if (diag.grade === "UNCHECKED") {
      body.innerHTML = `<div class="row" style="margin-bottom:12px"><span class="chip" style="font-size:14px;padding:3px 10px">UNCHECKED</span>` +
        `<span class="meta">${esc(diag.file || "")}</span></div><div class="why">${esc(diag.note || "")}</div>`;
      return;
    }
    const cell = (v) => (v == null || v === "" ? "—" : esc(v));
    const gClass = diag.grade === "HEALTHY" ? "pass" : (diag.grade === "WARN" ? "check" : "fail");
    const verdict = diag.health_score === 100
      ? `Mesh is fully manifold and clean. Automatic bone heat skinning will bind accurately.`
      : `Defects can make the Rig step fall back to voxel-proxy skinning.`;
    body.innerHTML = `
      <div class="row" style="margin-bottom:12px"><span class="chip ${gClass}" style="font-size:14px;padding:3px 10px">${esc(diag.grade)}</span>` +
      `<h3 style="margin:0;font-size:18px">Health Score: ${cell(diag.health_score)}/100</h3><span class="meta">${esc(diag.file || "")}</span></div>` +
      `<div class="meta" style="margin-bottom:12px">${cell(diag.verts)} vertices · ${cell(diag.faces)} faces</div>` +
      `<table style="margin-top:8px">` +
      `<tr><td>Non-manifold edges (must be 0 for voxel proxy)</td><td class="num"><b>${cell(diag.non_manifold_edges)}</b></td></tr>` +
      `<tr><td>Degenerate / zero-area faces</td><td class="num"><b>${cell(diag.degenerate_faces)}</b></td></tr>` +
      `<tr><td>Loose isolated vertices</td><td class="num"><b>${cell(diag.loose_verts)}</b></td></tr>` +
      `<tr><td>Duplicate coincident vertices</td><td class="num"><b>${cell(diag.duplicate_verts)}</b></td></tr>` +
      `</table>` +
      (diag.note ? `<div class="why" style="margin-top:8px">${esc(diag.note)}</div>` : "") +
      `<div class="why" style="margin-top:14px">${verdict}</div>` +
      `<div class="row" style="margin-top:14px;gap:8px">` +
      `<button type="button" id="btnDoctorRepair" title="PyMeshLab: duplicates, non-manifold edges and vertices, small holes; UVs kept">Repair a copy</button>` +
      `<button type="button" id="btnDoctorWatertight" title="PyMeshFix closes every shell, manifold3d joins them; the colour is baked back">Make a watertight copy</button>` +
      `</div><div class="meta" style="margin-top:6px">Both write a copy to work/doctor and leave the source as it is. They need the optional extras (pymeshlab, pymeshfix, manifold3d, xatlas).</div>`;
    const go = (step) => { $("#doctorModal").close(); runStep(step); };
    $("#btnDoctorRepair").onclick = () => go("doctor-repair");
    $("#btnDoctorWatertight").onclick = () => go("doctor-watertight");
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---- Facing Views Modal
async function openFacingViews() {
  if (!currentModel) return;
  openModal("#facingModal");
  const body = $("#facingModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading facing renders for ${esc(currentModel)}…</div>`;

  try {
    const d = await api("/api/model?name=" + encodeURIComponent(currentModel));
    if (!d.facing || !d.facing.length) {
      body.innerHTML = `<div class="empty">No facing renders yet. Run <b>Survey</b> first to generate the 4 orthographic views.</div>` +
        `<div style="text-align:center;margin-top:12px"><button class="primary" id="btnRunSurveyNow">Run Survey Now</button></div>`;
      const btn = $("#btnRunSurveyNow");
      if (btn) btn.onclick = () => { $("#facingModal").close(); runStep("survey"); };
      return;
    }

    body.innerHTML = `<div class="meta" style="margin-bottom:10px">Read <code>forward</code> off the view showing the front face/nose:</div>` +
      `<div class="pics">${d.facing.map((u) => {
        const label = u.split("__").pop().replace(".png", "").replace("m", "-").replace("p", "+") + " view";
        return `<figure><img src="${esc(fileUrl(u))}" alt="${esc(label)}"><figcaption>${esc(label)}</figcaption></figure>`;
      }).join("")}</div>`;
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---- Model Details Modal
async function openModelDetails() {
  if (!currentModel) return;
  openModal("#detailsModal");
  const body = $("#detailsModalBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading metadata…</div>`;

  try {
    const d = await api("/api/model?name=" + encodeURIComponent(currentModel));
    let html = `<div class="meta" style="margin-bottom:10px"><b>Directory:</b> ${esc(d.dir)}</div>`;

    if (d.prepare) {
      const p = d.prepare;
      const uv = (p.uv && p.uv.note) || "";
      const joints = (p.joints && p.joints.note) || "";
      html += `<h3>Prepare</h3><table class="kv">` +
        `<tr><td>Copy</td><td>${esc(p.output || "")}</td></tr>` +
        `<tr><td>Turned</td><td>${esc(p.turned_deg)}° (${esc(p.forward_how || "")})</td></tr>` +
        `<tr><td>Origin</td><td>${esc(p.origin || "")}</td></tr>` +
        `<tr><td>UVs</td><td>${esc(uv)}</td></tr>` +
        `<tr><td>Joints</td><td>${esc(joints)}</td></tr>` +
        `</table>`;
    }

    if (d.survey) {
      html += `<h3>Survey Information</h3><table class="kv">${Object.entries(d.survey).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v) ? v.join(" × ") : v)}</td></tr>`).join("")}</table>`;
    }

    if (d.notes && Object.keys(d.notes).length) {
      html += `<h3 style="margin-top:14px">Notes</h3><table class="kv">${Object.entries(d.notes).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
    }

    if (d.card) {
      html += `<h3 style="margin-top:14px">Model Card (model.json)</h3><pre class="log" style="height:auto;max-height:220px">${esc(JSON.stringify(d.card, null, 2))}</pre>`;
    }

    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Root Working Folder & Model Management
// ---------------------------------------------------------------------------------------------------------------

let rootsData = null;

async function openRootModal() {
  openModal("#rootModal");
  const activeRootDisplay = $("#activeRootDisplay");
  const activeWorkDisplay = $("#activeWorkDisplay");
  const activeRootStats = $("#activeRootStats");
  const suggestedRootsList = $("#suggestedRootsList");
  const msgEl = $("#rootSwitchMsg");
  if (msgEl) { msgEl.className = "msg"; msgEl.textContent = ""; }

  if (activeRootDisplay && state) {
    activeRootDisplay.textContent = state.root || "None";
  }
  if (activeWorkDisplay && state) {
    activeWorkDisplay.textContent = `Work Folder: ${state.work || "Default (_autorig)"}`;
  }
  if (activeRootStats && state && state.models) {
    activeRootStats.textContent = `Active Root contains ${state.models.length} model(s)`;
  }

  if (suggestedRootsList) {
    suggestedRootsList.innerHTML = `<div class="empty">Scanning available roots & packs…</div>`;
  }

  try {
    rootsData = await api("/api/workspace/roots");
    if (!suggestedRootsList) return;
    const roots = (rootsData && rootsData.roots) || [];
    const kinds = { group: "group", collection: "collection", pack: "pack", recent: "recent", samples: "samples" };
    if (!roots.length) {
      suggestedRootsList.innerHTML = `<div class="empty">No suggested roots discovered. Enter a custom folder below.</div>`;
      return;
    }

    let html = "";
    for (const r of roots) {
      const isCur = r.current;
      const countLabel = `${r.models} model${r.models === 1 ? "" : "s"}`;
      const kindBadge = kinds[r.kind] ? `<span class="group-chip">${esc(kinds[r.kind])}</span>` : "";
      const curBadge = isCur ? `<span class="chip pass" style="font-size:10px;">Active</span>` : "";

      html += `
        <div class="suggested-root-card ${isCur ? "current" : ""}">
          <div style="flex:1; min-width:0;">
            <div style="display:flex; align-items:center; gap:6px; margin-bottom:2px;">
              <span style="font-weight:600; font-size:13px; color:var(--ink);">${esc(r.name)}</span>
              ${curBadge}
              ${kindBadge}
              <span style="font-size:11.5px; color:var(--muted);">${countLabel}</span>
            </div>
            <div style="font-size:11px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(r.path)}">${esc(r.path)}</div>
          </div>
          <div>
            ${isCur 
              ? `<button type="button" class="small" disabled style="opacity:0.6;">Current</button>`
              : `<button type="button" class="small primary" data-switch-root="${esc(r.path)}">Select</button>`
            }
          </div>
        </div>
      `;
    }
    suggestedRootsList.innerHTML = html;

    suggestedRootsList.querySelectorAll("button[data-switch-root]").forEach((btn) => {
      btn.onclick = () => {
        const p = btn.dataset.switchRoot;
        switchRoot(p);
      };
    });
  } catch (err) {
    if (suggestedRootsList) {
      suggestedRootsList.innerHTML = `<div class="msg err">${esc(err.message)}</div>`;
    }
  }
}

async function switchRoot(newRoot, newWork = null) {
  if (!newRoot) return;
  const msgEl = $("#rootSwitchMsg");
  if (msgEl) {
    msgEl.className = "msg";
    msgEl.textContent = `Switching root to "${newRoot}"…`;
  }
  try {
    const payload = { root: newRoot };
    if (newWork) payload.work = newWork;
    const res = await api("/api/workspace/set-root", payload);
    if (msgEl) {
      msgEl.className = "msg ok";
      msgEl.textContent = `Switched to ${res.name} (${res.models} model${res.models === 1 ? "" : "s"}).`;
    }
    currentGroupFilter = "all";
    currentModelSearch = "";
    selectedModelNames.clear();
    await refreshState();
    const allModels = (state && state.models) || [];
    if (allModels.length > 0) {
      await selectModel(allModels[0].name, { force: true });
    } else {
      currentModel = "";
      updateModelBadge();
    }
    setTimeout(() => {
      const dlg = $("#rootModal");
      if (dlg && dlg.open) dlg.close();
    }, 450);
  } catch (e) {
    if (msgEl) {
      msgEl.className = "msg err";
      msgEl.textContent = e.message;
    } else {
      alert("Failed to switch root: " + e.message);
    }
  }
}

function openModelManagerModal() {
  selectedModelNames.clear();
  openModal("#modelManagerModal");
  renderModelManager();
}

function getFilteredMgrModels() {
  const allModels = (state && state.models) || [];
  const searchInput = $("#mgrSearchInput");
  const search = searchInput ? searchInput.value.trim() : "";
  const groupSel = $("#mgrGroupFilter");
  const group = groupSel ? groupSel.value : "all";
  const statusSel = $("#mgrStatusFilter");
  const status = statusSel ? statusSel.value : "all";

  let filtered = allModels.filter((m) => matchesFilter(m, status, group, search));

  const sort = mgrCurrentSort;
  const gradeRank = { FAIL: 0, CHECK: 1, NOSPEC: 2, UNRIGGED: 3, RIGGED: 4, PASS: 5 };

  filtered.sort((a, b) => {
    if (sort === "name_desc") return b.name.localeCompare(a.name);
    if (sort === "group") {
      const ga = a.group || "";
      const gb = b.group || "";
      const c = ga.localeCompare(gb);
      return c !== 0 ? c : a.name.localeCompare(b.name);
    }
    if (sort === "audit_worst" || sort === "audit_best") {
      const getRank = (m) => {
        const aud = (m.audit || "").toUpperCase();
        if (aud === "FAIL") return gradeRank.FAIL;
        if (aud === "CHECK") return gradeRank.CHECK;
        if (aud === "PASS") return gradeRank.PASS;
        if (m.rigged) return gradeRank.RIGGED;
        if (m.spec) return gradeRank.UNRIGGED;
        return gradeRank.NOSPEC;
      };
      const ra = getRank(a);
      const rb = getRank(b);
      if (ra !== rb) {
        return sort === "audit_worst" ? ra - rb : rb - ra;
      }
      return a.name.localeCompare(b.name);
    }
    return a.name.localeCompare(b.name);
  });

  return filtered;
}

function updateMgrBatchBar(filtered = null) {
  if (!filtered) filtered = getFilteredMgrModels();
  const batchBar = $("#mgrBatchBar");
  const selectedCountEl = $("#mgrSelectedCount");
  const selectAllChk = $("#mgrSelectAll");

  const count = selectedModelNames.size;
  if (batchBar) {
    batchBar.style.display = count > 0 ? "flex" : "none";
  }
  if (selectedCountEl) {
    selectedCountEl.textContent = `${count} model${count === 1 ? "" : "s"} selected`;
  }
  if (selectAllChk) {
    selectAllChk.checked = filtered.length > 0 && filtered.every((m) => selectedModelNames.has(m.name));
    selectAllChk.indeterminate = count > 0 && !selectAllChk.checked && filtered.some((m) => selectedModelNames.has(m.name));
  }
}

function renderMgrTableRows(filtered) {
  const tbody = $("#mgrTableBody");
  if (!tbody) return;

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--muted);">No models match the filter criteria.</td></tr>`;
    return;
  }

  let html = "";
  for (const m of filtered) {
    const isSelected = selectedModelNames.has(m.name);
    const isCurrent = m.name === currentModel;
    const audit = (m.audit || "").toUpperCase();
    const statusLabel = audit || (m.rigged ? "RIGGED" : (m.spec ? "SPEC" : "NEW"));
    const statusClass = gradeClass(audit) || (m.rigged ? "on" : "");

    const trisStr = m.budget ? `${esc(m.budget)} tris` : "—";
    const ext = (m.source_ext || "").replace(".", "").toUpperCase() || "—";
    const hasTex = m.has_textures;

    html += `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.06); ${isCurrent ? "background:rgba(67,120,255,0.08);" : ""}" data-model="${esc(m.name)}">
        <td style="text-align:center; padding:8px 4px;">
          <input type="checkbox" class="mgr-row-chk" data-model="${esc(m.name)}" ${isSelected ? "checked" : ""}>
        </td>
        <td style="padding:8px 6px;">
          <span class="chip ${statusClass}" style="font-size:10.5px;">${esc(statusLabel)}</span>
        </td>
        <td style="padding:8px 6px;">
          <span class="mgr-model-title" data-open-model="${esc(m.name)}" style="font-weight:600; cursor:pointer; color:${isCurrent ? "var(--accent)" : "var(--ink)"};" title="Click to open in 3D Viewport">
            ${esc(m.name)} ${isCurrent ? '<span style="font-size:10px; opacity:0.75;">(Active)</span>' : ""}
          </span>
        </td>
        <td style="padding:8px 6px;">
          <span class="group-chip" title="Group folder">${esc(m.group || "Root")}</span>
        </td>
        <td style="padding:8px 6px;">
          <span class="format-chip">${esc(ext)}</span>
          ${hasTex ? '<span class="chip" style="font-size:10px; padding:1px 4px; margin-left:3px;" title="Contains texture image maps">Tex</span>' : ""}
        </td>
        <td style="padding:8px 6px; font-size:12px; color:var(--muted);">${trisStr}</td>
        <td style="padding:8px 6px; text-align:right; white-space:nowrap;">
          <button type="button" class="small" data-mgr-open="${esc(m.name)}" title="Open in 3D Viewport">Open</button>
          <button type="button" class="small" data-mgr-move="${esc(m.name)}" title="Move to another group">Move</button>
          <button type="button" class="small" data-mgr-rename="${esc(m.name)}" title="Rename model">Rename</button>
          <button type="button" class="small" data-mgr-dup="${esc(m.name)}" title="Duplicate model">Duplicate</button>
          <button type="button" class="small" data-mgr-del="${esc(m.name)}" title="Delete model" style="color:var(--fail);">🗑️</button>
        </td>
      </tr>
    `;
  }

  tbody.innerHTML = html;

  // Wire row checkboxes
  tbody.querySelectorAll(".mgr-row-chk").forEach((chk) => {
    chk.onchange = (e) => {
      const name = chk.dataset.model;
      if (e.target.checked) selectedModelNames.add(name);
      else selectedModelNames.delete(name);
      updateMgrBatchBar(filtered);
    };
  });

  // Wire row clicks
  tbody.querySelectorAll("[data-open-model], [data-mgr-open]").forEach((el) => {
    el.onclick = () => {
      const name = el.dataset.openModel || el.dataset.mgrOpen;
      selectModel(name);
      $("#modelManagerModal").close();
    };
  });

  tbody.querySelectorAll("[data-mgr-move]").forEach((btn) => {
    btn.onclick = () => promptMoveModel(btn.dataset.mgrMove);
  });

  tbody.querySelectorAll("[data-mgr-rename]").forEach((btn) => {
    btn.onclick = () => promptRenameModel(btn.dataset.mgrRename);
  });

  tbody.querySelectorAll("[data-mgr-dup]").forEach((btn) => {
    btn.onclick = () => promptDuplicateModel(btn.dataset.mgrDup);
  });

  tbody.querySelectorAll("[data-mgr-del]").forEach((btn) => {
    btn.onclick = () => deleteSpecificModel(btn.dataset.mgrDel);
  });
}

function renderModelManager() {
  const allModels = (state && state.models) || [];
  const groups = new Set((state && state.groups) || []);       // empty groups too: a move can go there
  for (const m of allModels) {
    if (m.group) groups.add(m.group);
  }
  const groupList = Array.from(groups).sort();

  // Update group filter dropdown in modal
  const grpFilter = $("#mgrGroupFilter");
  if (grpFilter) {
    const curVal = grpFilter.value;
    let html = `<option value="all">📂 All Groups (${allModels.length})</option>`;
    html += `<option value="(root)" ${curVal === "(root)" ? "selected" : ""}>📁 (Root / Direct)</option>`;
    for (const g of groupList) {
      html += `<option value="${esc(g)}" ${curVal === g ? "selected" : ""}>📁 ${esc(g)}</option>`;
    }
    grpFilter.innerHTML = html;
  }

  // Update batch target group dropdown
  const batchGrpTarget = $("#mgrBatchGroupTarget");
  if (batchGrpTarget) {
    let html = `<option value="">📁 Root Folder</option>`;
    for (const g of groupList) {
      html += `<option value="${esc(g)}">📁 ${esc(g)}</option>`;
    }
    batchGrpTarget.innerHTML = html;
  }

  const filtered = getFilteredMgrModels();

  // Update badge stats
  const statsBadge = $("#mgrStatsBadge");
  if (statsBadge) {
    statsBadge.textContent = `${filtered.length} of ${allModels.length} models`;
  }

  updateMgrBatchBar(filtered);
  renderMgrTableRows(filtered);
}

function promptMoveModel(modelName) {
  if (!modelName) return;
  const m = state && state.models && state.models.find((x) => x.name === modelName);
  const curGroup = m ? (m.group || "") : "";

  const nameEl = $("#moveModelTargetName");
  if (nameEl) {
    nameEl.textContent = modelName;
    nameEl.dataset.modelName = modelName;
  }

  const allModels = (state && state.models) || [];
  const groups = new Set((state && state.groups) || []);
  for (const x of allModels) {
    if (x.group) groups.add(x.group);
  }

  const grpSel = $("#moveTargetGroupSelect");
  if (grpSel) {
    let html = `<option value="" ${curGroup === "" ? "selected" : ""}>📁 Root Folder (Direct)</option>`;
    for (const g of Array.from(groups).sort()) {
      html += `<option value="${esc(g)}" ${curGroup === g ? "selected" : ""}>📁 ${esc(g)}</option>`;
    }
    grpSel.innerHTML = html;
  }

  const newGrpInput = $("#moveNewGroupNameInput");
  if (newGrpInput) newGrpInput.value = "";

  openModal("#moveModelModal");
}

function promptRenameModel(modelName) {
  if (!modelName) return;
  const input = $("#renameModelInput");
  if (input) {
    input.value = modelName;
    input.dataset.oldName = modelName;
  }
  openModal("#renameModelModal");
  setTimeout(() => { if (input) { input.focus(); input.select(); } }, 50);
}

async function promptDuplicateModel(modelName) {
  if (!modelName) return;
  const defName = modelName + "_copy";
  const newName = prompt(`Copy "${modelName}" (its source and spec, not its rig or clips) as:`, defName);
  if (!newName || newName.trim() === "" || newName.trim() === modelName) return;
  try {
    const res = await api("/api/model/duplicate", { model: modelName, new_name: newName.trim() });
    await refreshState();
    await selectModel(res.name);
    if ($("#modelManagerModal") && $("#modelManagerModal").open) {
      renderModelManager();
    }
  } catch (e) {
    alert("Error duplicating model: " + e.message);
  }
}

function promptNewGroup() {
  const input = $("#newGroupNameInput");
  if (input) input.value = "";
  openModal("#newGroupModal");
  setTimeout(() => { if (input) input.focus(); }, 50);
}

function setupModelManagement() {
  const grpSel = $("#groupSelect");
  if (grpSel) {
    grpSel.onchange = () => {
      currentGroupFilter = grpSel.value;
      applyModelFilters();
    };
  }
  const searchInput = $("#modelSearchInput");
  if (searchInput) {
    searchInput.oninput = () => {
      currentModelSearch = searchInput.value.trim();
      applyModelFilters();
    };
  }
  const clearBtn = $("#clearModelSearch");
  if (clearBtn) {
    clearBtn.onclick = () => {
      currentModelSearch = "";
      if (searchInput) searchInput.value = "";
      applyModelFilters();
    };
  }

  // Toolbar buttons
  const btnSwitchRoot = $("#btnSwitchRoot");
  if (btnSwitchRoot) {
    btnSwitchRoot.onclick = () => openRootModal();
  }

  const btnOpenModelManager = $("#btnOpenModelManager");
  if (btnOpenModelManager) {
    btnOpenModelManager.onclick = () => openModelManagerModal();
  }

  // Root modal controls
  const btnOpenRoot = $("#btnOpenRootInExplorer");
  if (btnOpenRoot) {
    btnOpenRoot.onclick = () => {
      api("/api/open-root", {}).catch((e) => alert(e.message));
    };
  }

  const btnApply = $("#btnApplyCustomRoot");
  const customRootInput = $("#customRootInput");
  const customWorkInput = $("#customWorkInput");
  if (btnApply && customRootInput) {
    const doApply = () => {
      const r = customRootInput.value.trim();
      const w = customWorkInput ? customWorkInput.value.trim() : null;
      if (!r) {
        alert("Please enter a valid path (e.g. /asset-packs, /asset-packs/Heartroot, or C:\\Users\\... )");
        return;
      }
      switchRoot(r, w || null);
    };
    btnApply.onclick = doApply;
    customRootInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        doApply();
      }
    });
  }

  // Model Manager modal controls
  const mgrSearchInput = $("#mgrSearchInput");
  if (mgrSearchInput) {
    mgrSearchInput.oninput = () => renderModelManager();
  }

  const mgrGroupFilter = $("#mgrGroupFilter");
  if (mgrGroupFilter) {
    mgrGroupFilter.onchange = () => renderModelManager();
  }

  const mgrStatusFilter = $("#mgrStatusFilter");
  if (mgrStatusFilter) {
    mgrStatusFilter.onchange = () => renderModelManager();
  }

  const mgrSortBy = $("#mgrSortBy");
  if (mgrSortBy) {
    mgrSortBy.onchange = (e) => {
      mgrCurrentSort = e.target.value;
      renderModelManager();
    };
  }

  const btnMgrNewGroup = $("#btnMgrNewGroup");
  if (btnMgrNewGroup) {
    btnMgrNewGroup.onclick = () => promptNewGroup();
  }

  const btnMgrChangeRoot = $("#btnMgrChangeRoot");
  if (btnMgrChangeRoot) {
    btnMgrChangeRoot.onclick = () => {
      $("#modelManagerModal").close();
      openRootModal();
    };
  }

  const mgrSelectAll = $("#mgrSelectAll");
  if (mgrSelectAll) {
    mgrSelectAll.onchange = (e) => {
      const allFiltered = getFilteredMgrModels();
      if (e.target.checked) {
        allFiltered.forEach((m) => selectedModelNames.add(m.name));
      } else {
        allFiltered.forEach((m) => selectedModelNames.delete(m.name));
      }
      updateMgrBatchBar(allFiltered);
      renderMgrTableRows(allFiltered);
    };
  }

  const btnBatchRig = $("#btnBatchRig");
  if (btnBatchRig) {
    btnBatchRig.onclick = async () => {
      const names = Array.from(selectedModelNames);
      if (!names.length) return;
      if (!confirm(`Rig & Skin ${names.length} selected model(s)?`)) return;
      try {
        const j = await api("/api/batch/rig-selected", { models: names });
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
        $("#modelManagerModal").close();
      } catch (e) {
        alert(e.message);
      }
    };
  }

  const btnBatchAudit = $("#btnBatchAudit");
  if (btnBatchAudit) {
    btnBatchAudit.onclick = async () => {
      const names = Array.from(selectedModelNames);
      if (!names.length) return;
      if (!confirm(`Run QA Audit on ${names.length} selected model(s)?`)) return;
      try {
        const j = await api("/api/batch/audit-selected", { models: names });
        followJob(j);
        setDrawerOpen($("#logDrawer"), true);
        $("#modelManagerModal").close();
      } catch (e) {
        alert(e.message);
      }
    };
  }

  const btnBatchMove = $("#btnBatchMove");
  const mgrBatchGroupTarget = $("#mgrBatchGroupTarget");
  if (btnBatchMove && mgrBatchGroupTarget) {
    btnBatchMove.onclick = async () => {
      const names = Array.from(selectedModelNames);
      if (!names.length) return;
      const targetGroup = mgrBatchGroupTarget.value;
      const groupLabel = targetGroup ? `group "${targetGroup}"` : "root folder";
      if (!confirm(`Move ${names.length} model(s) to ${groupLabel}?`)) return;
      try {
        for (const name of names) {
          await api("/api/model/move", { model: name, group: targetGroup });
        }
        await refreshState();
        selectedModelNames.clear();
        renderModelManager();
      } catch (e) {
        alert("Failed to move models: " + e.message);
      }
    };
  }

  const btnBatchDeselect = $("#btnBatchDeselect");
  if (btnBatchDeselect) {
    btnBatchDeselect.onclick = () => {
      selectedModelNames.clear();
      updateMgrBatchBar();
      renderModelManager();
    };
  }

  // Move Model Modal confirm
  const btnConfirmMoveModel = $("#btnConfirmMoveModel");
  if (btnConfirmMoveModel) {
    btnConfirmMoveModel.onclick = async () => {
      const targetName = $("#moveModelTargetName")?.dataset.modelName;
      const newGroup = $("#moveNewGroupNameInput")?.value.trim();
      const targetGroup = newGroup || $("#moveTargetGroupSelect")?.value;
      if (!targetName) return;
      try {
        await api("/api/model/move", { model: targetName, group: targetGroup });
        await refreshState();
        $("#moveModelModal").close();
        if ($("#modelManagerModal") && $("#modelManagerModal").open) {
          renderModelManager();
        }
      } catch (e) {
        alert("Error moving model: " + e.message);
      }
    };
  }

  // Rename Model Modal confirm
  const btnConfirmRenameModel = $("#btnConfirmRenameModel");
  const renameModelInput = $("#renameModelInput");
  if (btnConfirmRenameModel && renameModelInput) {
    const doRename = async () => {
      const oldName = renameModelInput.dataset.oldName;
      const newName = renameModelInput.value.trim();
      if (!oldName || !newName || oldName === newName) return;
      try {
        await api("/api/model/rename", { model: oldName, new_name: newName });
        selectedModelNames.delete(oldName);
        await refreshState();
        $("#renameModelModal").close();
        if (currentModel === oldName) {
          await selectModel(newName, { force: true });
        }
        if ($("#modelManagerModal") && $("#modelManagerModal").open) {
          renderModelManager();
        }
      } catch (e) {
        alert("Error renaming model: " + e.message);
      }
    };
    btnConfirmRenameModel.onclick = doRename;
    renameModelInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        doRename();
      }
    });
  }

  // New Group Modal confirm
  const btnConfirmCreateGroup = $("#btnConfirmCreateGroup");
  const newGroupNameInput = $("#newGroupNameInput");
  if (btnConfirmCreateGroup && newGroupNameInput) {
    const doCreateGroup = async () => {
      const groupName = newGroupNameInput.value.trim();
      if (!groupName) return;
      try {
        await api("/api/group/create", { group: groupName });
        await refreshState();
        $("#newGroupModal").close();
        if ($("#modelManagerModal") && $("#modelManagerModal").open) {
          renderModelManager();
        }
      } catch (e) {
        alert("Error creating group: " + e.message);
      }
    };
    btnConfirmCreateGroup.onclick = doCreateGroup;
    newGroupNameInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        doCreateGroup();
      }
    });
  }
}

// ---------------------------------------------------------------------------------------------------------------
// File Upload & Drag and Drop
// ---------------------------------------------------------------------------------------------------------------

function setupDragAndDrop() {
  const dropZone = $("#dropZone");
  const pickFiles = $("#pickFiles");
  const pickFolder = $("#pickFolder");
  const fileInput = $("#fileInput");
  const folderInput = $("#folderInput");
  const btnAddPaths = $("#btnAddPaths");

  // Global window drop prevention & modal trigger
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
  });

  window.addEventListener("drop", (e) => {
    e.preventDefault();
    if (!e.target.closest("#dropZone")) {
      openModal("#importModal");
    }
  });

  if (dropZone) {
    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add("over"); };
    dropZone.ondragleave = () => dropZone.classList.remove("over");
    dropZone.ondrop = async (e) => {
      e.preventDefault();
      dropZone.classList.remove("over");
      const out = [];
      const items = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      if (items.length) {
        for (const it of items) await walkEntries(it, "", out);
      } else {
        for (const f of e.dataTransfer.files) out.push({ path: f.name, file: f });
      }
      uploadFiles(out);
    };
  }

  if (pickFiles && fileInput) {
    pickFiles.onclick = () => fileInput.click();
    fileInput.onchange = (e) => {
      uploadFiles([...e.target.files].map((f) => ({ path: f.name, file: f })));
      e.target.value = "";
    };
  }

  if (pickFolder && folderInput) {
    pickFolder.onclick = () => folderInput.click();
    folderInput.onchange = (e) => {
      uploadFiles([...e.target.files].map((f) => ({ path: f.webkitRelativePath || f.name, file: f })));
      e.target.value = "";
    };
  }

  if (btnAddPaths) {
    btnAddPaths.onclick = async () => {
      const paths = ($("#importPaths")?.value || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      const msgEl = $("#importMsg");
      if (!paths.length) {
        if (msgEl) { msgEl.className = "msg err"; msgEl.textContent = "Please provide at least one valid path."; }
        return;
      }
      try {
        if (msgEl) { msgEl.className = "msg"; msgEl.textContent = "Importing model files…"; }
        const res = await api("/api/import", {
          paths,
          group: $("#importGroup")?.value.trim() || "",
          name: $("#importName")?.value.trim() || ""
        });
        if (msgEl) { msgEl.className = "msg ok"; msgEl.textContent = `Successfully added ${res.name} (${res.files.length} files).`; }
        $("#importPaths").value = "";
        $("#importName").value = "";
        await refreshState();
        $("#importModal").close();
        await selectModel(res.name);
      } catch (e) {
        if (msgEl) { msgEl.className = "msg err"; msgEl.textContent = e.message; }
      }
    };
  }
}

async function walkEntries(entry, prefix, out) {
  if (entry.isFile) {
    out.push({ path: prefix + entry.name, file: await new Promise((ok, no) => entry.file(ok, no)) });
    return;
  }
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((ok, no) => reader.readEntries(ok, no));
    if (!batch.length) break;
    for (const e of batch) await walkEntries(e, prefix + entry.name + "/", out);
  }
}

async function uploadFiles(entries) {
  if (!entries.length) return;
  const msgEl = $("#importMsg");
  const top = entries[0].path.split("/");
  const suggest = (n) => n.toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  const stem = (p) => { const b = p.split(/[\\/]/).filter(Boolean).pop() || ""; return b.replace(/\.[^.]+$/, ""); };
  const name = $("#importName")?.value.trim() || suggest(top.length > 1 ? top[0] : stem(entries.find((e) => /\.(fbx|glb|gltf|obj|zip)$/i.test(e.path))?.path || top[0]));
  const batch = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");

  if (msgEl) { msgEl.className = "msg"; msgEl.textContent = `Uploading ${entries.length} file(s) as ${name}…`; }
  try {
    for (const e of entries) {
      const r = await fetch(`/api/upload?batch=${batch}&path=${encodeURIComponent(e.path)}`, {
        method: "POST",
        headers: { "X-Autorig-Token": TOKEN },
        body: e.file
      });
      if (!r.ok) throw new Error((await r.json()).error);
    }
    const res = await api("/api/upload/done", {
      batch,
      group: $("#importGroup")?.value.trim() || "",
      name
    });
    if (msgEl) { msgEl.className = "msg ok"; msgEl.textContent = `Added ${res.name} (${res.files.length} files).`; }
    $("#importName").value = "";
    await refreshState();
    $("#importModal").close();
    await selectModel(res.name);
  } catch (e) {
    if (msgEl) { msgEl.className = "msg err"; msgEl.textContent = e.message; }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Global Keyboard Shortcuts
// ---------------------------------------------------------------------------------------------------------------

function setupShortcuts() {
  window.addEventListener("keydown", (e) => {
    // Ignore keystrokes inside text inputs or textareas
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;

    // Ctrl+S / Cmd+S: Save spec
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      handleAction("save-spec");
      return;
    }

    // Ctrl+Shift+S: Save and re-rig
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      handleAction("save-rerig");
      return;
    }

    // Ctrl+O: Open Import Modal
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "o") {
      e.preventDefault();
      handleAction("import-model");
      return;
    }

    // Ctrl+M: Open Model Manager
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "m") {
      e.preventDefault();
      handleAction("model-manager");
      return;
    }

    // Ctrl+I: Toggle Inspector
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "i") {
      e.preventDefault();
      handleAction("toggle-inspector");
      return;
    }

    // Space: Play / Pause clip
    if (e.key === " ") {
      e.preventDefault();
      if (window.specEditor && window.specEditor.togglePlay) {
        window.specEditor.togglePlay();
      }
      return;
    }

    // 1: Camera Front
    if (e.key === "1") {
      e.preventDefault();
      handleAction("view-front");
      return;
    }

    // 3: Camera Side
    if (e.key === "3") {
      e.preventDefault();
      handleAction("view-side");
      return;
    }

    // 7: Camera Top
    if (e.key === "7") {
      e.preventDefault();
      handleAction("view-top");
      return;
    }

    // Ctrl+U / Cmd+U: Toggle Toolbox Options
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "u") {
      e.preventDefault();
      toggleViewbar();
      return;
    }

    // F: Frame View
    if (e.key.toLowerCase() === "f") {
      e.preventDefault();
      handleAction("view-reset");
      return;
    }
  });
}

// Initialize on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

window.workbench = {
  selectModel,
  setWorkbenchMode,
  runStep,
  followJob,
  loadJobLog,
  refreshState,
  handleAction,
  openModal,
  openSamplesGallery,
  openAuditModal,
  openCollectionTable,
  openExportModal,
  openMeshDoctor,
  setModelFilter,
  getModelFilter: () => currentModelFilter,
  toggleViewbar,
  setViewbarCollapsed,
  applyLogHeight,
  getCurrentJobWorkingModel: () => currentJobWorkingModel,
  getCurrentModel: () => currentModel,
  openRootModal,
  switchRoot,
  openModelManagerModal,
  renderModelManager,
  promptMoveModel,
  promptRenameModel,
  promptDuplicateModel,
  promptNewGroup,
  deleteSpecificModel,
  deleteCurrentModel,
};

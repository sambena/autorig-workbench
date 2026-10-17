// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: AI Assist dialogs. The settings (which AI, its key, the monthly limit) and Fix this rig, a
// conversation with that AI about one model: it shows what a request could cost before it runs, the AI's steps as they
// happen, and its plan, which is applied (and undone) only from here.

const TOKEN = window.AUTORIG_TOKEN;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x > 0 && x < 0.01 ? "$" + x.toFixed(3) : "$" + Number(x || 0).toFixed(2));

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

function show(sel) {
  const d = $(sel);
  // the menu click is still in flight: showModal during it lands on the backdrop and closes the dialog at once
  setTimeout(() => { if (!d.open) d.showModal(); }, 0);
}

// ---- settings ------------------------------------------------------------------------------------------------

let catalog = null;

async function openSettings() {
  show("#aiSettingsModal");
  $("#aiSettingsMsg").className = "msg";
  $("#aiSettingsMsg").textContent = "";
  try {
    const d = await api("/api/ai/settings");
    catalog = d.catalog;
    fillSettings(d.settings);
    loadUsage();
  } catch (e) {
    $("#aiSettingsMsg").className = "msg err";
    $("#aiSettingsMsg").textContent = e.message;
  }
}

function fillSettings(s) {
  const sel = $("#aiProvider");
  sel.innerHTML = `<option value="off">Off</option>` +
    Object.entries(catalog.providers).map(([k, label]) => `<option value="${k}">${esc(label)}</option>`).join("");
  sel.value = s.provider;
  $("#aiLimit").value = s.monthly_limit;
  $("#aiSteps").value = s.max_steps;
  $("#aiTokens").value = s.max_tokens;
  const fields = () => {
    const p = sel.value;
    const box = $("#aiProviderFields");
    if (p === "off") { box.innerHTML = ""; return; }
    if (p === "ollama") {
      box.innerHTML = `<label>Ollama address <input id="aiOllamaUrl" value="${esc(s.ollama_url)}"></label>` +
        `<label>Model (free; it must handle pictures and tools) <input id="aiModel" value="${esc(s.ollama_model)}"></label>`;
      return;
    }
    const models = catalog.models.filter((m) => m.provider === p);
    const keyState = s[p + "_key"];
    box.innerHTML = `<label>Model <select id="aiModel">${models.map((m) =>
      `<option value="${esc(m.id)}">${esc(m.label)}: $${m.in} in / $${m.out} out per million tokens</option>`).join("")}</select></label>` +
      `<label>API key <input id="aiKey" type="password" autocomplete="off" placeholder="${keyState ? esc(keyState) + " (leave empty to keep)" : "paste the key"}"></label>` +
      (p === "gemini" ? `<label>The key has no billing (Google's free tier) <input id="aiGeminiFree" type="checkbox" style="width:auto" ${s.gemini_free_tier ? "checked" : ""}></label>` : "");
    $("#aiModel").value = s[p + "_model"];
  };
  sel.onchange = fields;
  fields();
}

async function saveSettings() {
  const p = $("#aiProvider").value;
  const body = { provider: p, monthly_limit: $("#aiLimit").value, max_steps: $("#aiSteps").value, max_tokens: $("#aiTokens").value };
  if (p === "ollama") {
    body.ollama_url = $("#aiOllamaUrl").value;
    body.ollama_model = $("#aiModel").value;
  } else if (p !== "off") {
    body[p + "_model"] = $("#aiModel").value;
    body[p + "_key"] = $("#aiKey").value;
    if (p === "gemini") body.gemini_free_tier = $("#aiGeminiFree").checked;
  }
  const msg = $("#aiSettingsMsg");
  try {
    const d = await api("/api/ai/settings", body);
    fillSettings(d.settings);
    msg.className = d.status.enabled || p === "off" ? "msg ok" : "msg err";
    msg.textContent = p === "off" ? "AI is off." : d.status.enabled ? `Saved. AI Assist will use ${d.status.model_label}.` : d.status.problem;
    if ($("#aiFixModal").open) refreshEstimate();
  } catch (e) {
    msg.className = "msg err";
    msg.textContent = e.message;
  }
}

async function loadUsage() {
  try {
    const u = await api("/api/ai/usage");
    const rows = u.recent.slice(0, 12).map((r) =>
      `<tr><td>${esc(new Date(r.at * 1000).toLocaleString())}</td><td>${esc(r.model)}</td><td class="num">${r.in}</td><td class="num">${r.out}</td><td class="num">${money(r.cost)}</td></tr>`).join("");
    $("#aiUsage").innerHTML = `Spent ${money(u.spent)} of the ${money(u.limit)} limit.` +
      (rows ? `<table style="margin-top:8px"><tr><th>When</th><th>Model</th><th>In</th><th>Out</th><th>Cost</th></tr>${rows}</table>` : " No calls yet.");
  } catch (e) {
    $("#aiUsage").textContent = e.message;
  }
}

// ---- Fix this rig --------------------------------------------------------------------------------------------

let fix = { model: "", conversation: "", job: "", timer: 0, undo: null };

function log(html, cls) {
  const div = document.createElement("div");
  div.className = cls;
  div.innerHTML = html;
  $("#aiFixLog").append(div);
  $("#aiFixLog").scrollTop = 1e9;
  return div;
}

async function openFixRig(model) {
  if (fix.model !== model) newConversation();
  fix.model = model;
  $("#aiFixModel").textContent = model;
  show("#aiFixModal");
  await refreshEstimate();
}

function newConversation() {
  clearTimeout(fix.timer);
  fix = { model: fix.model, conversation: "", job: "", timer: 0, undo: null };
  $("#aiFixLog").innerHTML = "";
  $("#aiFixPlan").innerHTML = "";
  $("#aiFixMsg").className = "msg";
  $("#aiFixMsg").textContent = "";
  $("#aiFixRun").disabled = false;
}

async function refreshEstimate() {
  const btn = $("#aiFixRun"), est = $("#aiFixEstimate"), status = $("#aiFixStatus");
  try {
    const st = await api("/api/ai/status");
    if (!st.enabled) {
      status.innerHTML = `${esc(st.problem)} <a href="#" id="aiOpenSettings">Open AI settings</a>`;
      $("#aiOpenSettings").onclick = (e) => { e.preventDefault(); openSettings(); };
      btn.disabled = true;
      est.textContent = "";
      return;
    }
    status.textContent = `${st.model_label} · this month ${money(st.spent)} of ${money(st.limit)}`;
    const q = new URLSearchParams({ task: "fix-rig", model: fix.model, message: $("#aiFixMessage").value, conversation: fix.conversation });
    const e = await api("/api/ai/estimate?" + q);
    btn.disabled = !e.allowed || !!fix.job;
    // the button says what pressing it may cost: the ask before any paid call
    btn.textContent = e.free ? `Ask ${e.model_label} (free)` : `Ask ${e.model_label} (up to ${money(e.worst)})`;
    est.textContent = e.allowed ? (e.free ? "" : `usually about ${money(e.typical)}; at most ${e.max_steps} model calls`) : e.reason;
  } catch (err) {
    status.textContent = err.message;
    btn.disabled = true;
  }
}

async function run() {
  const message = $("#aiFixMessage").value.trim();
  if (!message) return;
  $("#aiFixRun").disabled = true;
  $("#aiFixPlan").innerHTML = "";
  log(esc(message), "ai-you");
  try {
    const r = await api("/api/ai/start", { task: "fix-rig", model: fix.model, message, conversation: fix.conversation });
    fix.conversation = r.conversation;
    fix.job = r.job;
    fix.shown = 0;
    fix.live = log("Working…", "ai-step");
    poll();
  } catch (e) {
    log(esc(e.message), "ai-step err");
    $("#aiFixRun").disabled = false;
  }
}

async function poll() {
  let j;
  try {
    j = await api("/api/ai/job?id=" + encodeURIComponent(fix.job));
  } catch (e) {
    log(esc(e.message), "ai-step err");
    fix.job = "";
    return refreshEstimate();
  }
  // steps not shown yet; the last one may still be running and is redrawn until it settles
  const steps = j.steps || [];
  for (let k = fix.shown; k < steps.length; k++) {
    const s = steps[k];
    const cls = "ai-step" + (s.error ? " err" : "");
    if (k === steps.length - 1 && s.running && !j.done) break;
    fix.live.before(Object.assign(document.createElement("div"), { className: cls, textContent: s.summary }));
    fix.shown = k + 1;
  }
  const running = steps.length && steps[steps.length - 1].running ? steps[steps.length - 1].summary + "…" : "Thinking…";
  fix.live.textContent = `${running} (${money(j.cost || 0)} so far)`;
  if (!j.done) {
    fix.timer = setTimeout(poll, 1500);
    return;
  }
  fix.live.remove();
  if (j.error) log(esc(j.error), "ai-step err");
  if (j.reply) log(esc(j.reply), "ai-reply");
  log(`This request cost ${money(j.cost || 0)}.`, "ai-step");
  fix.job = "";
  if (j.plan) showPlan(j.plan);
  refreshEstimate();
}

function showPlan(plan) {
  const box = $("#aiFixPlan");
  box.innerHTML = `<div class="ai-plan"><h3 style="margin:0 0 6px">Plan</h3>` + plan.changes.map((c) =>
    `<label style="display:flex; gap:6px; align-items:flex-start"><input type="checkbox" data-n="${c.n}" checked style="width:auto">` +
    `<span>${esc(c.summary)}</span></label>` +
    (c.detail ? `<details><summary class="meta">rig.json changes</summary><pre>${esc(c.detail)}</pre></details>` : "")).join("") +
    `<div style="display:flex; gap:8px; margin-top:8px"><button type="button" class="primary" id="aiApply">Apply and re-rig</button>` +
    `<span class="meta">Saves rig.json (the old one is kept for Undo), then rigs, trims and audits again. Free: it runs on this computer.</span></div></div>`;
  $("#aiApply").onclick = async () => {
    const skip = [...box.querySelectorAll("input[data-n]")].filter((c) => !c.checked).map((c) => Number(c.dataset.n));
    const msg = $("#aiFixMsg");
    try {
      const r = await api("/api/ai/apply", { plan: plan.id, skip });
      msg.className = r.error ? "msg err" : "msg ok";
      msg.innerHTML = esc(r.error || "Applied. The re-rig is running; the audit shows the result.") +
        ` <button type="button" class="small" id="aiUndo">Undo</button>`;
      $("#aiApply").disabled = true;
      fix.undo = r.journal;
      $("#aiUndo").onclick = undo;
      if (r.job && window.workbench) window.workbench.followJob(r.job);
    } catch (e) {
      msg.className = "msg err";
      msg.textContent = e.message;
    }
  };
}

async function undo() {
  const msg = $("#aiFixMsg");
  try {
    const r = await api("/api/ai/undo", { task: "fix-rig", journal: fix.undo });
    msg.className = r.error ? "msg err" : "msg ok";
    msg.textContent = r.error || "Undone: rig.json is back as it was, and it is re-rigging.";
    fix.undo = null;
    if (r.job && window.workbench) window.workbench.followJob(r.job);
  } catch (e) {
    msg.className = "msg err";
    msg.textContent = e.message;
  }
}

function setup() {
  $("#aiSettingsSave").onclick = saveSettings;
  $("#aiFixRun").onclick = run;
  $("#aiFixNew").onclick = () => { newConversation(); refreshEstimate(); };
  let t = 0;
  $("#aiFixMessage").oninput = () => { clearTimeout(t); t = setTimeout(refreshEstimate, 400); };
  $("#aiSettingsModal").addEventListener("close", () => { if ($("#aiFixModal").open) refreshEstimate(); });
}

setup();
window.aiAssist = { openFixRig, openSettings };

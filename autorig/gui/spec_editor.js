// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the spec editor (gui/spec_editor.html). Plain ES modules, no build step; three.js is vendored
// under gui/vendor/three (MIT).
//
// Left: the source model as it came (steps/source_preview.py), with its own skeleton drawn over it, every joint
// named; the chains the draft spec makes are coloured, the way the rig step will read them. Right: the spec as a
// form built from the schema the server sends (gui/spec_api.py). "Pick" beside a field puts the view in a picking
// mode: click bones (or points on the model, or on the flat views) to fill the field. Save writes rig.json; Save
// and re-rig also runs the rig and shows the audit before and after.
//
// Coordinates: joints come in Blender's world frame (Z up); three.js is Y up, so a Blender point (x, y, z) is drawn
// at (x, z, -y). A spec's points are 0..1 of the model's box after the rig step turns it to face -Y (SPEC.md,
// "Coordinates"); frame() below does that turn exactly as rerig.normalise does, from the draft, so it follows edits.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { mirrorName, mirrorChainData, generateStations } from "./viewer_logic.js";

const TOKEN = window.AUTORIG_TOKEN;
const MODEL = new URLSearchParams(location.search).get("model") || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const withToken = (u) => u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
const clone = (o) => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));
const r3 = (v) => v.map((x) => Math.round(x * 1000) / 1000);
const CHECKS = { bleed_pct: "Bleed %", combined_tears: "Tears, combined pose", bend_tears: "Tears, single bends",
                 head_pct: "Head share %", max_influences: "Influences per vertex" };
const HIGHER_IS_BETTER = { head_pct: true };
const ROLES = ["leg", "leg_front", "leg_hind", "arm", "wing", "wing_fore", "wing_hind", "tail", "abdomen", "neck", "ear",
               "antenna", "jaw", "mandible", "claw", "tentacle", "fin", "flipper", "streamer", "lid"];
const PALETTE = [0x2ec4b6, 0xff9f1c, 0xf15bb5, 0x9b5de5, 0x00bbf9, 0xfee440, 0x8ac926, 0xff595e, 0x4cc9f0, 0xf4a261];
const SPINE = 0x3b6fe0, LEG = 0x2fb344, GUESS = 0x8a8f99, UNUSED = 0x5a606b, DROP = 0xb03030, CONNECT = 0x6b7280;
const hex = (c) => "#" + c.toString(16).padStart(6, "0");

async function api(path, body) {
  const opt = { headers: { "X-Autorig-Token": TOKEN } };
  if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.data = j; e.status = r.status; throw e; }
  return j;
}

// ---------------------------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------------------------

let B = null;                 // the bundle from /api/spec
let SRC = null;               // the source view's joints and bounds (source_preview.py), or null
let draft = null;             // the spec being edited
let undo = [];                // earlier drafts, as JSON text
let base = null;              // hash of the rig.json text the page loaded (the server refuses a blind overwrite)
let checked = { errors: [], warnings: [], diff: "", changed: false };
let tab = "rig";
let pick = null;              // {label, mode, path, ...}: what a click on a bone or the model fills in
let selected = null;          // the selected joint's name
let job = null, es = null;
let flatBig = false;           // the flat views drawn large over the 3D view, instead of in the panel
let meshVerts = null;         // the source mesh's vertices in Blender's frame (Float32Array xyz), for the box
let F = null, L = null;       // the draft's frame (turn, box) and layout (which joint is in which chain)

const rig = () => (draft.rig ||= {});
const kind = () => (draft && draft.rig && draft.rig.kind) || "";

function getPath(path) {
  let o = draft;
  for (const k of path) { if (o == null) return undefined; o = o[k]; }
  return o;
}
function empty(v) { return v === undefined || v === "" || (Array.isArray(v) && !v.length) || (v !== null && typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length); }
function setPath(path, value, opts = {}) {
  if (!opts.noUndo) pushUndo();
  let o = draft;
  for (let i = 0; i < path.length - 1; i++) {
    const k = path[i];
    if (o[k] == null || typeof o[k] !== "object") o[k] = typeof path[i + 1] === "number" ? [] : {};
    o = o[k];
  }
  const last = path[path.length - 1];
  if (value === undefined || (empty(value) && !opts.keepEmpty)) {
    if (Array.isArray(o) && typeof last === "number") o.splice(last, 1); else delete o[last];
  } else o[last] = value;
  // an emptied list or object under rig goes away, so the file only says what it means
  if (path[0] === "rig" && path.length > 2) {
    const top = draft.rig[path[1]];
    if (empty(top) && !opts.keepEmpty) delete draft.rig[path[1]];
  }
  if (draft.notes && !Object.keys(draft.notes).length) delete draft.notes;
  changed();
}
function pushUndo() { undo.push(JSON.stringify(draft)); if (undo.length > 200) undo.shift(); $("bUndo").disabled = false; }

// ---------------------------------------------------------------------------------------------------------------
// The frame: the turn the rig step makes (rerig.normalise), and the 0..1 box after it
// ---------------------------------------------------------------------------------------------------------------

const joint = (n) => SRC && SRC.byName[n];

function frame() {
  const R = rig();
  let d = R.forward;
  if (kind() === "tripo" && !d && joint(R.head) && joint(R.hips)) {
    const h = joint(R.head).head, p = joint(R.hips).head;
    d = [h[0] - p[0], h[1] - p[1], h[2] - p[2]];
  }
  if (!Array.isArray(d) || d.length !== 3 || (!d[0] && !d[1])) d = [0, -1, 0];
  let turn = Math.atan2(-1, 0) - Math.atan2(d[1], d[0]);
  if (!R.straighten) turn = Math.round(turn / (Math.PI / 2)) * (Math.PI / 2);
  const c = Math.cos(turn), s = Math.sin(turn);
  const rot = (p) => [p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2]];
  const unrot = (q) => [q[0] * c + q[1] * s, -q[0] * s + q[1] * c, q[2]];
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  const add = (p) => { const q = rot(p); for (let k = 0; k < 3; k++) { if (q[k] < lo[k]) lo[k] = q[k]; if (q[k] > hi[k]) hi[k] = q[k]; } };
  if (meshVerts) for (let i = 0; i < meshVerts.length; i += 3) add([meshVerts[i], meshVerts[i + 1], meshVerts[i + 2]]);
  else if (SRC) { const a = SRC.bounds.lo, b = SRC.bounds.hi; for (const x of [a[0], b[0]]) for (const y of [a[1], b[1]]) for (const z of [a[2], b[2]]) add([x, y, z]); }
  else { add([-0.5, -0.5, 0]); add([0.5, 0.5, 1]); }
  const size = [0, 1, 2].map((k) => Math.max(1e-9, hi[k] - lo[k]));
  let cx = (lo[0] + hi[0]) / 2;
  if (R.body === "single") {
    const tops = (R.legs || []).filter(joint).map((n) => rot(joint(n).head)[0]);
    if (tops.length) cx = tops.reduce((a, b) => a + b, 0) / tops.length;
  } else if (kind() === "tripo" && joint(R.head) && joint(R.hips)) cx = (rot(joint(R.head).head)[0] + rot(joint(R.hips).head)[0]) / 2;
  return {
    turn, rot, unrot, lo, hi, size, cx, max: Math.max(...size),
    toUnit: (p) => { const q = rot(p); return [0, 1, 2].map((k) => (q[k] - lo[k]) / size[k]); },
    fromUnit: (u) => unrot([0, 1, 2].map((k) => lo[k] + size[k] * u[k])),
    mirror: (p) => { const q = rot(p); return unrot([2 * cx - q[0], q[1], q[2]]); },
    centred: (p) => { const q = rot(p); return [q[0] - cx, q[1], q[2]]; },
    dir: (v) => unrot(v),                       // a direction in the turned frame, back in the source's
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The layout: which joint the draft puts in which chain (rerig.repair and rerig.tripo_chains, read-only)
// ---------------------------------------------------------------------------------------------------------------

function layoutTripo() {
  const R = rig();
  const out = { of: {}, chains: [], virtual: {}, dropped: new Set(), unused: new Set(), problems: [], alias: {} };
  if (!SRC || !SRC.joints.length) return out;
  const J = {};
  for (const j of SRC.joints) J[j.name] = { pos: j.head, tail: j.tail, parent: j.parent };
  // repair: a joint on top of its parent folds into it
  for (const j of SRC.joints) {
    const n = j.name, p = J[n] && J[n].parent;
    if (!j.folds_into || !J[p] || p === "bone_0") continue;
    for (const c of Object.values(J)) if (c.parent === n) c.parent = p;
    out.alias[n] = out.alias[p] || p; delete J[n];
  }
  const fix = (n) => out.alias[n] || n;
  const head = fix(R.head), hips = fix(R.hips);
  const legsIn = (R.legs || []).map(fix);
  const namedIn = {};
  for (const [role, tops] of Object.entries(R.chains || {})) for (const t of tops || []) namedIn[fix(t)] = role;
  const kids0 = {};
  for (const [n, j] of Object.entries(J)) (kids0[j.parent] ||= []).push(n);
  const drop = (n) => { for (const c of kids0[n] || []) drop(c); if (J[n]) { out.dropped.add(n); delete J[n]; } };
  for (const n of R.delete || []) if (J[n]) drop(n);
  for (const chain of R.mirror || []) {
    if (!Array.isArray(chain) || !J[chain[0]]) continue;
    let prev = J[chain[0]].parent;
    for (const n of chain) {
      if (!J[n]) continue;
      const m = n + "_m";
      J[m] = { pos: F.mirror(J[n].pos), tail: F.mirror(J[n].tail), parent: prev, virtual: n };
      out.virtual[m] = J[m]; prev = m;
    }
  }
  const single = R.body === "single";
  let H = hips, Hd = head;
  if (single) {
    const u = [0.5, 0.5, R.body_height ?? 0.5];
    J.__body = { pos: F.fromUnit(u), tail: F.fromUnit([0.5, 0.2, u[2]]), parent: null, virtual: "body" };
    out.virtual.__body = J.__body;
    const listed = new Set([...legsIn, ...Object.keys(namedIn)]);
    const underListed = (n) => { let p = J[n].parent; while (J[p]) { if (listed.has(p)) return true; p = J[p].parent; } return false; };
    for (const t of [...listed].filter((t) => J[t] && !underListed(t))) J[t].parent = "__body";
    H = Hd = "__body";
  }
  const names = Object.keys(J).filter((n) => n !== "bone_0");
  const adj = {};
  for (const n of names) adj[n] = new Set();
  for (const n of names) { const p = J[n].parent; if (adj[p]) { adj[n].add(p); adj[p].add(n); } }
  if (!adj[H] || !adj[Hd]) {
    if (!single) out.problems.push(!adj[H] ? "pick the hips bone" : "pick the head bone");
    for (const n of names) out.unused.add(n);
    return out;
  }
  const reach = (s) => { const seen = new Set([s]), st = [s]; while (st.length) for (const c of adj[st.pop()]) if (!seen.has(c)) { seen.add(c); st.push(c); } return seen; };
  let got = reach(H);
  if (!single) for (const n of names) if (!got.has(n) && !adj[J[n].parent]) { adj[n].add(H); adj[H].add(n); for (const x of reach(n)) got.add(x); }
  const par = { [H]: null }, kids = {}, order = [H];
  for (let i = 0; i < order.length; i++) {
    const n = order[i];
    for (const c of [...adj[n]].sort()) if (!(c in par)) { par[c] = n; (kids[n] ||= []).push(c); order.push(c); }
  }
  for (const n of names) if (!(n in par)) out.unused.add(n);
  if (!(Hd in par)) { out.problems.push("the head bone is not joined to the hips bone"); return out; }
  const depth = {};
  const deep = (n) => (depth[n] = 1 + Math.max(0, ...(kids[n] || []).map(deep)));
  deep(H);
  const spine = [Hd];
  while (spine[spine.length - 1] !== H) spine.push(par[spine[spine.length - 1]]);
  spine.reverse();
  const legs = new Set(legsIn.filter((n) => n in par));
  const named = {};
  for (const [t, r] of Object.entries(namedIn)) if (t in par) named[t] = r;
  const P = (n) => F.centred(J[n].pos);
  const sx = F.size[0], height = F.size[2], sy = F.size[1];
  const colourOf = {};
  let ci = 0;
  for (const r of Object.keys(R.chains || {})) colourOf[r] = PALETTE[ci++ % PALETTE.length];

  function guess(top, at) {
    if (legs.has(top)) return "leg";
    if (named[top]) return named[top];
    let end = top;
    while ((kids[end] || []).length) end = kids[end].reduce((a, b) => (depth[b] > depth[a] ? b : a));
    if (at === Hd || (spine.length > 2 && at === spine[spine.length - 2])) {
      const h = [P(end)[0] - P(Hd)[0], P(end)[1] - P(Hd)[1], P(end)[2] - P(Hd)[2]];
      if (Math.abs(P(end)[0]) > 0.06 * sx && h[2] > -0.05 * height) return "ear";
      if (Math.abs(P(end)[0]) <= 0.06 * sx && h[2] < 0) return "jaw";
      return "head_part";
    }
    const e = P(end)[1] - P(H)[1];
    if (e > 0.1 * sy && Math.abs(P(top)[0]) < 0.1 * sx && depth[top] >= 2 && spine.slice(0, 2).includes(at)) return "tail";
    return "extra";
  }
  function grow(top, role, at, how, colour) {
    const path = [top];
    for (;;) {
      const nxt = (kids[path[path.length - 1]] || []).filter((c) => !legs.has(c) && !named[c]);
      if (!nxt.length) break;
      const last = path[path.length - 1];
      path.push(nxt.reduce((a, b) => {
        const da = depth[a], db = depth[b];
        if (db !== da) return db > da ? b : a;
        const la = dist(J[a].pos, J[last].pos), lb = dist(J[b].pos, J[last].pos);
        return lb < la ? b : a;
      }));
    }
    const idx = out.chains.length;
    out.chains.push({ role, joints: path, at, how, colour });
    path.forEach((n, i) => (out.of[n] = { chain: idx, role, index: i, how }));
    path.forEach((n, i) => {
      for (const c of kids[n] || []) {
        if (i + 1 < path.length && c === path[i + 1]) continue;
        let sub, h2 = "sub", col = colour;
        if (legs.has(c)) { sub = "leg"; h2 = "leg"; col = LEG; }
        else if (named[c]) { sub = named[c]; h2 = "named"; col = colourOf[sub]; }
        else {
          sub = role === "leg" && i >= path.length - 2 ? role + "_toe" : role + "_b";
          if (depth[c] === 1 && role !== "leg") { out.unused.add(c); continue; }
        }
        grow(c, sub, n, h2, col);
      }
    });
  }
  out.chains.push({ role: "spine", joints: spine, at: null, how: "spine", colour: SPINE });
  spine.forEach((n, i) => (out.of[n] = { chain: 0, role: single ? "body" : "spine", index: i, how: "spine" }));
  spine.forEach((n, i) => {
    for (const c of kids[n] || []) {
      if (i + 1 < spine.length && c === spine[i + 1]) continue;
      const role = guess(c, n);
      if (role === "extra" && depth[c] === 1) { out.unused.add(c); continue; }
      const how = legs.has(c) ? "leg" : named[c] ? "named" : "guessed";
      grow(c, role, n, how, how === "leg" ? LEG : how === "named" ? colourOf[role] : GUESS);
    }
  });
  out.J = J; out.par = par; out.colourOf = colourOf; out.spine = spine;
  return out;
}

function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); }

// ---------------------------------------------------------------------------------------------------------------
// three.js: the stage
// ---------------------------------------------------------------------------------------------------------------

const stageEl = $("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
stageEl.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
Object.assign(labelRenderer.domElement.style, { position: "absolute", top: "0", left: "0", pointerEvents: "none" });
stageEl.appendChild(labelRenderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x15181e);
const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 1000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.12;
scene.add(new THREE.HemisphereLight(0xeef2fa, 0x55504a, 2.6));
scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const headLight = new THREE.DirectionalLight(0xffffff, 2.4);      // follows the camera: what is seen is lit
headLight.position.set(0.3, 0.4, 1); camera.add(headLight); scene.add(camera);
const V = (b) => new THREE.Vector3(b[0], b[2], -b[1]);             // Blender (Z up) -> three (Y up)
const fromV = (v) => [v.x, -v.z, v.y];

function resize() {
  const w = stageEl.clientWidth, h = stageEl.clientHeight;
  renderer.setSize(w, h); labelRenderer.setSize(w, h);
  camera.aspect = w / Math.max(1, h); camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(stageEl);

let model = null;                       // the GLB scene
const meshes = [];
const skel = new THREE.Group(); skel.renderOrder = 10; scene.add(skel);
const marks = new THREE.Group(); scene.add(marks);
const spotsG = new THREE.Group(); scene.add(spotsG);
const pickables = [];                   // joint spheres (userData.joint)
const labels = {};                      // joint name -> CSS2DObject

let riggedScene = null;
let riggedClips = [];
let mixer = null;
let action = null;
let curClip = 0;
let isPlaying = true;
let viewMode = "source";
let scrubbing = false;
const clock = new THREE.Clock();

function tick() {
  requestAnimationFrame(tick);
  const delta = clock.getDelta();
  if (mixer && isPlaying && viewMode === "rigged") {
    mixer.update(delta);
    updateClipUI();
  }
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}
tick();

function updateClipUI() {
  if (!action || !riggedClips.length) return;
  const clip = riggedClips[curClip];
  if (!clip) return;
  const dur = clip.duration;
  const t = (action.time % dur + dur) % dur;
  const elTime = $("clipTime");
  if (elTime) elTime.textContent = `${t.toFixed(2)} / ${dur.toFixed(2)} s`;
  const elScrub = $("clipScrub");
  if (elScrub && !scrubbing) {
    elScrub.value = dur > 0 ? Math.round((t / dur) * 1000) : 0;
  }
}

async function loadRigged(url) {
  if (riggedScene) {
    scene.remove(riggedScene);
    clearGroup(riggedScene);
    riggedScene = null;
  }
  if (mixer) { mixer.stopAllAction(); mixer.uncacheRoot(mixer.getRoot()); mixer = null; }
  action = null;
  riggedClips = [];
  try {
    const gltf = await new GLTFLoader().loadAsync(withToken(url));
    riggedScene = gltf.scene;
    riggedClips = gltf.animations || [];
    riggedScene.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = true;
        o.receiveShadow = true;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) { m.side = THREE.DoubleSide; m.transparent = true; }
      }
      if (o.isSkinnedMesh) {
        o.frustumCulled = false;
      }
    });
    scene.add(riggedScene);
    mixer = new THREE.AnimationMixer(riggedScene);
    populateClips();
    if (riggedClips.length > 0) {
      playClip(0);
    }
    riggedScene.visible = viewMode === "rigged";
    setOpacity();
  } catch (e) {
    console.error("Could not load rigged preview:", e);
  }
}

function populateClips() {
  const sel = $("clipSelect");
  if (!sel) return;
  if (!riggedClips.length) {
    sel.innerHTML = `<option value="0">bind pose (no clips)</option>`;
    sel.disabled = true;
    $("clipPlay").disabled = true;
    $("clipScrub").disabled = true;
    $("clipTime").textContent = "bind pose";
    return;
  }
  sel.disabled = false;
  $("clipPlay").disabled = false;
  $("clipScrub").disabled = false;
  sel.innerHTML = riggedClips.map((c, i) => `<option value="${i}">${esc(c.name || "clip " + (i + 1))} (${c.duration.toFixed(2)}s)</option>`).join("");
  sel.value = String(curClip);
}

function playClip(idx) {
  if (!mixer || !riggedClips.length) return;
  curClip = Math.max(0, Math.min(idx, riggedClips.length - 1));
  const clip = riggedClips[curClip];
  if (!clip) return;
  if (action) action.stop();
  action = mixer.clipAction(clip);
  action.reset();
  action.play();
  isPlaying = true;
  $("clipPlay").textContent = "❚❚";
  $("clipPlay").title = "Pause (Space)";
  const sel = $("clipSelect");
  if (sel) sel.value = String(curClip);
  updateClipUI();
}

function togglePlay() {
  if (!action) {
    if (riggedClips.length) playClip(curClip);
    return;
  }
  isPlaying = !isPlaying;
  $("clipPlay").textContent = isPlaying ? "❚❚" : "▶";
  $("clipPlay").title = isPlaying ? "Pause (Space)" : "Play (Space)";
}

function seekClip(frac) {
  if (!action || !riggedClips.length) return;
  const clip = riggedClips[curClip];
  if (!clip) return;
  action.time = frac * clip.duration;
  mixer.update(0);
  updateClipUI();
}

async function setViewMode(mode) {
  viewMode = mode;
  $("vSource").classList.toggle("on", mode === "source");
  $("vRigged").classList.toggle("on", mode === "rigged");
  $("clipBar").style.display = mode === "rigged" ? "inline-flex" : "none";

  if (mode === "rigged") {
    if (model) model.visible = false;
    skel.visible = false;
    marks.visible = false;
    spotsG.visible = false;
    if (!riggedScene && B && B.preview_url) {
      await loadRigged(B.preview_url);
    }
    if (riggedScene) {
      riggedScene.visible = true;
      if (riggedClips.length && (!action || !action.isRunning())) {
        playClip(curClip);
      }
    }
  } else {
    if (model) model.visible = true;
    skel.visible = true;
    marks.visible = true;
    spotsG.visible = true;
    if (riggedScene) riggedScene.visible = false;
    if (action) action.stop();
    isPlaying = false;
  }
}

async function loadModel(url) {
  const gltf = await new GLTFLoader().loadAsync(withToken(url));
  model = gltf.scene;
  scene.add(model);
  model.updateMatrixWorld(true);
  const pts = [];
  model.traverse((o) => {
    if (!o.isMesh) return;
    meshes.push(o);
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) { m.side = THREE.DoubleSide; m.transparent = true; }
    const pos = o.geometry.attributes.position, v = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) { v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld); pts.push(v.x, -v.z, v.y); }
  });
  meshVerts = new Float32Array(pts);
  setOpacity();
}

function setOpacity() {
  const a = Number($("opacity").value);
  for (const o of meshes) for (const m of Array.isArray(o.material) ? o.material : [o.material]) { m.opacity = a; m.depthWrite = a > 0.95; }
  if (riggedScene) {
    riggedScene.traverse((o) => {
      if (o.isMesh) for (const m of Array.isArray(o.material) ? o.material : [o.material]) { m.opacity = a; m.depthWrite = a > 0.95; m.transparent = a < 1; }
    });
  }
}
$("opacity").oninput = setOpacity;

function frameView(dir) {
  // dir: a direction in the turned frame (face is -Y, its left +X, up +Z), from the model's middle
  const c = F.fromUnit([0.5, 0.5, 0.5]);
  const d = F.dir(dir);
  const dist = (F.max * 0.62) / Math.tan((camera.fov * Math.PI) / 360);
  controls.target.copy(V(c));
  camera.position.copy(V([c[0] + d[0] * dist, c[1] + d[1] * dist, c[2] + d[2] * dist]));
  camera.near = F.max * 0.01; camera.far = F.max * 50; camera.updateProjectionMatrix();
}
$("vFront").onclick = () => frameView([0.35, -1, 0.3]);
$("vSide").onclick = () => frameView([1, 0, 0.05]);
$("vTop").onclick = () => frameView([0, -0.02, 1]);

function clearGroup(g) {
  for (const o of [...g.children]) {
    o.traverse((x) => { if (x.geometry) x.geometry.dispose(); if (x.material && x.material.dispose) x.material.dispose(); });
    if (o.isCSS2DObject) o.element.remove();
    o.removeFromParent();
  }
}

function stick(a, b, colour, radius, opacity = 1) {
  const A = V(a), Bv = V(b), d = new THREE.Vector3().subVectors(Bv, A), len = d.length();
  if (len < 1e-9) return null;
  const g = new THREE.CylinderGeometry(radius * 0.45, radius, len, 8, 1);
  g.translate(0, len / 2, 0);
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity }));
  m.position.copy(A);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
  m.renderOrder = 11;
  return m;
}
function ball(p, colour, radius, opacity = 1) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 14, 10),
                           new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity }));
  m.position.copy(V(p)); m.renderOrder = 12;
  return m;
}
function tag(text, cls, colour) {
  const d = document.createElement("div");
  d.className = "lbl " + (cls || "");
  d.textContent = text;
  if (colour !== undefined) d.style.borderLeftColor = hex(colour);
  return new CSS2DObject(d);
}

// ---------------------------------------------------------------------------------------------------------------
// Drawing the skeleton, the draft's points and boxes, and the audit's spots
// ---------------------------------------------------------------------------------------------------------------

function usesOf(n) {
  // every field of the draft that names this joint, in words
  const R = rig(), out = [];
  if (R.head === n) out.push("Head bone");
  if (R.hips === n) out.push("Hips bone");
  if (R.shell === n) out.push("Shell bone");
  for (const [role, tops] of Object.entries(R.chains && !Array.isArray(R.chains) ? R.chains : {})) if ((tops || []).includes(n)) out.push("first bone of " + role);
  if ((R.legs || []).includes(n)) out.push("a leg");
  (R.mirror || []).forEach((c, i) => { if ((c || []).includes(n)) out.push("mirrored (chain " + (i + 1) + ")"); });
  if ((R.delete || []).includes(n)) out.push("thrown away");
  if ((R.nodeform || []).includes(n)) out.push("carries no skin");
  return out;
}

function drawSkeleton() {
  clearGroup(skel);
  pickables.length = 0;
  for (const k of Object.keys(labels)) delete labels[k];
  if (!SRC || !SRC.joints.length) return;
  const rad = F.max * 0.006, jr = F.max * 0.011;
  const J = L.J || Object.fromEntries(SRC.joints.map((j) => [j.name, { pos: j.head, tail: j.tail, parent: j.parent }]));
  const all = { ...Object.fromEntries(SRC.joints.map((j) => [j.name, { pos: j.head, tail: j.tail, parent: j.parent }])), ...L.virtual };
  const colourOfJoint = (n) => {
    if (L.dropped.has(n)) return DROP;
    const o = L.of[n];
    if (!o) return UNUSED;
    return L.chains[o.chain].colour ?? GUESS;
  };
  // the bones: joint to next joint down each chain, and the last joint to its tip
  for (const c of L.chains) {
    for (let i = 0; i < c.joints.length; i++) {
      const a = J[c.joints[i]]; if (!a) continue;
      const b = i + 1 < c.joints.length ? J[c.joints[i + 1]].pos : a.tail;
      const s = stick(a.pos, b, c.colour ?? GUESS, rad * (c.how === "guessed" ? 0.7 : 1), c.how === "guessed" ? 0.75 : 1);
      if (s) { s.userData.joint = c.joints[i]; skel.add(s); pickables.push(s); }
    }
    if (c.at && J[c.at]) { const s = stick(J[c.at].pos, J[c.joints[0]].pos, CONNECT, rad * 0.35, 0.6); if (s) skel.add(s); }
  }
  // joints not in any chain (unused, thrown away, folded): thin lines to their parent
  for (const [n, j] of Object.entries(all)) {
    if (L.of[n] || n === "__body") continue;
    const p = all[j.parent];
    const col = L.dropped.has(n) ? DROP : UNUSED;
    if (p) { const s = stick(p.pos, j.pos, col, rad * 0.4, 0.55); if (s) { s.userData.joint = n; skel.add(s); pickables.push(s); } }
  }
  const showNames = $("showLabels").checked;
  for (const [n, j] of Object.entries(all)) {
    if (n === "__body") continue;
    const folded = L.alias[n];
    const col = folded ? UNUSED : colourOfJoint(n);
    const b = ball(j.pos, n === selected ? 0xffffff : col, jr * (n === selected ? 1.35 : 1), folded ? 0.5 : 1);
    b.userData.joint = n; skel.add(b); pickables.push(b);
    // a bigger, invisible ball: an easier target
    const hit = new THREE.Mesh(new THREE.SphereGeometry(jr * 2.2, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.copy(V(j.pos)); hit.userData.joint = n; skel.add(hit); pickables.push(hit);
    if (showNames || n === selected) {
      const o = L.of[n];
      const role = o && o.index === 0 && o.how !== "spine" ? "  " + o.role : (n === rig().head ? "  head" : n === rig().hips ? "  hips" : "");
      const t = tag(n + role, (n === selected ? "sel" : "") + (L.of[n] || j.virtual ? "" : " dim"), col);
      t.position.copy(V(j.pos)); t.element.onclick = (e) => { e.stopPropagation(); clickJoint(n); };
      t.element.title = (usesOf(n).join(", ") || "not named in the spec") + (folded ? " (folds into " + folded + ")" : "");
      skel.add(t); labels[n] = t;
    }
  }
  if (L.virtual.__body) skel.add(ball(L.virtual.__body.pos, SPINE, jr * 1.6));
}

function pointFields() {
  // every 0..1 point of the draft, with a colour and a name: build chains, head line, jaw, rigid boxes' corners
  const R = rig(), out = [];
  if (Array.isArray(R.chains)) R.chains.forEach((c, i) => {
    const col = i === 0 ? SPINE : PALETTE[i % PALETTE.length];
    const nm = c.name || "chain " + (i + 1);
    const cp = ["rig", "chains", i];
    if (Array.isArray(c.points)) {
      out.push({
        pts: c.points,
        paths: c.points.map((_, k) => [...cp, "points", k]),
        col,
        name: nm,
        line: true,
        type: "point"
      });
    }
    if (c.tip) {
      if (c.base) {
        out.push({
          pts: [c.base, c.tip],
          paths: [[...cp, "base"], [...cp, "tip"]],
          col,
          name: nm,
          line: true,
          type: "point"
        });
      } else {
        out.push({
          pts: [c.tip],
          paths: [[...cp, "tip"]],
          col,
          name: nm + " tip",
          line: false,
          type: "point"
        });
      }
    }
    if (Array.isArray(c.tube)) {
      out.push({
        pts: c.tube,
        paths: [[...cp, "tube", 0], [...cp, "tube", 1]],
        col,
        name: nm,
        line: true,
        type: "point"
      });
    }
    if (Array.isArray(c.slice)) {
      if (Array.isArray(c.stations) && c.stations.length) {
        c.stations.forEach((y, k) => {
          if (typeof y === "number") {
            out.push({
              pts: [[0.35, y, 0.5], [0.65, y, 0.5]],
              paths: [[...cp, "stations", k], [...cp, "stations", k]],
              col,
              name: `${nm} st${k + 1} (${y.toFixed(2)})`,
              line: true,
              approx: true,
              type: "station"
            });
          }
        });
        const validStations = c.stations.map((y, k) => ({ y, k })).filter((x) => typeof x.y === "number");
        const spinePts = validStations.map((x) => [0.5, x.y, 0.5]);
        const spinePaths = validStations.map((x) => [...cp, "stations", x.k]);
        if (spinePts.length >= 2) {
          out.push({
            pts: spinePts,
            paths: spinePaths,
            col,
            name: nm + " spine",
            line: true,
            type: "station"
          });
        }
      } else {
        out.push({
          pts: [[0.5, c.slice[0], 0.5], [0.5, c.slice[1], 0.5]],
          paths: [[...cp, "slice", 0], [...cp, "slice", 1]],
          col,
          name: nm + " (slice)",
          line: true,
          approx: true,
          type: "slice_y"
        });
      }
    }
    if (c.first) {
      out.push({
        pts: [c.first],
        paths: [[...cp, "first"]],
        col,
        name: nm + " first",
        type: "point"
      });
    }
  });
  if (Array.isArray(R.head_line)) {
    out.push({
      pts: R.head_line,
      paths: [["rig", "head_line", 0], ["rig", "head_line", 1]],
      col: 0xd070ff,
      name: "head line",
      line: true,
      type: "point"
    });
  }
  if (R.jaw && R.jaw.hinge) {
    const pts = [R.jaw.hinge, R.jaw.tip].filter(Boolean);
    const paths = [["rig", "jaw", "hinge"], R.jaw.tip ? ["rig", "jaw", "tip"] : null].filter(Boolean);
    out.push({
      pts,
      paths,
      col: 0xe0508a,
      name: "jaw",
      line: true,
      type: "point"
    });
  }
  if (draft.humanoid) {
    const H = draft.humanoid;
    if (H.z && typeof H.z === "object") {
      const armZ = typeof H.z.arm === "number" ? H.z.arm : 0.77;
      for (const [k, lv] of Object.entries(H.z)) {
        if (typeof lv === "number") {
          out.push({
            pts: [[0.35, 0.5, lv], [0.65, 0.5, lv]],
            paths: [["humanoid", "z", k], ["humanoid", "z", k]],
            col: 0x5b8ff0,
            name: k + " (" + lv.toFixed(2) + ")",
            line: true,
            type: "height"
          });
        }
      }
      if (H.x && typeof H.x === "object") {
        for (const [k, sp] of Object.entries(H.x)) {
          if (typeof sp === "number") {
            out.push({
              pts: [[sp, 0.5, armZ]],
              paths: [["humanoid", "x", k]],
              col: 0x4cc9f0,
              name: k + ".R (" + sp.toFixed(2) + ")",
              type: "span"
            });
            out.push({
              pts: [[1 - sp, 0.5, armZ]],
              paths: [["humanoid", "x", k]],
              col: 0x4cc9f0,
              name: k + ".L (" + sp.toFixed(2) + ")",
              type: "span"
            });
          }
        }
      }
    }
  }
  return out;
}

function drawMarks() {
  clearGroup(marks);
  const r = F.max * 0.012;
  for (const f of pointFields()) {
    const P = f.pts.filter((p) => Array.isArray(p) && p.length === 3).map((u) => F.fromUnit(u));
    P.forEach((p, i) => {
      const b = ball(p, f.col, r * (i === P.length - 1 ? 1.2 : 0.9));
      if (f.paths && f.paths[i]) {
        b.userData.pointPath = f.paths[i];
        b.userData.pointType = f.type || "point";
      }
      marks.add(b);
      if (i === 0 || i === P.length - 1 || f.type === "station") {
        const t = tag(f.name + (P.length > 2 ? " " + (i + 1) : ""), "", f.col);
        t.position.copy(V(p));
        marks.add(t);
      }
    });
    if (f.line) for (let i = 0; i + 1 < P.length; i++) {
      const s = stick(P[i], P[i + 1], f.col, r * 0.4, f.approx ? 0.5 : 0.9);
      if (s) marks.add(s);
    }
  }
  // rigid parts: each box as a wire box, with its bone
  (rig().rigid_to || []).forEach((b, i) => {
    if (!Array.isArray(b) || !Array.isArray(b[1]) || !Array.isArray(b[2])) return;
    const col = PALETTE[(i + 3) % PALETTE.length];
    const c = []; for (const x of [0, 1]) for (const y of [0, 1]) for (const z of [0, 1]) c.push(F.fromUnit([b[x + 1][0], b[y + 1][1], b[z + 1][2]]));
    const edges = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
    const g = new THREE.BufferGeometry().setFromPoints(edges.flatMap(([a, z]) => [V(c[a]), V(c[z])]));
    const ls = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: col, depthTest: false, transparent: true }));
    ls.renderOrder = 13; marks.add(ls);
    const t = tag("rigid: " + b[0], "", col); t.position.copy(V(c[7])); marks.add(t);
  });
  if (pick && pick.preview) { const p = F.fromUnit(pick.preview); marks.add(ball(p, 0xffffff, r * 1.4)); }
}

function drawSpots() {
  clearGroup(spotsG);
  if (!$("showSpots").checked || !B || !B.spots) return;
  const r = F.max * 0.02;
  B.spots.filter((s) => s.mode === "bend" && Array.isArray(s.at)).slice(0, 8).forEach((s) => {
    const p = F.fromUnit(s.at);
    const m = ball(p, 0xff3030, r * Math.min(2, 0.6 + Math.log10(1 + s.tears) * 0.6), 0.55);
    spotsG.add(m);
    const t = tag(`${s.tears} ${s.tears === 1 ? "tear" : "tears"}`, "spot");
    t.element.title = `${s.tears} edges tore when the new ${s.bone} bent 40°: click for its source bone`;
    t.position.copy(V(p)); t.element.onclick = (e) => { e.stopPropagation(); showSpot(s); };
    spotsG.add(t);
  });
}

function redraw() { F = frame(); L = kind() === "tripo" ? layoutTripo() : { of: {}, chains: [], virtual: {}, dropped: new Set(), unused: new Set(), problems: [], alias: {} }; drawSkeleton(); drawMarks(); drawSpots(); legend(); boneInfo(); }
$("showLabels").onchange = redraw;
$("showSpots").onchange = redraw;

function legend() {
  const el = $("legend");
  if (!SRC) { el.style.display = "none"; return; }
  el.style.display = "";
  let h = "";
  if (kind() === "tripo" && SRC.joints.length) {
    h += `<h4>Chains, as the rig step will read them</h4>`;
    const seen = new Set();
    for (const c of L.chains) {
      const key = c.role + c.how; if (seen.has(key)) continue; seen.add(key);
      const n = L.chains.filter((x) => x.role === c.role && x.how === c.how).length;
      h += `<div class="row"><span class="sw" style="background:${hex(c.colour ?? GUESS)}"></span>${esc(c.role)}${n > 1 ? " ×" + n : ""}` +
           `${c.how === "guessed" ? ' <span class="muted">(guessed: name it)</span>' : ""}</div>`;
    }
    if (L.unused.size) h += `<div class="row"><span class="sw" style="background:${hex(UNUSED)}"></span><span class="muted">not used: ${L.unused.size}</span></div>`;
    if (L.dropped.size) h += `<div class="row"><span class="sw" style="background:${hex(DROP)}"></span><span class="muted">thrown away: ${L.dropped.size}</span></div>`;
    for (const p of L.problems) h += `<div class="note">${esc(p)}</div>`;
  } else if (!SRC.joints.length) {
    h += `<h4>No skeleton in the source</h4><div class="muted">Place the chains by clicking on the model:<br>Pick beside a chain's point, then click.</div>`;
  } else h += `<h4>${esc(SRC.joints.length)} source bones</h4><div class="muted">This kind does not use them.</div>`;
  h += `<div class="muted" style="margin-top:4px">Click a bone to select it. Drag to turn, scroll to zoom.</div>`;
  el.innerHTML = h;
}

// ---------------------------------------------------------------------------------------------------------------
// Clicking: bones, the model, the flat views
// ---------------------------------------------------------------------------------------------------------------

function applyPointEdit(path, type, u) {
  if (!path || !path.length) return;
  if (type === "station" || type === "slice_y") {
    setPath(path, Math.round(u[1] * 1000) / 1000, { noUndo: true });
  } else if (type === "height") {
    setPath(path, Math.round(u[2] * 1000) / 1000, { noUndo: true });
  } else if (type === "span") {
    setPath(path, Math.round(Math.min(u[0], 1 - u[0]) * 1000) / 1000, { noUndo: true });
  } else {
    setPath(path, u, { noUndo: true });
  }
}

const ray = new THREE.Raycaster();
let downAt = null;
let drag3D = null;
const dragPlane = new THREE.Plane();
const planeIntersect = new THREE.Vector3();

renderer.domElement.addEventListener("pointerdown", (e) => {
  downAt = [e.clientX, e.clientY];
  if (!pick) {
    const rect = renderer.domElement.getBoundingClientRect();
    const m = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(m, camera);
    const markHits = ray.intersectObjects(marks.children, false);
    const hitWithPoint = markHits.find((h) => h.object.userData && h.object.userData.pointPath);
    if (hitWithPoint) {
      pushUndo();
      const hitObj = hitWithPoint.object;
      const camDir = new THREE.Vector3();
      camera.getWorldDirection(camDir);
      dragPlane.setFromNormalAndCoplanarPoint(camDir.negate(), hitObj.position);
      controls.enabled = false;
      drag3D = {
        path: hitObj.userData.pointPath,
        type: hitObj.userData.pointType,
        obj: hitObj
      };
    }
  }
});

renderer.domElement.addEventListener("pointermove", (e) => {
  if (drag3D) {
    const rect = renderer.domElement.getBoundingClientRect();
    const m = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(m, camera);
    if (ray.ray.intersectPlane(dragPlane, planeIntersect)) {
      const u = r3(F.toUnit(fromV(planeIntersect)));
      applyPointEdit(drag3D.path, drag3D.type, u);
      drawMarks();
      if (tab === "flat") drawFlat();
    }
  }
});

renderer.domElement.addEventListener("pointerup", (e) => {
  if (drag3D) {
    drag3D = null;
    controls.enabled = true;
    changed();
    return;
  }
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;   // a drag turns the view
  const rect = renderer.domElement.getBoundingClientRect();
  const m = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  ray.setFromCamera(m, camera);
  const wantsPoint = pick && pick.point;
  if (!wantsPoint) {
    const hits = ray.intersectObjects(pickables, false);
    if (hits.length) return clickJoint(hits[0].object.userData.joint);
  }
  const hits = ray.intersectObjects(meshes, false);
  if (!hits.length) { if (!pick) { selected = null; redraw(); } return; }
  let p = hits[0].point.clone();
  if ($("throughMiddle").checked) {
    const out = hits.find((h) => h.distance > hits[0].distance + F.max * 0.002);
    if (out) p = hits[0].point.clone().lerp(out.point, 0.5);
  }
  const u = r3(F.toUnit(fromV(p)));
  if (wantsPoint) pick.point(u); else if (pick) flash("Click a bone for " + pick.label + " (or press Done).");
});

function clickJoint(n) {
  if (pick && pick.joint) { pick.joint(n); return; }
  if (pick && pick.point) return;
  selected = selected === n ? null : n;
  redraw();
}

function flash(t) { const b = $("bannerText"); const old = b.textContent; b.textContent = t; setTimeout(() => { if (pick) b.textContent = pick.text || old; }, 1600); }

function startPick(p) {
  endPick(false);
  pick = p;
  pick.text = p.text || (p.point ? `${p.label}: click on the model${p.many ? " (each click adds a point)" : ""}` : `${p.label}: click a bone${p.many ? " (each click adds or removes one)" : ""}`);
  $("bannerText").textContent = pick.text;
  $("banner").style.display = "block";
  $("view").classList.toggle("pointpick", !!p.point);      // names stop catching clicks meant for the model
  $("bannerNext").style.display = p.next ? "" : "none";
  renderPane();
}
function endPick(render = true) {
  if (!pick) return;
  const done = pick.done; pick = null;
  $("banner").style.display = "none";
  $("view").classList.remove("pointpick");
  if (done) done();
  drawMarks();
  if (render) renderPane();
}
$("bannerDone").onclick = () => endPick();
$("bannerNext").onclick = () => { if (pick && pick.next) { pick.next(); if (tab === "flat") renderPane(); } };
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") endPick();
  if ((e.ctrlKey || e.metaKey) && e.key === "z" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); doUndo(); }
});

// Pick modes for each kind of field. Every one writes through setPath, so Undo takes it back.
const pickers = {
  joint: (path, label) => ({ label, joint: (n) => { setPath(path, n); endPick(); } }),
  joints: (path, label) => ({ label, many: true, joint: (n) => {
    const cur = [...(getPath(path) || [])]; const i = cur.indexOf(n);
    if (i >= 0) cur.splice(i, 1); else cur.push(n);
    setPath(path, cur, { keepEmpty: true });
  }, done: () => { if (empty(getPath(path))) setPath(path, undefined, { noUndo: true }); } }),
  chain: (path, label) => ({ label, many: true, text: label + ": click its bones in order, from the body out (click the last one again to take it back)", joint: (n) => {
    const cur = [...(getPath(path) || [])];
    if (cur[cur.length - 1] === n) cur.pop(); else if (!cur.includes(n)) cur.push(n);
    setPath(path, cur, { keepEmpty: true });
  }, done: () => { if (empty(getPath(path))) setPath(path, undefined, { noUndo: true }); } }),
  point: (path, label) => ({ label, point: (u) => { setPath(path, u); endPick(); }, flat: (u) => setPath(path, u), current: () => getPath(path) }),
  points: (path, label) => ({ label, many: true, point: (u) => { setPath(path, [...(getPath(path) || []), u], { keepEmpty: true }); },
    next: () => setPath(path, [...(getPath(path) || []), clone((getPath(path) || []).slice(-1)[0] || [0.5, 0.5, 0.5])], { keepEmpty: true }),
    flat: (u) => { const cur = [...(getPath(path) || [])]; if (cur.length) cur[cur.length - 1] = u; else cur.push(u); setPath(path, cur); },
    current: () => (getPath(path) || []).slice(-1)[0] }),
  pair: (path, label) => {
    let k = 0;
    return { label, text: label + ": click the first point, then the second", point: (u) => {
      const cur = clone(getPath(path)) || [null, null]; cur[k] = u; if (!cur[1]) cur[1] = u; if (!cur[0]) cur[0] = u;
      setPath(path, cur); k++;
      if (k > 1) endPick(); else { pick.text = label + ": now the second point"; $("bannerText").textContent = pick.text; }
    }, flat: (u) => { const cur = clone(getPath(path)) || [u, u]; cur[Math.min(k, 1)] = u; setPath(path, cur); }, current: () => (getPath(path) || [])[Math.min(k, 1)],
    next: () => { if (k >= 1) return endPick(); k = 1; pick.text = label + ": now the second point"; $("bannerText").textContent = pick.text; $("bannerNext").textContent = "Done"; } };
  },
  box: (path, label) => {
    let first = null;
    return { label, text: label + ": click one corner of the part, then the opposite corner", point: (u) => {
      if (!first) { first = u; pick.preview = u; pick.text = label + ": now the opposite corner"; $("bannerText").textContent = pick.text; drawMarks(); return; }
      const lo = [0, 1, 2].map((k) => Math.max(0, Math.min(first[k], u[k]) - 0.01)), hi = [0, 1, 2].map((k) => Math.min(1, Math.max(first[k], u[k]) + 0.01));
      const cur = clone(getPath(path)) || ["", [0, 0, 0], [1, 1, 1]];
      cur[1] = r3(lo); cur[2] = r3(hi); setPath(path, cur); endPick();
    } };
  },
  height: (path, label) => ({
    label,
    text: label + ": click on the model or flat views to pick height (Z)",
    point: (u) => { setPath(path, Math.round(u[2] * 1000) / 1000); endPick(); },
    flat: (u) => setPath(path, Math.round(u[2] * 1000) / 1000),
    current: () => [0.5, 0.5, getPath(path) ?? 0.5]
  }),
  span: (path, label) => ({
    label,
    text: label + ": click on an arm/hand or flat views to pick span (X)",
    point: (u) => { setPath(path, Math.round(Math.min(u[0], 1 - u[0]) * 1000) / 1000); endPick(); },
    flat: (u) => setPath(path, Math.round(Math.min(u[0], 1 - u[0]) * 1000) / 1000),
    current: () => [getPath(path) ?? 0.2, 0.5, 0.7]
  }),
  station: (path, label) => ({
    label,
    text: label + ": click on the model or flat views to set station position (Y)",
    point: (u) => { setPath(path, Math.round(u[1] * 1000) / 1000); endPick(); },
    flat: (u) => setPath(path, Math.round(u[1] * 1000) / 1000),
    current: () => [0.5, getPath(path) ?? 0.5, 0.5]
  }),
  stations: (path, label) => ({
    label,
    many: true,
    text: label + ": click along the body in 3D or flat views to place stations (Y)",
    point: (u) => {
      const cur = [...(getPath(path) || [])];
      cur.push(Math.round(u[1] * 1000) / 1000);
      cur.sort((a, b) => a - b);
      setPath(path, cur, { keepEmpty: true });
    },
    flat: (u) => {
      const cur = [...(getPath(path) || [])];
      if (cur.length) cur[cur.length - 1] = Math.round(u[1] * 1000) / 1000;
      else cur.push(Math.round(u[1] * 1000) / 1000);
      setPath(path, cur);
    },
    next: () => {
      const cur = [...(getPath(path) || [])];
      const last = cur.length ? cur[cur.length - 1] : 0.5;
      cur.push(Math.round(Math.min(1.0, last + 0.1) * 1000) / 1000);
      setPath(path, cur, { keepEmpty: true });
    },
    current: () => {
      const cur = getPath(path) || [];
      return [0.5, cur.length ? cur[cur.length - 1] : 0.5, 0.5];
    }
  }),
};

// ---------------------------------------------------------------------------------------------------------------
// The selected bone's panel: what it is, what the spec makes of it, and one-click jobs for it
// ---------------------------------------------------------------------------------------------------------------

function boneInfo() {
  const el = $("boneinfo");
  if (!selected || !SRC) { el.style.display = "none"; return; }
  const j = joint(selected) || (L.virtual[selected] && { name: selected, parent: L.virtual[selected].parent, children: [], head: L.virtual[selected].pos });
  if (!j) { el.style.display = "none"; return; }
  const o = L.of[selected];
  const uses = usesOf(selected);
  const made = Object.entries(B.bone_from || {}).filter(([, s]) => s === selected).map(([b]) => b);
  const u = F.toUnit(j.head).map((x) => x.toFixed(2)).join(", ");
  let h = `<h4>Selected bone</h4><div class="n">${esc(selected)}</div>`;
  h += `<div class="sub">parent ${esc(j.parent || "none")}${(j.children || []).length ? " · children " + esc(j.children.join(", ")) : ""}</div>`;
  if (L.virtual[selected]) h += `<div class="sub">a mirrored copy of ${esc(L.virtual[selected].virtual)}</div>`;
  if (L.alias[selected]) h += `<div class="note">sits on ${esc(L.alias[selected])} (no length): the rig uses ${esc(L.alias[selected])}</div>`;
  h += `<div class="sub">at ${u} of the box</div>`;
  h += `<div style="margin-top:4px">${o ? `In the <b>${esc(o.role)}</b> chain, bone ${o.index + 1}${o.how === "guessed" ? " (guessed)" : ""}` : L.dropped.has(selected) ? "Thrown away" : "Not used by the rig"}</div>`;
  if (uses.length) h += `<div class="sub">The spec: ${esc(uses.join(", "))}</div>`;
  if (made.length) h += `<div class="sub">The last rig made ${esc(made.join(", "))} from it</div>`;
  if (kind() === "tripo") {
    h += `<div class="acts"><button class="small" data-q="head">Head bone</button><button class="small" data-q="hips">Hips bone</button>` +
         `<button class="small" data-q="leg">A leg</button><button class="small" data-q="delete">Throw away</button>` +
         `<button class="small" data-q="mirror">Mirror from here</button><button class="small" data-q="clear">Clear from the spec</button></div>`;
    h += `<div class="acts"><input type="text" id="qRole" list="roles" placeholder="role, e.g. wing" style="width:130px"><button class="small" data-q="chain">Start a chain here</button></div>`;
  }
  el.innerHTML = h + `<datalist id="roles">${ROLES.map((r) => `<option value="${r}">`).join("")}</datalist>`;
  el.style.display = "block";
  el.querySelectorAll("button[data-q]").forEach((b) => (b.onclick = () => quick(b.dataset.q)));
}

function quick(q) {
  const n = selected, R = rig();
  if (q === "head" || q === "hips") setPath(["rig", q], n);
  else if (q === "leg") setPath(["rig", "legs"], [...new Set([...(R.legs || []), n])]);
  else if (q === "delete") setPath(["rig", "delete"], [...new Set([...(R.delete || []), n])]);
  else if (q === "mirror") {
    // the bone and its line down to the tip
    const line = [n]; let c = n;
    while (joint(c) && joint(c).children.length === 1) { c = joint(c).children[0]; line.push(c); }
    setPath(["rig", "mirror"], [...(R.mirror || []), line]);
  } else if (q === "chain") {
    const role = ($("qRole").value || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    if (!role) { $("qRole").focus(); return; }
    const ch = clone(R.chains && !Array.isArray(R.chains) ? R.chains : {}) || {};
    for (const k of Object.keys(ch)) { ch[k] = ch[k].filter((x) => x !== n); if (!ch[k].length) delete ch[k]; }
    ch[role] = [...(ch[role] || []), n];
    setPath(["rig", "chains"], ch);
  } else if (q === "clear") {
    pushUndo();
    // a mirror chain the bone is in goes whole, and so do its copies (<bone>_m) wherever they are named
    const gone = new Set([n]);
    if (R.mirror) for (const c of R.mirror) if ((c || []).includes(n)) for (const x of c) gone.add(x + "_m");
    if (R.mirror) { R.mirror = R.mirror.filter((c) => !(c || []).includes(n)); if (!R.mirror.length) delete R.mirror; }
    for (const g of gone) clearOne(R, g);
    changed();
  }
}

function clearOne(R, n) {
  {
    for (const k of ["head", "hips", "shell"]) if (R[k] === n) delete R[k];
    for (const k of ["legs", "delete", "nodeform"]) if (R[k]) { R[k] = R[k].filter((x) => x !== n); if (!R[k].length) delete R[k]; }
    if (R.chains && !Array.isArray(R.chains)) { for (const k of Object.keys(R.chains)) { R.chains[k] = R.chains[k].filter((x) => x !== n); if (!R.chains[k].length) delete R.chains[k]; } if (!Object.keys(R.chains).length) delete R.chains; }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// The side panel: tabs
// ---------------------------------------------------------------------------------------------------------------

function renderTabs() {
  const ne = checked.errors.length, nw = checked.warnings.length;
  const tabs = [["rig", "Rig"], ["audit", "Audit"], ["flat", "Flat views"], ["changes", "Changes" + (ne ? `<span class="count">${ne}</span>` : nw ? `<span class="count w">${nw}</span>` : "")], ["run", "Run"]];
  $("tabs").innerHTML = tabs.map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${l}</button>`).join("");
  $("tabs").querySelectorAll("button").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; renderTabs(); renderPane(); }));
}

function renderPane() {
  const pane = $("pane"), top = pane.scrollTop;
  if (!draft) return;
  const f = { rig: paneRig, audit: paneAudit, flat: paneFlat, changes: paneChanges, run: paneRun }[tab];
  pane.innerHTML = f();
  wirePane(pane);
  if (tab === "flat") drawFlat();
  pane.scrollTop = top;
  if (tab === "run") { const lg = $("log"); if (lg) lg.scrollTop = lg.scrollHeight; }
}

// ---- fields

const P = (path) => esc(JSON.stringify(path));
const errsAt = (path) => {
  const s = path.join(".");
  return { e: checked.errors.filter((x) => x.path === s || x.path.startsWith(s + ".")), w: checked.warnings.filter((x) => x.path === s || x.path.startsWith(s + ".")) };
};
function pickBtn(mode, path, label, text = "Pick") {
  const on = pick && pick.path && JSON.stringify(pick.path) === JSON.stringify(path);
  return `<button class="small ${on ? "picking" : ""}" data-act="pick" data-mode="${mode}" data-path="${P(path)}" data-label="${esc(label)}">${on ? "Picking…" : text}</button>`;
}
function chip(name, path, i) {
  const o = L.of[name];
  const col = L.dropped.has(name) ? DROP : o ? L.chains[o.chain].colour ?? GUESS : UNUSED;
  const bad = SRC && SRC.joints.length && !joint(name) && !L.virtual[name];
  return `<span class="chip" data-act="select" data-joint="${esc(name)}" style="border-left-color:${hex(col)}${bad ? ";border-color:var(--bad)" : ""}" title="${bad ? "no such bone in the source" : "select it in the view"}">` +
         `<b>${esc(name)}</b><span class="x" data-act="rm" data-path="${P(path)}" data-i="${i}" title="remove">×</span></span>`;
}
function pointText(u) { return Array.isArray(u) ? u.map((x) => Number(x).toFixed(2)).join(", ") : "not set"; }
function pointInputs(path, u) {
  return ["x", "y", "z"].map((a, k) => `<input class="pt" type="number" step="0.01" min="0" max="1" title="${a}: ${["0 its right .. 1 its left", "0 nose .. 1 tail", "0 bottom .. 1 top"][k]}" data-set="num" data-path="${P([...path, k])}" value="${Array.isArray(u) ? u[k] : ""}" placeholder="${a}">`).join("");
}

function fieldHtml(f) {
  const key = f.store || f.key, path = ["rig", key], v = getPath(path), t = f.type;
  const { e, w } = errsAt(path);
  let body = "";
  if (t === "enum") {
    body = `<select data-set="enum" data-path="${P(path)}">${f.options.map((o) => `<option value="${esc(JSON.stringify(o))}" ${JSON.stringify(v ?? "") === JSON.stringify(o) ? "selected" : ""}>${o === "" ? "(not set)" : o === false ? "false (plain bone heat)" : esc(o)}</option>`).join("")}</select>`;
  } else if (t === "bool") {
    const on = v === undefined ? !!f.default : !!v;
    body = `<label><input type="checkbox" data-set="bool" data-default="${f.default ? 1 : 0}" data-path="${P(path)}" ${on ? "checked" : ""}> ${on ? "on" : "off"}</label>`;
  } else if (t === "number" || t === "int") {
    body = `<input type="number" data-set="num" data-path="${P(path)}" value="${v ?? ""}" step="${t === "int" ? 1 : 0.01}" ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""} placeholder="default">`;
  } else if (t === "text") {
    body = `<input type="text" data-set="text" data-path="${P(path)}" value="${esc(v ?? "")}" placeholder="default">`;
  } else if (t === "texts") {
    body = `<input type="text" data-set="texts" data-path="${P(path)}" value="${esc((v || []).join(", "))}" placeholder="names, separated by commas" style="width:100%">`;
  } else if (t === "vec3") {
    const dirs = [["", "from head and hips"], ["[0,-1,0]", "-Y"], ["[0,1,0]", "+Y"], ["[1,0,0]", "+X"], ["[-1,0,0]", "-X"]];
    const cur = v ? JSON.stringify(v) : "";
    body = `<div class="chips">${dirs.map(([d, l]) => `<button class="small ${cur === d ? "on" : ""}" data-act="setjson" data-path="${P(path)}" data-v="${esc(d)}">${l}</button>`).join("")}` +
           `${cur && !dirs.some(([d]) => d === cur) ? `<span class="muted">${esc(cur)}</span>` : ""}</div>`;
  } else if (t === "joint") {
    body = `<div class="chips">${v ? chip(v, path, -1) : `<span class="chip empty">not set</span>`}${pickBtn("joint", path, f.label)}</div>`;
  } else if (t === "joints") {
    body = `<div class="chips">${(v || []).map((n, i) => chip(n, path, i)).join("") || `<span class="chip empty">none</span>`}${pickBtn("joints", path, f.label)}</div>`;
  } else if (t === "joint_chains") {
    body = (v || []).map((c, i) => `<div class="sub-row"><span class="muted">${i + 1}.</span><div class="chips grow">${(c || []).map((n, k) => chip(n, [...path, i], k)).join("") || `<span class="chip empty">no bones yet</span>`}</div>` +
           `${pickBtn("chain", [...path, i], f.label + " " + (i + 1))}<button class="small" data-act="rmrow" data-path="${P(path)}" data-i="${i}" title="remove this chain">×</button></div>`).join("") +
           `<button class="small" data-act="addrow" data-path="${P(path)}" data-v="[]">+ Mirror a limb</button>`;
  } else if (t === "role_joints") {
    const obj = v && !Array.isArray(v) ? v : {};
    body = Object.entries(obj).map(([role, tops]) => {
      const col = L.colourOf && L.colourOf[role];
      return `<div class="sub-row"><span class="sw" style="width:10px;height:10px;border-radius:3px;background:${col !== undefined ? hex(col) : "#666"}"></span>` +
             `<input type="text" list="roles" value="${esc(role)}" style="width:108px" data-set="rolename" data-role="${esc(role)}" title="the role: wing, leg, tail, abdomen...">` +
             `<div class="chips grow">${(tops || []).map((n, k) => chip(n, [...path, role], k)).join("") || `<span class="chip empty">pick its first bones</span>`}</div>` +
             `${pickBtn("joints", [...path, role], "Chain " + role)}<button class="small" data-act="rmkey" data-path="${P(path)}" data-k="${esc(role)}" title="remove this chain">×</button></div>`;
    }).join("") + `<div class="sub-row"><input type="text" list="roles" id="newRole" placeholder="new role, e.g. wing" style="width:140px"><button class="small" data-act="addrole">+ Add a chain and pick its bones</button></div>`;
  } else if (t === "point_pair") {
    body = [0, 1].map((k) => `<div class="sub-row"><span class="muted">${k ? "to" : "from"}</span>${pointInputs([...path, k], (v || [])[k])}</div>`).join("") +
           `<div class="chips">${pickBtn("pair", path, f.label, "Pick both points")}${v ? `<button class="small" data-act="clear" data-path="${P(path)}">Clear</button>` : ""}</div>`;
  } else if (t === "jaw") {
    const j = v || {};
    body = ["hinge", "tip"].map((k) => `<div class="sub-row"><span class="muted" style="width:40px">${k}</span>${pointInputs([...path, k], j[k])}${pickBtn("point", [...path, k], "Jaw " + k)}</div>`).join("") +
           `<div class="sub-row"><span class="muted">band</span><input type="number" step="0.01" data-set="num" data-path="${P([...path, "band"])}" value="${j.band ?? ""}" placeholder="0.08">` +
           `${v ? `<button class="small" data-act="clear" data-path="${P(path)}">Clear</button>` : ""}</div>`;
  } else if (t === "add_tail") {
    const a = v || {};
    body = `<div class="sub-row">${[["from", "0.6"], ["bones", "4"], ["width", "0.5"], ["above", ""]].map(([k, ph]) => `<span class="muted">${k}</span><input type="number" step="${k === "bones" ? 1 : 0.01}" data-set="num" data-path="${P([...path, k])}" value="${a[k] ?? ""}" placeholder="${ph}">`).join("")}</div>`;
  } else if (t === "hard_split") {
    const a = v || {};
    body = `<div class="sub-row"><span class="muted">bone</span>${boneInput([...path, "bone"], a.bone)}<span class="muted">else</span>${boneInput([...path, "else"], a.else)}` +
           `<span class="muted">above</span><input type="number" step="0.01" data-set="num" data-path="${P([...path, "above"])}" value="${a.above ?? ""}"></div>`;
  } else if (t === "box_list") {
    body = (v || []).map((b, i) => `<div class="card"><div class="hd"><span class="muted">bone</span>${boneInput([...path, i, 0], b[0])}<span class="grow"></span>` +
           `${pickBtn("box", [...path, i], "Rigid part " + (i + 1), "Pick corners")}<button class="small" data-act="rmrow" data-path="${P(path)}" data-i="${i}">×</button></div>` +
           `<div class="sub-row"><span class="muted" style="width:34px">from</span>${pointInputs([...path, i, 1], b[1])}</div><div class="sub-row"><span class="muted" style="width:34px">to</span>${pointInputs([...path, i, 2], b[2])}</div></div>`).join("") +
           `<button class="small" data-act="addrow" data-path="${P(path)}" data-v='["", [0.4, 0.4, 0.4], [0.6, 0.6, 0.6]]'>+ Add a rigid part</button>`;
  } else if (t === "allowances") {
    const a = v && v.thresholds ? v.thresholds : v || {};
    const inner = v && v.thresholds ? [...path, "thresholds"] : path;
    body = `<table><tr><th>Check</th><th>Normal</th><th>This model</th></tr>${(B.schema.thresholds || []).map((k) => `<tr><td>${esc(CHECKS[k] || k)}</td><td class="num">${HIGHER_IS_BETTER[k] ? "≥ " : "≤ "}${esc(defaultThreshold(k))}</td>` +
           `<td><input type="number" step="any" data-set="num" data-path="${P([...inner, k])}" value="${a[k] ?? ""}" placeholder="normal"></td></tr>`).join("")}</table>` +
           `<div class="muted" style="margin-top:4px">Why (required with an allowance):</div><textarea data-set="text" data-path="${P(["notes", "rig.audit"])}" placeholder="e.g. the wing tips are paper-thin; a small tear there does not show">${esc((draft.notes || {})["rig.audit"] || "")}</textarea>`;
    const n = errsAt(["notes", "rig.audit"]); e.push(...n.e);
  } else if (t === "build_chains") {
    body = buildChains(Array.isArray(v) ? v : []);
  }
  return `<div class="field ${e.length ? "err" : ""}" data-key="${esc(key)}"><div class="lab"><span>${esc(f.label)}</span><span class="grow"></span>` +
         `${v !== undefined && !["kind"].includes(key) && t !== "build_chains" ? `<button class="small" data-act="clear" data-path="${P(path)}" title="remove the field: back to the default">reset</button>` : ""}</div>` +
         `<div class="help">${esc(f.help)}</div>${body}${e.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("")}${w.map((x) => `<div class="fwarn">${esc(x.message)}</div>`).join("")}</div>`;
}

function boneInput(path, v) {
  return `<input type="text" list="rigbones" data-set="text" data-path="${P(path)}" value="${esc(v ?? "")}" placeholder="a bone of the new rig" style="width:130px" title="a bone of the NEW rig (the names the last rig made: head, wing_fore_1.L...)">`;
}
function defaultThreshold(k) { return { bleed_pct: 2, combined_tears: 0, bend_tears: 0, head_pct: 2.5, max_influences: 4 }[k]; }

function mirrorChain(idx) {
  const chains = rig().chains;
  if (!Array.isArray(chains) || !chains[idx]) return;
  pushUndo();
  const orig = chains[idx];
  const copy = mirrorChainData(orig, chains);
  chains.splice(idx + 1, 0, copy);
  changed();
  flash(`Mirrored ${orig.name || "chain"} → ${copy.name || "chain"}`);
}

function buildChains(chains) {
  const path = ["rig", "chains"];
  let h = "";
  chains.forEach((c, i) => {
    const cp = [...path, i];
    const how = ["tip", "points", "slice", "tube"].find((k) => k in c) || "tip";
    const col = i === 0 ? SPINE : PALETTE[i % PALETTE.length];
    h += `<div class="card" style="border-left:4px solid ${hex(col)}"><div class="hd"><b>${i === 0 ? "Body" : "Chain " + (i + 1)}</b>` +
         `<input type="text" data-set="text" data-path="${P([...cp, "name"])}" value="${esc(c.name || "")}" placeholder="name" style="width:100px">` +
         `<input type="text" list="roles" data-set="text" data-path="${P([...cp, "role"])}" value="${esc(c.role || "")}" placeholder="role (= name)" style="width:100px">` +
         `<span class="grow"></span>` +
         (i > 0 ? `<button class="small" data-act="mirrorchain" data-i="${i}" title="Mirror this placed chain across the symmetry plane (X -> 1 - X)">+ Mirror chain</button>` : "") +
         `<button class="small" data-act="rmrow" data-path="${P(path)}" data-i="${i}">×</button></div>` +
         `<div class="sub-row"><span class="muted">placed by</span><select data-set="how" data-path="${P(cp)}">${[["tip", "its tip (a limb)"], ["points", "its joints"], ["slice", "a slice along the body"], ["tube", "two ends (a tube)"]].map(([k, l]) => `<option value="${k}" ${how === k ? "selected" : ""}>${l}</option>`).join("")}</select>` +
         `<span class="muted">bones</span><input type="number" min="1" step="1" data-set="num" data-path="${P([...cp, "bones"])}" value="${c.bones ?? ""}" placeholder="1" style="width:50px"></div>`;
    if (how === "tip") {
      h += `<div class="sub-row"><span class="muted" style="width:34px">tip</span>${pointInputs([...cp, "tip"], c.tip)}${pickBtn("point", [...cp, "tip"], (c.name || "chain") + " tip")}</div>` +
           `<div class="sub-row"><span class="muted" style="width:34px">base</span>${pointInputs([...cp, "base"], c.base)}${pickBtn("point", [...cp, "base"], (c.name || "chain") + " base")}</div>`;
    } else if (how === "points") {
      h += `<div class="sub-row"><div class="grow">${(c.points || []).map((u, k) => `<div>${k + 1}. ${pointText(u)} <span class="chip" data-act="rm" data-path="${P([...cp, "points"])}" data-i="${k}" style="padding:0 6px">×</span></div>`).join("") || '<span class="muted">no joints yet</span>'}</div>${pickBtn("points", [...cp, "points"], (c.name || "chain") + " joints")}</div>`;
    } else if (how === "slice") {
      h += `<div class="sub-row"><span class="muted">from y</span><input type="number" step="0.01" data-set="num" data-path="${P([...cp, "slice", 0])}" value="${(c.slice || [])[0] ?? ""}">` +
           `<span class="muted">to y</span><input type="number" step="0.01" data-set="num" data-path="${P([...cp, "slice", 1])}" value="${(c.slice || [])[1] ?? ""}"><span class="muted">(0 nose .. 1 tail)</span></div>`;
      if (Array.isArray(c.stations)) {
        h += `<div class="sub-row"><span class="muted" style="width:50px">stations</span><div class="chips grow">` +
             c.stations.map((stVal, sIdx) =>
               `<span class="chip" style="padding:2px 5px" title="Station ${sIdx + 1}: Y = ${Number(stVal).toFixed(2)}">` +
               `<span class="muted" style="font-size:10px">#${sIdx + 1}</span> ` +
               `<input type="number" step="0.01" min="0" max="1" data-set="num" data-path="${P([...cp, "stations", sIdx])}" value="${stVal ?? ""}" style="width:54px;padding:1px 3px">` +
               pickBtn("station", [...cp, "stations", sIdx], `${c.name || "body"} station ${sIdx + 1}`) +
               `<span class="x" data-act="rm" data-path="${P([...cp, "stations"])}" data-i="${sIdx}" title="remove station">×</span></span>`
             ).join("") +
             `</div></div>` +
             `<div class="chips" style="margin-top:3px;margin-bottom:4px">` +
             pickBtn("stations", [...cp, "stations"], `${c.name || "body"} stations`, "+ Pick stations") +
             `<button class="small" data-act="genstations" data-path="${P(cp)}" title="Evenly space stations between slice endpoints">Even spacing</button>` +
             `<button class="small" data-act="clearstations" data-path="${P(cp)}" title="Remove custom stations (revert to uniform slice)">Clear stations</button>` +
             `</div>`;
      } else {
        h += `<div class="chips" style="margin-top:3px;margin-bottom:4px">` +
             `<button class="small" data-act="addstations" data-path="${P(cp)}" title="Set custom joint positions along the body instead of even spacing">+ Custom stations</button>` +
             pickBtn("stations", [...cp, "stations"], `${c.name || "body"} stations`, "Pick stations") +
             `</div>`;
      }
    } else {
      h += [0, 1].map((k) => `<div class="sub-row"><span class="muted" style="width:34px">${k ? "end" : "start"}</span>${pointInputs([...cp, "tube", k], (c.tube || [])[k])}</div>`).join("") + `<div class="chips">${pickBtn("pair", [...cp, "tube"], (c.name || "chain") + " ends")}</div>`;
    }
    if (i > 0) {
      const names = chains.slice(0, i).map((x) => x.name).filter(Boolean);
      h += `<div class="sub-row"><span class="muted">hangs from</span><select data-set="parent" data-path="${P([...cp, "parent"])}"><option value="">the body (nearest)</option>${names.map((n) => `<option ${c.parent && c.parent[0] === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>` +
           `<label><input type="checkbox" data-set="bool" data-path="${P([...cp, "ik"])}" ${c.ik ? "checked" : ""}> IK foot</label>` +
           `<label><input type="checkbox" data-set="bool" data-path="${P([...cp, "parent_nearest"])}" ${c.parent_nearest ? "checked" : ""}> nearest body bone</label></div>`;
    }
    const { e } = errsAt(cp); h += e.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("") + `</div>`;
  });
  return h + `<button class="small" data-act="addrow" data-path="${P(path)}" data-v='${esc(JSON.stringify(chains.length ? { name: "", tip: [0.5, 0.5, 0.5] } : { name: "spine", slice: [0.1, 0.9], bones: 4 }))}'>+ Add a chain</button>`;
}

function humanoidHtml() {
  const H = draft.humanoid || {};
  let h = `<div class="group" data-group="Humanoid"><h3>Humanoid</h3><div class="fields">`;
  if (!draft.humanoid) {
    h += `<div class="note info">This rig is kind: humanoid but has no humanoid section yet.</div>` +
         `<div style="margin:6px 0"><button class="small primary" data-act="inithumanoid">+ Initialize standard humanoid proportions</button></div>`;
    return h + `</div></div>`;
  }
  // Facing
  const dirs = [["[0,-1,0]", "-Y (facing front)"], ["[0,1,0]", "+Y (facing back)"], ["[1,0,0]", "+X"], ["[-1,0,0]", "-X"]];
  const curF = H.forward ? JSON.stringify(H.forward) : "";
  const { e: fe } = errsAt(["humanoid", "forward"]);
  h += `<div class="field ${fe.length ? "err" : ""}"><div class="lab">Facing</div>` +
       `<div class="help">The direction the source sculpt faces. Standard humanoid rigs expect -Y.</div>` +
       `<div class="chips">${dirs.map(([d, l]) => `<button class="small ${curF === d ? "on" : ""}" data-act="setjson" data-path="${P(["humanoid", "forward"])}" data-v="${esc(d)}">${l}</button>`).join("")}</div>` +
       fe.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("") + `</div>`;

  // Heights (Z)
  const zKeys = [
    ["top", "Top of head", "top of skull / helmet (usually 1.0)"],
    ["head", "Head joint", "base of skull / chin level"],
    ["neck", "Neck joint", "base of neck"],
    ["arm", "Arm height", "shoulder level where arms attach"],
    ["spine2", "Spine 2 (chest)", "upper chest"],
    ["spine1", "Spine 1 (mid spine)", "mid-back / ribcage"],
    ["spine", "Spine (waist)", "narrowest part of waist"],
    ["hip", "Hip (pelvis)", "hip joints / pelvis"],
    ["knee", "Knee joint", "kneecap level"],
    ["ankle", "Ankle joint", "ankle bones / top of foot"]
  ];
  const { e: ze, w: zw } = errsAt(["humanoid", "z"]);
  h += `<div class="field ${ze.length ? "err" : ""}"><div class="lab">Heights (Z)</div>` +
       `<div class="help">Heights as 0 (floor) to 1 (top of head). Click Pick to click points in 3D or flat views.</div>` +
       ze.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("") +
       zw.map((x) => `<div class="fwarn">${esc(x.message)}</div>`).join("");
  for (const [k, lbl, desc] of zKeys) {
    const val = (H.z || {})[k];
    const path = ["humanoid", "z", k];
    const { e: ke } = errsAt(path);
    h += `<div class="sub-row ${ke.length ? "err" : ""}"><span style="width:115px" title="${esc(desc)}"><b>${esc(lbl)}</b></span>` +
         `<input type="number" step="0.005" min="0" max="1" data-set="num" data-path="${P(path)}" value="${val ?? ""}" style="width:70px">` +
         pickBtn("height", path, lbl) +
         `<span class="muted grow" style="font-size:11px">${esc(desc)}</span>` +
         ke.map((x) => `<div class="ferr" style="width:100%">${esc(x.message)}</div>`).join("") +
         `</div>`;
  }
  h += `</div>`;

  // Spans (X)
  const xKeys = [
    ["shoulder", "Shoulder span", "center to shoulder joint"],
    ["elbow", "Elbow span", "center to elbow"],
    ["wrist", "Wrist span", "center to wrist joint"],
    ["knuckle", "Knuckle span", "center to base of fingers"],
    ["tip", "Fingertip span", "outermost fingertips (usually 0.0)"]
  ];
  const { e: xe, w: xw } = errsAt(["humanoid", "x"]);
  h += `<div class="field ${xe.length ? "err" : ""}"><div class="lab">Spans (X)</div>` +
       `<div class="help">Distance from center (0 outermost tip .. 0.5 center). Click Pick to click an arm/hand in 3D or flat views.</div>` +
       xe.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("") +
       xw.map((x) => `<div class="fwarn">${esc(x.message)}</div>`).join("");
  for (const [k, lbl, desc] of xKeys) {
    const val = (H.x || {})[k];
    const path = ["humanoid", "x", k];
    const { e: ke } = errsAt(path);
    h += `<div class="sub-row ${ke.length ? "err" : ""}"><span style="width:115px" title="${esc(desc)}"><b>${esc(lbl)}</b></span>` +
         `<input type="number" step="0.005" min="0" max="0.5" data-set="num" data-path="${P(path)}" value="${val ?? ""}" style="width:70px">` +
         pickBtn("span", path, lbl) +
         `<span class="muted grow" style="font-size:11px">${esc(desc)}</span>` +
         ke.map((x) => `<div class="ferr" style="width:100%">${esc(x.message)}</div>`).join("") +
         `</div>`;
  }
  h += `</div>`;

  h += `</div></div>`;
  return h;
}

function paneRig() {
  const S = B.schema;
  let h = "";
  if (B.parse_error) h += `<div class="note">rig.json on disk cannot be read (${esc(B.parse_error)}). The form starts from an empty spec; Save replaces the file (the old one is kept as rig.json.bak).</div>`;
  if (!B.text) h += `<div class="note info">This model has no rig.json yet. Fill in the fields and press Save.</div>`;
  const groups = {};
  for (const f of S.rig) if (!f.kinds || f.kinds.includes(kind())) (groups[f.group] ||= []).push(f);
  const shut = JSON.parse(sessionStorage.getItem("shut") || '["Tuning"]');
  for (const [g, fs] of Object.entries(groups)) {
    h += `<div class="group ${shut.includes(g) ? "shut" : ""}" data-group="${esc(g)}"><h3>${esc(g)}</h3><div class="fields">${fs.map(fieldHtml).join("")}</div></div>`;
    if (g === "Basics" && kind() === "humanoid") {
      h += humanoidHtml();
    }
  }
  if (!groups["Basics"] && kind() === "humanoid") {
    h += humanoidHtml();
  }
  h += `<div class="group ${shut.includes("Output") ? "shut" : ""}" data-group="Output"><h3>Output</h3><div class="fields">${S.other.map(otherHtml).join("")}</div></div>`;
  h += `<datalist id="roles">${ROLES.map((r) => `<option value="${r}">`).join("")}</datalist>`;
  h += `<datalist id="rigbones">${(B.rig_bones || []).map((r) => `<option value="${esc(r)}">`).join("")}</datalist>`;
  return h;
}

function otherHtml(f) {
  const path = f.key.split("."), v = getPath(path);
  const { e } = errsAt(path);
  let body;
  if (f.type === "enum") body = `<select data-set="enum" data-path="${P(path)}">${f.options.map((o) => `<option value="${esc(JSON.stringify(o))}" ${(v ?? "") === o ? "selected" : ""}>${o === "" ? "(none)" : esc(o)}</option>`).join("")}</select>`;
  else if (f.type === "text") body = `<input type="text" data-set="text" data-path="${P(path)}" value="${esc(v ?? "")}">`;
  else if (f.type === "int_or_null") body = `<input type="number" step="1" data-set="budget" data-path="${P(path)}" value="${v ?? ""}" placeholder="${v === null ? "full resolution" : "collection default"}"> <label><input type="checkbox" data-set="fullres" ${v === null ? "checked" : ""}> full resolution</label>`;
  else body = `<input type="number" step="0.01" data-set="num" data-path="${P(path)}" value="${v ?? ""}">`;
  return `<div class="field ${e.length ? "err" : ""}"><div class="lab">${esc(f.label)}</div>${f.help ? `<div class="help">${esc(f.help)}</div>` : ""}${body}${e.map((x) => `<div class="ferr">${esc(x.message)}</div>`).join("")}</div>`;
}

// ---- audit

function verdictTable(a, before) {
  if (!a) return `<div class="muted">No audit yet.</div>`;
  let h = `<table><tr><th>Check</th>${before ? "<th>Before</th>" : ""}<th>${before ? "After" : "Value"}</th><th>Limit</th><th></th></tr>`;
  for (const [k, c] of Object.entries(a.checks)) {
    const b = before && before.checks[k];
    let cls = "";
    if (b && b.value != null && c.value != null && b.value !== c.value) cls = (HIGHER_IS_BETTER[k] ? c.value > b.value : c.value < b.value) ? "good" : "worse";
    h += `<tr><td>${esc(CHECKS[k] || k)}</td>${before ? `<td class="num">${esc(b ? b.value : "")}${b ? (b.ok ? " ✓" : " ✗") : ""}</td>` : ""}` +
         `<td class="num ${cls}"><b>${esc(c.value)}</b>${cls ? (cls === "good" ? " ▲" : " ▼") : ""}</td><td class="num">${HIGHER_IS_BETTER[k] ? "≥ " : "≤ "}${esc(c.limit)}</td>` +
         `<td><span class="verdict ${c.ok ? "pass" : "fail"}">${c.ok ? "PASS" : "FAIL"}</span></td></tr>`;
  }
  return h + `</table>`;
}

function paneAudit() {
  let h = "";
  const after = B.audit, before = B.before;
  const fresh = before && B.audit_time && B.audit_time > before.time;
  if (fresh) {
    h += `<h3 style="margin:6px 0">Before and after your edit</h3><div>Before <span class="verdict ${before.pass ? "pass" : "fail"}">${before.pass ? "PASS" : "FAIL"}</span> → after <span class="verdict ${after.pass ? "pass" : "fail"}">${after.pass ? "PASS" : "FAIL"}</span></div>`;
    h += verdictTable(after, before);
  } else if (after) {
    h += `<h3 style="margin:6px 0">The last audit <span class="verdict ${after.pass ? "pass" : "fail"}">${after.pass ? "PASS" : "FAIL"}</span></h3>` + verdictTable(after);
    if (before) h += `<div class="muted">A re-rig is under way or failed: the audit has not been written again yet.</div>`;
  } else h += `<div class="muted">No audit yet: press Save and re-rig.</div>`;
  if (after && after.warnings.length) h += `<div class="note">Warnings: ${esc(after.warnings.join("; "))}</div>`;
  const hc = after && after.checks.head_pct;
  if (hc && !hc.ok) {
    h += `<div class="note">The head moves too little of the model (${esc(hc.value)}%, needs ${esc(hc.limit)}%). ` +
         `Check the Head bone is really the head (it is ${esc(rig().head || "not set")}), and that the head chain reaches the face: a Head line or Jaw can give it more.</div>`;
  }
  if (B.spots && B.spots.length) {
    h += `<h3 style="margin:10px 0 4px">Where it tears</h3><div class="muted">Each joint of the new rig is bent 40° on its own; these tore (the red balls in the view). Click one to see which source bone it came from.</div><ul class="spotlist">`;
    for (const s of B.spots.slice(0, 16)) {
      const from = B.bone_from[s.bone];
      h += `<li data-act="spot" data-bone="${esc(s.bone)}" data-mode="${s.mode}"><b>${s.tears}</b> ${s.tears === 1 ? "tear" : "tears"} ${s.mode === "bend" ? "bending" : "twisting"} <b>${esc(s.bone)}</b>${from ? ` (from ${esc(from)})` : ""}` +
           `${s.owners.length ? `<span class="muted"> · worst edge on ${esc(s.owners.join(", "))}</span>` : ""}</li>`;
    }
    h += `</ul>`;
  }
  if (B.rig_error) h += `<div class="note">The last rig failed: ${esc(B.rig_error)}</div>`;
  return h;
}

function showSpot(s) {
  const from = B.bone_from[s.bone];
  if (from && (joint(from) || L.virtual[from])) selected = from;
  redraw();
  const p = F.fromUnit(s.at);
  controls.target.copy(V(p));
  flash(`${s.tears} edges tore when ${s.bone}${from ? " (from " + from + ")" : ""} bent; the worst edge is on ${s.owners.join(", ")}`);
  $("banner").style.display = "block"; setTimeout(() => { if (!pick) $("banner").style.display = "none"; }, 2600);
}

// ---- flat views (measure.py on the draft)

function paneFlat() {
  let h = `<div class="help">Three flat views of the model on the 0..1 grid, with the rig <b>this draft</b> would build drawn over it (side, front, top). ` +
          `Use them to place points exactly: press Pick beside a point field, then click here; a click in one view sets two of its three numbers, so click in two views.</div>`;
  h += `<div class="chips" style="margin:6px 0"><button data-act="measure" class="${B.measure ? "" : "primary"}">${B.measure ? "Draw again from the draft" : "Draw the flat views"}</button><span class="muted">about 5-20 s</span>` +
       `${B.measure ? `<button data-act="flatbig">${flatBig ? "Back in the panel" : "Show them large"}</button>` : ""}</div>`;
  if (B.measure) h += `<div id="flat" class="${flatBig ? "large" : ""}"><img id="flatImg" src="${esc(withToken(B.measure))}" alt="side, front and top views"><canvas id="flatCv"></canvas></div>` +
                      `<div class="muted">side (nose left) · front (its left on the right) · top (nose up). Red lines every 0.5, blue every 0.1.</div>`;
  if (pick && pick.flat) h += `<div class="note info">${esc(pick.label)}: now ${pointText(pick.current && pick.current())}</div>`;
  return h;
}

function flatMap(tile, fx, fy, u) {
  // a click at (fx, fy), 0..1 across one of measure.py's views (fy down), to the 0..1 point it sets
  const ext = F.max * 1.12, s = F.size, out = [...(u || [0.5, 0.5, 0.5])];
  const a = (f, k) => 0.5 + (f - 0.5) * ext / s[k];
  if (tile === 0) { out[1] = a(fx, 1); out[2] = a(1 - fy, 2); }
  else if (tile === 1) { out[0] = a(fx, 0); out[2] = a(1 - fy, 2); }
  else { out[0] = a(1 - fx, 0); out[1] = a(fy, 1); }
  return r3(out);
}
function flatPix(tile, u) {
  const ext = F.max * 1.12, s = F.size;
  const f = (v, k) => 0.5 + (v - 0.5) * s[k] / ext;
  if (tile === 0) return [f(u[1], 1), 1 - f(u[2], 2)];
  if (tile === 1) return [f(u[0], 0), 1 - f(u[2], 2)];
  return [1 - f(u[0], 0), f(u[1], 1)];
}
let dragTarget = null;
let draggingPick = false;

function findNearestFlatPoint(clientX, clientY, img) {
  const r = img.getBoundingClientRect();
  const tw = r.width / 3, th = r.height;
  const mx = clientX - r.left, my = clientY - r.top;
  let best = null, bestDist = 16;

  const fields = pointFields();
  for (const f of fields) {
    if (!f.paths) continue;
    for (let i = 0; i < f.pts.length; i++) {
      const u = f.pts[i];
      const pth = f.paths[i];
      if (!Array.isArray(u) || !pth) continue;
      for (let t = 0; t < 3; t++) {
        const [px, py] = flatPix(t, u);
        const sx = t * tw + px * tw, sy = py * th;
        const d = Math.hypot(mx - sx, my - sy);
        if (d < bestDist) {
          bestDist = d;
          best = { field: f, ptIdx: i, pt: [...u], path: pth, type: f.type || "point", tile: t };
        }
      }
    }
  }
  return best;
}

function wireFlatEvents(img, cv) {
  if (img._flatWired) return;
  img._flatWired = true;

  const getTileAndU = (e, currentPt) => {
    const r = img.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * 3;
    const t = Math.min(2, Math.max(0, Math.floor(x)));
    const fx = Math.min(1, Math.max(0, x - t));
    const fy = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    return { tile: t, u: flatMap(t, fx, fy, currentPt) };
  };

  img.addEventListener("pointerdown", (e) => {
    if (pick && pick.flat) {
      draggingPick = true;
      try { img.setPointerCapture(e.pointerId); } catch (_) {}
      const { u } = getTileAndU(e, pick.current && pick.current());
      pick.flat(u);
      drawFlat();
      drawMarks();
      const banner = $("bannerText");
      if (banner && pick.label) banner.textContent = `${pick.label}: ${pointText(u)}`;
      return;
    }
    const near = findNearestFlatPoint(e.clientX, e.clientY, img);
    if (near) {
      pushUndo();
      dragTarget = near;
      dragTarget.currentPt = [...near.pt];
      try { img.setPointerCapture(e.pointerId); } catch (_) {}
      drawFlat();
      drawMarks();
    } else {
      flash("Press Pick beside a point field, or drag an existing joint.");
      $("banner").style.display = "block";
      setTimeout(() => { if (!pick && !dragTarget) $("banner").style.display = "none"; }, 1800);
    }
  });

  img.addEventListener("pointermove", (e) => {
    if (draggingPick && pick && pick.flat) {
      const { u } = getTileAndU(e, pick.current && pick.current());
      pick.flat(u);
      drawFlat();
      drawMarks();
      const banner = $("bannerText");
      if (banner && pick.label) banner.textContent = `${pick.label}: ${pointText(u)}`;
      return;
    }
    if (dragTarget) {
      const { u } = getTileAndU(e, dragTarget.currentPt);
      dragTarget.currentPt = u;
      applyPointEdit(dragTarget.path, dragTarget.type, u);
      drawFlat();
      drawMarks();
      return;
    }
  });

  const onPointerUp = (e) => {
    if (draggingPick) {
      draggingPick = false;
      try { img.releasePointerCapture(e.pointerId); } catch (_) {}
      renderPane();
      return;
    }
    if (dragTarget) {
      dragTarget = null;
      try { img.releasePointerCapture(e.pointerId); } catch (_) {}
      changed();
    }
  };

  img.addEventListener("pointerup", onPointerUp);
  img.addEventListener("pointercancel", onPointerUp);
}

function drawFlat() {
  const img = $("flatImg"), cv = $("flatCv");
  if (!img || !cv) return;
  const go = () => {
    cv.width = img.clientWidth; cv.height = img.clientHeight;
    const g = cv.getContext("2d"), tw = cv.width / 3, th = cv.height;
    g.clearRect(0, 0, cv.width, cv.height);
    const dot = (u, col, r) => {
      for (let t = 0; t < 3; t++) {
        const [x, y] = flatPix(t, u);
        g.fillStyle = col;
        g.beginPath();
        g.arc(t * tw + x * tw, y * th, r, 0, 7);
        g.fill();
      }
    };
    for (const f of pointFields()) {
      if (f.line && f.pts.length >= 2) {
        g.strokeStyle = hex(f.col);
        g.lineWidth = 1.5;
        for (let t = 0; t < 3; t++) {
          g.beginPath();
          let started = false;
          for (let i = 0; i < f.pts.length; i++) {
            if (!Array.isArray(f.pts[i])) continue;
            const [x, y] = flatPix(t, f.pts[i]);
            if (!started) { g.moveTo(t * tw + x * tw, y * th); started = true; }
            else g.lineTo(t * tw + x * tw, y * th);
          }
          if (started) g.stroke();
        }
      }
      for (const u of f.pts) if (Array.isArray(u)) dot(u, hex(f.col), 3.5);
    }
    for (const b of rig().rigid_to || []) if (Array.isArray(b[1]) && Array.isArray(b[2])) {
      dot(b[1], "#ff9f1c", 3);
      dot(b[2], "#ff9f1c", 3);
    }
    const cur = pick && pick.current && pick.current();
    if (Array.isArray(cur)) { dot(cur, "#000", 6); dot(cur, "#fff", 3.5); }
    if (dragTarget && dragTarget.currentPt) {
      dot(dragTarget.currentPt, "#ffeb3b", 6.5);
      dot(dragTarget.currentPt, "#000", 3.5);
    }
  };
  if (img.complete) go(); else img.onload = go;
  wireFlatEvents(img, cv);
}

// ---- changes: problems, the diff, the JSON itself

function paneChanges() {
  let h = "";
  if (checked.errors.length) h += `<h3 style="margin:6px 0">Problems (fix these to save)</h3><ul class="problems">${checked.errors.map((e) => `<li><span class="p">${esc(e.path)}</span> ${esc(e.message)}</li>`).join("")}</ul>`;
  if (checked.warnings.length) h += `<h3 style="margin:6px 0">Warnings</h3><ul class="problems">${checked.warnings.map((e) => `<li><span class="p">${esc(e.path)}</span> ${esc(e.message)}</li>`).join("")}</ul>`;
  if (!checked.errors.length && !checked.warnings.length) h += `<div class="note info">The spec checks out.</div>`;
  h += `<h3 style="margin:10px 0 4px">What Save will change in rig.json</h3>`;
  h += checked.changed ? `<pre class="diff">${colourDiff(checked.diff)}</pre>` : `<div class="muted">Nothing: the form matches the file.</div>`;
  h += `<h3 style="margin:10px 0 4px">The whole spec, as JSON</h3><div class="help">For anything the form does not cover. Edit and press Apply; the form follows.</div>` +
       `<textarea id="rawJson" style="min-height:220px">${esc(checked.text || JSON.stringify(draft, null, 2))}</textarea><div class="chips"><button data-act="applyraw">Apply</button><span id="rawErr" class="ferr"></span></div>`;
  if (B.backup) h += `<div class="muted" style="margin-top:6px">The version before the last save is kept beside it as rig.json.bak.</div>`;
  return h;
}
function colourDiff(d) {
  return d.replace(/\n$/, "").split("\n").map((l) => {
    const cls = l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@") ? "at" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return `<span class="${cls}">${esc(l) || " "}</span>`;
  }).join("");
}

// ---- run: the log of the last job this page started

function paneRun() {
  let h = "";
  if (job) {
    h += `<div>job ${job.id}: <b>${esc(job.step)}</b> · <span class="state ${job.state}">${esc(job.state)}</span>${job.pid ? " · PID " + job.pid : ""}` +
         ` ${job.state === "running" || job.state === "queued" ? `<button class="small" data-act="cancel">Cancel</button>` : ""}</div>`;
  } else h += `<div class="muted">Nothing run from here yet. Save and re-rig runs rig, trim, audit, clips and preview; the log shows here.</div>`;
  h += `<pre class="log" id="log">${esc((job && job.log || []).join("\n"))}</pre>`;
  if (job && job.state === "done" && job.step === "save and re-rig") {
    const pair = B.before && B.audit_time > B.before.time;
    h += `<h3 style="margin:8px 0 4px">${pair ? "Before and after" : "The audit (the first one: nothing to compare with yet)"}</h3>` + (pair ? verdictTable(B.audit, B.before) : verdictTable(B.audit));
    h += `<div class="chips" style="margin-top:6px">` +
         (B.preview_url ? `<button class="primary" data-act="playclip">▶ Play clip in editor</button>` : "") +
         `<button class="${B.preview_url ? "" : "primary"}" data-act="view">View results</button><button data-act="tab" data-tab="audit">Where it tears</button></div>`;
  }
  return h;
}

// ---- events

function wirePane(pane) {
  pane.querySelectorAll(".group > h3").forEach((h3) => (h3.onclick = () => {
    const g = h3.parentElement; g.classList.toggle("shut");
    const shut = [...pane.querySelectorAll(".group.shut")].map((x) => x.dataset.group);
    try { sessionStorage.setItem("shut", JSON.stringify(shut)); } catch (e) {}
  }));
  pane.querySelectorAll("[data-act]").forEach((el) => (el.onclick = (e) => { e.stopPropagation(); act(el); }));
  pane.querySelectorAll("[data-set]").forEach((el) => (el.onchange = () => set(el)));
}

function act(el) {
  const path = el.dataset.path ? JSON.parse(el.dataset.path) : null;
  const a = el.dataset.act;
  if (a === "pick") {
    if (pick && JSON.stringify(pick.path) === JSON.stringify(path)) return endPick();
    const p = pickers[el.dataset.mode](path, el.dataset.label); p.path = path; startPick(p);
  } else if (a === "rm") {
    const cur = getPath(path);
    if (Array.isArray(cur)) { const c = [...cur]; c.splice(Number(el.dataset.i), 1); setPath(path, c); } else setPath(path, undefined);
  } else if (a === "select") { selected = el.dataset.joint; redraw(); }
  else if (a === "clear") setPath(path, undefined);
  else if (a === "setjson") setPath(path, el.dataset.v ? JSON.parse(el.dataset.v) : undefined);
  else if (a === "rmrow") { const c = [...(getPath(path) || [])]; c.splice(Number(el.dataset.i), 1); setPath(path, c); }
  else if (a === "addrow") {
    const c = [...(getPath(path) || [])]; c.push(JSON.parse(el.dataset.v)); setPath(path, c, { keepEmpty: true });
    if (el.dataset.v === "[]") { const p = pickers.chain([...path, c.length - 1], "Mirror " + c.length); p.path = [...path, c.length - 1]; startPick(p); }
  } else if (a === "rmkey") { const o = clone(getPath(path)) || {}; delete o[el.dataset.k]; setPath(path, o); }
  else if (a === "addrole") {
    const role = ($("newRole").value || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    if (!role) { $("newRole").focus(); return; }
    const o = clone(rig().chains && !Array.isArray(rig().chains) ? rig().chains : {}) || {};
    o[role] ||= [];
    setPath(["rig", "chains"], o, { keepEmpty: true });
    const p = pickers.joints(["rig", "chains", role], "Chain " + role); p.path = ["rig", "chains", role]; startPick(p);
  } else if (a === "inithumanoid") {
    draft.humanoid = {
      forward: [0, -1, 0],
      z: { top: 1.0, head: 0.87, neck: 0.83, arm: 0.77, spine2: 0.72, spine1: 0.65, spine: 0.57, hip: 0.47, knee: 0.28, ankle: 0.08 },
      x: { shoulder: 0.38, elbow: 0.23, wrist: 0.11, knuckle: 0.05, tip: 0.0 }
    };
    changed();
  } else if (a === "playclip") {
    setViewMode("rigged");
  } else if (a === "mirrorchain") {
    mirrorChain(Number(el.dataset.i));
  } else if (a === "addstations" || a === "genstations") {
    pushUndo();
    const c = getPath(path);
    if (c) {
      c.stations = generateStations(c.slice, c.bones);
      setPath(path, c);
    }
  } else if (a === "clearstations") {
    pushUndo();
    const c = clone(getPath(path));
    if (c && "stations" in c) {
      delete c.stations;
      setPath(path, c);
    }
  } else if (a === "spot") { const s = B.spots.find((x) => x.bone === el.dataset.bone && x.mode === el.dataset.mode); if (s) showSpot(s); }
  else if (a === "measure") runMeasure();
  else if (a === "flatbig") { flatBig = !flatBig; renderPane(); }
  else if (a === "applyraw") {
    try { const d = JSON.parse($("rawJson").value); pushUndo(); draft = d; changed(); } catch (e) { $("rawErr").textContent = e.message; }
  } else if (a === "cancel") api("/api/cancel", { job: job.id }).catch((e) => alert(e.message));
  else if (a === "view") openViewer();
  else if (a === "tab") { tab = el.dataset.tab; renderTabs(); renderPane(); }
}

function set(el) {
  const path = el.dataset.path ? JSON.parse(el.dataset.path) : null;
  const s = el.dataset.set;
  if (s === "enum") { const v = JSON.parse(el.value); setPath(path, v === "" ? undefined : v); if (path.join(".") === "rig.kind") kindChanged(); }
  else if (s === "bool") {
    const def = el.dataset.default === "1";
    setPath(path, el.checked === def && path.length === 2 ? undefined : el.checked || (def ? false : undefined));
  } else if (s === "num") {
    const v = el.value.trim();
    if (v === "") { setPath(path, undefined); return; }
    const n = Number(v);
    if (!Number.isFinite(n)) return;
    // a point's number: the rest of the point must exist
    if (typeof path[path.length - 1] === "number" && (el.classList.contains("pt") || path.includes("slice"))) {
      const pp = path.slice(0, -1); const cur = clone(getPath(pp)) || (el.classList.contains("pt") ? [0.5, 0.5, 0.5] : [0, 1]);
      cur[path[path.length - 1]] = n; setPath(pp, cur);
    } else setPath(path, n);
  } else if (s === "text") setPath(path, el.value.trim() || undefined);
  else if (s === "texts") setPath(path, el.value.split(",").map((x) => x.trim()).filter(Boolean));
  else if (s === "rolename") {
    const old = el.dataset.role, nu = el.value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    if (!nu || nu === old) return;
    const o = {}; for (const [k, v] of Object.entries(rig().chains || {})) o[k === old ? nu : k] = v;
    setPath(["rig", "chains"], o);
  } else if (s === "how") {
    const c = clone(getPath(path)); for (const k of ["tip", "base", "points", "slice", "tube", "stations"]) delete c[k];
    c[{ tip: "tip", points: "points", slice: "slice", tube: "tube" }[el.value]] = el.value === "tip" ? [0.5, 0.5, 0.5] : el.value === "slice" ? [0.1, 0.9] : el.value === "tube" ? [[0.5, 0.2, 0.5], [0.5, 0.8, 0.5]] : [];
    setPath(path, c, { keepEmpty: true });
  } else if (s === "parent") {
    setPath(path, el.value ? [el.value, -1] : undefined);
  } else if (s === "budget") setPath(path, el.value.trim() === "" ? undefined : Math.round(Number(el.value)));
  else if (s === "fullres") setPath(["budget"], el.checked ? null : undefined, { keepEmpty: true });
}

function kindChanged() {
  const R = rig();
  if (R.kind === "build" && !Array.isArray(R.chains)) { delete R.chains; R.chains = []; changed(); }
  if (R.kind === "tripo" && Array.isArray(R.chains)) { delete R.chains; changed(); }
  if (R.kind === "humanoid" && !draft.humanoid) {
    draft.humanoid = {
      forward: [0, -1, 0],
      z: { top: 1.0, head: 0.87, neck: 0.83, arm: 0.77, spine2: 0.72, spine1: 0.65, spine: 0.57, hip: 0.47, knee: 0.28, ankle: 0.08 },
      x: { shoulder: 0.38, elbow: 0.23, wrist: 0.11, knuckle: 0.05, tip: 0.0 }
    };
    changed();
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Changes, checking, saving, running
// ---------------------------------------------------------------------------------------------------------------

let checkTimer = null;
function changed() {
  redraw();
  renderPane();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 250);
}

async function runCheck() {
  try { checked = await api("/api/spec/check", { model: MODEL, spec: draft }); }
  catch (e) { checked = { errors: [{ path: "", message: e.message }], warnings: [], diff: "", changed: true }; }
  $("dirty").textContent = checked.changed ? "● unsaved changes" : "";
  $("bSave").disabled = !checked.changed || checked.errors.length > 0;
  $("bSave").title = checked.errors.length ? "Fix the problems first (Changes tab)" : "Write rig.json (the old one is kept as rig.json.bak)";
  $("bRerig").disabled = checked.errors.length > 0 || (job && (job.state === "running" || job.state === "queued"));
  renderTabs();
  if (tab === "rig" || tab === "changes") renderPane();
}

function doUndo() {
  if (!undo.length) return;
  draft = JSON.parse(undo.pop());
  $("bUndo").disabled = !undo.length;
  changed();
}
$("bUndo").onclick = doUndo;
$("bRevert").onclick = () => { if (!checked.changed || confirm("Throw away every change since the last save?")) { pushUndo(); draft = startDraft(); changed(); } };

async function save(rerig) {
  endPick(false);
  try {
    const r = await api("/api/spec/" + (rerig ? "rerig" : "save"), { model: MODEL, spec: draft, base });
    base = r.base; B.text = r.text;
    if (r.job) { follow(r.job); tab = "run"; }
    await reload(false);
    redraw();
    renderTabs(); renderPane();
    await runCheck();
    flashTop(rerig ? "Saved. Re-rigging…" : "Saved rig.json (the old one is rig.json.bak).");
  } catch (e) {
    if (e.data && e.data.errors) { checked.errors = e.data.errors; tab = "changes"; renderTabs(); renderPane(); }
    alert(e.message);
  }
}
$("bSave").onclick = () => save(false);
$("bRerig").onclick = () => save(true);
function flashTop(t) { const d = $("dirty"); d.textContent = t; d.style.color = "var(--ok)"; setTimeout(() => { d.style.color = ""; d.textContent = checked.changed ? "● unsaved changes" : ""; }, 3000); }

function follow(j) {
  if (es) es.close();
  job = Object.assign({}, j, { log: [] });
  es = new EventSource(withToken(`/api/jobs/${j.id}/events?from=0`));
  es.onmessage = (ev) => {
    job.log.push(JSON.parse(ev.data));
    const lg = $("log");
    if (lg && tab === "run") { const stick = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 30; lg.textContent += JSON.parse(ev.data) + "\n"; if (stick) lg.scrollTop = lg.scrollHeight; }
  };
  es.addEventListener("end", async (ev) => {
    es.close(); es = null;
    Object.assign(job, JSON.parse(ev.data));
    await reload(false);
    redraw();
    if (job.step === "source view" && SRC && !model) await loadModel(SRC.glb_url).catch(() => {});
    if (job.step === "source view") { redraw(); frameView([0.35, -1, 0.3]); }
    if (job.step === "save and re-rig" && job.state === "done") {
      tab = "run";
      if (B.preview_url) {
        await loadRigged(B.preview_url);
      }
    }
    if (job.step === "flat views") tab = "flat";
    renderTabs(); renderPane(); runCheck();
  });
  const poll = setInterval(async () => {
    if (!job || job.id !== j.id || !(job.state === "running" || job.state === "queued" || job.state === undefined)) return clearInterval(poll);
    try { const s = await api(`/api/jobs/${j.id}`); job.state = s.state; job.pid = s.pid; if (tab === "run") { const top = $("log") && $("log").scrollTop; renderPane(); } } catch (e) {}
  }, 2500);
  renderTabs(); renderPane();
}

async function runMeasure() {
  try { follow(await api("/api/spec/measure", { model: MODEL, spec: draft })); tab = "run"; renderTabs(); renderPane(); }
  catch (e) { alert(e.message); }
}

function openViewer() { window.open("/viewer.html?model=" + encodeURIComponent(MODEL) + "&t=" + encodeURIComponent(TOKEN), "_blank"); }
$("bView").onclick = openViewer;
$("back").href = "/?t=" + encodeURIComponent(TOKEN) + "#model=" + encodeURIComponent(MODEL) + "&tab=spec";

// ---------------------------------------------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------------------------------------------

function startDraft() {
  if (B.spec) return clone(B.spec);
  const sk = B.source ? B.source.skeleton : B.survey ? B.survey.skeleton : "none";
  const k = sk === "tripo" ? "tripo" : sk === "mixamo" ? "humanoid" : "build";
  const d = { schema: "autorig-spec/1", rig: { kind: k } };
  if (k === "humanoid") {
    d.humanoid = {
      forward: [0, -1, 0],
      z: { top: 1.0, head: 0.87, neck: 0.83, arm: 0.77, spine2: 0.72, spine1: 0.65, spine: 0.57, hip: 0.47, knee: 0.28, ankle: 0.08 },
      x: { shoulder: 0.38, elbow: 0.23, wrist: 0.11, knuckle: 0.05, tip: 0.0 }
    };
  }
  return d;
}

async function reload(fresh) {
  B = await api("/api/spec?name=" + encodeURIComponent(MODEL));
  if (B.source) { SRC = B.source; SRC.byName = Object.fromEntries(SRC.joints.map((j) => [j.name, j])); }
  if (fresh) { draft = startDraft(); base = B.base; undo = []; }
  document.title = MODEL + " - spec editor";
  $("title").textContent = MODEL;
  $("sub").textContent = `${B.group || "(root)"} · ${B.source_file || "no source"} · ${B.text ? "rig.json" : "no rig.json yet"}` +
                         (SRC ? ` · ${SRC.skeleton === "none" ? "no skeleton" : SRC.joints.length + " source bones"}` : "");
  $("bView").disabled = !B.audit && !B.rig_bones.length;
  const btnRigged = $("vRigged");
  if (btnRigged) {
    btnRigged.disabled = !B.preview_url;
    btnRigged.title = B.preview_url ? "Rigged mesh and animated clips" : "Rig the model first to see preview and clips";
  }
  if (B.preview_url && viewMode === "rigged") {
    await loadRigged(B.preview_url);
  }
}

function message(big, sub, act) {
  $("msg").style.display = big ? "block" : "none";
  $("msgBig").textContent = big || ""; $("msgSub").textContent = sub || ""; $("msgAct").innerHTML = act || "";
}

async function main() {
  if (!MODEL) return message("No model named", "Open the editor from the workbench (Edit spec).");
  try { await reload(true); } catch (e) { return message("Cannot open " + MODEL, e.message); }
  renderTabs();
  redraw();
  if (SRC) frameView([0.35, -1, 0.3]);       // the skeleton shows at once; the model follows when it has loaded
  renderPane();
  runCheck();
  if (!SRC || SRC.stale) {
    message(SRC ? "The source file changed" : "Making the source view…", "A few seconds: the source model and its own skeleton, exactly as they came.");
    try { follow(await api("/api/spec/source", { model: MODEL })); tab = "run"; renderTabs(); renderPane(); }
    catch (e) { message("Cannot make the source view", e.message); return; }
    const wait = setInterval(() => { if (job && job.state && !["running", "queued"].includes(job.state)) { clearInterval(wait); message(job.state === "done" ? "" : "The source view failed", job.state === "done" ? "" : "See the Run tab."); if (job.state === "done") { tab = "rig"; renderTabs(); renderPane(); } } }, 400);
  }
  if (SRC) {
    try { await loadModel(SRC.glb_url); } catch (e) { message("Cannot load the source view", e.message); }
    redraw();
    frameView([0.35, -1, 0.3]);
  }
  if (B.preview_url) {
    loadRigged(B.preview_url).catch(() => {});
  }
  $("vSource").onclick = () => setViewMode("source");
  $("vRigged").onclick = () => setViewMode("rigged");
  $("clipPlay").onclick = togglePlay;
  $("clipSelect").onchange = (e) => playClip(Number(e.target.value));
  const scrubEl = $("clipScrub");
  if (scrubEl) {
    scrubEl.onpointerdown = () => { scrubbing = true; };
    scrubEl.onpointerup = () => { scrubbing = false; };
    scrubEl.oninput = (e) => seekClip(Number(e.target.value) / 1000);
  }
  document.body.dataset.ready = "1";            // for tests and screenshots: the page has what it needs
  // for tests and screenshots: the draft, and where a 0..1 point or a joint is on the screen
  window.specEditor = {
    draft: () => clone(draft),
    viewMode: () => viewMode,
    setViewMode,
    clips: () => riggedClips.map((c) => c.name),
    playClip,
    screen: (u, isJoint) => {
      const p = V(isJoint ? (joint(u) || L.virtual[u]).head || L.virtual[u].pos : F.fromUnit(u)).project(camera);
      const r = renderer.domElement.getBoundingClientRect();
      return [r.left + (p.x + 1) / 2 * r.width, r.top + (1 - p.y) / 2 * r.height];
    },
  };
}
main();

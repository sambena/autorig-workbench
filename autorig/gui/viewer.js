// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the results viewer (gui/viewer.html). Plain ES modules, no build step; three.js is vendored
// under gui/vendor/three (MIT).
//
// A stage (dark, a floor ruled every unit, a 1.8 m reference figure a short gap to the left), the model standing
// centred on the floor facing +X as a side-on game shows it, its clips with a scrub bar, a rig overlay (skeleton,
// bone names, weights of one bone, bleed), the audit's verdict and its worst bones, the mesh's loose parts and the
// bone each rides, and a few checks: real height, facing, bones, clips.
//
// Framing fits the model (over every clip, not only its bind pose) and the figure into the largest part of the
// window the HUD leaves clear, so neither is ever cut off, whatever the model's size or the window's.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { padStep, scrubTicks, scrubTime, keyTimes, isBleed, segDist2, islands as meshIslands, sideOf, hops,
         tearSeverity, tearMarkerRadius, mapTearPoint, defaultCombinedAngle,
         VIEWS, viewLabel, nextView, isHumanoidOrBiped, defaultCameraView,
         formatClipOption, findClipIndexByName } from "./viewer_logic.js";

const TOKEN = window.AUTORIG_TOKEN;
const REF_HEIGHT = 1.8;
const OVERLAYS = ["off", "skeleton", "names", "weights", "bleed", "tears"];
const AUDIT_BAD = new THREE.Color(1.0, 0.16, 0.16), AUDIT_WARN = new THREE.Color(1.0, 0.62, 0.1);
// Bone colours by role, as the rig step's QA pictures draw them (steps/rerig.py ROLE_COLOURS).
const ROLE_COLOURS = {
  spine: [0.1, 0.35, 0.9], leg: [0.1, 0.65, 0.2], tail: [0.95, 0.5, 0.05], head: [0.6, 0.15, 0.75],
  ik: [0.95, 0.85, 0.1], extra: [0.45, 0.45, 0.45], wing: [0.0, 0.7, 0.75], jaw: [0.85, 0.1, 0.45],
  tentacle: [0.8, 0.3, 0.6], claw: [0.75, 0.1, 0.1],
};

const $ = (id) => document.getElementById(id);
const withToken = (u) => u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);

async function api(path, body) {
  const opt = { headers: { "X-Autorig-Token": TOKEN } };
  if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------------------------------------------------------------------------------------------------------------
// Renderer, camera, lights
// ---------------------------------------------------------------------------------------------------------------

const stageEl = $("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
stageEl.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
Object.assign(labelRenderer.domElement.style, { position: "absolute", top: "0", left: "0", pointerEvents: "none" });
stageEl.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x15181e);
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 5000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.12;

scene.add(new THREE.HemisphereLight(0xdfe6f5, 0x3a3632, 1.6));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.bias = -0.0005;
scene.add(key, key.target);
const rim = new THREE.DirectionalLight(0x9fb4ff, 0.8);
scene.add(rim);

let resizeTimer = 0;
function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  layoutPanels();
  drawScrub();
  clearTimeout(resizeTimer);                       // the view refits once the window stops changing
  resizeTimer = setTimeout(() => { if (view !== "orbit") frame(); }, 120);
}
window.addEventListener("resize", () => resize());

// ---------------------------------------------------------------------------------------------------------------
// The stage: floor, ruler, reference figure (rebuilt to the model's size)
// ---------------------------------------------------------------------------------------------------------------

let stage = null, stageLabels = [];
const figure = makeFigure();
scene.add(figure);

function label(text, cls, place = "above") {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = text;
  const obj = new CSS2DObject(d);
  if (place === "above") obj.center.set(0.5, 1.45);
  else if (place === "below") obj.center.set(0.5, -0.45);
  return obj;
}

function makeFigure() {
  // A neutral mannequin, 1.8 m to the top of its head, facing the camera; built here, no asset.
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x8b93a3, roughness: 0.85, metalness: 0 });
  const capsule = (r, total, x, yMid, sz = 1) => {
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.001, total - 2 * r), 6, 16), mat);
    m.position.set(x, yMid, 0); m.scale.z = sz; m.castShadow = true; g.add(m); return m;
  };
  capsule(0.075, 0.88, -0.1, 0.44); capsule(0.075, 0.88, 0.1, 0.44);                 // legs
  capsule(0.17, 0.64, 0, 1.13, 0.62);                                                 // torso
  capsule(0.052, 0.66, -0.255, 1.1); capsule(0.052, 0.66, 0.255, 1.1);               // arms
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.055, 0.16, 12), mat);
  neck.position.set(0, 1.5, 0); g.add(neck);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 14), mat);
  head.position.set(0, REF_HEIGHT - 0.11, 0); head.castShadow = true; g.add(head);
  const top = label("1.8 m", "ref"); top.position.set(0, REF_HEIGHT + 0.08, 0); g.add(top);
  g.userData.width = 0.62;
  return g;
}

function figureGap(box) {
  const s = box.getSize(new THREE.Vector3());
  return Math.max(0.04, 0.08 * Math.max(REF_HEIGHT, s.x, s.y));
}

function buildStage(box) {
  if (stage) { scene.remove(stage); disposeTree(stage); }
  for (const l of stageLabels) l.removeFromParent();
  stageLabels = [];
  stage = new THREE.Group();
  const size = box.getSize(new THREE.Vector3());
  const extent = Math.max(size.x, size.z, REF_HEIGHT, size.y);
  const half = Math.min(400, Math.max(6, Math.ceil(extent * 2.2)));

  const floor = new THREE.Mesh(new THREE.PlaneGeometry(half * 2, half * 2),
                               new THREE.MeshStandardMaterial({ color: 0x1d2129, roughness: 1, metalness: 0 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  stage.add(floor);
  const grid = new THREE.GridHelper(half * 2, half * 2, 0x4a5468, 0x2e3440);   // one line every unit
  grid.position.y = 0.0005;
  stage.add(grid);

  // the ruler: along X in front of the model, a tick every unit, numbered
  const zr = Math.max(box.max.z, 0.3) + 0.25;
  const pts = [new THREE.Vector3(-half, 0.002, zr), new THREE.Vector3(half, 0.002, zr)];
  const every = half > 40 ? 10 : half > 16 ? 5 : 1;
  for (let i = -half; i <= half; i++) {
    const major = i % every === 0;
    pts.push(new THREE.Vector3(i, 0.002, zr), new THREE.Vector3(i, 0.002, zr + (major ? 0.16 : 0.08)));
    pts.push(new THREE.Vector3(i, 0.002, zr), new THREE.Vector3(i, major ? 0.06 : 0.03, zr));
    if (major && Math.abs(i) <= Math.max(12, extent * 1.5)) {
      const l = label(i + " m", "tick"); l.position.set(i, 0.003, zr + 0.3); stage.add(l); stageLabels.push(l);
    }
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const segs = [];
  for (let i = 0; i < pts.length; i += 2) segs.push(i, i + 1);
  geo.setIndex(segs);
  stage.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x8a93a6 })));
  scene.add(stage);

  // the figure stands to the model's left (behind it, as it faces +X), a short gap clear of everything it does in
  // its clips; the gap scales with the larger of the two
  figure.position.set(box.min.x - figureGap(box) - figure.userData.width / 2, 0, 0);

  const r = Math.max(extent, REF_HEIGHT) * 1.6 + Math.abs(figure.position.x);
  key.position.set(r * 0.6, r * 1.3, r * 0.9);
  key.target.position.set(0, 0, 0);
  Object.assign(key.shadow.camera, { left: -r, right: r, top: r, bottom: -r, near: 0.01, far: r * 5 });
  key.shadow.camera.updateProjectionMatrix();
  rim.position.set(-r, r * 0.6, -r);
}

// ---------------------------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------------------------

const loader = new GLTFLoader();
let models = [], mi = -1;            // every rigged model the server knows; the one shown
let cur = null;                      // {item, gltf, holder, mixer, clips, bones, skinned, box, fps, names, ...}
let ci = 0, action = null, playing = true, loop = true, speed = 1;
let overlay = 0, view = "hero", framing = "model", sel = -1;
let loadToken = 0;

function disposeTree(o) {
  o.traverse((n) => {
    if (n.geometry) n.geometry.dispose();
    const mats = n.material ? (Array.isArray(n.material) ? n.material : [n.material]) : [];
    for (const m of mats) {
      for (const k of Object.keys(m)) if (m[k] && m[k].isTexture) m[k].dispose();
      m.dispose();
    }
  });
}

function unload() {
  if (!cur) return;
  cur.mixer && cur.mixer.stopAllAction();
  for (const b of cur.bones) { b.label.removeFromParent(); }
  clearTearMarkers();
  scene.remove(cur.holder, cur.overlay);
  disposeTree(cur.holder);
  cur.overlay.traverse((n) => { if (n.material) n.material.dispose(); });     // the bone shapes are shared
  for (const m of cur.weightMats) m.dispose();
  cur = null; action = null;
}

// ---------------------------------------------------------------------------------------------------------------
// Loading a model
// ---------------------------------------------------------------------------------------------------------------

function message(big, sub, actionEl) {
  $("msg").style.display = big ? "block" : "none";
  $("msgBig").textContent = big || "";
  $("msgSub").textContent = sub || "";
  const a = $("msgAct"); a.innerHTML = "";
  if (actionEl) a.appendChild(actionEl);
}

async function refreshList(want) {
  const r = await api("/api/previews");
  models = r.models;
  const name = want ?? (models[mi] && models[mi].name);
  mi = Math.max(0, models.findIndex((m) => m.name === name));
}

async function show(i) {
  if (!models.length) {
    unload(); hud();
    message("No rigged models yet", "Rig a model in the workbench, then press Preview (or Run all).");
    renderChecks([]);
    return;
  }
  mi = (i + models.length) % models.length;
  const item = models[mi];
  try { history.replaceState(null, "", "?model=" + encodeURIComponent(item.name) + "&t=" + encodeURIComponent(TOKEN)); } catch (e) { /* file: or sandboxed */ }
  $("back").href = "/?t=" + encodeURIComponent(TOKEN) + "#model=" + encodeURIComponent(item.name);
  if ($("helpLink")) $("helpLink").href = "/help.html?t=" + encodeURIComponent(TOKEN) + "#preview-button";
  const token = ++loadToken;
  unload();
  hud();
  if (!item.preview) {
    const b = document.createElement("button");
    b.textContent = "Run Preview";
    b.onclick = () => runPreview(item.name, b);
    message("run Preview first", `${item.name} is rigged but has no ${item.rig_folder}/preview.glb yet: the viewer's copy of the rig and its clips.`, b);
    renderChecks([["warn", "no preview.glb"]]);
    return;
  }
  message("Loading " + item.name + "…", "");
  let gltf;
  try {
    gltf = await loader.loadAsync(withToken(item.preview));
  } catch (e) {
    if (token !== loadToken) return;
    message("Could not load preview.glb", String(e && e.message || e));
    return;
  }
  if (token !== loadToken) { disposeTree(gltf.scene); return; }
  message("");
  setup(item, gltf);
}

function setup(item, gltf) {
  const info = item.info || {};
  const root = gltf.scene;
  const holder = new THREE.Group();
  root.rotation.y = Math.PI / 2;             // the GLB faces +Z (Blender -Y); the game view wants +X
  holder.add(root);
  scene.add(holder);

  const skinned = [], meshes = [];
  root.traverse((o) => {
    if (o.isMesh) { meshes.push(o); o.castShadow = true; o.receiveShadow = true; }
    if (o.isSkinnedMesh) { skinned.push(o); o.frustumCulled = false; }
  });
  // the skeleton: every joint of every skin (one armature, so usually one skin)
  const boneSet = new Set();
  for (const s of skinned) for (const b of s.skeleton.bones) boneSet.add(b);
  if (!boneSet.size) root.traverse((o) => { if (o.isBone) boneSet.add(o); });
  const nameOf = (o) => {
    const a = gltf.parser.associations.get(o);
    const n = a && a.nodes !== undefined ? gltf.parser.json.nodes[a.nodes].name : null;
    return n || o.name;
  };

  // size: at the card's real size when it says, else one file unit is a metre
  holder.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(holder);
  const fileSize = box.getSize(new THREE.Vector3());
  const longest = Math.max(fileSize.x, fileSize.y, fileSize.z) || 1;
  const scale = info.metres ? info.metres / longest : 1;
  holder.scale.setScalar(scale);
  holder.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(holder);
  const c = box.getCenter(new THREE.Vector3());
  holder.position.set(-c.x, -box.min.y, -c.z);
  holder.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(holder);

  // the rig overlay: one octahedron per bone, drawn over the mesh
  const overlayGroup = new THREE.Group();
  overlayGroup.renderOrder = 10;
  scene.add(overlayGroup);
  const lengths = info.bone_lengths || {};
  const deform = new Set(info.deform || []);
  const bones = [];
  const all = [...boneSet];
  const indexOf = new Map(all.map((b, i) => [b, i]));
  for (const b of all) {
    const name = nameOf(b);
    let len = lengths[name];
    if (!len) {                                   // no length recorded: to the first child, else half the parent's
      const kid = b.children.find((k) => indexOf.has(k));
      len = kid ? kid.position.length() : (b.parent && indexOf.has(b.parent) ? b.position.length() * 0.5 : longest * 0.05);
    }
    const role = roleOf(name);
    const col = new THREE.Color(...ROLE_COLOURS[role]);
    const mat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.55, depthTest: false, depthWrite: false });
    const mesh = new THREE.Mesh(BONE_GEO, mat);
    mesh.matrixAutoUpdate = false;
    mesh.renderOrder = 10;
    const edge = new THREE.LineSegments(BONE_EDGES, new THREE.LineBasicMaterial({ color: col.clone().lerp(new THREE.Color(1, 1, 1), 0.45), depthTest: false, transparent: true }));
    edge.renderOrder = 11;
    mesh.add(edge);
    overlayGroup.add(mesh);
    const lab = label(name, "lbl");
    lab.visible = false;
    scene.add(lab);
    bones.push({ bone: b, name, len: Math.max(len, 1e-5), role, col, mesh, edge, lab: null, label: lab,
                 parent: indexOf.has(b.parent) ? indexOf.get(b.parent) : -1, deform: deform.size ? deform.has(name) : true });
  }

  const mixer = new THREE.AnimationMixer(root);
  mixer.addEventListener("finished", () => { playing = false; hudPlay(); });
  const clips = gltf.animations.slice();
  cur = { item, info, gltf, holder, root, overlay: overlayGroup, mixer, clips, bones, skinned, meshes, box, scale,
          fileSize, fps: info.fps || 24, weightMats: [], origMats: new Map(), audit: item.audit || null,
          analysis: null, keys: clips.map((c) => keyTimes(c.tracks.map((t) => t.times))),
          fullAudit: undefined, tearSites: null, tearPoseIndex: 0, tearsGroup: null, tearLabels: [], _inTearPose: false };
  for (const m of meshes) cur.origMats.set(m, m.material);
  const blame = new Map(((cur.audit && cur.audit.bones) || []).map((r) => [r.bone, r]));
  for (const b of bones) b.audit = blame.get(b.name) || null;
  fetchFullAudit(item.name);

  cur.envelope = envelope();                     // where the model goes over all its clips
  buildStage(cur.envelope);
  sel = pickDefaultBone();
  ci = Math.min(ci, Math.max(0, clips.length - 1));
  if (!clips.length) ci = 0;
  updateClipSelect();
  playClip(ci);
  applyOverlay();
  renderChecks(checks());
  renderWorst();
  const defView = defaultCameraView(info, cur.bones, box);
  setView(defView);
  layoutPanels();
}

function updateClipSelect() {
  const selEl = $("clipSelect");
  if (!selEl) return;
  selEl.innerHTML = "";
  if (!cur || !cur.clips || !cur.clips.length) {
    const opt = document.createElement("option");
    opt.value = "-1";
    opt.textContent = "No clips (bind pose)";
    selEl.appendChild(opt);
    selEl.disabled = true;
    return;
  }
  selEl.disabled = false;
  cur.clips.forEach((c, idx) => {
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = formatClipOption(c, idx, cur.fps);
    selEl.appendChild(opt);
  });
  selEl.value = String(ci);
}

// The box the model fills over every clip: its bind-pose mesh, and each clip sampled through, the skeleton's box
// grown by how far the mesh stood past the skeleton at bind pose.
function envelope() {
  const pts = new THREE.Box3(), p = new THREE.Vector3();
  const boneBox = () => {
    cur.holder.updateMatrixWorld(true);
    pts.makeEmpty();
    for (const b of cur.bones) {
      pts.expandByPoint(b.bone.getWorldPosition(p));
      pts.expandByPoint(b.bone.localToWorld(p.set(0, b.len, 0)));
    }
    return pts.clone();
  };
  for (const s of cur.skinned) s.skeleton.pose();
  const env = cur.box.clone();
  if (!cur.bones.length) return env;
  const bind = boneBox();
  const padLo = bind.min.clone().sub(cur.box.min).max(new THREE.Vector3());
  const padHi = cur.box.max.clone().sub(bind.max).max(new THREE.Vector3());
  for (const clip of cur.clips) {
    const a = cur.mixer.clipAction(clip);
    a.reset(); a.play();
    const n = Math.min(48, Math.max(8, Math.round(clip.duration * cur.fps)));
    for (let k = 0; k <= n; k++) {
      a.time = clip.duration * k / n;
      cur.mixer.update(0);
      const bb = boneBox();
      bb.min.sub(padLo); bb.max.add(padHi);
      env.union(bb);
    }
    a.stop();
  }
  cur.mixer.stopAllAction();
  for (const s of cur.skinned) s.skeleton.pose();
  env.min.y = Math.min(env.min.y, 0);
  return env;
}

// A unit bone along +Y, as Blender draws one: a point at the head, a square waist a tenth of the way, the tail.
const BONE_GEO = (() => {
  const r = 0.09, w = 0.12;
  const v = [0, 0, 0, r, w, 0, 0, w, r, -r, w, 0, 0, w, -r, 0, 1, 0];
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
  g.setIndex([0, 2, 1, 0, 3, 2, 0, 4, 3, 0, 1, 4, 5, 1, 2, 5, 2, 3, 5, 3, 4, 5, 4, 1]);
  g.computeVertexNormals();
  return g;
})();
const BONE_EDGES = new THREE.EdgesGeometry(BONE_GEO);

function roleOf(name) {
  const n = name.toLowerCase();
  if (/(^|[_.:\s-])ik([_.:\s-]|$)|^ik|pole/.test(n)) return "ik";
  if (/jaw|mandible|tongue|mouth/.test(n)) return "jaw";
  if (/tail/.test(n)) return "tail";
  if (/wing|fin($|[_.])|flipper|fluke/.test(n)) return "wing";
  if (/tentacle/.test(n)) return "tentacle";
  if (/claw|pincer/.test(n)) return "claw";
  if (/(^|[_.:\s-])ear/.test(n) || /head|neck|skull|eye/.test(n)) return "head";
  if (/leg|foot|toe|thigh|shin|calf|knee|ankle|hand|arm|finger|thumb|index|middle|ring|pinky|shoulder|clavicle|wrist|elbow/.test(n)) return "leg";
  if (/spine|hips|pelvis|root|body|chest|abdomen|thorax|torso|belly/.test(n)) return "spine";
  return "extra";
}

function pickDefaultBone() {
  if (!cur.bones.length) return -1;
  const a = cur.audit;
  if (a && (a.grade === "CHECK" || a.grade === "FAIL" || a.pass === false)) {
    const wb = a.worst_bone || (a.bones && a.bones.length ? a.bones[0].bone : null);
    if (wb) {
      const idx = boneByName(wb);
      if (idx >= 0) return idx;
    }
  }
  // the head when there is one (what the audit's head share measures), else the bone with the most weight
  const head = cur.bones.findIndex((b) => /^head$|(^|[^a-z])head$/i.test(b.name));
  if (head >= 0) return head;
  const tot = new Float64Array(cur.bones.length);
  for (const s of cur.skinned) {
    const si = s.geometry.attributes.skinIndex, sw = s.geometry.attributes.skinWeight;
    if (!si || !sw) continue;
    const map = s.skeleton.bones.map((b) => cur.bones.findIndex((x) => x.bone === b));
    for (let v = 0; v < si.count; v++) for (let k = 0; k < 4; k++) {
      const j = map[si.getComponent(v, k)];
      if (j >= 0) tot[j] += sw.getComponent(v, k);
    }
  }
  let best = 0;
  for (let i = 1; i < tot.length; i++) if (tot[i] > tot[best]) best = i;
  return best;
}

// ---------------------------------------------------------------------------------------------------------------
// Clips
// ---------------------------------------------------------------------------------------------------------------

function playClip(i) {
  if (!cur) return;
  cur.mixer.stopAllAction();
  action = null;
  if (!cur.clips.length) {
    for (const s of cur.skinned) s.skeleton.pose();              // bind pose
    hud(); return;
  }
  ci = (i + cur.clips.length) % cur.clips.length;
  action = cur.mixer.clipAction(cur.clips[ci]);
  action.reset();
  action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
  action.clampWhenFinished = true;
  action.play();
  action.paused = !playing;
  cur.mixer.timeScale = speed;
  if (!playing) cur.mixer.update(0);
  const selEl = $("clipSelect");
  if (selEl && selEl.value !== String(ci)) selEl.value = String(ci);
  hud();
  drawScrub();
}

function setPlaying(p) {
  playing = p;
  if (action) {
    if (p && !action.isRunning() && action.time >= action.getClip().duration - 1e-6 && !loop) action.reset();
    action.paused = !p;
    if (p) action.play();
  }
  hudPlay();
}

function stepFrame(d) {
  if (!action) return;
  setPlaying(false);
  const dur = action.getClip().duration;
  const f = Math.round(action.time * cur.fps) + d;
  seek(Math.min(dur, Math.max(0, f / cur.fps)));
}

function seek(t) {
  if (!action) return;
  action.enabled = true;
  action.time = t;
  cur.mixer.update(0);
  drawScrub();
  hudTime();
}

function setLoop(l) {
  loop = l;
  $("loop").checked = l;
  if (action) { action.setLoop(l ? THREE.LoopRepeat : THREE.LoopOnce, Infinity); if (l && playing) action.play(); }
}

function setSpeed(s) {
  speed = Math.min(2, Math.max(0.05, Math.round(s * 20) / 20));
  $("speed").value = speed;
  $("speedVal").textContent = speed.toFixed(2) + "×";
  if (cur) cur.mixer.timeScale = speed;
}

// ---------------------------------------------------------------------------------------------------------------
// Overlays: off, skeleton, bone names, weights, bleed
// ---------------------------------------------------------------------------------------------------------------

function applyOverlay() {
  const mode = OVERLAYS[overlay];
  $("bOverlay").textContent = "Overlay: " + mode;
  $("bOverlay").classList.toggle("on", overlay > 0);
  for (const id of ["boneinfo", "worst", "islands", "bleed", "tears"]) $(id).style.display = "none";
  if (!cur) { layoutPanels(); return; }
  cur.overlay.visible = mode !== "off";
  for (const b of cur.bones) b.label.visible = mode === "names";
  if (mode === "weights" || mode === "bleed") analyse();
  paintWeights(mode === "weights");
  if (mode === "bleed") paintBleed();
  if (mode !== "tears") {
    clearTearMarkers();
    if (cur._inTearPose) {
      for (const s of cur.skinned) s.skeleton.pose();
      cur.holder.updateMatrixWorld(true);
      updateOverlay();
      cur._inTearPose = false;
    }
  }
  if (mode === "tears") setupTears();
  $("boneinfo").style.display = mode === "weights" && sel >= 0 ? "block" : "none";
  $("worst").style.display = mode !== "off" && cur.audit ? "block" : "none";
  $("islands").style.display = mode === "weights" ? "block" : "none";
  $("bleed").style.display = mode === "bleed" ? "block" : "none";
  $("tears").style.display = mode === "tears" ? "block" : "none";
  if (mode === "weights") renderIslands();
  if (mode === "bleed") renderBleed();
  if (mode === "tears") renderTears();
  renderWorst();
  hud();
  layoutPanels();
}

function highlight() {
  if (!cur) return;
  const mode = OVERLAYS[overlay];
  const white = new THREE.Color(1, 1, 1);
  cur.bones.forEach((b, i) => {
    const on = i === sel && mode !== "off";
    // the audit's worst bones: red where a failed check blames them, amber where it only warns
    const blamed = b.audit ? (b.audit.level === "bad" ? AUDIT_BAD : AUDIT_WARN) : null;
    const c = on ? white : blamed || b.col;
    b.mesh.material.color.copy(c);
    b.edge.material.color.copy(on ? white : c.clone().lerp(white, 0.45));
    b.mesh.material.opacity = on ? 0.95 : blamed ? 0.85 : (mode === "weights" || mode === "bleed" ? 0.3 : 0.55);
    b.label.element.classList.toggle("sel", on);
  });
  for (const id of ["worstList", "islandList", "bleedList", "tearsList"]) {
    for (const li of $(id).children) li.classList.toggle("sel", li.dataset.bone !== undefined && Number(li.dataset.bone) === sel);
  }
}

function weightColour(w, out) {
  if (w <= 0.0005) return out.setRGB(0.07, 0.08, 0.22);
  return out.setHSL((1 - Math.min(1, w)) * 0.66, 1, 0.5);        // blue -> cyan -> green -> yellow -> red
}

function paintWeights(on) {
  for (const m of cur.meshes) m.material = cur.origMats.get(m);
  for (const m of cur.weightMats) m.dispose();
  cur.weightMats = [];
  if (!on || sel < 0) return;
  const target = cur.bones[sel].bone;
  const col = new THREE.Color();
  let owned = 0, strong = 0, total = 0;
  for (const m of cur.meshes) {
    const g = m.geometry;
    const n = g.attributes.position.count;
    const colours = new Float32Array(n * 3);
    if (m.isSkinnedMesh && g.attributes.skinIndex) {
      const si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
      const j = m.skeleton.bones.indexOf(target);
      for (let v = 0; v < n; v++) {
        let w = 0;
        if (j >= 0) for (let k = 0; k < 4; k++) if (si.getComponent(v, k) === j) w += sw.getComponent(v, k);
        weightColour(w, col).toArray(colours, v * 3);
        total++; if (w > 0.01) owned++; if (w > 0.5) strong++;
      }
    } else {
      // a rigid piece rides one bone whole: its parent bone
      let p = m.parent, rides = false;
      while (p) { if (p === target) { rides = true; break; } if (p.isBone) break; p = p.parent; }
      for (let v = 0; v < n; v++) weightColour(rides ? 1 : 0, col).toArray(colours, v * 3);
      total += n; if (rides) { owned += n; strong += n; }
    }
    g.setAttribute("color", new THREE.BufferAttribute(colours, 3));
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true });
    cur.weightMats.push(mat);
    m.material = mat;
  }
  const b = cur.bones[sel];
  $("boneName").textContent = b.name + (b.deform ? "" : " (not deforming)");
  $("boneShare").textContent = total ? `touches ${(100 * owned / total).toFixed(1)}% of vertices, over half ${(100 * strong / total).toFixed(1)}%` : "";
}

function selectBone(i) {
  if (!cur || !cur.bones.length) return;
  sel = (i + cur.bones.length) % cur.bones.length;
  const mode = OVERLAYS[overlay];
  if (mode === "weights") { paintWeights(true); $("boneinfo").style.display = "block"; }
  else if (mode === "bleed") paintBleed();
  else if (mode === "tears" && cur.tearSites) {
    const ti = cur.tearSites.findIndex((s) => s.bone === cur.bones[sel].name);
    if (ti >= 0 && ti !== cur.tearPoseIndex) applyTearPose(ti);
  }
  highlight();
}
const boneByName = (n) => cur ? cur.bones.findIndex((b) => b.name === n) : -1;

// ---------------------------------------------------------------------------------------------------------------
// The mesh, vertex by vertex, at bind pose: which bone owns each vertex, which is nearest, the audit's bleed rule,
// and the loose parts (islands) with the bone that dominates each. Computed once per model, when first needed.
// ---------------------------------------------------------------------------------------------------------------

function analyse() {
  if (cur.analysis) return cur.analysis;
  const B = cur.bones, nb = B.length;
  const names = B.map((b) => b.name), parent = B.map((b) => b.parent);
  for (const s of cur.skinned) s.skeleton.pose();
  cur.holder.updateMatrixWorld(true);
  const p = new THREE.Vector3();
  const heads = B.map((b) => b.bone.getWorldPosition(new THREE.Vector3()).toArray());
  const tails = B.map((b) => b.bone.localToWorld(new THREE.Vector3(0, b.len, 0)).toArray());
  const size = cur.box.getSize(new THREE.Vector3());
  const S = Math.max(size.x, size.y, size.z) || 1;
  const perMesh = [], isl = [];
  const weighted = new Float64Array(nb);
  for (const m of cur.meshes) {
    const g = m.geometry, n = g.attributes.position.count;
    const pos = new Float32Array(n * 3), dom = new Int32Array(n).fill(-1), share = new Float32Array(n);
    const si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
    const map = m.isSkinnedMesh && si ? m.skeleton.bones.map((b) => B.findIndex((x) => x.bone === b)) : null;
    let rider = -1;                                   // a rigid piece rides its parent bone whole
    if (!map) for (let q = m.parent; q && rider < 0; q = q.parent) rider = B.findIndex((x) => x.bone === q);
    const w = new Float64Array(nb);
    for (let v = 0; v < n; v++) {
      if (m.isSkinnedMesh) m.getVertexPosition(v, p); else p.fromBufferAttribute(g.attributes.position, v);
      m.localToWorld(p);
      pos[3 * v] = p.x; pos[3 * v + 1] = p.y; pos[3 * v + 2] = p.z;
      if (map) {
        let best = -1, bw = 0, tot = 0;
        const js = [];
        for (let k = 0; k < 4; k++) {
          const j = map[si.getComponent(v, k)], x = sw.getComponent(v, k);
          if (j < 0 || !(x > 0)) continue;
          tot += x; w[j] += x; js.push(j); weighted[j] += x;
        }
        for (const j of js) if (w[j] > bw) { bw = w[j]; best = j; }
        for (const j of js) w[j] = 0;
        if (tot > 1e-4) { dom[v] = best; share[v] = bw / tot; }
      } else if (rider >= 0) { dom[v] = rider; share[v] = 1; weighted[rider] += 1; }
    }
    const idx = g.index ? g.index.array : null;
    const { labels, count, sizes } = meshIslands(pos, idx, 1e-5 * S);
    // each vertex's share of the surface, as the audit weighs its bleed: a third of every triangle it is on
    const area = new Float32Array(n);
    const nt = idx ? idx.length / 3 : n / 3;
    for (let t = 0; t < nt; t++) {
      const a = idx ? idx[3 * t] : 3 * t, b = idx ? idx[3 * t + 1] : 3 * t + 1, c = idx ? idx[3 * t + 2] : 3 * t + 2;
      const ux = pos[3 * b] - pos[3 * a], uy = pos[3 * b + 1] - pos[3 * a + 1], uz = pos[3 * b + 2] - pos[3 * a + 2];
      const vx = pos[3 * c] - pos[3 * a], vy = pos[3 * c + 1] - pos[3 * a + 1], vz = pos[3 * c + 2] - pos[3 * a + 2];
      const ar = 0.5 * Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 3;
      area[a] += ar; area[b] += ar; area[c] += ar;
    }
    perMesh.push({ m, n, pos, dom, share, labels, area, offset: isl.length });
    for (let k = 0; k < count; k++) isl.push({ verts: sizes[k], glbVerts: 0, weight: new Float64Array(nb), nearVotes: new Map(),
                                              bleed: 0, box: new THREE.Box3(), rigid: true, unweighted: 0 });
  }
  // the nearest of the bones that carry weight, and the bleed rule, per vertex
  const deform = [];
  for (let j = 0; j < nb; j++) if (weighted[j] > 0) deform.push(j);
  let nBleed = 0, nAll = 0, aBleed = 0, aAll = 0;
  for (const pm of perMesh) {
    pm.near = new Int32Array(pm.n).fill(-1);
    pm.bleed = new Uint8Array(pm.n);
    for (let v = 0; v < pm.n; v++) {
      const x = pm.pos[3 * v], y = pm.pos[3 * v + 1], z = pm.pos[3 * v + 2];
      let best = -1, bd = Infinity;
      for (const j of deform) { const d = segDist2(x, y, z, heads[j], tails[j]); if (d < bd) { bd = d; best = j; } }
      pm.near[v] = best;
      const d = pm.dom[v];
      const I = isl[pm.offset + pm.labels[v]];
      aAll += pm.area[v];
      I.glbVerts++; I.box.expandByPoint(p.set(x, y, z));
      if (d < 0) { I.unweighted++; I.rigid = false; continue; }
      I.weight[d] += pm.share[v];
      if (pm.share[v] < 0.99) I.rigid = false;
      if (best >= 0) I.nearVotes.set(best, (I.nearVotes.get(best) || 0) + 1);
      nAll++;
      if (best >= 0 && isBleed(parent, names, d, best, Math.sqrt(segDist2(x, y, z, heads[d], tails[d])), Math.sqrt(bd), S)) {
        pm.bleed[v] = 1; I.bleed++; nBleed++; aBleed += pm.area[v];
      }
    }
  }
  // each island: the bone that dominates it, the bone it sits nearest, and whether it rides the wrong one
  const lat = 0.03 * S;
  for (const I of isl) {
    let dom = -1, dw = 0, tot = 0, used = 0;
    I.weight.forEach((x, j) => { if (x > 0) used++; tot += x; if (x > dw) { dw = x; dom = j; } });
    let near = -1, nv = 0;
    for (const [j, c] of I.nearVotes) if (c > nv) { nv = c; near = j; }
    I.dom = dom; I.domShare = tot ? dw / tot : 0; I.near = near;
    I.bleedFrac = I.glbVerts ? I.bleed / I.glbVerts : 0;
    I.rigid = I.rigid && used === 1;
    const why = [];
    if (I.bleedFrac > 0.3) why.push(`${Math.round(100 * I.bleedFrac)}% of it is bleed, it sits by ${near >= 0 ? names[near] : "?"}`);
    // a part across the middle carried by one side's bone (a collar or a belt riding one leg); the stage's
    // model is centred on Z, its left-right axis
    if (dom >= 0 && sideOf(names[dom]) && I.box.min.z < -lat && I.box.max.z > lat && I.domShare > 0.6)
      why.push(`it crosses the middle, but one side's ${names[dom]} carries ${Math.round(100 * I.domShare)}%`);
    if (!why.length && dom >= 0 && near >= 0 && I.domShare > 0.8 && hops(parent, dom, near) > 3)
      why.push(`${names[dom]} is ${hops(parent, dom, near)} bones from ${names[near]}, which it sits by`);
    I.why = why;
  }
  if (action) cur.mixer.update(0);
  cur.analysis = { perMesh, islands: isl, nBleed, nAll, bleedPct: aAll ? 100 * aBleed / aAll : 0, names, parent };
  return cur.analysis;
}

function paintBleed() {
  const A = analyse();
  for (const m of cur.meshes) m.material = cur.origMats.get(m);
  for (const m of cur.weightMats) m.dispose();
  cur.weightMats = [];
  const base = new THREE.Color(0.42, 0.45, 0.52), red = new THREE.Color(1, 0.12, 0.1), hot = new THREE.Color(1, 0.9, 0.3),
        orange = new THREE.Color(1, 0.55, 0.1), none = new THREE.Color(1, 0, 1), col = new THREE.Color();
  for (const pm of A.perMesh) {
    const colours = new Float32Array(pm.n * 3);
    for (let v = 0; v < pm.n; v++) {
      const I = A.islands[pm.offset + pm.labels[v]];
      if (pm.dom[v] < 0) col.copy(none);
      else if (pm.bleed[v]) col.copy(pm.dom[v] === sel ? hot : red);
      else if (I.why.length) col.copy(pm.dom[v] === sel ? hot : orange);
      else col.copy(base);
      col.toArray(colours, v * 3);
    }
    pm.m.geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true });
    cur.weightMats.push(mat);
    pm.m.material = mat;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// The lists: the audit's worst bones, the loose parts, the bleed
// ---------------------------------------------------------------------------------------------------------------

function listRow(ul, level, bone, title, why, onClick) {
  const li = document.createElement("li");
  li.className = level;
  const b = document.createElement("span"); b.className = "b"; b.textContent = title; li.appendChild(b);
  if (why) { const w = document.createElement("span"); w.className = "why"; w.textContent = why; li.appendChild(w); }
  if (bone >= 0) li.dataset.bone = bone;
  if (onClick) li.onclick = onClick; else li.classList.add("off");
  ul.appendChild(li);
  return li;
}

function renderWorst() {
  const ul = $("worstList"); ul.innerHTML = "";
  const a = cur && cur.audit;
  if (!a) return;
  $("worstVerdict").textContent = a.error ? "?" : a.grade;
  $("worstVerdict").className = "v " + (a.error ? "" : a.grade.toLowerCase());
  const bones = a.bones || [];
  $("worstNote").textContent = a.error ? a.error : (bones.length
    ? "worst bones, tinted on the skeleton (red: behind a failed check, amber: a warning); click one to select it"
    : "no bone stands out") + (a.stale ? ". The audit is older than the rig: run Audit again." : "");
  for (const r of bones) {
    const i = boneByName(r.bone);
    listRow(ul, r.level, i, r.bone, r.reasons.join("; "), i >= 0 ? () => selectBone(i) : null).title = r.reasons.join("\n");
  }
  for (const m of a.meshes || []) listRow(ul, "bad", -1, `mesh ${m.name}`, `${m.verts_over_4} vertices over 4 influences (max ${m.max_influences})`, null);
  highlight();
}

function renderIslands() {
  const A = analyse(), ul = $("islandList"); ul.innerHTML = "";
  const list = A.islands.filter((I) => I.glbVerts > 0)
    .sort((p, q) => (q.why.length > 0) - (p.why.length > 0) || q.verts - p.verts);
  const a = cur.audit;
  $("islandsNote").textContent = `${list.length} loose part${list.length === 1 ? "" : "s"} and the bone that carries each` +
    (a && a.islands != null ? ` (the audit: ${a.islands}, ${a.rigid_islands} on one bone)` : "") + "; click one for its bone";
  const shown = list.slice(0, 14);
  for (const I of shown) {
    const bone = I.dom >= 0 ? A.names[I.dom] : "no bone";
    const what = I.dom < 0 ? "unweighted" : I.rigid ? `rides ${bone} whole` : `mostly ${bone} (${Math.round(100 * I.domShare)}%)`;
    listRow(ul, I.why.length ? "warn" : "", I.dom, what, `${I.verts} verts${I.why.length ? " · " + I.why.join("; ") : ""}`,
            I.dom >= 0 ? () => selectBone(I.dom) : null);
  }
  if (list.length > shown.length) listRow(ul, "", -1, `+ ${list.length - shown.length} smaller`, "", null);
  highlight();
}

function renderBleed() {
  const A = analyse(), ul = $("bleedList"); ul.innerHTML = "";
  const a = cur.audit, note = $("bleedNote");
  note.innerHTML = "";
  const line = (c, t) => {
    const d = document.createElement("div"), sw = document.createElement("span");
    sw.className = "swatch"; sw.style.background = c; d.append(sw, t); note.appendChild(d);
  };
  line("#ff2019", `owned by a bone far from it (the audit's rule, here on the preview): ${A.bleedPct.toFixed(2)}% of the surface`);
  line("#ff8c1a", "a loose part on the wrong bone");
  line("#ffe64d", "either, on the selected bone");
  if (a && a.bleed_total_pct != null) {
    const d = document.createElement("div"); d.style.marginTop = "3px";
    const lim = a.checks && a.checks.bleed_pct ? a.checks.bleed_pct.limit : null;
    d.textContent = `The audit: ${a.bleed_total_pct}% of the surface${lim != null ? ` (limit ${lim}%)` : ""}. Each owner, and the bone the surface is nearer:`;
    note.appendChild(d);
  }
  for (const [owner, nearer, p] of (a && a.bleed_pairs) || []) {
    const i = boneByName(owner);
    listRow(ul, p >= 0.25 ? "bad" : "warn", i, owner, `${p}% of the surface, nearer ${nearer}`, i >= 0 ? () => selectBone(i) : null);
  }
  for (const I of A.islands.filter((x) => x.why.length))
    listRow(ul, "warn", I.dom, `loose part on ${I.dom >= 0 ? A.names[I.dom] : "no bone"}`, `${I.verts} verts · ${I.why.join("; ")}`,
            I.dom >= 0 ? () => selectBone(I.dom) : null);
  if (!ul.children.length) listRow(ul, "", -1, "no bleed", "", null);
  highlight();
}

// ---------------------------------------------------------------------------------------------------------------
// Tears overlay: audit tear markers in 3D and test poses
// ---------------------------------------------------------------------------------------------------------------

async function fetchFullAudit(name) {
  if (!cur) return null;
  if (cur.fullAudit !== undefined) return cur.fullAudit;
  try {
    const res = await fetch(withToken("/api/audit?name=" + encodeURIComponent(name)));
    cur.fullAudit = res.ok ? await res.json() : null;
  } catch (e) {
    cur.fullAudit = null;
  }
  return cur.fullAudit;
}

function clearTearMarkers() {
  if (!cur) return;
  if (cur.tearsGroup) {
    cur.tearsGroup.traverse((n) => { if (n.geometry) n.geometry.dispose(); if (n.material) n.material.dispose(); });
    cur.tearsGroup.clear();
  }
  if (cur.tearLabels) {
    for (const l of cur.tearLabels) l.removeFromParent();
    cur.tearLabels = [];
  }
}

async function setupTears() {
  if (!cur) return;
  if (playing) { playing = false; $("bPlay").textContent = "Play"; }
  if (action) { action.stop(); action = null; }
  const full = await fetchFullAudit(cur.item.name);
  if (!cur) return;
  cur.tearSites = (full && full.tear_sites) || [];
  if (cur.tearPoseIndex === undefined || cur.tearPoseIndex >= cur.tearSites.length) cur.tearPoseIndex = 0;
  renderTears();
  applyTearPose(cur.tearPoseIndex);
}

function applyTearPose(index) {
  if (!cur) return;
  for (const s of cur.skinned) s.skeleton.pose();
  if (!cur.tearSites || !cur.tearSites.length) {
    clearTearMarkers();
    cur.holder.updateMatrixWorld(true);
    updateOverlay();
    cur._inTearPose = false;
    return;
  }
  cur._inTearPose = true;
  cur.tearPoseIndex = Math.max(0, Math.min(index, cur.tearSites.length - 1));
  const site = cur.tearSites[cur.tearPoseIndex];
  if (site.pose === "combined") {
    const angles = (cur.fullAudit && cur.fullAudit.combined_pose && cur.fullAudit.combined_pose.angles_deg) || site.angles_deg;
    if (angles) {
      for (const [boneName, deg] of Object.entries(angles)) {
        const bi = boneByName(boneName);
        if (bi >= 0 && deg) cur.bones[bi].bone.rotateX(deg * Math.PI / 180);
      }
    } else {
      for (const b of cur.bones) {
        if (b.deform && b.parent >= 0) {
          const deg = defaultCombinedAngle(b.role);
          if (deg) b.bone.rotateX(deg * Math.PI / 180);
        }
      }
    }
    if (site.clusters && site.clusters.length && site.clusters[0].bone) {
      const bi = boneByName(site.clusters[0].bone);
      if (bi >= 0) selectBone(bi);
    }
  } else {
    const bi = boneByName(site.bone);
    if (bi >= 0) {
      const rot = site.rotation_deg || (site.pose === "twist" ? [0, 60, 0] : [40, 0, 0]);
      const b = cur.bones[bi];
      if (rot[0]) b.bone.rotateX(rot[0] * Math.PI / 180);
      if (rot[1]) b.bone.rotateY(rot[1] * Math.PI / 180);
      if (rot[2]) b.bone.rotateZ(rot[2] * Math.PI / 180);
      selectBone(bi);
    }
  }
  cur.holder.updateMatrixWorld(true);
  updateOverlay();
  drawTearMarkers(site);
  for (let k = 0; k < $("tearsList").children.length; k++) {
    $("tearsList").children[k].classList.toggle("sel", k === cur.tearPoseIndex);
  }
}

function drawTearMarkers(site) {
  if (!cur) return;
  if (!cur.tearsGroup) {
    cur.tearsGroup = new THREE.Group();
    cur.tearsGroup.renderOrder = 14;
    cur.root.add(cur.tearsGroup);
  }
  clearTearMarkers();
  if (!site || !site.clusters || !site.clusters.length) return;
  const longest = Math.max(cur.fileSize.x, cur.fileSize.y, cur.fileSize.z) || 1;
  const r0 = longest * 0.015;
  for (const c of site.clusters) {
    const p = mapTearPoint(c.at, c.at_bbox, cur.box);
    const pos = new THREE.Vector3(p[0], p[1], p[2]);
    const r = tearMarkerRadius(c.edges, r0);
    const isBad = tearSeverity(c.gap_pct, site.pose) === "bad";
    const col = isBad ? AUDIT_BAD : AUDIT_WARN;
    const geo = new THREE.SphereGeometry(r, 14, 10);
    const mat = new THREE.MeshBasicMaterial({ color: col, depthTest: false, transparent: true, opacity: 0.85 });
    const m = new THREE.Mesh(geo, mat);
    m.position.copy(pos);
    m.renderOrder = 14;
    cur.tearsGroup.add(m);

    const boneName = c.bone || site.bone || "tear";
    const t = label(`${boneName} · ${c.edges} tear${c.edges === 1 ? "" : "s"} (${c.gap_pct}%)`, "teartag " + (isBad ? "bad" : "warn"));
    t.element.title = `${c.edges} edge${c.edges === 1 ? "" : "s"} tore (${c.gap_pct}% gap) on ${boneName}` +
      (c.owners && c.owners.length ? ` · owners: ${c.owners.join(", ")}` : "");
    t.position.copy(pos);
    t.element.onclick = (e) => {
      e.stopPropagation();
      if (c.bone) {
        const bi = boneByName(c.bone);
        if (bi >= 0) selectBone(bi);
      }
    };
    cur.root.add(t);
    cur.tearLabels.push(t);
  }
}

function renderTears() {
  const ul = $("tearsList"); ul.innerHTML = "";
  const a = cur && cur.audit;
  if (!a) {
    $("tearsVerdict").textContent = "";
    $("tearsNote").textContent = "no audit for this model yet";
    return;
  }
  $("tearsVerdict").textContent = a.error ? "?" : a.grade;
  $("tearsVerdict").className = "v " + (a.error ? "" : a.grade.toLowerCase());
  const sites = cur.tearSites || [];
  if (!sites.length) {
    $("tearsNote").textContent = "no tears in the audit";
    return;
  }
  $("tearsNote").textContent = `${sites.length} tear site${sites.length === 1 ? "" : "s"} · click a pose to test`;
  sites.forEach((site, idx) => {
    const isComb = site.pose === "combined";
    const title = isComb ? "Combined pose" : `${site.bone} (${site.pose === "twist" ? "twist 60°" : "bend 40°"})`;
    const isBad = tearSeverity(site.worst_gap_pct, site.pose) === "bad";
    const bi = site.bone ? boneByName(site.bone) : -1;
    const li = listRow(ul, isBad ? "bad" : "warn", bi, title,
      `${site.edges} edge${site.edges === 1 ? "" : "s"} · worst gap ${site.worst_gap_pct}%`,
      () => applyTearPose(idx));
    if (idx === cur.tearPoseIndex) li.classList.add("sel");
  });
}

function updateOverlay() {
  if (!cur || !cur.overlay.visible) return;
  const s = new THREE.Matrix4(), mid = new THREE.Vector3();
  for (const b of cur.bones) {
    s.makeScale(b.len, b.len, b.len);
    b.mesh.matrix.multiplyMatrices(b.bone.matrixWorld, s);
    b.mesh.matrixWorldNeedsUpdate = true;
    if (b.label.visible) {
      mid.set(0, b.len * 0.5, 0);
      b.bone.localToWorld(mid);
      b.label.position.copy(mid);
    }
  }
}

// click a bone: the nearest head or middle on screen, within reach
function pickBone(x, y) {
  if (!cur || !cur.bones.length || OVERLAYS[overlay] === "off") return;
  const rect = renderer.domElement.getBoundingClientRect();
  const p = new THREE.Vector3();
  let best = -1, bestD = 22 * 22;
  cur.bones.forEach((b, i) => {
    for (const t of [0.0, 0.5]) {
      p.set(0, b.len * t, 0); b.bone.localToWorld(p); p.project(camera);
      if (p.z < -1 || p.z > 1) continue;
      const sx = (p.x + 1) / 2 * rect.width + rect.left, sy = (1 - p.y) / 2 * rect.height + rect.top;
      const d = (sx - x) ** 2 + (sy - y) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    }
  });
  if (OVERLAYS[overlay] === "tears" && cur.tearSites && cur.tearsGroup) {
    const site = cur.tearSites[cur.tearPoseIndex];
    if (site && site.clusters) {
      site.clusters.forEach((c) => {
        const cp = mapTearPoint(c.at, c.at_bbox, cur.box);
        const v = new THREE.Vector3(cp[0], cp[1], cp[2]);
        cur.root.localToWorld(v);
        v.project(camera);
        if (v.z >= -1 && v.z <= 1) {
          const sx = (v.x + 1) / 2 * rect.width + rect.left, sy = (1 - v.y) / 2 * rect.height + rect.top;
          const d = (sx - x) ** 2 + (sy - y) ** 2;
          if (d < 28 * 28 && c.bone) {
            const bi = boneByName(c.bone);
            if (bi >= 0 && d < bestD) { best = bi; bestD = d; }
          }
        }
      });
    }
  }
  if (best >= 0) selectBone(best);
}
let down = null;
renderer.domElement.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", (e) => {
  if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) pickBone(e.clientX, e.clientY);
  down = null;
});

// ---------------------------------------------------------------------------------------------------------------
// Camera: the side view (the game's) or a free orbit
// ---------------------------------------------------------------------------------------------------------------

// What must be in frame: the model over all its clips and the figure with its label, both whole. "model" fits
// them tight; "all" (the stage) leaves room round them for the floor and the ruler.
function frameBox() {
  const box = cur.envelope.clone();
  const fig = new THREE.Box3().setFromObject(figure);
  fig.max.y += 0.12;                                  // the "1.8 m" label over its head
  box.union(fig);
  if (framing === "all") {
    const s = box.getSize(new THREE.Vector3());
    box.min.y -= 0.08 * s.y;
    box.max.z = Math.max(box.max.z, Math.max(cur.box.max.z, 0.3) + 0.6);      // the ruler and its numbers
  }
  return box;
}

// The largest rectangle of the window no HUD panel covers, for content of aspect `aspect` (width / height): every
// panel's edges are tried as the rectangle's edges, and the one the content fills biggest wins.
function clearRect(aspect) {
  const W = window.innerWidth, H = window.innerHeight, gap = 8;
  const panels = [];
  for (const el of document.querySelectorAll(".hud")) {
    if (el.id === "msg" || el.offsetParent === null && getComputedStyle(el).position !== "fixed") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    panels.push({ l: r.left - gap, r: r.right + gap, t: r.top - gap, b: r.bottom + gap });
  }
  const xs = [0, W, ...panels.flatMap((p) => [p.l, p.r])].filter((x) => x >= 0 && x <= W);
  const ys = [0, H, ...panels.flatMap((p) => [p.t, p.b])].filter((y) => y >= 0 && y <= H);
  let best = null, bestS = 0;
  for (const l of xs) for (const r of xs) {
    if (r - l < 60) continue;
    for (const t of ys) for (const b of ys) {
      if (b - t < 60) continue;
      if (panels.some((p) => p.l < r && p.r > l && p.t < b && p.b > t)) continue;
      const s = Math.min((r - l) / aspect, b - t);
      if (s > bestS) { bestS = s; best = { l, r, t, b }; }
    }
  }
  // a window too small for any clear rectangle: the whole of it (the HUD shrinks on a narrow window)
  if (!best || bestS < Math.min(W / aspect, H) * 0.3) best = { l: 0, r: W, t: 0, b: H };
  return best;
}

// Fit the box's corners into the clear rectangle with the camera looking along `dir` (from the target): move the
// camera across and back until the projected corners fill it, less a margin, with the rectangle's centre on theirs.
function fitCamera(box, dir, rect, margin) {
  const W = window.innerWidth, H = window.innerHeight;
  const nx0 = rect.l / W * 2 - 1, nx1 = rect.r / W * 2 - 1, ny0 = 1 - rect.b / H * 2, ny1 = 1 - rect.t / H * 2;
  const mx = (nx1 - nx0) * margin, my = (ny1 - ny0) * margin;
  const R = { x0: nx0 + mx, x1: nx1 - mx, y0: ny0 + my, y1: ny1 - my };
  const corners = [];
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) corners.push(new THREE.Vector3(x, y, z));
  const target = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length() || 1;
  const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  let dist = size / t;
  const right = new THREE.Vector3(), up = new THREE.Vector3(), p = new THREE.Vector3();
  for (let it = 0; it < 40; it++) {
    camera.near = Math.max(0.001, dist / 400); camera.far = dist * 60; camera.updateProjectionMatrix();
    camera.position.copy(target).addScaledVector(dir, dist);
    camera.lookAt(target);
    camera.updateMatrixWorld(true);
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const c of corners) {
      p.copy(c).project(camera);
      x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
    }
    const s = Math.max((x1 - x0) / (R.x1 - R.x0), (y1 - y0) / (R.y1 - R.y0));
    const dx = (x0 + x1) / 2 - (R.x0 + R.x1) / 2, dy = (y0 + y1) / 2 - (R.y0 + R.y1) / 2;
    right.setFromMatrixColumn(camera.matrixWorld, 0);
    up.setFromMatrixColumn(camera.matrixWorld, 1);
    target.addScaledVector(right, dx * dist * t * camera.aspect).addScaledVector(up, dy * dist * t);
    const nd = dist * (1 + (s - 1) * 0.9);
    if (Math.abs(s - 1) < 1e-4 && Math.abs(dx) < 1e-4 && Math.abs(dy) < 1e-4) break;
    dist = Math.max(1e-4, nd);
  }
  camera.position.copy(target).addScaledVector(dir, dist);
  camera.near = Math.max(0.001, dist / 400); camera.far = dist * 60; camera.updateProjectionMatrix();
  controls.target.copy(target);
  controls.update();
}

function frame() {
  $("bFrame").textContent = framing === "all" ? "Frame: stage" : "Frame: model";
  if (!cur) return;
  const box = frameBox();
  const s = box.getSize(new THREE.Vector3());
  let dir, aspect;
  if (view === "front") {
    dir = new THREE.Vector3(1, 0.05, 0).normalize();
    aspect = s.z / Math.max(s.y, 1e-6);
  } else if (view === "side") {
    dir = new THREE.Vector3(0, 0.16, 1).normalize();
    aspect = s.x / Math.max(s.y, 1e-6);
  } else if (view === "hero") {
    dir = new THREE.Vector3(0.72, 0.22, 0.65).normalize();
    aspect = (s.x + s.z) / Math.max(s.y * 1.2, 1e-6);
  } else {
    // orbit: preserve current direction if valid, else default to hero 3/4
    const curDir = camera.position.clone().sub(controls.target).normalize();
    dir = curDir.lengthSq() > 0.5 ? curDir : new THREE.Vector3(0.62, 0.35, 0.72).normalize();
    aspect = (s.x + s.z) / Math.max(s.y * 1.2, 1e-6);
  }
  fitCamera(box, dir, clearRect(aspect), framing === "all" ? 0.08 : 0.03);
}

// The lists sit under the model's name; the scrub bar shows only with a clip.
function layoutPanels() {
  const info = $("info").getBoundingClientRect();
  if (window.innerWidth > 760) {
    $("left").style.top = Math.round(info.bottom + 8) + "px";
    const bottom = $("bottom").getBoundingClientRect(), keys = $("keys").getBoundingClientRect();
    const floor = keys.height > 0 ? Math.min(bottom.top, keys.top) : bottom.top;
    $("left").style.maxHeight = Math.max(80, floor - info.bottom - 20) + "px";
    $("right").style.maxHeight = Math.max(80, bottom.top - 20) + "px";
  } else {                                             // narrow: the checks and the audit stack under the name
    $("left").style.top = ""; $("left").style.maxHeight = ""; $("right").style.maxHeight = "";
    $("right").style.top = Math.round(info.bottom + 6) + "px";
  }
  if (window.innerWidth > 760) $("right").style.top = "";
}

// ---------------------------------------------------------------------------------------------------------------
// The scrub bar: a tick every frame (or every few when they crowd), numbered ones, the clip's keys, the playhead
// ---------------------------------------------------------------------------------------------------------------

const scrubEl = $("scrub"), scrubCanvas = $("scrubCanvas");
let scrubbing = null;                                  // {wasPlaying} while dragging

function drawScrub() {
  const has = !!(cur && action && cur.clips.length);
  scrubEl.style.display = has ? "block" : "none";
  if (!has) return;
  const clip = cur.clips[ci], dur = clip.duration, fps = cur.fps;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = scrubCanvas.clientWidth, h = scrubCanvas.clientHeight;
  if (!w) return;
  if (scrubCanvas.width !== Math.round(w * dpr) || scrubCanvas.height !== Math.round(h * dpr)) {
    scrubCanvas.width = Math.round(w * dpr); scrubCanvas.height = Math.round(h * dpr);
  }
  const g = scrubCanvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const pad = 6, W = w - 2 * pad;
  const X = (t) => pad + (dur > 0 ? t / dur : 0) * W;
  const frames = dur * fps;
  const tk = scrubTicks(frames, W);
  g.fillStyle = "#252b36"; g.fillRect(pad, 13, W, 8);
  const tNow = Math.min(action.time, dur);
  g.fillStyle = "rgba(91,143,240,.45)"; g.fillRect(pad, 13, X(tNow) - pad, 8);
  g.font = "10px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "top";
  for (let f = 0; f <= tk.last; f += tk.minor) {
    const x = Math.round(X(Math.min(dur, f / fps))) + 0.5, major = f % tk.major === 0;
    g.strokeStyle = major ? "#8a93a6" : "#4a5468";
    g.beginPath(); g.moveTo(x, major ? 21 : 21); g.lineTo(x, major ? 27 : 24); g.stroke();
    if (major) { g.fillStyle = "#8a93a6"; g.fillText(String(f), Math.min(w - 8, Math.max(8, x)), 25); }
  }
  // the clip's keys, as three.js holds them (the preview samples every frame, so a key a frame is normal)
  const keys = cur.keys[ci] || [];
  g.fillStyle = "#e8b64a";
  const step = Math.max(1, Math.ceil(keys.length / Math.max(1, W / 3)));
  for (let k = 0; k < keys.length; k += step) { const x = X(keys[k]); g.fillRect(x - 1, 8, 2, 4); }
  // the playhead
  const x = X(tNow);
  g.fillStyle = "#ffffff"; g.fillRect(x - 1, 5, 2, 18);
  g.beginPath(); g.moveTo(x - 5, 3); g.lineTo(x + 5, 3); g.lineTo(x, 9); g.closePath(); g.fill();
  const fr = Math.round(tNow * fps), last = Math.round(dur * fps);
  const pct = Math.round(dur > 0 ? (tNow / dur) * 100 : 0);
  $("scrubLeft").textContent = `clip ${ci + 1}/${cur.clips.length}: ${cur.clips[ci].name} · frame ${fr} / ${last}${scrubbing ? " · scrubbing (paused)" : ""}`;
  $("scrubRight").textContent = `${keys.length} key${keys.length === 1 ? "" : "s"} · ${tNow.toFixed(2)} s / ${dur.toFixed(2)} s (${pct}%)`;
}

function scrubAt(e) {
  const r = scrubCanvas.getBoundingClientRect(), pad = 6;
  seek(scrubTime(e.clientX - r.left - pad, r.width - 2 * pad, cur.clips[ci].duration, cur.fps));
}
scrubCanvas.addEventListener("pointerdown", (e) => {
  if (!cur || !action) return;
  scrubbing = { wasPlaying: playing };
  setPlaying(false);
  try { scrubCanvas.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic pointer */ }
  scrubAt(e);
  e.preventDefault();
});
scrubCanvas.addEventListener("pointermove", (e) => { if (scrubbing) scrubAt(e); });
const endScrub = (e) => {
  if (!scrubbing) return;
  const was = scrubbing.wasPlaying;
  scrubbing = null;
  try { scrubCanvas.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  if (was) setPlaying(true);
  drawScrub();
};
scrubCanvas.addEventListener("pointerup", endScrub);
scrubCanvas.addEventListener("pointercancel", endScrub);

function setView(v) {
  view = v;
  controls.enableRotate = v === "orbit";
  $("bView").textContent = viewLabel(v);
  $("bView").classList.toggle("on", v === "orbit");
  frame();
  hud();
}

// ---------------------------------------------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------------------------------------------

function checks() {
  const out = [], info = cur.info, s = cur.box.getSize(new THREE.Vector3());
  const h = s.y;
  if (info.metres) {
    out.push(["ok", `height ${h.toFixed(2)} m, ${(h / REF_HEIGHT).toFixed(2)}× the 1.8 m figure (longest ${info.metres.toFixed(2)} m, from the card)`]);
    if (Math.abs(cur.scale - 1) > 0.1) out.push(["warn", `the file is not in metres: shown ×${cur.scale.toPrecision(3)} (its units per metre ${(1 / cur.scale).toPrecision(3)})`]);
  } else {
    out.push(["warn", `height ${h.toFixed(2)} units, no real size on the card (rig.json "card": {"metres": ...}): 1 unit shown as 1 m`]);
  }
  if (h < 0.02 || h > 60) out.push(["bad", `height ${h.toPrecision(3)} m looks wrong (units?)`]);

  const f = facing();
  if (!f) out.push(["warn", "facing: no head, toe or tail bone to tell it by"]);
  else if (f.dir.x > 0.7) out.push(["ok", `faces +X, the side view's forward (by ${f.by})`]);
  else if (f.dir.x < -0.7) out.push(["bad", `faces -X, backwards (by ${f.by}): check rig.json "forward"`]);
  else out.push(["warn", `faces ${f.dir.z > 0 ? "+Z (the camera)" : "-Z (away)"}, not +X (by ${f.by}): check rig.json "forward"`]);

  const nb = cur.bones.length;
  const nd = cur.bones.filter((b) => b.deform).length;
  if (!nb) out.push(["bad", "no skeleton in the preview"]);
  else out.push(["ok", `${nb} bones, ${nd} deforming`]);
  if (!cur.skinned.length && cur.meshes.length) out.push(["warn", "the mesh is not skinned"]);

  if (!cur.clips.length) out.push(["warn", "no clips: bind pose only (Make clips, then Preview)"]);
  else out.push(["ok", `${cur.clips.length} clip${cur.clips.length === 1 ? "" : "s"}: ${cur.clips.map((c) => c.name).join(", ")}`]);
  for (const c of cur.clips) if (!(c.duration > 0.5 / cur.fps)) out.push(["bad", `clip "${c.name}" has zero length`]);
  if (cur.item.stale) out.push(["warn", "preview.glb is older than the rig or its clips: run Preview again"]);

  const a = cur.audit;
  if (!a) out.push(["warn", "no audit yet: run Audit for its verdict and worst bones"]);
  else if (a.error) out.push(["warn", a.error]);
  else {
    const NAMES = { bleed_pct: "bleed", combined_tears: "combined-pose tears", bend_tears: "bend tears", head_pct: "head share",
                    max_influences: "influences" };
    const failed = Object.entries(a.checks || {}).filter(([, c]) => c.ok === false).map(([k, c]) => {
      const u = k.endsWith("_pct") ? "%" : "";
      return `${NAMES[k] || k.replace(/_/g, " ")} ${c.value}${u} (limit ${c.limit}${u})`;
    });
    out.push([a.grade === "PASS" ? "ok" : a.grade === "CHECK" ? "warn" : "bad",
              a.grade === "PASS" ? "audit PASS" + (a.warnings.length ? `, ${a.warnings.length} warning${a.warnings.length === 1 ? "" : "s"}` : "")
                                 : `audit ${a.grade}: ` + failed.join(", ") + (a.grade === "CHECK" ? " (look at it here)" : "")]);
    if (a.stale) out.push(["warn", "the audit is older than the rig: run Audit again"]);
  }
  return out;
}

function facing() {
  // measured at the bind pose, in the stage's frame (after the turn to +X)
  const B = cur.bones;
  if (!B.length) return null;
  for (const s of cur.skinned) s.skeleton.pose();
  cur.holder.updateMatrixWorld(true);
  const pos = (b, t = 0) => b.bone.localToWorld(new THREE.Vector3(0, b.len * t, 0));
  const find = (re) => B.filter((b) => re.test(b.name.toLowerCase()));
  const size = cur.box.getSize(new THREE.Vector3()).length();
  const flat = (v) => new THREE.Vector3(v.x, 0, v.z);
  const hips = find(/hips|pelvis|^root$|^body$|spine_?0?1?$|^spine/)[0] || B.find((b) => b.parent < 0);
  const head = find(/(^|[^a-z])head|skull/)[0];
  let res = null;
  if (head && hips) {
    const d = flat(pos(head, 0.5).sub(pos(hips)));
    if (d.length() > size * 0.08) res = { dir: d.normalize(), by: "head ahead of the hips" };
  }
  if (!res) {
    const toes = find(/toe/).filter((b) => b.parent >= 0);
    const d = new THREE.Vector3();
    for (const t of toes) d.add(flat(pos(t).sub(pos(B[t.parent]))));
    if (toes.length && d.length() > 1e-6) res = { dir: d.normalize(), by: "the toes" };
  }
  if (!res) {
    const feet = find(/foot/);
    const d = new THREE.Vector3();
    for (const t of feet) d.add(flat(pos(t, 1).sub(pos(t))));
    if (feet.length && d.length() > size * 0.01) res = { dir: d.normalize(), by: "the feet" };
  }
  if (!res && hips) {
    const tail = find(/tail/);
    if (tail.length) {
      const d = flat(pos(hips).sub(pos(tail[tail.length - 1], 1)));
      if (d.length() > size * 0.05) res = { dir: d.normalize(), by: "the tail behind" };
    }
  }
  if (action) cur.mixer.update(0);
  return res;
}

function renderChecks(list) {
  $("checkList").innerHTML = "";
  for (const [lvl, text] of list) {
    const li = document.createElement("li");
    li.className = lvl === "ok" ? "" : lvl;
    li.textContent = text;
    $("checkList").appendChild(li);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// HUD
// ---------------------------------------------------------------------------------------------------------------

function hud() {
  const item = models[mi];
  $("modelName").textContent = item ? item.name : "no models";
  $("modelGroup").textContent = item && item.group ? item.group : "";
  $("modelIndex").textContent = models.length ? `model ${mi + 1} / ${models.length}` : "";
  if (cur && cur.clips.length) {
    $("clipName").textContent = cur.clips[ci].name;
  } else {
    $("clipName").textContent = cur ? "bind pose (no clips)" : "";
  }
  $("modeLine").textContent = cur ? `view: ${viewLabel(view)} · overlay: ${OVERLAYS[overlay]}` : "";
  hudTime(); hudPlay();
  drawScrub();
}

function hudTime() {
  if (!cur || !cur.clips.length || !action) { $("clipTime").textContent = cur ? "frame 0" : ""; return; }
  const dur = cur.clips[ci].duration, t = Math.min(action.time, dur);
  const fr = Math.round(t * cur.fps), last = Math.round(dur * cur.fps);
  $("clipTime").textContent = `clip ${ci + 1} / ${cur.clips.length} · frame ${fr} / ${last} · ${t.toFixed(2)} s / ${dur.toFixed(2)} s${loop ? " · loop" : ""}`;
}

function hudPlay() { $("bPlay").textContent = playing ? "Pause" : "Play"; }

// ---------------------------------------------------------------------------------------------------------------
// Running the preview step from here
// ---------------------------------------------------------------------------------------------------------------

async function runPreview(name, btn) {
  btn.disabled = true;
  try {
    const job = await api("/api/run", { model: name, step: "preview" });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1000));
      const j = await api("/api/jobs/" + job.id);
      btn.textContent = `${j.state}… (${j.lines} log lines)`;
      if (j.state !== "queued" && j.state !== "running") {
        if (j.state !== "done") { message("Preview " + j.state, j.log.slice(-6).join("\n")); return; }
        break;
      }
    }
    await refreshList(name);
    show(mi);
  } catch (e) {
    message("Could not run Preview", e.message);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Input: keyboard, buttons, gamepad
// ---------------------------------------------------------------------------------------------------------------

const act = {
  prevModel: () => show(mi - 1), nextModel: () => show(mi + 1),
  prevClip: () => cur && cur.clips.length && playClip(ci - 1), nextClip: () => cur && cur.clips.length && playClip(ci + 1),
  play: () => setPlaying(!playing),
  overlay: () => { overlay = (overlay + 1) % OVERLAYS.length; applyOverlay(); if (view !== "orbit") frame(); },
  view: () => setView(nextView(view)),
  frame: () => { framing = framing === "all" ? "model" : "all"; frame(); },
  prevBone: () => {
    if (OVERLAYS[overlay] === "tears" && cur && cur.tearSites && cur.tearSites.length > 1) {
      applyTearPose((cur.tearPoseIndex - 1 + cur.tearSites.length) % cur.tearSites.length);
    } else {
      selectBone(sel - 1);
    }
  },
  nextBone: () => {
    if (OVERLAYS[overlay] === "tears" && cur && cur.tearSites && cur.tearSites.length > 1) {
      applyTearPose((cur.tearPoseIndex + 1) % cur.tearSites.length);
    } else {
      selectBone(sel + 1);
    }
  },
  loop: () => setLoop(!loop),
  stepBack: () => stepFrame(-1), stepFwd: () => stepFrame(1),
  start: () => { if (action) { setPlaying(false); seek(0); } },
  end: () => { if (action) { setPlaying(false); seek(action.getClip().duration); } },
};

window.addEventListener("keydown", (e) => {
  if (e.target && ((e.target.tagName === "INPUT" && e.target.type !== "checkbox" && e.target.type !== "range") || e.target.tagName === "SELECT")) return;
  const k = e.key;
  if (k >= "1" && k <= "9" && !e.ctrlKey && !e.altKey && !e.metaKey) {
    const idx = parseInt(k, 10) - 1;
    if (cur && cur.clips && idx < cur.clips.length) {
      e.preventDefault();
      playClip(idx);
      return;
    }
  }
  const map = {
    ArrowLeft: act.prevModel, ArrowRight: act.nextModel, ArrowUp: act.prevClip, ArrowDown: act.nextClip,
    " ": act.play, r: act.overlay, R: act.overlay, v: act.view, V: act.view, f: act.frame, F: act.frame,
    "[": act.prevBone, "]": act.nextBone, l: () => setLoop(!loop), L: () => setLoop(!loop),
    "-": () => setSpeed(speed - 0.1), "=": () => setSpeed(speed + 0.1), "+": () => setSpeed(speed + 0.1),
    ",": () => stepFrame(-1), ".": () => stepFrame(1), Home: act.start, End: act.end,
  };
  if (map[k]) { e.preventDefault(); if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); map[k](); }
});

const click = (id, fn) => $(id).addEventListener("click", (e) => { fn(); e.currentTarget.blur(); });
click("bPrevModel", act.prevModel); click("bNextModel", act.nextModel);
click("bPrevClip", act.prevClip); click("bNextClip", act.nextClip);
click("bPlay", act.play); click("bOverlay", act.overlay); click("bView", act.view); click("bFrame", act.frame);
if ($("clipSelect")) {
  $("clipSelect").addEventListener("change", (e) => {
    const idx = parseInt(e.target.value, 10);
    if (cur && cur.clips && idx >= 0 && idx < cur.clips.length) {
      playClip(idx);
    }
    e.target.blur();
  });
}
$("speed").addEventListener("input", (e) => setSpeed(Number(e.target.value)));
$("speed").addEventListener("change", (e) => e.target.blur());
$("loop").addEventListener("change", (e) => { setLoop(e.target.checked); e.target.blur(); });

// Gamepads (viewer_logic.js padStep): the "standard" mapping that Xbox and PlayStation pads report, with a
// fallback for others; the D-pad or the left stick (with a deadzone) moves through models and clips, and a held
// direction repeats. navigator.getGamepads can be replaced to simulate a pad.
const padState = new Map();
function pollPads(now) {
  let pads = [];
  try { pads = navigator.getGamepads ? Array.from(navigator.getGamepads()) : []; } catch (e) { pads = []; }
  const live = new Set();
  for (const p of pads) {
    if (!p || p.connected === false) continue;
    const key = p.index + ":" + p.id;
    live.add(key);
    const r = padStep(p, padState.get(key), now / 1000);
    padState.set(key, r.state);
    for (const name of r.fire) if (act[name]) act[name]();
  }
  for (const k of [...padState.keys()]) if (!live.has(k)) padState.delete(k);
}

// ---------------------------------------------------------------------------------------------------------------
// The loop
// ---------------------------------------------------------------------------------------------------------------

let last = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  pollPads(now);
  if (cur && action && playing && !scrubbing) { cur.mixer.update(dt); drawScrub(); }
  if (cur) { cur.holder.updateMatrixWorld(true); updateOverlay(); hudTime(); }
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  requestAnimationFrame(tick);
}

// A link can open the viewer on a given state: ?model=&clip=<name|index>&overlay=<off|skeleton|names|weights|bleed>
// &bone=<name>&view=<side|orbit>&framing=<model|all>&time=<seconds, paused there>&speed=&loop=0
function applyParams(q) {
  if (q.get("speed")) setSpeed(Number(q.get("speed")));
  if (q.get("loop") === "0") setLoop(false);
  if (q.get("view") === "orbit") setView("orbit");
  if (q.get("framing") === "all" || q.get("framing") === "model") framing = q.get("framing");
  if (!cur) { frame(); return; }
  const c = q.get("clip");
  if (c !== null && cur.clips.length) {
    const i = cur.clips.findIndex((x) => x.name === c);
    playClip(i >= 0 ? i : Number(c) || 0);
  }
  const b = q.get("bone");
  if (b) { const i = cur.bones.findIndex((x) => x.name === b); if (i >= 0) sel = i; }
  const o = OVERLAYS.indexOf(q.get("overlay"));
  if (o >= 0) overlay = o;
  applyOverlay();
  if (q.get("time") !== null && action) {
    setPlaying(false);
    seek(Math.min(action.getClip().duration, Math.max(0, Number(q.get("time")))));
  }
  hud();
  layoutPanels();
  frame();
}

let ready = false;                                   // the first model is up (for a script taking pictures)
resize();
setView("side");
setSpeed(1);
applyOverlay();
(async () => {
  try {
    const q = new URLSearchParams(location.search);
    await refreshList(q.get("model"));
    await show(mi);
    applyParams(q);
  } catch (e) {
    message("Could not reach the workbench", e.message);
  }
  ready = true;
  requestAnimationFrame(tick);
})();

// for inspection from a console: the loaded model and the controls
window.autorigViewer = { get model() { return cur; }, camera, controls, act, selectBone, seek, frame, clearRect, pollPads,
                         get overlay() { return OVERLAYS[overlay]; }, get time() { return action ? action.time : null; },
                         get playing() { return playing; }, get ready() { return ready; }, get selected() { return cur && sel >= 0 ? cur.bones[sel].name : null; } };


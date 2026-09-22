// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the results viewer (gui/viewer.html). Plain ES modules, no build step; three.js is vendored
// under gui/vendor/three (MIT).
//
// A stage (dark, a floor ruled every unit, a 1.8 m reference figure to the left), the model standing centred on
// the floor facing +X as a side-on game shows it, its clips, a rig overlay (skeleton, bone names, weights of one
// bone), and a few checks: real height, facing, bones, clips.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const TOKEN = window.AUTORIG_TOKEN;
const REF_HEIGHT = 1.8;
const OVERLAYS = ["off", "skeleton", "names", "weights"];
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

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

// ---------------------------------------------------------------------------------------------------------------
// The stage: floor, ruler, reference figure (rebuilt to the model's size)
// ---------------------------------------------------------------------------------------------------------------

let stage = null, stageLabels = [];
const figure = makeFigure();
scene.add(figure);

function label(text, cls) {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = text;
  return new CSS2DObject(d);
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

  // the figure stands to the model's left (behind it, as it faces +X), clear of it
  figure.position.set(box.min.x - 0.45 - figure.userData.width / 2, 0, 0);

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
let overlay = 0, view = "side", framing = "all", sel = -1;
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
          fileSize, fps: info.fps || 24, weightMats: [], origMats: new Map() };
  for (const m of meshes) cur.origMats.set(m, m.material);

  buildStage(box);
  sel = pickDefaultBone();
  ci = Math.min(ci, Math.max(0, clips.length - 1));
  if (!clips.length) ci = 0;
  playClip(ci);
  applyOverlay();
  frame();
  renderChecks(checks());
  hud();
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
  hud();
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
  action.time = Math.min(dur, Math.max(0, action.time + d / cur.fps));
  cur.mixer.update(0);
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
// Overlays: off, skeleton, bone names, weights
// ---------------------------------------------------------------------------------------------------------------

function applyOverlay() {
  const mode = OVERLAYS[overlay];
  $("bOverlay").textContent = "Overlay: " + mode;
  $("bOverlay").classList.toggle("on", overlay > 0);
  if (!cur) return;
  cur.overlay.visible = mode !== "off";
  for (const b of cur.bones) b.label.visible = mode === "names";
  paintWeights(mode === "weights");
  highlight();
  $("boneinfo").style.display = mode === "weights" && sel >= 0 ? "block" : "none";
  hud();
}

function highlight() {
  if (!cur) return;
  const mode = OVERLAYS[overlay];
  cur.bones.forEach((b, i) => {
    const on = i === sel && (mode === "weights" || mode === "names" || mode === "skeleton");
    b.mesh.material.color.copy(on ? new THREE.Color(1, 1, 1) : b.col);
    b.mesh.material.opacity = on ? 0.95 : (mode === "weights" ? 0.35 : 0.55);
    b.label.element.classList.toggle("sel", on);
  });
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
  applyOverlay();
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

function frame() {
  if (!cur) return;
  const box = cur.box.clone();
  const h = box.max.y - box.min.y;
  box.max.y += h * 0.3;                       // headroom: clips rear up, leap and stretch past the bind pose
  if (framing === "all") box.union(new THREE.Box3().setFromObject(figure));
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
  const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const dist = Math.max(s.y / 2 / t, s.x / 2 / (t * camera.aspect)) * 1.5 + s.z / 2;
  c.y -= s.y * 0.14;                          // a little floor below: the ruler clears the controls
  camera.near = Math.max(0.001, dist / 200); camera.far = dist * 50; camera.updateProjectionMatrix();
  controls.target.copy(c);
  if (view === "side") camera.position.set(c.x, c.y + dist * 0.16, c.z + dist);
  else camera.position.set(c.x + dist * 0.62, c.y + dist * 0.35, c.z + dist * 0.72);
  controls.update();
  $("bFrame").textContent = framing === "all" ? "Frame: model + figure" : "Frame: model";
}

function setView(v) {
  view = v;
  controls.enableRotate = v === "orbit";
  $("bView").textContent = v === "side" ? "Side view" : "Free orbit";
  $("bView").classList.toggle("on", v === "orbit");
  frame(); hud();
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
  $("modeLine").textContent = cur ? `view: ${view === "side" ? "side (+X forward)" : "free orbit"} · overlay: ${OVERLAYS[overlay]}` : "";
  hudTime(); hudPlay();
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
  overlay: () => { overlay = (overlay + 1) % OVERLAYS.length; applyOverlay(); },
  view: () => setView(view === "side" ? "orbit" : "side"),
  frame: () => { framing = framing === "all" ? "model" : "all"; frame(); },
  prevBone: () => selectBone(sel - 1), nextBone: () => selectBone(sel + 1),
};

window.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" && e.target.type !== "checkbox" && e.target.type !== "range")) return;
  const k = e.key;
  const map = {
    ArrowLeft: act.prevModel, ArrowRight: act.nextModel, ArrowUp: act.prevClip, ArrowDown: act.nextClip,
    " ": act.play, r: act.overlay, R: act.overlay, v: act.view, V: act.view, f: act.frame, F: act.frame,
    "[": act.prevBone, "]": act.nextBone, l: () => setLoop(!loop), L: () => setLoop(!loop),
    "-": () => setSpeed(speed - 0.1), "=": () => setSpeed(speed + 0.1), "+": () => setSpeed(speed + 0.1),
    ",": () => stepFrame(-1), ".": () => stepFrame(1),
  };
  if (map[k]) { e.preventDefault(); if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); map[k](); }
});

const click = (id, fn) => $(id).addEventListener("click", (e) => { fn(); e.currentTarget.blur(); });
click("bPrevModel", act.prevModel); click("bNextModel", act.nextModel);
click("bPrevClip", act.prevClip); click("bNextClip", act.nextClip);
click("bPlay", act.play); click("bOverlay", act.overlay); click("bView", act.view); click("bFrame", act.frame);
$("speed").addEventListener("input", (e) => setSpeed(Number(e.target.value)));
$("speed").addEventListener("change", (e) => e.target.blur());
$("loop").addEventListener("change", (e) => { setLoop(e.target.checked); e.target.blur(); });

// Standard gamepad mapping: 0 A, 2 X, 3 Y, 4 LB, 5 RB, 12-15 the D-pad.
const PAD = { 0: act.play, 2: act.view, 3: act.overlay, 4: act.prevBone, 5: act.nextBone,
              12: act.prevClip, 13: act.nextClip, 14: act.prevModel, 15: act.nextModel };
const padPrev = new Map();
function pollPads() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const p of pads) {
    if (!p) continue;
    const prev = padPrev.get(p.index) || [];
    const now = p.buttons.map((b) => b.pressed);
    for (const [i, fn] of Object.entries(PAD)) if (now[i] && !prev[i]) fn();
    padPrev.set(p.index, now);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// The loop
// ---------------------------------------------------------------------------------------------------------------

let last = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  pollPads();
  if (cur && action && playing) cur.mixer.update(dt);
  if (cur) { cur.holder.updateMatrixWorld(true); updateOverlay(); hudTime(); }
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  requestAnimationFrame(tick);
}

// A link can open the viewer on a given state: ?model=&clip=<name|index>&overlay=<off|skeleton|names|weights>
// &bone=<name>&view=<side|orbit>&framing=<all|model>&time=<seconds, paused there>&speed=&loop=0
function applyParams(q) {
  if (q.get("speed")) setSpeed(Number(q.get("speed")));
  if (q.get("loop") === "0") setLoop(false);
  if (q.get("view") === "orbit") setView("orbit");
  if (q.get("framing") === "model") { framing = "model"; frame(); }
  if (!cur) return;
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
    action.time = Math.min(action.getClip().duration, Math.max(0, Number(q.get("time"))));
    cur.mixer.update(0);
  }
  hud();
}

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
  requestAnimationFrame(tick);
})();

// for inspection from a console: the loaded model and the controls
window.autorigViewer = { get model() { return cur; }, camera, act, selectBone, get overlay() { return OVERLAYS[overlay]; } };

// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the results viewer's plain logic, with no three.js and no page, so tests can run it under
// Node (tests/viewer_logic_test.mjs): gamepad mapping, the scrub bar's ticks, the audit's bone relations and its
// bleed rule, and mesh islands.

// ---------------------------------------------------------------------------------------------------------------
// Gamepad
// ---------------------------------------------------------------------------------------------------------------

// Buttons by the Gamepad API's "standard" mapping (Xbox layout; a PlayStation pad reports the same indices:
// cross 0, circle 1, square 2, triangle 3).
export const PAD_BUTTONS = {
  0: "play",        // A / cross
  1: "frame",       // B / circle
  2: "view",        // X / square
  3: "overlay",     // Y / triangle
  4: "prevBone",    // LB / L1
  5: "nextBone",    // RB / R1
  6: "stepBack",    // LT / L2 (analogue: past half way)
  7: "stepFwd",     // RT / R2
  8: "loop",        // Back, View, Share
  9: "play",        // Start, Menu, Options
  12: "prevClip", 13: "nextClip", 14: "prevModel", 15: "nextModel",   // the D-pad
};
export const STICK_DEADZONE = 0.25;   // radial, on the left stick: below it the stick is at rest
export const STICK_PUSH = 0.6;        // how far past the deadzone (rescaled 0..1) counts as a push
export const REPEAT_DELAY = 0.45;     // seconds held before a push or D-pad repeats
export const REPEAT_EVERY = 0.2;
const REPEATS = new Set(["prevClip", "nextClip", "prevModel", "nextModel", "stepBack", "stepFwd", "prevBone", "nextBone"]);

/** A stick's (x, y) with a radial deadzone, rescaled so the edge of the deadzone is 0 and full tilt is 1. */
export function deadzone(x, y, dz = STICK_DEADZONE) {
  const m = Math.hypot(x || 0, y || 0);
  if (!(m > dz)) return [0, 0];
  const k = Math.min(1, (m - dz) / (1 - dz)) / m;
  return [x * k, y * k];
}

/** Which logical controls a pad holds now: a Set of action names. Standard-mapping pads use PAD_BUTTONS; any
 * other pad keeps the face and shoulder buttons (0-9 are the same on nearly every pad) and reads its D-pad from
 * buttons 12-15 when it has them, else from a hat on axes 6/7 or a single hat axis 9 (common off the standard
 * mapping). The left stick moves through models (x) and clips (y), as the D-pad does. */
export function padHeld(pad) {
  const held = new Set();
  const btn = (i) => {
    const b = pad.buttons && pad.buttons[i];
    if (b === undefined || b === null) return false;
    if (typeof b === "number") return b > 0.5;
    return !!b.pressed || (typeof b.value === "number" && b.value > 0.5);
  };
  const standard = pad.mapping === "standard";
  const nb = pad.buttons ? pad.buttons.length : 0;
  for (const [i, name] of Object.entries(PAD_BUTTONS)) {
    const k = Number(i);
    if (k >= 12 && !standard && nb <= 12) continue;       // no D-pad buttons on this pad: read the hat below
    if (btn(k)) held.add(name);
  }
  const ax = pad.axes || [];
  if (!standard && nb <= 12) {
    // a hat as one axis reads odd sevenths: -1 up, then clockwise to 1 up-left, and 9/7 at rest
    const v = ax[9], hat = ax.length > 9 && [-7, -5, -3, -1, 1, 3, 5, 7, 9].some((k) => Math.abs(v - k / 7) < 0.05);
    if (hat) {
      const near = (t) => Math.abs(v - t) < 0.08;
      if (near(-1) || near(-5 / 7) || near(1)) held.add("prevClip");      // up, up-right, up-left
      if (near(-3 / 7) || near(-5 / 7) || near(-1 / 7)) held.add("nextModel");
      if (near(1 / 7) || near(-1 / 7) || near(3 / 7)) held.add("nextClip");
      if (near(5 / 7) || near(3 / 7) || near(1)) held.add("prevModel");
    } else if (ax.length > 7) {                           // a hat as two axes
      if (ax[6] < -0.5) held.add("prevModel"); if (ax[6] > 0.5) held.add("nextModel");
      if (ax[7] < -0.5) held.add("prevClip"); if (ax[7] > 0.5) held.add("nextClip");
    }
  }
  const [x, y] = deadzone(ax[0], ax[1]);
  if (Math.abs(x) >= Math.abs(y)) {
    if (x <= -STICK_PUSH) held.add("prevModel"); if (x >= STICK_PUSH) held.add("nextModel");
  } else {
    if (y <= -STICK_PUSH) held.add("prevClip"); if (y >= STICK_PUSH) held.add("nextClip");   // up is -1
  }
  return held;
}

/** One poll of one pad: the actions to fire now (a press, or a repeat while held), and the state to keep. */
export function padStep(pad, state, now) {
  state = state || { held: new Map() };
  const heldNow = padHeld(pad);
  const fire = [];
  const next = new Map();
  for (const name of heldNow) {
    const since = state.held.get(name);
    if (since === undefined) { fire.push(name); next.set(name, { t0: now, last: now }); continue; }
    if (REPEATS.has(name) && now - since.t0 >= REPEAT_DELAY && now - since.last >= REPEAT_EVERY) {
      fire.push(name); next.set(name, { t0: since.t0, last: now });
    } else next.set(name, since);
  }
  return { fire, state: { held: next } };
}

// ---------------------------------------------------------------------------------------------------------------
// The scrub bar
// ---------------------------------------------------------------------------------------------------------------

/** Tick spacing for a clip of `frames` frames drawn `width` px wide: a small tick every `minor` frames and a
 * numbered one every `major`, never closer than a few pixels. */
export function scrubTicks(frames, width) {
  const last = Math.max(1, Math.round(frames));
  const px = width / last;
  const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  const minor = steps.find((s) => s * px >= 5) || steps[steps.length - 1];
  const major = steps.find((s) => s >= minor * 2 && s * px >= 42) || steps[steps.length - 1];
  return { last, minor, major, px };
}

/** The frame nearest a pointer at `x` px on a bar `width` px wide, as a time in seconds. */
export function scrubTime(x, width, duration, fps) {
  const f = Math.max(0, Math.min(1, x / Math.max(1, width))) * duration;
  const snapped = Math.round(f * fps) / fps;
  return Math.max(0, Math.min(duration, snapped));
}

/** Every distinct key time over a clip's tracks (arrays of seconds), sorted, merged within `eps`. */
export function keyTimes(trackTimes, eps = 1e-4) {
  const all = [];
  for (const t of trackTimes) for (const x of t) all.push(x);
  all.sort((a, b) => a - b);
  const out = [];
  for (const x of all) if (!out.length || x - out[out.length - 1] > eps) out.push(x);
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Bones: the audit's relations (steps/audit.py side_of, base_of, related, ancestor), for the bleed view
// ---------------------------------------------------------------------------------------------------------------

const SIDE_RE = /(\.L|\.R|_L|_R|\.l|\.r)$/;
export function sideOf(n) {
  if (n.includes("Left")) return "L";
  if (n.includes("Right")) return "R";
  const m = SIDE_RE.exec(n);
  return m ? m[1].slice(-1).toUpperCase() : "";
}
export function baseOf(n) {
  n = n.split(":").pop().replace(SIDE_RE, "").replaceAll("Left", "").replaceAll("Right", "");
  return n.replace(/_?\d+$/, "");
}
/** a is upstream of b: b hangs, however far down, from a. `parent` is an array of parent indices (-1 at a root). */
export function ancestor(parent, a, b) {
  while (b >= 0) { b = parent[b]; if (b === a) return true; }
  return false;
}
export function related(parent, names, a, b) {
  return a === b || parent[a] === b || parent[b] === a ||
    (parent[a] === parent[b] && parent[a] >= 0 && baseOf(names[a]) === baseOf(names[b]) && sideOf(names[a]) === sideOf(names[b]));
}
/** Steps between two bones through the hierarchy (a common ancestor), or Infinity in different trees. */
export function hops(parent, a, b) {
  const up = new Map();
  for (let x = a, d = 0; x >= 0; x = parent[x], d++) up.set(x, d);
  for (let y = b, d = 0; y >= 0; y = parent[y], d++) if (up.has(y)) return up.get(y) + d;
  return Infinity;
}

/** The audit's bleed rule for one vertex: owned by `dom`, nearest to `near` (bone indices), at distances dDom and
 * dNear from those bones' segments, on a model `size` across. Bleed is ownership by a bone that is neither the
 * nearest, nor next to it, nor upstream of it, and clearly further away. */
export function isBleed(parent, names, dom, near, dDom, dNear, size) {
  if (dom < 0 || near < 0) return false;
  if (related(parent, names, dom, near)) return false;
  if (!(dDom > 1.5 * dNear + 0.01 * size)) return false;
  return !ancestor(parent, dom, near);
}

/** Squared distance from p to the segment a-b (each [x, y, z]). */
export function segDist2(px, py, pz, a, b) {
  const abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
  const L2 = Math.max(1e-12, abx * abx + aby * aby + abz * abz);
  let t = ((px - a[0]) * abx + (py - a[1]) * aby + (pz - a[2]) * abz) / L2;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const dx = px - (a[0] + t * abx), dy = py - (a[1] + t * aby), dz = pz - (a[2] + t * abz);
  return dx * dx + dy * dy + dz * dz;
}

// ---------------------------------------------------------------------------------------------------------------
// Mesh islands: loose parts, welded across UV and normal seams (a GLB splits vertices there)
// ---------------------------------------------------------------------------------------------------------------

/** Island label per vertex, 0.. in order of first vertex. positions: flat xyz array; index: triangle indices (or
 * null for a non-indexed mesh); tol: the weld distance. Returns {labels: Int32Array, count, sizes: welded vertices
 * per island}. */
export function islands(positions, index, tol) {
  const n = positions.length / 3;
  const par = new Int32Array(n);
  for (let i = 0; i < n; i++) par[i] = i;
  const find = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  const join = (a, b) => { a = find(a); b = find(b); if (a !== b) par[a] = b; };
  const q = 1 / Math.max(tol, 1e-9);
  const seen = new Map();
  for (let i = 0; i < n; i++) {
    const k = Math.round(positions[3 * i] * q) + "," + Math.round(positions[3 * i + 1] * q) + "," + Math.round(positions[3 * i + 2] * q);
    const j = seen.get(k);
    if (j === undefined) seen.set(k, i); else join(i, j);
  }
  if (index) for (let t = 0; t + 2 < index.length; t += 3) { join(index[t], index[t + 1]); join(index[t + 1], index[t + 2]); }
  else for (let t = 0; t + 2 < n; t += 3) { join(t, t + 1); join(t + 1, t + 2); }
  const labels = new Int32Array(n), ids = new Map();
  for (let i = 0; i < n; i++) {
    const r = find(i);
    let id = ids.get(r);
    if (id === undefined) { id = ids.size; ids.set(r, id); }
    labels[i] = id;
  }
  const sizes = new Int32Array(ids.size);            // welded vertices per island: one per position
  for (const i of seen.values()) sizes[labels[i]]++;
  return { labels, count: ids.size, sizes };
}

// ---------------------------------------------------------------------------------------------------------------
// Tears overlay: gap thresholds (CHECK vs FAIL), marker sizing and coordinate mapping
// ---------------------------------------------------------------------------------------------------------------

/** The CHECK gap threshold in % of the model size: under it is CHECK (amber), over it is FAIL (red).
 * Calibrated in autorig/core/grades.py GAP_CHECK_PCT (8% for combined pose, 5% for single-joint bend/twist). */
export const TEAR_CHECK_GAP_PCT = { combined: 8.0, bend: 5.0, twist: 5.0 };

/** Severity level for a tear gap: "bad" (red) if over the pose's CHECK gap, else "warn" (amber). */
export function tearSeverity(gapPct, pose = "bend") {
  const limit = TEAR_CHECK_GAP_PCT[pose] ?? 5.0;
  return gapPct > limit ? "bad" : "warn";
}

/** Marker radius scaled by edge count so clusters with more torn edges stand out. */
export function tearMarkerRadius(edges, baseRadius) {
  return baseRadius * Math.min(2.5, 0.7 + Math.log10(1 + Math.max(1, edges)) * 0.6);
}

/** Maps Blender world coordinates [x, y, z] (+Z up, -Y forward) to glTF space [x, z, -y] (Y up, +Z forward).
 * If `at` is missing, falls back to `at_bbox` [fx, fy, fz] relative to the bounding box if provided. */
export function mapTearPoint(at, atBbox, box) {
  if (Array.isArray(at) && at.length === 3 && at.every((x) => typeof x === "number" && !isNaN(x))) {
    return [at[0], at[2], -at[1]];
  }
  if (Array.isArray(atBbox) && atBbox.length === 3 && box) {
    const szX = box.max.x - box.min.x, szY = box.max.y - box.min.y, szZ = box.max.z - box.min.z;
    return [box.min.x + atBbox[0] * szX, box.min.y + atBbox[2] * szY, -(box.min.z + atBbox[1] * szZ)];
  }
  return [0, 0, 0];
}

/** Default joint bend angles for the combined pose (in degrees about bone local X). */
export const DEFAULT_COMBINED_ANGLES = { spine: 12, neck: 20, head: 15, tail: 14, jaw: 25, finger: 0, root: 0 };
export function defaultCombinedAngle(role) {
  return DEFAULT_COMBINED_ANGLES[role] ?? (role === "unnamed" ? 20 : 30);
}

// ---------------------------------------------------------------------------------------------------------------
// Build chains: intelligent mirroring and stations generation
// ---------------------------------------------------------------------------------------------------------------

/** Mirrors a chain or bone name across symmetry (.R <-> .L, _right <-> _left, _r <-> _l, or fallback). */
export function mirrorName(s, isLeftSide = false) {
  if (!s || typeof s !== "string") return s;
  if (/\.R\b/.test(s)) return s.replace(/\.R\b/, ".L");
  if (/\.L\b/.test(s)) return s.replace(/\.L\b/, ".R");
  if (/\.r\b/.test(s)) return s.replace(/\.r\b/, ".l");
  if (/\.l\b/.test(s)) return s.replace(/\.l\b/, ".r");
  if (/_right\b/i.test(s)) {
    return s.replace(/_right\b/i, (m) => m === "_Right" ? "_Left" : m === "_RIGHT" ? "_LEFT" : "_left");
  }
  if (/_left\b/i.test(s)) {
    return s.replace(/_left\b/i, (m) => m === "_Left" ? "_Right" : m === "_LEFT" ? "_RIGHT" : "_right");
  }
  if (/_R\b/.test(s)) return s.replace(/_R\b/, "_L");
  if (/_L\b/.test(s)) return s.replace(/_L\b/, "_R");
  if (/_r\b/.test(s)) return s.replace(/_r\b/, "_l");
  if (/_l\b/.test(s)) return s.replace(/_l\b/, "_r");
  if (/\bright\b/i.test(s)) {
    return s.replace(/\bright\b/i, (m) => m[0] === "R" ? "Left" : "left");
  }
  if (/\bleft\b/i.test(s)) {
    return s.replace(/\bleft\b/i, (m) => m[0] === "L" ? "Right" : "right");
  }
  if (s.endsWith("_m")) return s.slice(0, -2);
  return isLeftSide ? s + ".R" : s + ".L";
}

/** Checks if a name contains an explicit side indicator (.R, .L, _right, _left, etc.) */
export function hasSideIndicator(s) {
  if (!s || typeof s !== "string") return false;
  return /\.(R|L|r|l)\b/.test(s) || /_(R|L|r|l)\b/.test(s) || /_(right|left)\b/i.test(s) || /\b(right|left)\b/i.test(s) || s.endsWith("_m");
}

/** Mirrors a chain object across the symmetry plane (X -> 1 - X), updating names and parent references. */
export function mirrorChainData(orig, existingChains = []) {
  if (!orig || typeof orig !== "object") return orig;
  const copy = JSON.parse(JSON.stringify(orig));
  const mx = (p) => Array.isArray(p) && p.length === 3 ? [Math.round((1 - p[0]) * 1000) / 1000, p[1], p[2]] : p;

  const allPts = [];
  if (copy.tip) allPts.push(copy.tip);
  if (copy.base) allPts.push(copy.base);
  if (Array.isArray(copy.tube)) allPts.push(...copy.tube);
  if (Array.isArray(copy.points)) allPts.push(...copy.points);
  const avgX = allPts.length ? allPts.reduce((acc, p) => acc + (Array.isArray(p) ? p[0] : 0.5), 0) / allPts.length : 0.5;
  const isLeftSide = avgX > 0.5;

  if (copy.name) copy.name = mirrorName(copy.name, isLeftSide);
  if (copy.role && copy.role !== "spine") {
    if (hasSideIndicator(copy.role)) copy.role = mirrorName(copy.role, isLeftSide);
  }

  if (copy.tip) copy.tip = mx(copy.tip);
  if (copy.base) copy.base = mx(copy.base);
  if (copy.first) copy.first = mx(copy.first);
  if (Array.isArray(copy.tube)) copy.tube = copy.tube.map(mx);
  if (Array.isArray(copy.points)) copy.points = copy.points.map(mx);

  if (Array.isArray(copy.parent) && copy.parent[0]) {
    const parentMirrored = mirrorName(copy.parent[0], isLeftSide);
    if (existingChains.some((c) => c && c.name === parentMirrored)) {
      copy.parent[0] = parentMirrored;
    }
  }
  return copy;
}

/** Generates N+1 evenly spaced stations along Y between slice[0] and slice[1]. */
export function generateStations(slice = [0.1, 0.9], bones = 4) {
  const n = Math.max(1, Number(bones) || 1);
  const s0 = (Array.isArray(slice) && typeof slice[0] === "number") ? slice[0] : 0.1;
  const s1 = (Array.isArray(slice) && typeof slice[1] === "number") ? slice[1] : 0.9;
  return Array.from({ length: n + 1 }, (_, k) => Math.round((s0 + (s1 - s0) * k / n) * 1000) / 1000);
}

// ---------------------------------------------------------------------------------------------------------------
// Interactive Joint Bend & Pose Preview Math
// ---------------------------------------------------------------------------------------------------------------

/** Rotates a 3D point around a pivot point along an arbitrary normalized axis using Rodrigues' formula. */
export function rodriguesRotate(p, c, axis, angle) {
  const vx = p[0] - c[0], vy = p[1] - c[1], vz = p[2] - c[2];
  const cosA = Math.cos(angle), sinA = Math.sin(angle);
  const dot = vx * axis[0] + vy * axis[1] + vz * axis[2];
  const crossX = axis[1] * vz - axis[2] * vy;
  const crossY = axis[2] * vx - axis[0] * vz;
  const crossZ = axis[0] * vy - axis[1] * vx;
  const rx = vx * cosA + crossX * sinA + axis[0] * dot * (1 - cosA);
  const ry = vy * cosA + crossY * sinA + axis[1] * dot * (1 - cosA);
  const rz = vz * cosA + crossZ * sinA + axis[2] * dot * (1 - cosA);
  return [c[0] + rx, c[1] + ry, c[2] + rz];
}

/** Computes the natural orthogonal hinge rotation axis for a 3-point limb chain (p0 -> p1 -> p2). */
export function computeHingeAxis(p0, p1, p2, role = "") {
  const ux = p1[0] - p0[0], uy = p1[1] - p0[1], uz = p1[2] - p0[2];
  const vx = p2[0] - p1[0], vy = p2[1] - p1[1], vz = p2[2] - p1[2];
  let cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
  let len = Math.sqrt(cx * cx + cy * cy + cz * cz);
  if (len > 1e-5) return [cx / len, cy / len, cz / len];

  // Colinear fallback: find axis perpendicular to bone direction
  const d = Math.sqrt(vx * vx + vy * vy + vz * vz) || 1;
  const dx = vx / d, dy = vy / d, dz = vz / d;
  const ref = Math.abs(dz) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  cx = dy * ref[2] - dz * ref[1];
  cy = dz * ref[0] - dx * ref[2];
  cz = dx * ref[1] - dy * ref[0];
  len = Math.sqrt(cx * cx + cy * cy + cz * cz) || 1;
  return [cx / len, cy / len, cz / len];
}

/** Deforms a vertex buffer by bending a limb at pivot p1 towards p2 by angleRad.
 * positions: Float32Array to update in-place.
 * originalPositions: Float32Array of rest-pose vertex positions.
 * p0, p1, p2: [x,y,z] coordinates in mesh space.
 * angleRad: bend angle in radians. */
export function bendVertices(positions, originalPositions, p0, p1, p2, angleRad, maxRadius, blendRadius) {
  const n = positions.length / 3;
  if (!angleRad || Math.abs(angleRad) < 1e-6) {
    positions.set(originalPositions);
    return positions;
  }
  const axis = computeHingeAxis(p0, p1, p2);
  const cx = p1[0], cy = p1[1], cz = p1[2];
  const vx = p2[0] - p1[0], vy = p2[1] - p1[1], vz = p2[2] - p1[2];
  const boneLen = Math.sqrt(vx * vx + vy * vy + vz * vz) || 1;
  const dx = vx / boneLen, dy = vy / boneLen, dz = vz / boneLen;

  const bRad = blendRadius ?? Math.max(0.02, boneLen * 0.25);
  const mRad = maxRadius ?? Math.max(0.08, boneLen * 0.8);

  for (let i = 0; i < n; i++) {
    const idx = i * 3;
    const px = originalPositions[idx], py = originalPositions[idx + 1], pz = originalPositions[idx + 2];
    const wx = px - cx, wy = py - cy, wz = pz - cz;
    const s = wx * dx + wy * dy + wz * dz;
    const perpX = wx - dx * s, perpY = wy - dy * s, perpZ = wz - dz * s;
    const rPerp = Math.sqrt(perpX * perpX + perpY * perpY + perpZ * perpZ);

    if (rPerp > mRad || s < -bRad) {
      positions[idx] = px; positions[idx + 1] = py; positions[idx + 2] = pz;
      continue;
    }

    let w = 0;
    if (s >= bRad) {
      w = 1.0;
    } else {
      const alpha = (s + bRad) / (2.0 * bRad);
      w = alpha * alpha * (3.0 - 2.0 * alpha);
    }
    if (rPerp > mRad * 0.65) {
      w *= (1.0 - (rPerp - mRad * 0.65) / (mRad * 0.35));
    }

    if (w < 1e-4) {
      positions[idx] = px; positions[idx + 1] = py; positions[idx + 2] = pz;
      continue;
    }

    const rotated = rodriguesRotate([px, py, pz], p1, axis, w * angleRad);
    positions[idx] = rotated[0];
    positions[idx + 1] = rotated[1];
    positions[idx + 2] = rotated[2];
  }
  return positions;
}

// ---------------------------------------------------------------------------------------------------------------
// Camera System: Front, 3/4 Hero, Side, and Free Orbit
// ---------------------------------------------------------------------------------------------------------------

export const VIEWS = ["hero", "front", "side", "orbit"];

/** Human-readable label for each camera view mode. */
export function viewLabel(v) {
  if (v === "front") return "Front view";
  if (v === "hero") return "3/4 Hero view";
  if (v === "side") return "Side view";
  if (v === "orbit") return "Free orbit";
  return v || "Hero view";
}

/** Returns the next view in cyclical order: Hero -> Front -> Side -> Orbit -> Hero. */
export function nextView(curView) {
  const idx = VIEWS.indexOf(curView);
  return idx >= 0 ? VIEWS[(idx + 1) % VIEWS.length] : VIEWS[0];
}

/** Determines if a model is humanoid or bipedal based on archetype, skeleton metadata, bone names, and proportions. */
export function isHumanoidOrBiped(info, bones, box) {
  if (!info && !bones && !box) return false;
  const arch = String((info && (info.archetype || info.skeleton)) || "").toLowerCase();
  if (arch === "humanoid" || arch === "biped" || arch === "walker") return true;

  if (Array.isArray(bones) && bones.length > 0) {
    const boneNames = bones.map((b) => (typeof b === "string" ? b : (b && b.name) || "").toLowerCase());
    const hasHips = boneNames.some((n) => n.includes("hip") || n.includes("pelvis"));
    const hasSpine = boneNames.some((n) => n.includes("spine"));
    const hasArm = boneNames.some((n) => n.includes("arm") || n.includes("shoulder") || n.includes("hand"));
    const hasLeg = boneNames.some((n) => n.includes("leg") || n.includes("thigh") || n.includes("foot"));
    const hasHead = boneNames.some((n) => n.includes("head"));
    if (hasHips && hasSpine && (hasArm || hasHead) && hasLeg) return true;
  }

  if (box && typeof box.getSize === "function") {
    const s = box.getSize({ x: 0, y: 0, z: 0 });
    if (s.y > 0.8 && s.y > 1.35 * Math.max(s.x, s.z)) {
      return true;
    }
  } else if (box && typeof box.x === "number" && typeof box.y === "number" && typeof box.z === "number") {
    if (box.y > 0.8 && box.y > 1.35 * Math.max(box.x, box.z)) {
      return true;
    }
  }
  return false;
}

/** Returns the recommended default camera view: "hero" (3/4 perspective) for humanoids/bipeds, "side" for quadrupeds/creatures. */
export function defaultCameraView(info, bones, box) {
  return isHumanoidOrBiped(info, bones, box) ? "hero" : "side";
}

// ---------------------------------------------------------------------------------------------------------------
// Orientation & Axis Locking: Pitch, Yaw, Roll, Leveling, and Ground Floor Calculations
// ---------------------------------------------------------------------------------------------------------------

/** Converts spherical polar and azimuthal angles (in radians) to human-readable degrees.
 * Pitch is 0° at horizontal ground level, +90° from above, -90° from below.
 * Yaw is 0°..360° around the vertical Y axis. Roll is camera bank angle (0°).
 */
export function computeOrientation(polarAngle, azimuthalAngle) {
  const pitchDeg = Math.round((90 - (polarAngle * 180 / Math.PI)) * 10) / 10;
  let yawDeg = Math.round((((azimuthalAngle * 180 / Math.PI) % 360 + 360) % 360) * 10) / 10;
  if (yawDeg === 360) yawDeg = 0;
  return { pitchDeg, yawDeg, rollDeg: 0.0 };
}

/** Computes the clamped OrbitControls angle bounds when locking axes of rotation.
 * Locking X freezes polar angle (pitch). Locking Y freezes azimuthal angle (yaw).
 */
export function clampAnglesToLocked(currentPolar, currentAzimuth, lockX, lockY) {
  return {
    minPolar: lockX ? currentPolar : 0.001,
    maxPolar: lockX ? currentPolar : Math.PI - 0.001,
    minAzimuth: lockY ? currentAzimuth : -Infinity,
    maxAzimuth: lockY ? currentAzimuth : Infinity,
  };
}

/** Calculates new camera position coordinates to level the view parallel to the ground (pitch = 0°)
 * while preserving distance to target, or setting an exact pitch and yaw.
 */
export function calculateLeveledCameraPosition(cameraPos, targetPos, pitchDeg = 0, yawDeg = null) {
  const cx = cameraPos?.x ?? cameraPos?.[0] ?? 0;
  const cy = cameraPos?.y ?? cameraPos?.[1] ?? 0;
  const cz = cameraPos?.z ?? cameraPos?.[2] ?? 2;
  const tx = targetPos?.x ?? targetPos?.[0] ?? 0;
  const ty = targetPos?.y ?? targetPos?.[1] ?? 0;
  const tz = targetPos?.z ?? targetPos?.[2] ?? 0;

  const dx = cx - tx, dy = cy - ty, dz = cz - tz;
  const radius = Math.max(1e-4, Math.hypot(dx, dy, dz));

  const phi = Math.max(0.001, Math.min(Math.PI - 0.001, (90 - pitchDeg) * Math.PI / 180));
  const theta = (yawDeg !== null && yawDeg !== undefined)
    ? (yawDeg * Math.PI / 180)
    : Math.atan2(dx, dz);

  const sinPhiR = radius * Math.sin(phi);
  return {
    x: Math.round((tx + sinPhiR * Math.sin(theta)) * 10000) / 10000,
    y: Math.round((ty + radius * Math.cos(phi)) * 10000) / 10000,
    z: Math.round((tz + sinPhiR * Math.cos(theta)) * 10000) / 10000,
  };
}

/** Computes ground plane dimensions and position from a model's bounding box so feet sit flush on ground. */
export function computeGroundPlaneParameters(box) {
  if (!box) {
    return { groundY: 0, center: [0, 0, 0], extent: 4, gridDim: 8, divisions: 16 };
  }
  const minX = box.min?.x ?? box.min?.[0] ?? -0.5;
  const minY = box.min?.y ?? box.min?.[1] ?? 0;
  const minZ = box.min?.z ?? box.min?.[2] ?? -0.5;
  const maxX = box.max?.x ?? box.max?.[0] ?? 0.5;
  const maxY = box.max?.y ?? box.max?.[1] ?? 1.0;
  const maxZ = box.max?.z ?? box.max?.[2] ?? 0.5;

  const szX = maxX - minX, szY = maxY - minY, szZ = maxZ - minZ;
  const extent = Math.max(szX, szZ, szY, 1.0);
  const gridDim = Math.max(4, Math.ceil(extent * 2.5));
  const divisions = Math.max(10, gridDim * 2);

  return {
    groundY: minY,
    center: [
      Math.round((minX + szX / 2) * 10000) / 10000,
      Math.round(minY * 10000) / 10000,
      Math.round((minZ + szZ / 2) * 10000) / 10000
    ],
    extent,
    gridDim,
    divisions,
  };
}

/**
 * Evaluates whether a model matches a status filter ("all", "pass", "check", "fail", "rigged", "unrigged", "nospec").
 */
export function matchesModelFilter(m, filter) {
  if (!filter || filter === "all") return true;
  const audit = (m?.audit || "").toUpperCase();
  if (filter === "pass") return audit === "PASS";
  if (filter === "check") return audit === "CHECK";
  if (filter === "fail") return audit === "FAIL";
  if (filter === "rigged") return Boolean(m?.rigged);
  if (filter === "unrigged") return !m?.rigged;
  if (filter === "nospec") return !m?.spec;
  return true;
}

/**
 * Calculates camera distance to center and fit a model bounding box without cutting it off.
 * If the model is wide or the viewport aspect ratio is narrow, zooms out automatically.
 */
export function calculateFramingDistance(size, fovDeg = 35, aspect = 1.0, padding = 1.25) {
  const sx = Math.max(1e-4, size?.x ?? size?.[0] ?? 1);
  const sy = Math.max(1e-4, size?.y ?? size?.[1] ?? 1);
  const sz = Math.max(1e-4, size?.z ?? size?.[2] ?? 1);
  const asp = Math.max(0.01, aspect);
  const pad = Math.max(1.0, padding);

  const vFovRad = (Math.max(1, Math.min(170, fovDeg)) * Math.PI) / 360;
  const hFovRad = Math.atan(Math.tan(vFovRad) * asp);

  const distV = (sy * 0.5 * pad) / Math.tan(vFovRad);
  const distH = (sx * 0.5 * pad) / Math.tan(hFovRad);
  const radius = 0.5 * Math.hypot(sx, sy, sz);
  const distSphere = (radius * pad) / Math.min(Math.sin(vFovRad), Math.sin(hFovRad));

  return Math.max(distV, distH, distSphere, 0.5);
}

/**
 * Verifies whether an asynchronous model load should be committed to the scene graph.
 * Returns true only if the load sequence number matches the active sequence and the target
 * model matches the currently selected model.
 */
export function isModelLoadValid(activeSeq, requestSeq, activeModel, requestModel) {
  return activeSeq === requestSeq && Boolean(activeModel) && activeModel === requestModel;
}

/**
 * Computes IK pole target position for a 2-bone or 3-bone limb chain.
 * Given root position [x,y,z], hinge/knee position [x,y,z], and foot/tip position [x,y,z],
 * computes the offset position along the bend normal or anatomical forward/backward direction.
 */
export function computePoleTargetPosition(root, hinge, tip, isFront = false, offsetDist = 0.3) {
  const rx = root[0], ry = root[1], rz = root[2];
  const hx = hinge[0], hy = hinge[1], hz = hinge[2];
  const tx = tip[0], ty = tip[1], tz = tip[2];

  const cx = tx - rx, cy = ty - ry, cz = tz - rz;
  const cLenSq = cx * cx + cy * cy + cz * cz;

  let px = 0, py = 0, pz = 0;
  if (cLenSq > 1e-8) {
    const dot = (hx - rx) * cx + (hy - ry) * cy + (hz - rz) * cz;
    const t = dot / cLenSq;
    const projX = rx + t * cx;
    const projY = ry + t * cy;
    const projZ = rz + t * cz;

    const bx = hx - projX;
    const by = hy - projY;
    const bz = hz - projZ;
    const bLen = Math.hypot(bx, by, bz);
    if (bLen > 1e-4) {
      px = bx / bLen;
      py = by / bLen;
      pz = bz / bLen;
    }
  }

  if (Math.hypot(px, py, pz) < 0.5) {
    px = 0;
    py = isFront ? 1.0 : -1.0;
    pz = 0;
  }

  return [
    Math.round((hx + px * offsetDist) * 10000) / 10000,
    Math.round((hy + py * offsetDist) * 10000) / 10000,
    Math.round((hz + pz * offsetDist) * 10000) / 10000
  ];
}

/**
 * Computes 4-beat lateral sequence gait phase offsets for quadrupeds:
 * LH (0.00) -> LF (0.25) -> RH (0.50) -> RF (0.75).
 */
export function computeQuadrupedGaitPhase(side, isFront) {
  const isLeft = side === "L" || side === 1 || side === 1.0;
  if (isLeft) {
    return isFront ? 0.25 : 0.00;
  } else {
    return isFront ? 0.75 : 0.50;
  }
}

export const GAIT_PRESET_DEFAULTS = {
  natural: { stride: 1.0, cadence: 1.0, bob: 1.0, sway: 1.0, yaw: 1.0, lean: 3.5, arm_swing: 1.0, duty_factor: 0.62 },
  soldier: { stride: 1.15, cadence: 1.10, bob: 1.25, sway: 0.40, yaw: 0.70, lean: 1.0, arm_swing: 1.60, duty_factor: 0.60 },
  swagger: { stride: 1.05, cadence: 0.90, bob: 1.10, sway: 2.20, yaw: 1.50, lean: 4.5, arm_swing: 1.35, duty_factor: 0.62 },
  stealth: { stride: 0.85, cadence: 0.75, bob: 0.20, sway: 0.60, yaw: 0.80, lean: 12.0, arm_swing: 0.30, duty_factor: 0.68 },
  heavy: { stride: 0.90, cadence: 0.85, bob: 1.40, sway: 1.60, yaw: 0.80, lean: 6.0, arm_swing: 0.70, duty_factor: 0.66 },
  run: { stride: 1.40, cadence: 1.50, bob: 1.60, sway: 0.50, yaw: 1.10, lean: 10.0, arm_swing: 1.50, duty_factor: 0.38 },
  sprint: { stride: 1.70, cadence: 1.80, bob: 1.90, sway: 0.35, yaw: 1.30, lean: 16.0, arm_swing: 1.80, duty_factor: 0.32 },
  quadruped_walk: { stride: 1.0, cadence: 1.0, bob: 0.80, sway: 1.0, yaw: 1.0, lean: 0.0, arm_swing: 0.0, duty_factor: 0.65 },
  quadruped_trot: { stride: 1.30, cadence: 1.50, bob: 1.60, sway: 0.50, yaw: 0.50, lean: 0.0, arm_swing: 0.0, duty_factor: 0.50 },
  quadruped_gallop: { stride: 1.60, cadence: 1.75, bob: 2.00, sway: 0.40, yaw: 0.40, lean: 0.0, arm_swing: 0.0, duty_factor: 0.28 }
};

export function mergeGaitParams(presetName = "natural", overrides = null) {
  const base = Object.assign({}, GAIT_PRESET_DEFAULTS[presetName] || GAIT_PRESET_DEFAULTS.natural);
  if (overrides && typeof overrides === "object") {
    for (const [k, v] of Object.entries(overrides)) {
      if (k in base && typeof v === "number" && !isNaN(v)) base[k] = v;
    }
  }
  base.stride = Math.max(0.2, Math.min(2.5, base.stride));
  base.cadence = Math.max(0.2, Math.min(3.0, base.cadence));
  base.bob = Math.max(0.0, Math.min(3.0, base.bob));
  base.sway = Math.max(0.0, Math.min(3.5, base.sway));
  base.yaw = Math.max(0.0, Math.min(3.0, base.yaw));
  base.lean = Math.max(-10.0, Math.min(35.0, base.lean));
  base.arm_swing = Math.max(0.0, Math.min(3.0, base.arm_swing));
  base.duty_factor = Math.max(0.25, Math.min(0.85, base.duty_factor));
  return base;
}

export function evaluatePelvisTrajectory(phase, params = null) {
  const p = mergeGaitParams("natural", params);
  const u = ((phase % 1.0) + 1.0) % 1.0;
  const w = 2.0 * Math.PI * u;
  const bob = p.bob * Math.cos(2.0 * w);
  const sway = p.sway * Math.sin(w);
  const yaw = p.yaw * Math.sin(w);
  const lean = p.lean + 0.8 * Math.cos(2.0 * w);
  return { bob, sway, yaw, lean };
}

export function quatMultiply(a, b) {
  return [
    a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
    a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
    a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]
  ];
}

export function quatInvert(q) {
  const d = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3];
  if (d < 1e-9) return [0, 0, 0, 1];
  return [-q[0] / d, -q[1] / d, -q[2] / d, q[3] / d];
}

export function scaleQuaternionRotation(q, scale, mean = [0, 0, 0, 1]) {
  const qRel = quatMultiply(quatInvert(mean), q);
  const w = Math.max(-1, Math.min(1, qRel[3]));
  const angle = 2 * Math.acos(w);
  if (Math.abs(angle) < 1e-6) return [q[0], q[1], q[2], q[3]];
  const s = Math.sqrt(Math.max(0, 1 - w * w));
  const axis = s < 1e-5 ? [1, 0, 0] : [qRel[0] / s, qRel[1] / s, qRel[2] / s];
  const half = (angle * scale) / 2;
  const sinH = Math.sin(half);
  const qScaled = [axis[0] * sinH, axis[1] * sinH, axis[2] * sinH, Math.cos(half)];
  return quatMultiply(mean, qScaled);
}

export function pitchQuaternion(q, pitchDeg) {
  const half = (pitchDeg * Math.PI) / 360;
  const qPitch = [Math.sin(half), 0, 0, Math.cos(half)];
  return quatMultiply(qPitch, q);
}

export function selectBaseClipForPreset(presetName = "natural", availableClipNames = []) {
  const names = (availableClipNames || []).map(n => String(n).toLowerCase());
  const find = (pat) => (availableClipNames || []).find((_, idx) => pat.test(names[idx]));

  if (presetName === "run" || presetName === "sprint") {
    const c = find(/\brun\b/) || find(/sprint/) || find(/\bwalk\b/);
    if (c) return c;
  } else if (presetName === "quadruped_gallop") {
    const c = find(/gallop/) || find(/trot/) || find(/\bwalk\b/);
    if (c) return c;
  } else if (presetName === "quadruped_trot") {
    const c = find(/trot/) || find(/\bwalk\b/);
    if (c) return c;
  } else {
    const c = find(/\bwalk\b/);
    if (c) return c;
  }
  return availableClipNames && availableClipNames[0] ? availableClipNames[0] : null;
}

export function modulateGaitTrackValues(trackName, times, values, params, spineCount = 3) {
  const out = new Float32Array(values.length);
  out.set(values);
  if (!params) return out;

  const isPos = trackName.endsWith(".position") || trackName.endsWith(".translation");
  const isRot = trackName.endsWith(".quaternion") || trackName.endsWith(".rotation");

  if (isPos && /hips|pelvis|root|body/i.test(trackName)) {
    const n = Math.floor(values.length / 3);
    if (n > 0) {
      let sumX = 0, sumY = 0;
      for (let i = 0; i < n; i++) {
        sumX += values[i * 3 + 0];
        sumY += values[i * 3 + 1];
      }
      const meanX = sumX / n;
      const meanY = sumY / n;
      const swayScale = params.sway ?? 1.0;
      const bobScale = params.bob ?? 1.0;
      for (let i = 0; i < n; i++) {
        const dx = values[i * 3 + 0] - meanX;
        const dy = values[i * 3 + 1] - meanY;
        out[i * 3 + 0] = meanX + dx * swayScale;
        out[i * 3 + 1] = meanY + dy * bobScale;
      }
    }
  } else if (isRot) {
    const isLeg = /leg|thigh|shin|foot|femur|tibia|knee|calf|up_leg/i.test(trackName);
    const isArm = /arm|forearm|hand|shoulder|clavicle|elbow|wrist|bicep/i.test(trackName);
    const isSpine = /hips|pelvis|spine|torso|chest/i.test(trackName);

    const scale = isLeg ? (params.stride ?? 1.0) : (isArm ? (params.arm_swing ?? 1.0) : 1.0);
    const n = Math.floor(values.length / 4);

    const deltaLean = (params.lean != null ? params.lean - 3.5 : 0.0);
    const leanPitchPerBone = isSpine && Math.abs(deltaLean) > 1e-4 ? deltaLean / Math.max(1, spineCount) : 0.0;

    if (Math.abs(scale - 1.0) > 1e-4 || Math.abs(leanPitchPerBone) > 1e-4) {
      for (let i = 0; i < n; i++) {
        let q = [values[i * 4 + 0], values[i * 4 + 1], values[i * 4 + 2], values[i * 4 + 3]];
        if (Math.abs(scale - 1.0) > 1e-4) {
          q = scaleQuaternionRotation(q, scale);
        }
        if (Math.abs(leanPitchPerBone) > 1e-4) {
          q = pitchQuaternion(q, leanPitchPerBone);
        }
        out[i * 4 + 0] = q[0];
        out[i * 4 + 1] = q[1];
        out[i * 4 + 2] = q[2];
        out[i * 4 + 3] = q[3];
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// 3D Bone Posing Gizmo Math (Pillar 5)
// ---------------------------------------------------------------------------------------------------------------

export function quatNormalize(q) {
  const len = Math.hypot(q[0], q[1], q[2], q[3]);
  if (len < 1e-9) return [0, 0, 0, 1];
  return [q[0] / len, q[1] / len, q[2] / len, q[3] / len];
}

/** Computes rotation angle delta in radians from 2D pointer drag on screen given an active gizmo axis ('X'|'Y'|'Z'). */
export function computeGizmoRotationDelta(axis, deltaX, deltaY, sensitivity = 0.015) {
  if (axis === "X" || axis === "x") {
    return -deltaY * sensitivity;
  } else if (axis === "Y" || axis === "y") {
    return deltaX * sensitivity;
  } else if (axis === "Z" || axis === "z") {
    return (deltaX - deltaY) * 0.7071 * sensitivity;
  }
  return 0.0;
}

/** Applies a delta rotation angle around a given local axis ('X'|'Y'|'Z') to an existing quaternion [x, y, z, w]. */
export function applyBoneRotationDelta(currentQuat, axis, deltaAngleRad) {
  const half = deltaAngleRad * 0.5;
  const sinH = Math.sin(half);
  const cosH = Math.cos(half);
  let qDelta = [0, 0, 0, 1];
  if (axis === "X" || axis === "x") {
    qDelta = [sinH, 0, 0, cosH];
  } else if (axis === "Y" || axis === "y") {
    qDelta = [0, sinH, 0, cosH];
  } else if (axis === "Z" || axis === "z") {
    qDelta = [0, 0, sinH, cosH];
  }
  return quatNormalize(quatMultiply(currentQuat, qDelta));
}

/** Converts quaternion [x, y, z, w] to Euler angles in degrees [pitchX, yawY, rollZ] (extrinsic XYZ / intrinsic ZYX). */
export function quaternionToEulerDegrees(q) {
  const x = q[0], y = q[1], z = q[2], w = q[3];

  // Pitch (X)
  const sinr_cosp = 2 * (w * x + y * z);
  const cosr_cosp = 1 - 2 * (x * x + y * y);
  const pitch = Math.atan2(sinr_cosp, cosr_cosp);

  // Yaw (Y)
  const sinp = 2 * (w * y - z * x);
  let yaw = 0;
  if (Math.abs(sinp) >= 1) {
    yaw = Math.sign(sinp) * (Math.PI / 2);
  } else {
    yaw = Math.asin(sinp);
  }

  // Roll (Z)
  const siny_cosp = 2 * (w * z + x * y);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  const roll = Math.atan2(siny_cosp, cosy_cosp);

  const rad2deg = 180 / Math.PI;
  return [pitch * rad2deg, yaw * rad2deg, roll * rad2deg];
}


// ---------------------------------------------------------------------------------------------------------------
// Job Progress & Console Header Feedback Helpers
// ---------------------------------------------------------------------------------------------------------------

/**
 * Parses progress and result information from a job log line.
 * Returns an object with any extracted fields: { current, total, model, lastResult: { model, status } }
 */
export function parseJobProgressLine(line) {
  if (!line || typeof line !== "string") return null;
  const res = {};

  // 1. Explicit protocol directives:
  // :: SUBJOB_PROGRESS 3 12 hero
  const mProg = line.match(/^::\s*SUBJOB_PROGRESS\s+(\d+)\s+(\d+)(?:\s+([A-Za-z0-9_\-]+))?/);
  if (mProg) {
    res.current = parseInt(mProg[1], 10);
    res.total = parseInt(mProg[2], 10);
    if (mProg[3]) res.model = mProg[3];
  }

  // :: SUBJOB_PREV orc FAILED / PASSED / CHECK
  const mSubPrev = line.match(/^::\s*SUBJOB_PREV\s+([A-Za-z0-9_\-]+)\s+([A-Za-z0-9_\-]+)/);
  if (mSubPrev) {
    let st = mSubPrev[2].toUpperCase();
    if (st === "FAIL") st = "FAILED";
    if (st === "PASS" || st === "OK") st = "PASSED";
    res.prevResult = { model: mSubPrev[1], status: st };
  }

  // :: SUBJOB_RESULT orc FAILED / PASSED / CHECK
  const mSubRes = line.match(/^::\s*SUBJOB_RESULT\s+([A-Za-z0-9_\-]+)\s+([A-Za-z0-9_\-]+)/);
  if (mSubRes) {
    let st = mSubRes[2].toUpperCase();
    if (st === "FAIL") st = "FAILED";
    if (st === "PASS" || st === "OK") st = "PASSED";
    res.lastResult = { model: mSubRes[1], status: st };
  }

  // 2. Banner lines:
  // >> [Job 3 of 12] Starting: hero
  const mBannerStart = line.match(/^>>\s*\[Job\s+(\d+)\s+of\s+(\d+)\]\s*(?:Starting:\s*([A-Za-z0-9_\-]+))?/i);
  if (mBannerStart) {
    res.current = parseInt(mBannerStart[1], 10);
    res.total = parseInt(mBannerStart[2], 10);
    if (mBannerStart[3]) res.model = mBannerStart[3];
  }

  // >> [Job 3 of 12] orc FAILED / PASSED / CHECK
  const mBannerRes = line.match(/^>>\s*\[Job\s+(\d+)\s+of\s+(\d+)\]\s+([A-Za-z0-9_\-]+)\s+(FAILED|PASSED|CHECK|PASS|FAIL|OK)/i);
  if (mBannerRes) {
    res.current = parseInt(mBannerRes[1], 10);
    res.total = parseInt(mBannerRes[2], 10);
    let st = mBannerRes[4].toUpperCase();
    if (st === "FAIL") st = "FAILED";
    if (st === "PASS" || st === "OK") st = "PASSED";
    res.lastResult = { model: mBannerRes[3], status: st };
  }

  // (Previous: orc FAILED / PASSED / CHECK)
  const mPrev = line.match(/\(Previous:\s*([A-Za-z0-9_\-]+)\s+(FAILED|PASSED|CHECK|PASS|FAIL|OK)\)/i);
  if (mPrev && !res.prevResult) {
    let st = mPrev[2].toUpperCase();
    if (st === "FAIL") st = "FAILED";
    if (st === "PASS" || st === "OK") st = "PASSED";
    res.prevResult = { model: mPrev[1], status: st };
  }

  // 3. Fallback standard step lines:
  // == 3/12: hero rig
  // == audit 3/12: hero
  // == source view 3/12: hero
  // == make clips 3/12: hero
  const mStep = line.match(/^==\s*(?:source view |audit |make clips |preview )?(\d+)\/(\d+):\s*([A-Za-z0-9_\-]+)/i);
  if (mStep && !res.current) {
    res.current = parseInt(mStep[1], 10);
    res.total = parseInt(mStep[2], 10);
    if (mStep[3]) res.model = mStep[3];
  }

  // !! 2/12: orc rig failed (exit code 1)
  const mFail = line.match(/^!!\s*(?:source view |audit |make clips |preview )?(?:(\d+)\/(\d+):\s*)?([A-Za-z0-9_\-]+).*failed/i);
  if (mFail && !res.lastResult) {
    if (mFail[1] && mFail[2] && !res.current) {
      res.current = parseInt(mFail[1], 10);
      res.total = parseInt(mFail[2], 10);
    }
    res.lastResult = { model: mFail[3], status: "FAILED" };
  }

  // == 2/12: hero rig done
  const mDone = line.match(/^==\s*(?:source view |audit |make clips |preview )?(?:(\d+)\/(\d+):\s*)?([A-Za-z0-9_\-]+).*done/i);
  if (mDone && !res.lastResult) {
    if (mDone[1] && mDone[2] && !res.current) {
      res.current = parseInt(mDone[1], 10);
      res.total = parseInt(mDone[2], 10);
    }
    res.lastResult = { model: mDone[3], status: "PASSED" };
  }

  // AUDIT_PASS / AUDIT_FAIL / AUDIT_CHECK
  const mAudit = line.match(/^AUDIT_(PASS|FAIL|CHECK)\s+([A-Za-z0-9_\-]+)/);
  if (mAudit && !res.lastResult) {
    let st = mAudit[1];
    if (st === "FAIL") st = "FAILED";
    if (st === "PASS") st = "PASSED";
    res.lastResult = { model: mAudit[2], status: st };
  }

  return Object.keys(res).length > 0 ? res : null;
}

/**
 * Formats console header HTML and plain text summary.
 * Supports:
 *   formatJobHeader(job, subCount, prevResult, lastResult, displayModel)
 *   formatJobHeader(job, subCount, { prevResult, lastResult }, displayModel)
 *   formatJobHeader(job, subCount, lastResult, displayModel)
 */
export function formatJobHeader(job, subCount, prevOrResult, lastOrDisplay, optDisplay) {
  if (!job) return { html: "Idle", text: "Idle" };

  let prevResult = null;
  let lastResult = null;
  let displayModel = "";

  if (optDisplay !== undefined) {
    // 5 arguments: (job, subCount, prevResult, lastResult, displayModel)
    prevResult = prevOrResult;
    lastResult = lastOrDisplay;
    displayModel = optDisplay;
  } else if (prevOrResult && typeof prevOrResult === "object" && ("prevResult" in prevOrResult || "lastResult" in prevOrResult)) {
    // 4 arguments with result bundle object: (job, subCount, { prevResult, lastResult }, displayModel)
    prevResult = prevOrResult.prevResult || null;
    lastResult = prevOrResult.lastResult || null;
    displayModel = lastOrDisplay || "";
  } else {
    // 4 arguments: (job, subCount, lastResult, displayModel)
    lastResult = prevOrResult;
    displayModel = lastOrDisplay || "";
  }

  const st = job.state || "unknown";
  const isRunning = st === "running" || st === "queued";
  const step = job.step || "";
  const model = displayModel || (job.model && !job.model.startsWith("(") ? job.model : "");

  let countHtml = "";
  let countText = "";
  if (subCount && subCount.total > 1) {
    const isDone = !isRunning && (st === "done" || st === "failed" || st === "cancelled");
    const countStr = isDone ? `${subCount.total} of ${subCount.total}` : `job ${subCount.current} of ${subCount.total}`;
    const doneCls = isDone ? " done" : "";
    countHtml = ` <span class="job-counter-pill${doneCls}">${countStr}</span>`;
    countText = ` [${countStr}]`;
  }

  let prevHtml = "";
  let prevText = "";

  // While running: display the previous completed model (prevResult)
  // When completed: display the last completed model (lastResult || prevResult)
  const shownResult = isRunning
    ? (prevResult || (lastResult && lastResult.model !== model ? lastResult : null))
    : (lastResult || prevResult);

  if (shownResult && shownResult.model && shownResult.status) {
    // Safety check: while running, never show the current active model as 'Prev:'!
    if (!isRunning || shownResult.model !== model) {
      const isFail = shownResult.status === "FAILED" || shownResult.status === "FAIL";
      const isPass = shownResult.status === "PASSED" || shownResult.status === "PASS" || shownResult.status === "OK";
      const badgeCls = isFail ? "result-failed" : (isPass ? "result-passed" : "result-check");
      const prefix = (!isRunning) ? "Last" : "Prev";
      prevHtml = ` · ${prefix}: <span class="job-prev-result ${badgeCls}"><b>${shownResult.model}</b> ${shownResult.status}</span>`;
      prevText = ` · ${prefix}: ${shownResult.model} ${shownResult.status}`;
    }
  }

  let countsSummary = "";
  if (job.passed !== undefined && job.failed !== undefined && (job.passed > 0 || job.failed > 0)) {
    countsSummary = ` (${job.passed} passed, ${job.failed} failed)`;
  }

  const modelHtml = model ? ` · Working on: <span class="job-active-model">${model}</span>` : (job.model && !job.model.startsWith("(") ? ` · ${job.model}` : "");
  const modelText = model ? ` · ${model}` : "";

  const pidHtml = job.pid ? ` · PID ${job.pid}` : "";

  const html = `Job ${job.id}: <b>${step}</b>${countHtml}${modelHtml}${prevHtml} · <span class="state ${st}">${st}</span>${countsSummary}${pidHtml}`;
  const text = `Job ${job.id}: ${step}${countText}${modelText}${prevText} · ${st}${countsSummary}`;

  return { html, text };
}

/** Formats a clip for a dropdown option or text label: e.g. "1. walk (1.00s, 24f)" */
export function formatClipOption(clip, index, fps = 24) {
  const num = (index !== undefined && index !== null) ? `${index + 1}. ` : "";
  const name = (clip && clip.name) ? clip.name : "unnamed";
  const dur = (clip && typeof clip.duration === "number") ? `${clip.duration.toFixed(2)}s` : "";
  const frames = (clip && typeof clip.duration === "number") ? `${Math.round(clip.duration * (fps || 24))}f` : "";
  const meta = [dur, frames].filter(Boolean).join(", ");
  return meta ? `${num}${name} (${meta})` : `${num}${name}`;
}

/** Finds a clip index by name (case-insensitive or substring match). Returns -1 if not found. */
export function findClipIndexByName(clips, name) {
  if (!Array.isArray(clips) || !name) return -1;
  const target = String(name).trim().toLowerCase();
  const exact = clips.findIndex((c) => c && c.name && c.name.toLowerCase() === target);
  if (exact !== -1) return exact;
  return clips.findIndex((c) => c && c.name && (c.name.toLowerCase().includes(target) || target.includes(c.name.toLowerCase())));
}


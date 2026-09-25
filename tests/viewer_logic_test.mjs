// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the results viewer's plain logic (autorig/gui/viewer_logic.js) under Node, no browser.
//
//   node tests/viewer_logic_test.mjs          (tests/test_viewer.py runs it when Node is installed)
//
// Gamepads (standard mapping, a pad off it with a hat, the stick's deadzone, repeats), the scrub bar's ticks and
// snapping, the audit's bone relations and bleed rule, and mesh islands welded across seams.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const L = await import(pathToFileURL(path.join(here, "..", "autorig", "gui", "viewer_logic.js")).href);

let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok", name); };
const buttons = (count, on = []) => Array.from({ length: count }, (_, i) => ({ pressed: on.includes(i), value: on.includes(i) ? 1 : 0 }));
const pad = (o) => ({ mapping: "standard", buttons: buttons(17), axes: [0, 0, 0, 0], ...o });

test("deadzone", () => {
  assert.deepEqual(L.deadzone(0.1, 0.1), [0, 0]);
  assert.deepEqual(L.deadzone(0.2, 0), [0, 0]);
  const [x, y] = L.deadzone(1, 0);
  assert.ok(Math.abs(x - 1) < 1e-9 && y === 0);
  const [hx] = L.deadzone(0.625, 0);                 // half way between the deadzone and full tilt
  assert.ok(Math.abs(hx - 0.5) < 1e-9);
  assert.deepEqual(L.deadzone(undefined, undefined), [0, 0]);
});

test("standard mapping: face buttons, shoulders, D-pad", () => {
  const held = (on) => [...L.padHeld(pad({ buttons: buttons(17, on) }))].sort();
  assert.deepEqual(held([0]), ["play"]);
  assert.deepEqual(held([3]), ["overlay"]);
  assert.deepEqual(held([2]), ["view"]);
  assert.deepEqual(held([1]), ["frame"]);
  assert.deepEqual(held([4, 5]), ["nextBone", "prevBone"]);
  assert.deepEqual(held([12]), ["prevClip"]);
  assert.deepEqual(held([15]), ["nextModel"]);
  // an analogue trigger past half way
  const p = pad({}); p.buttons[7] = { pressed: false, value: 0.7 };
  assert.deepEqual([...L.padHeld(p)], ["stepFwd"]);
  p.buttons[7] = { pressed: false, value: 0.3 };
  assert.deepEqual([...L.padHeld(p)], []);
});

test("left stick: deadzone, then models on X and clips on Y", () => {
  assert.deepEqual([...L.padHeld(pad({ axes: [0.3, 0.2, 0, 0] }))], []);          // resting drift
  assert.deepEqual([...L.padHeld(pad({ axes: [-0.95, 0.1, 0, 0] }))], ["prevModel"]);
  assert.deepEqual([...L.padHeld(pad({ axes: [0.9, 0, 0, 0] }))], ["nextModel"]);
  assert.deepEqual([...L.padHeld(pad({ axes: [0.1, -0.9, 0, 0] }))], ["prevClip"]);   // up is -1
  assert.deepEqual([...L.padHeld(pad({ axes: [0, 0.9, 0, 0] }))], ["nextClip"]);
  assert.deepEqual([...L.padHeld(pad({ axes: [0.5, 0.1, 0, 0] }))], []);          // past the deadzone, not a push
});

test("a pad off the standard mapping: hat as axes 6/7, or one hat axis", () => {
  const two = { mapping: "", buttons: buttons(12), axes: [0, 0, 0, 0, 0, 0, 1, 0] };
  assert.deepEqual([...L.padHeld(two)], ["nextModel"]);
  two.axes[6] = 0; two.axes[7] = -1;
  assert.deepEqual([...L.padHeld(two)], ["prevClip"]);
  const one = { mapping: "", buttons: buttons(12), axes: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1.2857] };   // at rest
  assert.deepEqual([...L.padHeld(one)], []);
  one.axes[9] = -1; assert.deepEqual([...L.padHeld(one)], ["prevClip"]);
  one.axes[9] = -3 / 7; assert.deepEqual([...L.padHeld(one)], ["nextModel"]);
  one.axes[9] = 1 / 7; assert.deepEqual([...L.padHeld(one)], ["nextClip"]);
  one.axes[9] = 5 / 7; assert.deepEqual([...L.padHeld(one)], ["prevModel"]);
  // the face buttons still work
  assert.deepEqual([...L.padHeld({ mapping: "", buttons: buttons(12, [0]), axes: [] })], ["play"]);
});

test("presses fire once; held directions repeat", () => {
  let s = null, fired = [];
  const step = (p, t) => { const r = L.padStep(p, s, t); s = r.state; fired.push(...r.fire); };
  const a = pad({ buttons: buttons(17, [0]) }), none = pad({});
  step(a, 0); step(a, 0.1); step(a, 1.0); step(none, 1.1); step(a, 1.2);
  assert.deepEqual(fired, ["play", "play"]);                                  // no repeat on A, again after release
  fired = []; s = null;
  const down = pad({ buttons: buttons(17, [13]) });
  for (let t = 0; t <= 1.0 + 1e-9; t += 0.05) step(down, t);
  // pressed at 0, then repeats from REPEAT_DELAY every REPEAT_EVERY: 0.45, 0.65, 0.85
  assert.equal(fired.filter((x) => x === "nextClip").length, 4);
});

test("scrub ticks and snapping", () => {
  const a = L.scrubTicks(24, 600);
  assert.equal(a.minor, 1); assert.ok(a.major >= 2 && a.major * a.px >= 42);
  const b = L.scrubTicks(2400, 600);
  assert.ok(b.minor * b.px >= 5 && b.major * b.px >= 42 && b.major > b.minor);
  assert.equal(L.scrubTime(0, 600, 1, 24), 0);
  assert.equal(L.scrubTime(600, 600, 1, 24), 1);
  assert.equal(L.scrubTime(-50, 600, 1, 24), 0);
  assert.equal(L.scrubTime(900, 600, 1, 24), 1);
  assert.ok(Math.abs(L.scrubTime(300, 600, 1, 24) - 12 / 24) < 1e-9);
  assert.ok(Math.abs(L.scrubTime(310, 600, 1, 24) - 12 / 24) < 1e-9);          // to the nearest frame
  assert.deepEqual(L.keyTimes([[0, 0.5, 1], [0, 0.50001, 0.25]]), [0, 0.25, 0.5, 1]);
});

test("bone relations and the bleed rule, as the audit has them", () => {
  assert.equal(L.sideOf("leg_front_1.L"), "L");
  assert.equal(L.sideOf("LeftArm"), "L");
  assert.equal(L.sideOf("spine_2"), "");
  assert.equal(L.baseOf("leg_front_1.L"), "leg_front");
  assert.equal(L.baseOf("tail_3"), "tail");
  //  0 hips -> 1 spine -> 2 neck -> 3 head ; 0 -> 4 leg_1.L -> 5 leg_2.L ; 0 -> 6 leg_1.R
  const names = ["hips", "spine", "neck", "head", "leg_1.L", "leg_2.L", "leg_1.R"];
  const parent = [-1, 0, 1, 2, 0, 4, 0];
  assert.ok(L.ancestor(parent, 0, 3));
  assert.ok(!L.ancestor(parent, 3, 0));
  assert.ok(L.related(parent, names, 4, 5));
  assert.ok(!L.related(parent, names, 4, 6));                       // siblings on different sides
  assert.equal(L.hops(parent, 3, 5), 5);
  // a vertex by the neck owned by a leg, far away: bleed
  assert.ok(L.isBleed(parent, names, 5, 2, 1.0, 0.1, 2));
  // owned by the hips, upstream of the neck: stiff, not bleed
  assert.ok(!L.isBleed(parent, names, 0, 2, 1.0, 0.1, 2));
  // owned by its nearest's neighbour: fine
  assert.ok(!L.isBleed(parent, names, 3, 2, 1.0, 0.1, 2));
  // not clearly further: fine
  assert.ok(!L.isBleed(parent, names, 5, 2, 0.14, 0.1, 2));
  assert.ok(!L.isBleed(parent, names, -1, 2, 1, 0.1, 2));
  assert.ok(Math.abs(L.segDist2(0, 1, 0, [0, 0, 0], [0, 0, 2]) - 1) < 1e-12);
  assert.ok(Math.abs(L.segDist2(0, 0, 3, [0, 0, 0], [0, 0, 2]) - 1) < 1e-12);
});

test("islands weld across seams", () => {
  // two triangles sharing an edge but with the shared corners duplicated (a UV seam), and a third, loose one
  const pos = [0, 0, 0, 1, 0, 0, 0, 1, 0, /* seam copies */ 1, 0, 0, 0, 1, 0, 1, 1, 0, /* loose */ 5, 5, 5, 6, 5, 5, 5, 6, 5];
  const idx = [0, 1, 2, 3, 5, 4, 6, 7, 8];
  const r = L.islands(new Float32Array(pos), idx, 1e-5);
  assert.equal(r.count, 2);
  assert.equal(r.labels[0], r.labels[5]);
  assert.notEqual(r.labels[0], r.labels[6]);
  assert.deepEqual([...r.sizes], [4, 3]);                             // welded: the seam copies count once
  const flat = L.islands(new Float32Array(pos.slice(0, 9)), null, 1e-5);
  assert.equal(flat.count, 1);
});

test("tears overlay: gap thresholds, marker sizing, and mapping", () => {
  // Severity based on GAP_CHECK_PCT: 8% for combined, 5% for bend
  assert.equal(L.tearSeverity(4.5, "bend"), "warn");
  assert.equal(L.tearSeverity(5.2, "bend"), "bad");
  assert.equal(L.tearSeverity(7.5, "combined"), "warn");
  assert.equal(L.tearSeverity(8.1, "combined"), "bad");

  // Marker sizing grows with edge count
  const r1 = L.tearMarkerRadius(1, 0.1);
  const r10 = L.tearMarkerRadius(10, 0.1);
  const r100 = L.tearMarkerRadius(100, 0.1);
  assert.ok(r1 < r10 && r10 < r100);
  assert.ok(r100 <= 0.25);                                            // capped at 2.5x base

  // Point mapping: [x, y, z] Blender (+Z up, -Y forward) -> [x, z, -y] glTF (Y up, +Z forward)
  assert.deepEqual(L.mapTearPoint([1, 2, 3]), [1, 3, -2]);

  // Combined pose angle heuristics
  assert.equal(L.defaultCombinedAngle("spine"), 12);
  assert.equal(L.defaultCombinedAngle("neck"), 20);
  assert.equal(L.defaultCombinedAngle("leg"), 30);
  assert.equal(L.defaultCombinedAngle("finger"), 0);
});

test("chain mirroring and stations generation", () => {
  // mirrorName
  assert.equal(L.mirrorName("wing.R"), "wing.L");
  assert.equal(L.mirrorName("wing.L"), "wing.R");
  assert.equal(L.mirrorName("leg_right"), "leg_left");
  assert.equal(L.mirrorName("leg_left"), "leg_right");
  assert.equal(L.mirrorName("arm_r"), "arm_l");
  assert.equal(L.mirrorName("arm_l"), "arm_r");
  assert.equal(L.mirrorName("fin_m"), "fin");
  assert.equal(L.mirrorName("horn", false), "horn.L");
  assert.equal(L.mirrorName("horn", true), "horn.R");

  // mirrorChainData (X -> 1 - X)
  const rightLimb = {
    name: "leg.R",
    role: "leg",
    tip: [0.2, 0.4, 0.1],
    base: [0.35, 0.4, 0.5],
    parent: ["hip.R", 0]
  };
  const existing = [{ name: "spine" }, { name: "hip.R" }, { name: "hip.L" }, rightLimb];
  const leftLimb = L.mirrorChainData(rightLimb, existing);
  assert.equal(leftLimb.name, "leg.L");
  assert.equal(leftLimb.role, "leg");
  assert.deepEqual(leftLimb.tip, [0.8, 0.4, 0.1]);
  assert.deepEqual(leftLimb.base, [0.65, 0.4, 0.5]);
  assert.deepEqual(leftLimb.parent, ["hip.L", 0]);

  // Polyline points mirroring
  const polyChain = {
    name: "antenna_right",
    role: "antenna",
    points: [[0.4, 0.2, 0.8], [0.3, 0.15, 0.9], [0.1, 0.1, 1.0]]
  };
  const mirroredPoly = L.mirrorChainData(polyChain, []);
  assert.equal(mirroredPoly.name, "antenna_left");
  assert.deepEqual(mirroredPoly.points, [[0.6, 0.2, 0.8], [0.7, 0.15, 0.9], [0.9, 0.1, 1.0]]);

  // generateStations
  const st = L.generateStations([0.1, 0.9], 4);
  assert.equal(st.length, 5);
  assert.deepEqual(st, [0.1, 0.3, 0.5, 0.7, 0.9]);

  const customSt = L.generateStations([0.2, 0.8], 3);
  assert.equal(customSt.length, 4);
  assert.deepEqual(customSt, [0.2, 0.4, 0.6, 0.8]);
});

test("interactive joint bend test math: rodrigues, hinge axis, and vertex deformation", () => {
  // Rodrigues 90-degree rotation around Z
  const r90 = L.rodriguesRotate([1, 0, 0], [0, 0, 0], [0, 0, 1], Math.PI / 2);
  assert.ok(Math.abs(r90[0]) < 1e-6);
  assert.ok(Math.abs(r90[1] - 1) < 1e-6);
  assert.ok(Math.abs(r90[2]) < 1e-6);

  // Natural hinge axis for bent limb
  const axis = L.computeHingeAxis([0, 0, 0], [1, 0, 0], [1, 1, 0]);
  assert.deepEqual(axis, [0, 0, 1]);

  // Vertex deformation: v0 (proximal), v1 (pivot), v2 (distal), v3 (far outside radius)
  const orig = new Float32Array([
    0.0, 0.0, 0.0,   // v0: proximal (behind pivot)
    1.0, 0.0, 0.0,   // v1: pivot
    2.0, 0.0, 0.0,   // v2: distal (after pivot)
    1.0, 5.0, 0.0    // v3: outside limb radius
  ]);
  const pos = new Float32Array(orig);

  // Bend 90 degrees (+PI/2)
  L.bendVertices(pos, orig, [0, 0, 0], [1, 0, 0], [2, 0, 0], Math.PI / 2, 0.5, 0.2);

  // v0 must remain untouched
  assert.ok(Math.abs(pos[0]) < 1e-6 && Math.abs(pos[1]) < 1e-6 && Math.abs(pos[2]) < 1e-6);
  // v1 is pivot, remains near (1, 0, 0)
  assert.ok(Math.abs(pos[3] - 1.0) < 1e-6);
  // v2 rotates 90 deg around pivot (1, 0, 0) to (1, 0, 1)
  assert.ok(Math.abs(pos[6] - 1.0) < 1e-4);
  assert.ok(Math.abs(pos[7] - 0.0) < 1e-4);
  assert.ok(Math.abs(pos[8] - 1.0) < 1e-4);
  // v3 outside radius must remain untouched
  assert.deepEqual([...pos.subarray(9, 12)], [1.0, 5.0, 0.0]);

  // Zero angle resets positions to original exactly
  L.bendVertices(pos, orig, [0, 0, 0], [1, 0, 0], [2, 0, 0], 0, 0.5, 0.2);
  assert.deepEqual(pos, orig);
});

test("universal camera system: views, labels, cycling, and humanoid detection", () => {
  assert.deepEqual(L.VIEWS, ["hero", "front", "side", "orbit"]);
  assert.equal(L.viewLabel("front"), "Front view");
  assert.equal(L.viewLabel("hero"), "3/4 Hero view");
  assert.equal(L.viewLabel("side"), "Side view");
  assert.equal(L.viewLabel("orbit"), "Free orbit");

  // Cyclical view switching
  assert.equal(L.nextView("hero"), "front");
  assert.equal(L.nextView("front"), "side");
  assert.equal(L.nextView("side"), "orbit");
  assert.equal(L.nextView("orbit"), "hero");
  assert.equal(L.nextView("unknown"), "hero");

  // Humanoid detection by archetype
  assert.ok(L.isHumanoidOrBiped({ archetype: "humanoid" }));
  assert.ok(L.isHumanoidOrBiped({ skeleton: "walker" }));
  assert.ok(L.isHumanoidOrBiped({ archetype: "biped" }));
  assert.ok(!L.isHumanoidOrBiped({ archetype: "quadruped" }));
  assert.ok(!L.isHumanoidOrBiped({ archetype: "serpent" }));

  // Humanoid detection by bone names
  const humanoidBones = [
    { name: "Hips" }, { name: "Spine" }, { name: "Head" },
    { name: "LeftArm" }, { name: "RightArm" },
    { name: "LeftLeg" }, { name: "RightLeg" }
  ];
  assert.ok(L.isHumanoidOrBiped({}, humanoidBones));

  const quadBones = [
    { name: "body" }, { name: "leg_front_1.L" }, { name: "leg_back_1.L" }
  ];
  assert.ok(!L.isHumanoidOrBiped({}, quadBones));

  // Humanoid detection by bounding box proportions (tall upright figure)
  const tallBox = { x: 0.5, y: 1.8, z: 0.3 };
  assert.ok(L.isHumanoidOrBiped({}, [], tallBox));

  const wideBox = { x: 2.0, y: 0.8, z: 1.2 };
  assert.ok(!L.isHumanoidOrBiped({}, [], wideBox));

  // Default camera view
  assert.equal(L.defaultCameraView({ archetype: "humanoid" }), "hero");
  assert.equal(L.defaultCameraView({}, humanoidBones), "hero");
  assert.equal(L.defaultCameraView({ archetype: "quadruped" }), "side");
});

test("orientation angles, axis locking, camera leveling, and ground plane", () => {
  // 1. Orientation angles: pitch, yaw, roll
  // When polar angle is PI/2 (horizontal ground level), pitch should be 0.0°
  const horiz = L.computeOrientation(Math.PI / 2, 0);
  assert.equal(horiz.pitchDeg, 0);
  assert.equal(horiz.yawDeg, 0);
  assert.equal(horiz.rollDeg, 0);

  // Top-down view (polar = 0) -> pitch = +90°
  const top = L.computeOrientation(0, Math.PI / 2);
  assert.equal(top.pitchDeg, 90);
  assert.equal(top.yawDeg, 90);

  // Bottom-up view (polar = PI) -> pitch = -90°
  const bottom = L.computeOrientation(Math.PI, -Math.PI / 2);
  assert.equal(bottom.pitchDeg, -90);
  assert.equal(bottom.yawDeg, 270);

  // 2. Axis locking: clampAnglesToLocked
  const unlocked = L.clampAnglesToLocked(1.2, 0.5, false, false);
  assert.equal(unlocked.minPolar, 0.001);
  assert.equal(unlocked.maxPolar, Math.PI - 0.001);
  assert.equal(unlocked.minAzimuth, -Infinity);
  assert.equal(unlocked.maxAzimuth, Infinity);

  // Lock X freezes polar angle (pitch)
  const lockedX = L.clampAnglesToLocked(1.2, 0.5, true, false);
  assert.equal(lockedX.minPolar, 1.2);
  assert.equal(lockedX.maxPolar, 1.2);
  assert.equal(lockedX.minAzimuth, -Infinity);

  // Lock Y freezes azimuthal angle (yaw)
  const lockedY = L.clampAnglesToLocked(1.2, 0.5, false, true);
  assert.equal(lockedY.minPolar, 0.001);
  assert.equal(lockedY.minAzimuth, 0.5);
  assert.equal(lockedY.maxAzimuth, 0.5);

  // Lock both X and Y
  const lockedBoth = L.clampAnglesToLocked(1.2, 0.5, true, true);
  assert.equal(lockedBoth.minPolar, 1.2);
  assert.equal(lockedBoth.maxPolar, 1.2);
  assert.equal(lockedBoth.minAzimuth, 0.5);
  assert.equal(lockedBoth.maxAzimuth, 0.5);

  // 3. Camera leveling: calculateLeveledCameraPosition
  // Camera at (0, 2, 2) looking at (0, 0, 0), pitchDeg=0
  const leveled = L.calculateLeveledCameraPosition({ x: 0, y: 2, z: 2 }, { x: 0, y: 0, z: 0 }, 0);
  // At pitch=0, Y must equal target Y (0)
  assert.equal(leveled.y, 0);
  assert.ok(Math.abs(Math.hypot(leveled.x, leveled.z) - Math.hypot(0, 2, 2)) < 1e-3);

  // 4. Ground plane parameters: computeGroundPlaneParameters
  const box = {
    min: { x: -0.5, y: -0.1, z: -0.2 },
    max: { x: 0.5, y: 1.7, z: 0.4 }
  };
  const ground = L.computeGroundPlaneParameters(box);
  assert.equal(ground.groundY, -0.1); // feet contact level
  assert.equal(ground.center[0], 0.0);
  assert.equal(ground.center[1], -0.1);
  assert.equal(ground.center[2], 0.1);
  assert.ok(ground.gridDim >= 4);

  // 5. Dynamic Camera Framing Distance (centering & auto-zoom)
  const dStandard = L.calculateFramingDistance({ x: 1, y: 1.8, z: 0.5 }, 35, 1.33);
  const dNarrow = L.calculateFramingDistance({ x: 1, y: 1.8, z: 0.5 }, 35, 0.6);
  assert.ok(dNarrow > dStandard, "Narrow aspect ratio must zoom out to prevent horizontal clipping");

  const dWide = L.calculateFramingDistance({ x: 4, y: 1.0, z: 0.5 }, 35, 1.0);
  const dCube = L.calculateFramingDistance({ x: 1, y: 1.0, z: 0.5 }, 35, 1.0);
  assert.ok(dWide > dCube * 2, "Wide model must zoom out significantly to prevent lateral clipping");
});

test("status filtering: matchesModelFilter", () => {
  const mPass = { name: "biped", audit: "PASS", rigged: true, spec: true };
  const mCheck = { name: "beetle", audit: "CHECK", rigged: true, spec: true };
  const mFail = { name: "wyvern", audit: "FAIL", rigged: true, spec: true };
  const mUnrigged = { name: "pedestal", audit: null, rigged: false, spec: true };
  const mNoSpec = { name: "canine", audit: null, rigged: false, spec: false };

  // All matches everything
  assert.equal(L.matchesModelFilter(mPass, "all"), true);
  assert.equal(L.matchesModelFilter(mFail, "all"), true);
  assert.equal(L.matchesModelFilter(mNoSpec, "all"), true);

  // Status-specific filters
  assert.equal(L.matchesModelFilter(mPass, "pass"), true);
  assert.equal(L.matchesModelFilter(mCheck, "pass"), false);
  assert.equal(L.matchesModelFilter(mFail, "pass"), false);

  assert.equal(L.matchesModelFilter(mCheck, "check"), true);
  assert.equal(L.matchesModelFilter(mPass, "check"), false);

  assert.equal(L.matchesModelFilter(mFail, "fail"), true);
  assert.equal(L.matchesModelFilter(mPass, "fail"), false);

  // Rigged vs unrigged
  assert.equal(L.matchesModelFilter(mPass, "rigged"), true);
  assert.equal(L.matchesModelFilter(mUnrigged, "rigged"), false);
  assert.equal(L.matchesModelFilter(mUnrigged, "unrigged"), true);
  assert.equal(L.matchesModelFilter(mPass, "unrigged"), false);

  // Missing spec
  assert.equal(L.matchesModelFilter(mNoSpec, "nospec"), true);
  assert.equal(L.matchesModelFilter(mPass, "nospec"), false);
});

test("model load sequence validation and race protection", () => {
  // Matching active sequence and model commits safely
  assert.equal(L.isModelLoadValid(1, 1, "biped", "biped"), true);
  assert.equal(L.isModelLoadValid(5, 5, "beetle", "beetle"), true);

  // Stale sequence number (new model switch occurred while first was loading) rejected
  assert.equal(L.isModelLoadValid(2, 1, "biped", "biped"), false);
  assert.equal(L.isModelLoadValid(10, 9, "beetle", "beetle"), false);

  // Model mismatch (user switched from biped to beetle) rejected
  assert.equal(L.isModelLoadValid(2, 2, "beetle", "biped"), false);
  assert.equal(L.isModelLoadValid(1, 1, "wyvern", "canine"), false);

  // Empty or uninitialized models rejected
  assert.equal(L.isModelLoadValid(1, 1, "", ""), false);
  assert.equal(L.isModelLoadValid(1, 1, null, "beetle"), false);
});

test("quadruped IK pole target position calculation", () => {
  // Bent hind leg: hip at (0, 0, 1), knee forward at (0, -0.1, 0.5), foot at (0, 0, 0)
  const hindPole = L.computePoleTargetPosition([0, 0, 1.0], [0, -0.1, 0.5], [0, 0, 0], false, 0.4);
  // Knee bends forward (-Y), so pole target must be in front (-Y < -0.1)
  assert.ok(hindPole[1] < -0.1);
  assert.equal(hindPole[0], 0);

  // Collinear front leg: elbow at (0, 0, 0.5), hip at (0, 0, 1.0), tip at (0, 0, 0)
  const frontPole = L.computePoleTargetPosition([0, 0, 1.0], [0, 0, 0.5], [0, 0, 0], true, 0.3);
  // Front leg anatomical fallback elbow bends backward (+Y)
  assert.ok(frontPole[1] > 0);
  assert.equal(frontPole[0], 0);
});

test("quadruped 4-beat lateral sequence gait phases", () => {
  // LH (0.00) -> LF (0.25) -> RH (0.50) -> RF (0.75)
  assert.equal(L.computeQuadrupedGaitPhase("L", false), 0.00); // Left Hind
  assert.equal(L.computeQuadrupedGaitPhase("L", true), 0.25);  // Left Front
  assert.equal(L.computeQuadrupedGaitPhase("R", false), 0.50); // Right Hind
  assert.equal(L.computeQuadrupedGaitPhase("R", true), 0.75);  // Right Front
});

test("procedural gait params merging and pelvis kinematics", () => {
  const merged = L.mergeGaitParams("soldier", { stride: 1.5, sway: 0.8 });
  assert.equal(merged.stride, 1.5);
  assert.equal(merged.sway, 0.8);
  assert.equal(merged.cadence, 1.10); // from soldier preset

  // Clamp checks
  const clamped = L.mergeGaitParams("natural", { stride: 10.0, sway: -5.0 });
  assert.equal(clamped.stride, 2.5);
  assert.equal(clamped.sway, 0.0);

  // Pelvis trajectory periodic evaluation f(0) === f(1)
  const p0 = L.evaluatePelvisTrajectory(0.0, merged);
  const p1 = L.evaluatePelvisTrajectory(1.0, merged);
  assert.ok(Math.abs(p0.bob - p1.bob) < 1e-9);
  assert.ok(Math.abs(p0.sway - p1.sway) < 1e-9);
  assert.ok(Math.abs(p0.yaw - p1.yaw) < 1e-9);
  assert.ok(Math.abs(p0.lean - p1.lean) < 1e-9);
});

test("quaternion rotation scaling and forward pitch tilt", () => {
  // 10 degree rotation about X axis
  const angRad = (10.0 * Math.PI) / 180.0;
  const q = [Math.sin(angRad / 2), 0, 0, Math.cos(angRad / 2)];

  // Scale 1.5x -> 15 degrees
  const scaled15 = L.scaleQuaternionRotation(q, 1.5);
  const ang15 = 2 * Math.acos(scaled15[3]) * 180 / Math.PI;
  assert.ok(Math.abs(ang15 - 15.0) < 1e-4);

  // Scale 0.5x -> 5 degrees
  const scaled05 = L.scaleQuaternionRotation(q, 0.5);
  const ang05 = 2 * Math.acos(scaled05[3]) * 180 / Math.PI;
  assert.ok(Math.abs(ang05 - 5.0) < 1e-4);

  // Identity / 0 scale -> 0 degrees
  const scaled00 = L.scaleQuaternionRotation(q, 0.0);
  const ang00 = 2 * Math.acos(scaled00[3]) * 180 / Math.PI;
  assert.ok(Math.abs(ang00) < 1e-4);

  // Forward pitch tilt
  const qPitched = L.pitchQuaternion([0, 0, 0, 1], 12.0);
  const angPitched = 2 * Math.acos(qPitched[3]) * 180 / Math.PI;
  assert.ok(Math.abs(angPitched - 12.0) < 1e-4);
});

test("base locomotion clip selection for style presets", () => {
  const clips = ["idle", "attack", "walk", "run", "trot", "gallop", "death"];
  assert.equal(L.selectBaseClipForPreset("natural", clips), "walk");
  assert.equal(L.selectBaseClipForPreset("soldier", clips), "walk");
  assert.equal(L.selectBaseClipForPreset("run", clips), "run");
  assert.equal(L.selectBaseClipForPreset("sprint", clips), "run");
  assert.equal(L.selectBaseClipForPreset("quadruped_gallop", clips), "gallop");
  assert.equal(L.selectBaseClipForPreset("quadruped_trot", clips), "trot");

  // Fallbacks when specific clip not baked
  const walkOnly = ["idle", "walk", "attack"];
  assert.equal(L.selectBaseClipForPreset("run", walkOnly), "walk");
  assert.equal(L.selectBaseClipForPreset("quadruped_gallop", walkOnly), "walk");
});

test("live procedural track modulation for hips and limbs", () => {
  // 1. Root/Hips translation: X sway, Y bob
  const posTimes = [0, 0.25, 0.5, 0.75];
  const posVals = new Float32Array([
    -0.04, 1.00, 0.0,
     0.00, 1.04, 0.0,
     0.04, 1.00, 0.0,
     0.00, 1.04, 0.0
  ]);
  const modPos = L.modulateGaitTrackValues("Hips.position", posTimes, posVals, { sway: 2.0, bob: 0.5 });
  // Mean X was 0.0, amplitude was 0.04 -> scaled 2.0x amplitude should be 0.08
  assert.ok(Math.abs(modPos[0] - (-0.08)) < 1e-4);
  assert.ok(Math.abs(modPos[6] - 0.08) < 1e-4);
  // Mean Y was 1.02, delta was +0.02 at t=0.25 -> scaled 0.5x delta should be +0.01 -> 1.03
  assert.ok(Math.abs(modPos[4] - 1.03) < 1e-4);

  // 2. Leg rotation: scaled by stride
  const angRad = (10.0 * Math.PI) / 180.0;
  const rotVals = new Float32Array([
    Math.sin(angRad / 2), 0, 0, Math.cos(angRad / 2),
    0, 0, 0, 1
  ]);
  const modLeg = L.modulateGaitTrackValues("LeftUpLeg.quaternion", [0, 1.0], rotVals, { stride: 1.4 });
  const modAng = 2 * Math.acos(modLeg[3]) * 180 / Math.PI;
  assert.ok(Math.abs(modAng - 14.0) < 1e-4);
});

test("interactive 3D bone posing gizmo rotation math", () => {
  // 1. computeGizmoRotationDelta
  assert.equal(L.computeGizmoRotationDelta("X", 0, 10, 0.01), -0.1);
  assert.equal(L.computeGizmoRotationDelta("Y", 10, 0, 0.01), 0.1);
  assert.ok(Math.abs(L.computeGizmoRotationDelta("Z", 10, 10, 0.01) - 0.0) < 1e-6);

  // 2. applyBoneRotationDelta on identity quaternion
  const q0 = [0, 0, 0, 1];
  const deltaRad = (30 * Math.PI) / 180;
  const qRotX = L.applyBoneRotationDelta(q0, "X", deltaRad);
  const eulerX = L.quaternionToEulerDegrees(qRotX);
  assert.ok(Math.abs(eulerX[0] - 30.0) < 1e-4);
  assert.ok(Math.abs(eulerX[1] - 0.0) < 1e-4);
  assert.ok(Math.abs(eulerX[2] - 0.0) < 1e-4);

  // 3. Yaw rotation (Y)
  const qRotY = L.applyBoneRotationDelta(q0, "Y", deltaRad);
  const eulerY = L.quaternionToEulerDegrees(qRotY);
  assert.ok(Math.abs(eulerY[0] - 0.0) < 1e-4);
  assert.ok(Math.abs(eulerY[1] - 30.0) < 1e-4);
  assert.ok(Math.abs(eulerY[2] - 0.0) < 1e-4);

  // 4. Roll rotation (Z)
  const qRotZ = L.applyBoneRotationDelta(q0, "Z", deltaRad);
  const eulerZ = L.quaternionToEulerDegrees(qRotZ);
  assert.ok(Math.abs(eulerZ[0] - 0.0) < 1e-4);
  assert.ok(Math.abs(eulerZ[1] - 0.0) < 1e-4);
  assert.ok(Math.abs(eulerZ[2] - 30.0) < 1e-4);
});

test("dynamic model centering and auto-zoom framing distance", () => {
  // 1. Tall model in standard 16:9 aspect (aspect = 1.77)
  const tallSize = { x: 0.5, y: 2.0, z: 0.5 };
  const dTall = L.calculateFramingDistance(tallSize, 35, 1.77, 1.25);
  assert.ok(dTall > 3.0, "Camera distance should comfortably fit tall model");

  // 2. Wide model in portrait/narrow aspect (e.g. aspect = 0.5 when inspector panel is open)
  const wideSize = { x: 4.0, y: 1.0, z: 1.0 };
  const dWideNarrow = L.calculateFramingDistance(wideSize, 35, 0.5, 1.25);
  const dWideNormal = L.calculateFramingDistance(wideSize, 35, 1.77, 1.25);
  assert.ok(dWideNarrow > dWideNormal, "Narrow aspect must automatically zoom out further to avoid cutting off sides");

  // 3. Diagonal bounding sphere ensures extremities don't clip
  const dSphere = L.calculateFramingDistance({ x: 2, y: 2, z: 2 }, 35, 1.0, 1.0);
  assert.ok(dSphere > 2.5);

  // 4. Fallback on invalid inputs
  assert.ok(L.calculateFramingDistance(null) > 3.0);
  assert.ok(L.calculateFramingDistance({ x: 0, y: 0, z: 0 }) >= 0.5);
});

test("parseJobProgressLine parses directives, banners, and fallbacks", () => {
  // 1. Explicit directives
  const p1 = L.parseJobProgressLine(":: SUBJOB_PROGRESS 3 12 hero_warrior");
  assert.deepEqual(p1, { current: 3, total: 12, model: "hero_warrior" });

  const p2 = L.parseJobProgressLine(":: SUBJOB_RESULT orc_grunt FAILED");
  assert.deepEqual(p2, { lastResult: { model: "orc_grunt", status: "FAILED" } });

  const p3 = L.parseJobProgressLine(":: SUBJOB_RESULT goblin_scout PASSED");
  assert.deepEqual(p3, { lastResult: { model: "goblin_scout", status: "PASSED" } });

  const p3b = L.parseJobProgressLine(":: SUBJOB_PREV troll_warlord FAILED");
  assert.deepEqual(p3b, { prevResult: { model: "troll_warlord", status: "FAILED" } });

  // 2. Banner lines
  const p4 = L.parseJobProgressLine(">> [Job 4 of 10] Starting: dragon_boss");
  assert.deepEqual(p4, { current: 4, total: 10, model: "dragon_boss" });

  const p5 = L.parseJobProgressLine("   (Previous: orc_grunt FAILED)");
  assert.deepEqual(p5, { prevResult: { model: "orc_grunt", status: "FAILED" } });

  // 3. Fallback and legacy lines
  const p6 = L.parseJobProgressLine("== 3/12: knight rig done");
  assert.deepEqual(p6, { current: 3, total: 12, model: "knight", lastResult: { model: "knight", status: "PASSED" } });

  const p7 = L.parseJobProgressLine("!! 2/12: archer rig failed");
  assert.deepEqual(p7, { current: 2, total: 12, lastResult: { model: "archer", status: "FAILED" } });

  const p8 = L.parseJobProgressLine("AUDIT_FAIL swamp_monster (score=0.45)");
  assert.deepEqual(p8, { lastResult: { model: "swamp_monster", status: "FAILED" } });

  const p9 = L.parseJobProgressLine("AUDIT_PASS dwarf_miner");
  assert.deepEqual(p9, { lastResult: { model: "dwarf_miner", status: "PASSED" } });

  const p10 = L.parseJobProgressLine("AUDIT_CHECK wizard_elder");
  assert.deepEqual(p10, { lastResult: { model: "wizard_elder", status: "CHECK" } });

  // 4. Irrelevant lines return null
  assert.equal(L.parseJobProgressLine("Normal blender log line with no progress"), null);
  assert.equal(L.parseJobProgressLine(null), null);
  assert.equal(L.parseJobProgressLine(""), null);
});

test("formatJobHeader displays count 'job x of y' and previous job result", () => {
  // 1. Idle or null job
  assert.deepEqual(L.formatJobHeader(null), { html: "Idle", text: "Idle" });

  // 2. Active single job (no subCount)
  const single = L.formatJobHeader({ id: 1, step: "rig", model: "hero", state: "running", pid: 1234 });
  assert.ok(single.html.includes("Job 1: <b>rig</b>"));
  assert.ok(single.html.includes("hero"));
  assert.ok(single.text.includes("Job 1: rig · hero · running"));

  // 3. Active bulk job with counter and previous job failure
  const bulkActive = L.formatJobHeader(
    { id: 2, step: "batch rig", model: "(all)", state: "running", pid: 5678 },
    { current: 3, total: 10 },
    { model: "orc_grunt", status: "FAILED" },
    null,
    "goblin_scout"
  );
  assert.ok(bulkActive.html.includes("job 3 of 10"), "HTML must contain current counter 'job x of y'");
  assert.ok(bulkActive.html.includes("job-counter-pill"));
  assert.ok(bulkActive.html.includes("result-failed"));
  assert.ok(bulkActive.html.includes("<b>orc_grunt</b> FAILED"), "HTML must show previous result model and FAILED status");
  assert.ok(bulkActive.html.includes("Working on: <span class=\"job-active-model\">goblin_scout</span>"));
  assert.ok(bulkActive.text.includes("[job 3 of 10]"), "Plain text must contain counter");
  assert.ok(bulkActive.text.includes("Prev: orc_grunt FAILED"), "Plain text must contain previous result");

  // 4. Active bulk job: Prev must NEVER show the current working model!
  const activeSameModel = L.formatJobHeader(
    { id: 3, step: "batch rig", model: "(all)", state: "running" },
    { current: 1, total: 5 },
    null, // no previous result yet on job 1
    { model: "hero_warrior", status: "PASSED" }, // e.g. current model just passed an audit step
    "hero_warrior" // currently working on hero_warrior
  );
  assert.ok(!activeSameModel.html.includes("Prev:"), "Must NOT show current model as Prev while running");
  assert.ok(!activeSameModel.text.includes("Prev:"));

  // 5. Completed bulk job with final summary
  const bulkDone = L.formatJobHeader(
    { id: 2, step: "batch rig", model: "(all)", state: "done", passed: 9, failed: 1 },
    { current: 10, total: 10 },
    null,
    { model: "dragon_boss", status: "PASSED" },
    ""
  );
  assert.ok(bulkDone.html.includes("10 of 10"));
  assert.ok(bulkDone.html.includes("job-counter-pill done"));
  assert.ok(bulkDone.html.includes("result-passed"));
  assert.ok(bulkDone.html.includes("<b>dragon_boss</b> PASSED"));
  assert.ok(bulkDone.html.includes("(9 passed, 1 failed)"));
  assert.ok(bulkDone.text.includes("[10 of 10]"));
  assert.ok(bulkDone.text.includes("Last: dragon_boss PASSED"));
  assert.ok(bulkDone.text.includes("(9 passed, 1 failed)"));
});

console.log(`${n} passed`);





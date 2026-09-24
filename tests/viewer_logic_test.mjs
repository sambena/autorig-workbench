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
});

console.log(`${n} passed`);


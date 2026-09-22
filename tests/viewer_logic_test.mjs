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

console.log(`${n} passed`);


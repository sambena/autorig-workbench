# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the prepare step's decisions, and which file source_model reads afterwards.

import math
import os
import json
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
import sys
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

import layout
import prepare


class ForwardTest(unittest.TestCase):
    def test_yaw_matches_the_rig_step(self):
        # atan2(-1, 0) - atan2(forward.y, forward.x), then the nearest right angle.
        self.assertAlmostEqual(prepare.snap_quarter(prepare.yaw_to_neg_y((0, -1, 0))), 0.0)
        self.assertAlmostEqual(prepare.snap_quarter(prepare.yaw_to_neg_y((0, 1, 0))), -math.pi)
        self.assertAlmostEqual(prepare.snap_quarter(prepare.yaw_to_neg_y((1, 0, 0))), -math.pi / 2)
        self.assertAlmostEqual(prepare.snap_quarter(prepare.yaw_to_neg_y((-1, 0))), -3 * math.pi / 2)

    def test_neg_y(self):
        self.assertTrue(prepare.is_neg_y([0, -1, 0]))
        self.assertFalse(prepare.is_neg_y([0, 1, 0]))
        self.assertFalse(prepare.is_neg_y("auto"))
        self.assertIsNone(prepare.as_forward("auto"))
        self.assertIsNone(prepare.as_forward([0, 0, 1]))

    def test_which_facing_a_run_trusts(self):
        # First run: the spec, when it names a direction.
        self.assertEqual(prepare.raw_forward([0, 1, 0], None, False), [0.0, 1.0, 0.0])
        self.assertIsNone(prepare.raw_forward("auto", None, False))
        # Re-run on the same file: the facing recorded then, not the spec prepare already rewrote to -Y.
        self.assertEqual(prepare.raw_forward([0, -1, 0], [0, 1, 0], True)[:2], [0.0, 1.0])
        # The raw file was replaced: measure it again, even though the spec now says -Y.
        self.assertIsNone(prepare.raw_forward([0, -1, 0], [0, 1, 0], False))
        # A re-run whose record is unreadable measures, rather than trusting the rewritten spec.
        self.assertIsNone(prepare.raw_forward([0, -1, 0], "nope", True))

    def test_feet_and_centre(self):
        self.assertEqual(prepare.feet_offset((1, 2, 3), (3, 6, 9)), (-2, -4, -3))
        self.assertEqual(prepare.feet_offset((1, 2, 3), (3, 6, 9), origin="center"), (-2, -4, -6))

    def test_rewrite_forward_keeps_unknown_keys(self):
        src = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "forward": [0, 1, 0], "notes_inside": 1},
               "humanoid": {"forward": [1, 0, 0], "z": {"hip": 0.5}}, "budget": None}
        out = prepare.rewrite_forward(src, [0, 1, 0])
        self.assertEqual(out["rig"]["forward"], [0, -1, 0])
        self.assertEqual(out["rig"]["notes_inside"], 1)
        self.assertEqual(out["humanoid"]["forward"], [0, -1, 0])
        self.assertEqual(out["humanoid"]["z"]["hip"], 0.5)
        self.assertEqual(out["budget"], None)
        self.assertIn("raw file faced", out["notes"]["rig.forward"])
        self.assertEqual(src["rig"]["forward"], [0, 1, 0])
        self.assertIsNone(prepare.rewrite_forward({"rig": {"forward": [0, -1, 0]}}, [0, -1, 0]))
        self.assertIsNone(prepare.rewrite_forward({"rig": {"kind": "build"}}, [0, 1, 0]))


class UvAndJointsTest(unittest.TestCase):
    def test_even_triangle_is_unstretched(self):
        # A right triangle in 3D mapped onto the same shape in UV.
        r = prepare.tri_stretch((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0), (1, 0), (0, 1))
        self.assertAlmostEqual(r, 1.0, places=5)

    def test_squashed_uv_is_stretched(self):
        r = prepare.tri_stretch((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0), (1, 0), (0, 0.1))
        self.assertGreater(r, 4.0)
        report = prepare.uv_report([r], missing=0, total=1)
        self.assertFalse(report["usable"])
        self.assertIn("--unwrap", report["note"])

    def test_usable_band_and_missing_uvs(self):
        ok = prepare.uv_report([1.0, 1.1, 1.2, 1.05], missing=0, total=4)
        self.assertTrue(ok["usable"])
        self.assertIn("left unchanged", ok["note"])
        none = prepare.uv_report([], missing=3, total=3)
        self.assertFalse(none["usable"])
        self.assertIn("no UVs", none["note"])

    def _tube(self, sides, height=1.0, radius=0.2):
        verts, edges = [], []
        for z in (0.0, height):
            base = len(verts)
            for i in range(sides):
                a = 2 * math.pi * i / sides
                verts.append((radius * math.cos(a), radius * math.sin(a), z))
            for i in range(sides):
                edges.append((base + i, base + (i + 1) % sides))
        for i in range(sides):
            edges.append((i, sides + i))
        lo = [min(v[k] for v in verts) for k in range(3)]
        hi = [max(v[k] for v in verts) for k in range(3)]
        return verts, edges, lo, hi

    def test_thin_tube_and_a_dense_one(self):
        thin = prepare.joint_report(*self._tube(6))
        self.assertTrue(thin["upright"])
        self.assertIn("knee", thin["thin"])
        self.assertIn("--loops", thin["note"])
        dense = prepare.joint_report(*self._tube(16))
        self.assertEqual(dense["thin"], [])
        self.assertIn("enough edges", dense["note"])

    def test_a_wide_body_is_not_given_knees(self):
        verts = [(0, 0, 0), (4, 0, 0), (0, 3, 0), (0, 0, 0.2)]
        edges = [(0, 1), (1, 2), (2, 0), (0, 3)]
        report = prepare.joint_report(verts, edges, (0, 0, 0), (4, 3, 0.2))
        self.assertFalse(report["upright"])
        self.assertEqual(report["bands"], [])


class PreparedSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-prepare-")
        self.old_root, self.old_work = layout.ROOT, layout.WORK
        layout.ROOT = self.tmp
        layout.WORK = os.path.join(self.tmp, "_autorig")
        self.model = os.path.join(self.tmp, "wolf")
        os.makedirs(self.model)
        self.raw = os.path.join(self.model, "wolf.obj")
        with open(self.raw, "w", encoding="utf-8") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    def tearDown(self):
        layout.ROOT, layout.WORK = self.old_root, self.old_work
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_report(self, output="wolf.glb", mtime=None, source_mtime=None):
        folder = os.path.join(self.model, "prepared")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, output)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("copy\n")
        report = {"output": output, "source_mtime": os.path.getmtime(self.raw) if source_mtime is None else source_mtime}
        with open(os.path.join(folder, "prepare.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh)
        return path

    def test_current_copy_wins_and_a_replaced_source_does_not(self):
        made = self._write_report()
        self.assertEqual(os.path.normcase(layout.source_model("wolf")), os.path.normcase(made))
        self.assertTrue(layout.is_prepared(made))
        self.assertEqual(os.path.normcase(layout.raw_source("wolf")), os.path.normcase(self.raw))

        stale = self._write_report(source_mtime=0)
        # source_mtime 0 is older than the raw file, so the raw export is read.
        self.assertEqual(os.path.normcase(layout.source_model("wolf")), os.path.normcase(self.raw))
        self.assertTrue(os.path.isfile(stale))

    def test_a_report_cannot_point_outside_prepared(self):
        folder = os.path.join(self.model, "prepared")
        os.makedirs(folder)
        with open(os.path.join(folder, "prepare.json"), "w", encoding="utf-8") as fh:
            json.dump({"output": "../wolf.obj", "source_mtime": os.path.getmtime(self.raw)}, fh)
        self.assertEqual(os.path.normcase(layout.source_model("wolf")), os.path.normcase(self.raw))
        self.assertFalse(layout.is_prepared(self.raw))


class ModelCardSourceTest(unittest.TestCase):
    """A model card names the source and the game files; a game export beside the download is never rigged."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-card-")
        self.old_root, self.old_work = layout.ROOT, layout.WORK
        layout.ROOT = self.tmp
        layout.WORK = os.path.join(self.tmp, "_autorig")
        self.model = os.path.join(self.tmp, "Ice_Tripo")
        os.makedirs(os.path.join(self.model, "IceAdult.fbm"))
        self.glb = self._touch("Ice_Tripo.glb")
        self.game = self._touch("IceAdult.fbx")   # textured: its .fbm folder is beside it

    def tearDown(self):
        layout.ROOT, layout.WORK = self.old_root, self.old_work
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.model, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        return path

    def _card(self, **fields):
        with open(os.path.join(self.model, "model.json"), "w", encoding="utf-8") as fh:
            json.dump(fields, fh)

    def same(self, a, b):
        self.assertEqual(os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b)))

    def test_without_a_card_the_textured_game_file_wins_as_before(self):
        self.same(layout.raw_source("Ice_Tripo"), self.game)

    def test_the_cards_model_file_is_the_source(self):
        self._card(model_file="Ice_Tripo.glb", game_files=["IceAdult.fbx", "IceAdult.fbm/"])
        self.same(layout.raw_source("Ice_Tripo"), self.glb)

    def test_game_files_are_never_a_source(self):
        self._card(game_files=["IceAdult.fbx", "IceAdult.fbm/"])
        self.same(layout.raw_source("Ice_Tripo"), self.glb)

    def test_a_missing_or_outside_model_file_falls_back(self):
        self._card(model_file="../elsewhere.glb", game_files=["IceAdult.fbx"])
        self.same(layout.raw_source("Ice_Tripo"), self.glb)
        self._card(model_file="gone.glb", game_files=["IceAdult.fbx"])
        self.same(layout.raw_source("Ice_Tripo"), self.glb)


if __name__ == "__main__":
    unittest.main()

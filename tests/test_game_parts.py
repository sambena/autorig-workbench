# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: tests for core/game_parts_spec.py, the plain-Python half of steps/game_parts.py (no Blender needed).
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import game_parts_spec as spec

SPECS = os.path.join(REPO, "autorig", "specs", "tornado_titans_parts.json")


class TestGameParts(unittest.TestCase):
    def test_components_join_faces_that_share_a_vertex(self):
        # two triangles sharing an edge, one alone, and a vertex no face uses
        labels = spec.components(8, [(0, 1, 2), (1, 2, 3), (4, 5, 6)])
        self.assertEqual(labels[0], labels[3])
        self.assertNotEqual(labels[0], labels[4])
        self.assertEqual(labels[4], labels[6])
        self.assertEqual(len(set(labels)), 3)
        self.assertEqual(labels[:5], [0, 0, 0, 0, 1])   # numbered in order of first vertex

    def test_box_and_exclude(self):
        p = spec.part({"name": "Beam", "box": {"min": [-1, -4, 5], "max": [1, 4, 7]},
                       "exclude": [{"box": {"min": [-1, -0.5, 5], "max": [1, 0.5, 5.9]}}]}, "t")
        self.assertTrue(spec.takes(p, (0, 2, 6)))
        self.assertFalse(spec.takes(p, (0, 0, 5.5)))      # the samson post's cap stays
        self.assertTrue(spec.takes(p, (0, 0, 6.1)))        # the beam above it goes
        self.assertFalse(spec.takes(p, (0, 2, 4)))

    def test_disc_radius_and_slab(self):
        p = spec.part({"name": "Fan", "disc": {"centre": [0, -1.4, 7.6], "axis": "y", "radius": 2.5, "slab": [-0.4, 0.3]}}, "t")
        self.assertTrue(spec.takes(p, (2.0, -1.4, 7.6)))
        self.assertFalse(spec.takes(p, (2.6, -1.4, 7.6)))   # past the rim
        self.assertTrue(spec.takes(p, (0, -1.75, 7.6)))
        self.assertFalse(spec.takes(p, (0, -0.9, 7.6)))     # behind the slab: the gearbox, the tail vane
        tilted = spec.part({"name": "Fan", "disc": {"centre": [0, 0, 0], "axis": [1, 1, 0], "radius": 1}}, "t")
        self.assertTrue(spec.takes(tilted, (0.3, 0.3, 0.5)))
        self.assertFalse(spec.takes(tilted, (0.6, 0.6, 0)))

    def test_first_part_wins_and_max_size(self):
        parts = [spec.part({"name": "A", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}, "max_size": [9, 9, 0.5]}, "t"),
                 spec.part({"name": "B", "box": {"min": [0, 0, 0], "max": [2, 2, 2]}}, "t")]
        owners = spec.assign(parts, [(0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.5, 1.5, 1.5), (5, 5, 5)],
                             [(0.1, 0.1, 0.1), (0.1, 0.1, 1.0), (0.1, 0.1, 0.1), (0.1, 0.1, 0.1)])
        self.assertEqual(owners, [0, 1, 1, -1])   # a long thin leg is not A's even with its centre inside

    def test_pivots(self):
        fixed = spec.part({"name": "Beam", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}, "pivot": [0, 0.75, 4.95]}, "t")
        self.assertEqual(spec.pivot(fixed, (0, 0, 0), (1, 1, 1)), (0.0, 0.75, 4.95))
        box = spec.part({"name": "Box", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}}, "t")
        self.assertEqual(spec.pivot(box, (0, 0, 0), (2, 4, 6)), (1, 2, 3))
        disc = spec.part({"name": "Fan", "disc": {"centre": [0, -1.4, 7.6], "axis": "y", "radius": 2.5}}, "t")
        # across the axis from the bounds; along it the disc's own centre
        for got, want in zip(spec.pivot(disc, (-2, -1.8, 5.2), (2.2, -0.8, 10.0)), (0.1, -1.4, 7.6)):
            self.assertAlmostEqual(got, want)

    def test_bad_specs_are_refused(self):
        with self.assertRaises(spec.SpecError):
            spec.parse({"M": {"parts": [{"name": "A"}]}})                       # no region
        with self.assertRaises(spec.SpecError):
            spec.parse({"M": {"parts": [{"name": "A", "box": {"min": [1, 0, 0], "max": [0, 1, 1]}}]}})
        with self.assertRaises(spec.SpecError):
            spec.parse({"M": {"parts": [{"name": "A", "disc": {"centre": [0, 0, 0], "radius": 0}}]}})
        with self.assertRaises(spec.SpecError):
            spec.parse({"M": {"parts": [{"name": "A", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}},
                                        {"name": "A", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}}]}})

    def test_tornado_titans_specs_load(self):
        specs = spec.load(SPECS)
        self.assertEqual(sorted(specs), ["Pumpjack", "Pumpjack_T", "Windpump", "Windpump_B", "Windpump_T"])
        self.assertEqual([p["name"] for p in specs["Pumpjack"]], ["Beam", "Rod", "Link"])
        self.assertEqual([p["name"] for p in specs["Pumpjack_T"]], ["Beam", "Rod", "Pitman"])
        for name in ("Windpump", "Windpump_B", "Windpump_T"):
            self.assertEqual([p["name"] for p in specs[name]], ["Fan"])
        # comments ride along as "_" keys
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"_comment": "x", "M": {"parts": [{"name": "A", "box": {"min": [0, 0, 0], "max": [1, 1, 1]}}]}}, f)
            self.assertEqual(list(spec.load(path)), ["M"])


if __name__ == "__main__":
    unittest.main()

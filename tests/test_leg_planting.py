# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: feet that step and plant (autorig/core/leg_planting.py, after Spider Walker).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import foot_contact
import leg_planting as lp


def quad():
    return [{"name": "fl", "side": 1.0, "along": 0.3, "is_front": True},
            {"name": "fr", "side": -1.0, "along": 0.3, "is_front": True},
            {"name": "hl", "side": 1.0, "along": -0.3, "is_front": False},
            {"name": "hr", "side": -1.0, "along": -0.3, "is_front": False}]


def hexapod():
    legs = []
    for side in (1.0, -1.0):
        for i, along in enumerate((0.3, 0.0, -0.3)):
            legs.append({"name": "%s%d" % ("L" if side > 0 else "R", i), "side": side, "along": along})
    return legs


REST = {"fl": (0.2, -0.3, 0.0), "fr": (-0.2, -0.3, 0.0), "hl": (0.2, 0.3, 0.0), "hr": (-0.2, 0.3, 0.0)}


class TestPhases(unittest.TestCase):
    def test_quadruped_lateral_sequence(self):
        p = lp.phases_for(quad(), "walk")
        self.assertEqual((p["hl"], p["fl"], p["hr"], p["fr"]), (0.0, 0.25, 0.5, 0.75))

    def test_trot_pairs_diagonals(self):
        p = lp.phases_for(quad(), "trot")
        self.assertEqual(p["fl"], p["hr"])
        self.assertEqual(p["fr"], p["hl"])
        self.assertNotEqual(p["fl"], p["fr"])

    def test_hexapod_tripod(self):
        p = lp.phases_for(hexapod(), "tripod")
        # L front, R middle, L back step together; the other three half a cycle later
        self.assertEqual(p["L0"], p["R1"])
        self.assertEqual(p["L0"], p["L2"])
        self.assertEqual(p["R0"], p["L1"])
        self.assertAlmostEqual(abs(p["L0"] - p["R0"]), 0.5)

    def test_round_robin_spreads_odd_counts(self):
        legs = hexapod() + [{"name": "X", "side": 1.0, "along": -0.6}, {"name": "Y", "side": -1.0, "along": -0.6}]
        p = lp.phases_for(legs, "walk")
        self.assertEqual(len(set(round(v, 6) for v in p.values())), 8)   # no two legs step together


class TestStepCycle(unittest.TestCase):
    def test_loops_and_plants(self):
        n, stride, duty = 24, 0.3, 0.75
        tracks = lp.step_cycle(REST, n, stride, duty, 0.08, lp.phases_for(quad(), "walk"))
        v = stride / (duty * n)
        for name, t in tracks.items():
            self.assertEqual(len(t), n)
            down = [f for f in range(n) if t[f][2] < 1e-9]
            self.assertGreaterEqual(len(down), int(duty * n) - 1)
            # planted frames move back with the ground, exactly
            for f in down:
                g = (f + 1) % n
                if g in down:
                    self.assertAlmostEqual(t[g][1] - t[f][1], v, places=9)
            # and the swing lifts
            self.assertGreater(max(p[2] for p in t), 0.07)
        # the measure agrees: on its own ground the walk barely skates
        r = foot_contact.foot_skate(tracks, 1.0, loop=True)
        self.assertLess(max(r["per_foot"].values()), 1.2)    # the score sums the feet: about 1 each, from liftoff and touchdown
        self.assertAlmostEqual(r["ground_speed"], v * 30, places=4)

    def test_stance_is_centred_under_the_hip(self):
        tracks = lp.step_cycle(REST, 24, 0.3, 0.75, 0.08, {k: 0.0 for k in REST})
        ys = [p[1] for p in tracks["fl"] if p[2] < 1e-9]
        self.assertAlmostEqual((max(ys) + min(ys)) / 2, REST["fl"][1], delta=0.3 / 18 + 1e-9)
        self.assertLess(min(ys), REST["fl"][1])           # lands ahead (-Y is forward)

    def test_raycast_lands_on_a_step(self):
        def stairs(origin, direction):
            h = 0.1 if origin[1] < -0.35 else 0.0          # a ledge ahead of the front feet
            return (origin[0], origin[1], h), (0.0, 0.0, 1.0)
        tracks = lp.step_cycle(REST, 24, 0.3, 0.75, 0.08, lp.phases_for(quad(), "walk"), raycast=stairs)
        self.assertTrue(any(abs(p[2] - 0.1) < 1e-9 for p in tracks["fl"]))

    def test_flat_ground(self):
        cast = lp.flat_ground(0.5)
        hit, normal = cast((1.0, 2.0, 3.0), (0.0, 0.0, -1.0))
        self.assertEqual(hit, (1.0, 2.0, 0.5))
        self.assertIsNone(cast((0, 0, 0), (0, 0, 1)))

    def test_smoothstep(self):
        self.assertEqual(lp.smoothstep(0), 0.0)
        self.assertEqual(lp.smoothstep(1), 1.0)
        self.assertAlmostEqual(lp.smoothstep(0.5), 0.5)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: foot contacts, foot skate and the leg re-solve (autorig/core/foot_contact.py, limb_ik.py).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import foot_contact as fc
import limb_ik


def walk_track(n=24, stride=0.3, duty=0.6, lift=0.1, phase=0.0, slip=0.0):
    """An in-place foot as Spider Walker steps one: in the ground's frame it stands still for `duty` of the cycle,
    then steps one cycle's travel forward (smoothstep along, a sine arc up). The ground moves back under the clip at
    stride / (duty n) a frame; `slip` makes the planted foot slide that much further each stance. -Y is forward."""
    v = stride / (duty * n)
    travel = v * n
    out = []
    for f in range(n):
        u = (f / float(n) + phase) % 1.0
        k = math.floor(f / float(n) + phase)
        base = -travel * k                                  # where this cycle's stance stands, in the ground's frame
        if u < duty:
            y = base + slip * (u / duty - 0.5)
            z = 0.0
        else:
            s = (u - duty) / (1.0 - duty)
            y = base - travel * s * s * (3 - 2 * s)
            z = lift * math.sin(math.pi * s)
        # back to the clip's frame: the ground has moved +v a frame; centre the stance under the hip
        out.append((0.0, y + v * f + travel * 0.5 * duty, z))
    return out


class TestContacts(unittest.TestCase):
    def test_alpha_eases_from_one_to_zero(self):
        self.assertEqual(fc.alpha(0.0), 1.0)
        self.assertEqual(fc.alpha(1.0), 0.0)
        self.assertAlmostEqual(fc.alpha(0.5), 0.5)

    def test_floor_ignores_a_jump(self):
        planted = [(0, 0, 0.01)] * 30 + [(0, 0, 0.5)] * 10
        self.assertAlmostEqual(fc.floor_height([planted]), 0.01)

    def test_continuous_filter_drops_flicker(self):
        c = [True] * 6 + [False] + [True] * 6 + [False] * 6
        self.assertEqual(fc.continuous_filter(c, 2), [True] * 13 + [False] * 6)

    def test_continuous_filter_wraps_a_loop(self):
        c = [False] + [True] * 8 + [False] * 6 + [True] * 5
        out = fc.continuous_filter(c, 2, loop=True)
        self.assertTrue(out[0])                         # the one-frame lift at the seam is a flicker in the loop

    def test_contact_runs_wrap(self):
        c = [True, True, False, False, True]
        self.assertEqual(fc.contact_runs(c, loop=True), [(4, 1)])
        self.assertEqual(fc.contact_runs(c, loop=False), [(0, 1), (4, 4)])

    def test_detect_low_and_slow(self):
        t = [(0, 0, 0)] * 5 + [(0, 0.1 * i, 0.2) for i in range(5)]
        c = fc.detect_contacts(t, 0.0, 0.04, 0.01, filter_length=0)
        self.assertEqual(c[:5], [True] * 5)
        self.assertFalse(any(c[6:]))


class TestSkate(unittest.TestCase):
    def test_planted_walk_on_its_ground_does_not_skate(self):
        feet = {"L": walk_track(), "R": walk_track(phase=0.5)}
        r = fc.foot_skate(feet, 1.0, loop=True)
        self.assertLess(r["score"], 1.5)                   # what is left is the liftoff and touchdown frames
        self.assertGreater(r["ground_speed"], 0.0)        # the in-place walk's ground was found

    def test_still_ground_counts_a_treadmill_foot(self):
        feet = {"L": walk_track(), "R": walk_track(phase=0.5)}
        r = fc.foot_skate(feet, 1.0, loop=True, velocity=(0.0, 0.0, 0.0))
        self.assertGreater(r["score"], 5.0)

    def test_sliding_foot_scores_higher(self):
        good = fc.foot_skate({"L": walk_track(), "R": walk_track(phase=0.5)}, 1.0, loop=True)
        bad = fc.foot_skate({"L": walk_track(), "R": walk_track(phase=0.5, slip=0.1)}, 1.0, loop=True)
        self.assertGreater(bad["score"], good["score"] + 1.0)
        self.assertEqual(bad["worst"], "R")

    def test_score_scales_with_model_size(self):
        t = [(0.002 * f, 0, 0) for f in range(30)]
        small = fc.foot_skate({"a": t}, 1.0, velocity=(0, 0, 0))["score"]
        big = fc.foot_skate({"a": [(x * 10, y, z) for x, y, z in t]}, 10.0, velocity=(0, 0, 0))["score"]
        self.assertAlmostEqual(small, big, places=3)

    def test_high_feet_do_not_count(self):
        t = [(0.01 * f, 0, 0.5) for f in range(30)]
        self.assertEqual(fc.foot_skate({"a": t}, 1.0, velocity=(0, 0, 0), floor=0.0)["score"], 0.0)

    def test_travels(self):
        self.assertTrue(fc.travels("walk"))
        self.assertTrue(fc.travels("gallop"))
        self.assertTrue(fc.travels("walk_v2"))
        self.assertFalse(fc.travels("idle"))
        self.assertFalse(fc.travels("walk_to_idle"))
        self.assertFalse(fc.travels("attack"))


class TestPins(unittest.TestCase):
    def test_pin_holds_the_mean_and_eases_out(self):
        t = [(0.001 * f, 0, 0) for f in range(10)] + [(0.01 + 0.05 * f, 0, 0.2) for f in range(10)]
        c = [True] * 10 + [False] * 10
        p = fc.pin_contacts(t, c, floor=0.0, blend=3)
        self.assertTrue(all(abs(q[0] - p[0][0]) < 1e-12 for q in p[:10]))
        self.assertAlmostEqual(p[0][0], 0.0045)
        self.assertNotEqual(p[10], t[10])                  # eased toward the pin
        self.assertEqual(p[15], t[15])                     # beyond the blend: untouched

    def test_plan_pins_removes_a_slide_on_still_ground(self):
        # an idle whose foot drifts a little while it is down
        t = [(0.003 * math.sin(f / 3.0), 0.002 * f % 0.01, 0.0) for f in range(24)]
        before = fc.foot_skate({"a": t}, 1.0, loop=True, velocity=(0, 0, 0))["score"]
        pins, contacts, v = fc.plan_pins({"a": t}, 1.0, loop=True, velocity=(0, 0, 0))
        after = fc.foot_skate(pins, 1.0, loop=True, velocity=(0, 0, 0))["score"]
        self.assertTrue(all(contacts["a"]))
        self.assertLess(after, 0.01)
        self.assertGreater(before, 1.0)

    def test_plan_pins_in_place_walk_stays_a_walk(self):
        feet = {"L": walk_track(slip=0.04), "R": walk_track(phase=0.5)}
        pins, contacts, v = fc.plan_pins(feet, 1.0, loop=True)
        self.assertGreater(v[1], 0.0)                      # planted feet move back (+Y): the ground does
        before = fc.foot_skate(feet, 1.0, loop=True, velocity=v)["score"]
        after = fc.foot_skate(pins, 1.0, loop=True, velocity=v)["score"]
        self.assertLess(after, before - 1.0)
        self.assertLess(after, 2.0)


class TestLimbIK(unittest.TestCase):
    def test_fabrik_reaches_and_keeps_lengths(self):
        leg = [(0, 0, 1.0), (0, -0.1, 0.5), (0, 0, 0)]
        out = limb_ik.fabrik(leg, (0, 0.2, 0.05))
        self.assertLess(math.dist(out[-1], (0, 0.2, 0.05)), 1e-4)
        self.assertEqual(out[0], leg[0])
        for i in range(2):
            self.assertAlmostEqual(math.dist(out[i], out[i + 1]), math.dist(leg[i], leg[i + 1]), places=6)

    def test_fabrik_keeps_the_knee_side(self):
        leg = [(0, 0, 1.0), (0, -0.1, 0.5), (0, 0, 0)]
        out = limb_ik.fabrik(leg, (0, 0, 0.2))
        self.assertLess(out[1][1], 0.0)                    # still bends forward (-Y), as it did

    def test_fabrik_out_of_reach_points_straight(self):
        leg = [(0, 0, 1.0), (0, 0, 0.5), (0, 0, 0)]
        out = limb_ik.fabrik(leg, (0, 0, -3))
        self.assertAlmostEqual(out[-1][2], 0.0)

    def test_rotation_between(self):
        w, x, y, z = limb_ik.rotation_between((1, 0, 0), (0, 1, 0))
        self.assertAlmostEqual(w, math.cos(math.pi / 4))
        self.assertAlmostEqual(z, math.sin(math.pi / 4))
        w, x, y, z = limb_ik.rotation_between((1, 0, 0), (-1, 0, 0))
        self.assertAlmostEqual(w, 0.0)


if __name__ == "__main__":
    unittest.main()

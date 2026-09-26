# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the engine contract every clips manifest follows (core/clip_contract.py), the step-error check
# the batch runner and pipeline use (core/blender.py), and gait params no preset carries (tail_wave).
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import blender
import clip_contract as cc
import gait


class ContractTest(unittest.TestCase):
    CRAWLER = ["idle", "walk", "attack", "attack_windup", "strike", "hit", "death", "jump_start", "dodge"]
    DRAGON = ["fly", "idle", "perch", "attack", "attack_inhale", "attack_windup", "strike", "hit", "death"]

    def test_slots(self):
        f = lambda n, names: cc.clip_fields(n, 1.0, names, 0.5833)["slot"]
        self.assertEqual([f(n, self.CRAWLER) for n in self.CRAWLER],
                         ["idle", "locomotion", "attack", "attack_windup", "attack_strike", "hit", "death", "extra", "extra"])
        self.assertEqual(f("fly", self.DRAGON), "locomotion")          # it hovers in idle, flies to travel
        self.assertEqual(f("fly", ["fly", "attack", "death"]), "idle")  # a flyer with no hover flies all the time
        self.assertEqual(f("attack_inhale", self.DRAGON), "attack")
        self.assertEqual(f("perch", self.DRAGON), "extra")

    def test_rate_follows_speed_for_feet_not_wings(self):
        self.assertTrue(cc.clip_fields("walk", 0.67, self.CRAWLER)["rateFollowsSpeed"])
        # the Dragon's wingbeat played at half rate when a game slowed its fly to its travel speed
        self.assertFalse(cc.clip_fields("fly", 1.0, self.DRAGON)["rateFollowsSpeed"])
        self.assertFalse(cc.clip_fields("idle", 1.0, self.DRAGON)["rateFollowsSpeed"])

    def test_wind_up_is_per_clip(self):
        # windUpEnd 0.5833 is 14 of the 24-frame attack's frames; attack_windup is all wind-up, strike none
        a = cc.clip_fields("attack", 1.0, self.CRAWLER, 14 / 24)
        self.assertEqual((a["windUpEnd"], a["windUpSeconds"]), (0.5833, 0.5833))
        long = cc.clip_fields("attack_inhale", 1.8, self.DRAGON, 0.5)
        self.assertEqual(long["windUpSeconds"], 0.9)                   # seconds follow the clip's own length
        w = cc.clip_fields("attack_windup", 0.5833, self.CRAWLER, 14 / 24)
        self.assertEqual((w["windUpEnd"], w["windUpSeconds"]), (1.0, 0.5833))
        self.assertEqual(cc.clip_fields("strike", 0.5, self.CRAWLER, 14 / 24)["windUpEnd"], 0.0)
        self.assertNotIn("windUpEnd", cc.clip_fields("walk", 0.67, self.CRAWLER, 14 / 24))

    def test_speed_only_on_locomotion(self):
        self.assertEqual(cc.clip_fields("walk", 0.67, self.CRAWLER, speed=1.23456)["speed"], 1.2346)
        self.assertNotIn("speed", cc.clip_fields("idle", 1.0, self.CRAWLER, speed=2.0))
        self.assertNotIn("speed", cc.clip_fields("walk", 0.67, self.CRAWLER, speed=None))
        h = cc.header()
        self.assertEqual(h["contract"], "autorig-clips-contract/1")
        self.assertIn("metres per second", h["speedUnits"])


class StepErrorTest(unittest.TestCase):
    def test_caught_errors(self):
        out = 'blah\nRERIG {"model": "biped", "kind": "placed", "error": "no fbx"}\nRERIG_DONE 0 of 1\n'
        self.assertIn("no fbx", blender.step_error(out))
        self.assertEqual(blender.step_error("RERIG_DONE 1 of 2\n"), "RERIG_DONE 1 of 2")
        self.assertIsNone(blender.step_error('RERIG {"model": "biped", "seconds": 3}\nRERIG_DONE 1 of 1\n'))
        self.assertIsNone(blender.step_error('DECIMATE {"model": "biped", "after": 1400}\nDECIMATE_DONE 1\n'))
        self.assertIsNone(blender.step_error('CLIPS {"model": "x", "notes": {"error": ""}}\n'))
        self.assertIsNone(blender.step_error(None))


class GaitOptionalParams(unittest.TestCase):
    def test_tail_wave_kept_and_clamped(self):
        self.assertNotIn("tail_wave", gait.merge_gait_params("natural", {}))
        self.assertEqual(gait.merge_gait_params("natural", {"tail_wave": 1.5})["tail_wave"], 1.5)
        self.assertEqual(gait.merge_gait_params("natural", {"tail_wave": 9})["tail_wave"], 3.0)
        self.assertNotIn("tail_wave", gait.merge_gait_params("natural", {"tail_wave": True}))


if __name__ == "__main__":
    unittest.main()

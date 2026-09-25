# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Unit tests for dual-segment twist bones (autorig/core/twist_bones.py).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import twist_bones


class TestTwistBones(unittest.TestCase):
    def test_identify_twist_pairs_mixamo_and_standard(self):
        bones = [
            "Hips", "Spine", "Spine1", "Neck", "Head",
            "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
            "RightShoulder", "RightArm", "RightForeArm", "RightHand",
            "LeftUpLeg", "LeftLeg", "LeftFoot",
            "RightUpLeg", "RightLeg", "RightFoot"
        ]
        pairs = twist_bones.identify_twist_pairs(bones)
        pair_bases = {p["base"] for p in pairs}

        # Must identify forearms, upper arms, and thighs
        self.assertIn("LeftForeArm", pair_bases)
        self.assertIn("RightForeArm", pair_bases)
        self.assertIn("LeftArm", pair_bases)
        self.assertIn("RightArm", pair_bases)
        self.assertIn("LeftUpLeg", pair_bases)
        self.assertIn("RightUpLeg", pair_bases)

        # Check driver mapping
        fa_l = next(p for p in pairs if p["base"] == "LeftForeArm")
        self.assertEqual(fa_l["driver"], "LeftHand")
        self.assertEqual(fa_l["twist"], "LeftForeArm_Twist")
        self.assertEqual(fa_l["type"], "forearm")

    def test_weight_split_conservation_and_falloff(self):
        # Weight must always sum to 1.0 (strict conservation)
        for t in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
            for ltype in ("forearm", "arm", "thigh"):
                base_f, twist_f = twist_bones.calculate_weight_split(t, ltype)
                self.assertAlmostEqual(base_f + twist_f, 1.0, places=6)
                self.assertGreaterEqual(base_f, 0.0)
                self.assertGreaterEqual(twist_f, 0.0)

        # At elbow (t=0.0), forearm twist fraction must be 0.0
        base_elbow, twist_elbow = twist_bones.calculate_weight_split(0.0, "forearm")
        self.assertEqual(twist_elbow, 0.0)
        self.assertEqual(base_elbow, 1.0)

        # Near wrist (t=0.95), forearm twist fraction reaches 50%
        base_wrist, twist_wrist = twist_bones.calculate_weight_split(0.95, "forearm")
        self.assertAlmostEqual(twist_wrist, 0.50, places=4)
        self.assertAlmostEqual(base_wrist, 0.50, places=4)

        # Monotonicity of forearm twist falloff
        prev_twist = -1.0
        for step in range(11):
            t = step / 10.0
            _, twist_f = twist_bones.calculate_weight_split(t, "forearm")
            self.assertGreaterEqual(twist_f, prev_twist)
            prev_twist = twist_f


if __name__ == "__main__":
    unittest.main()

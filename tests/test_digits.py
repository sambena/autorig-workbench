# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Unit tests for automated finger & digit articulation (autorig/core/digits.py).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import digits


class TestDigits(unittest.TestCase):
    def test_humanoid_digits_generation_and_counts(self):
        wrist = (0.35, 0.0, 0.8)
        knuckle = (0.42, 0.0, 0.8)
        tip = (0.48, 0.0, 0.8)

        # 5 fingers
        chains5 = digits.generate_humanoid_digits(wrist, knuckle, tip, side="Left", num_fingers=5)
        self.assertEqual(len(chains5), 5)
        fingers = [c["finger"] for c in chains5]
        self.assertEqual(fingers, ["Thumb", "Index", "Middle", "Ring", "Pinky"])

        # Each finger must have exactly 3 bones (proximal, intermediate, distal) and 4 points
        for c in chains5:
            self.assertEqual(len(c["bones"]), 3)
            self.assertEqual(len(c["points"]), 4)
            self.assertEqual(c["parent_bone"], "LeftHand")
            # Segments have strictly positive lengths
            for i in range(3):
                p0, p1 = c["points"][i], c["points"][i + 1]
                dist = math.dist(p0, p1)
                self.assertGreater(dist, 1e-4)

    def test_stylized_3_and_4_fingers(self):
        wrist = (0.35, 0.0, 0.8)
        knuckle = (0.42, 0.0, 0.8)
        tip = (0.48, 0.0, 0.8)

        chains3 = digits.generate_humanoid_digits(wrist, knuckle, tip, side="Right", num_fingers=3)
        self.assertEqual(len(chains3), 3)
        self.assertEqual([c["finger"] for c in chains3], ["Thumb", "Index", "Pinky"])

        chains4 = digits.generate_humanoid_digits(wrist, knuckle, tip, side="Right", num_fingers=4)
        self.assertEqual(len(chains4), 4)
        self.assertEqual([c["finger"] for c in chains4], ["Thumb", "Index", "Middle", "Pinky"])

    def test_paw_digits_generation(self):
        ankle = (0.15, 0.4, 0.2)
        foot = (0.15, 0.5, 0.05)
        toe = (0.15, 0.65, 0.0)

        paws = digits.generate_paw_digits(ankle, foot, toe, side="Left", num_claws=4)
        self.assertEqual(len(paws), 4)
        for p in paws:
            self.assertEqual(len(p["bones"]), 2)
            self.assertEqual(len(p["points"]), 3)

    def test_finger_curl_angles(self):
        relax = digits.compute_finger_curl_angles("relax")
        fist = digits.compute_finger_curl_angles("fist")
        splay = digits.compute_finger_curl_angles("splay")

        # In fist, curl pitch must be much higher than in relax
        self.assertGreater(fist["Index"][0], relax["Index"][0] + 40.0)
        self.assertGreater(fist["Middle"][0], relax["Middle"][0] + 40.0)

        # In splay, fingers spread outwards (pinky spread > 0, thumb spread < 0)
        self.assertGreater(splay["Pinky"][1], 15.0)
        self.assertLess(splay["Thumb"][1], -15.0)


if __name__ == "__main__":
    unittest.main()

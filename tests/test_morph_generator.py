# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Unit tests for procedural facial morph generator (autorig/core/morph_generator.py).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import morph_generator


class TestMorphGenerator(unittest.TestCase):
    def test_identify_facial_landmarks(self):
        # Create synthetic head vertices
        # Head joint at (0, 0, 1.6), H=0.25
        hx, hy, hz = 0.0, 0.0, 1.6
        H = 0.25

        verts = [
            # Left Eye: x > 0.08 H, z in [hz + 0.35H, hz + 0.60H], y < 0
            (0.03, -0.08, 1.6 + 0.45 * H),
            # Right Eye: x < -0.08 H, z in [hz + 0.35H, hz + 0.60H], y < 0
            (-0.03, -0.08, 1.6 + 0.45 * H),
            # Mouth center: |x| < 0.12 H, z in [hz + 0.05H, hz + 0.25H]
            (0.00, -0.09, 1.6 + 0.15 * H),
            # Mouth left corner
            (0.04, -0.08, 1.6 + 0.15 * H),
            # Mouth right corner
            (-0.04, -0.08, 1.6 + 0.15 * H),
            # Jaw / Chin: z in [-0.20 H, 0.05 H]
            (0.00, -0.07, 1.6 - 0.10 * H),
            # Back of head (should be ignored)
            (0.00, 0.12, 1.6 + 0.45 * H),
        ]

        landmarks = morph_generator.identify_facial_landmarks(verts, (hx, hy, hz), H)
        self.assertIn("eyes_L", landmarks)
        self.assertIn("eyes_R", landmarks)
        self.assertIn("mouth_center", landmarks)
        self.assertIn("jaw", landmarks)

        self.assertEqual(len(landmarks["eyes_L"]), 1)
        self.assertEqual(len(landmarks["eyes_R"]), 1)
        self.assertEqual(len(landmarks["mouth_center"]), 1)
        self.assertEqual(len(landmarks["jaw"]), 1)

    def test_compute_shape_key_deltas(self):
        hx, hy, hz = 0.0, 0.0, 1.6
        H = 0.25
        landmarks = {
            "eyes_L": [(0, 0.03, -0.08, 1.7)],
            "eyes_R": [(1, -0.03, -0.08, 1.7)],
            "mouth_center": [(2, 0.0, -0.09, 1.63)],
            "mouth_corner_L": [(3, 0.04, -0.08, 1.63)],
            "mouth_corner_R": [(4, -0.04, -0.08, 1.63)],
            "jaw": [(5, 0.0, -0.07, 1.57)],
            "head_length": H,
        }

        deltas = morph_generator.compute_shape_key_deltas(landmarks, 6)
        self.assertIn("eyeBlink_L", deltas)
        self.assertIn("eyeBlink_R", deltas)
        self.assertIn("jawOpen", deltas)
        self.assertIn("mouthSmile", deltas)
        self.assertIn("viseme_aa", deltas)

        # Eye blink delta should be negative along Z (eyelid closing downward)
        self.assertLess(deltas["eyeBlink_L"][0][2], 0.0)
        self.assertLess(deltas["eyeBlink_R"][1][2], 0.0)

        # Jaw open should be negative along Z (jaw dropping)
        self.assertLess(deltas["jawOpen"][5][2], 0.0)

        # Smile mouth corners should lift upward (+Z)
        self.assertGreater(deltas["mouthSmile"][3][2], 0.0)
        self.assertGreater(deltas["mouthSmile"][4][2], 0.0)


if __name__ == "__main__":
    unittest.main()

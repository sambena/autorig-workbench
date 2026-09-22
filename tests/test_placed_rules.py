# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for body-part rules and mathematical helpers in placed_rules.py.
import os, subprocess, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import blender

try:
    import numpy as np
    import placed_rules
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
    np = None
    placed_rules = None


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed in system Python (available in Blender)")
class TestPlacedRulesMath(unittest.TestCase):
    def test_compute_membrane_weights_gradient_and_normalization(self):
        # Two parallel spar bones: spar 0 along X=0, spar 1 along X=1
        spars = [
            (np.array([0.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
            (np.array([1.0, 0.0, 0.0]), np.array([1.0, 1.0, 0.0])),
        ]
        # Three test points: near spar 0, midway, and near spar 1
        coords = np.array([
            [0.1, 0.5, 0.0],
            [0.5, 0.5, 0.0],
            [0.9, 0.5, 0.0],
        ])
        W = placed_rules.compute_membrane_weights(coords, spars, exponent=2.0)
        self.assertEqual(W.shape, (3, 2))
        # Each row must sum to 1.0
        np.testing.assert_allclose(W.sum(axis=1), np.ones(3), rtol=1e-5)
        # Point 0 is near spar 0 -> spar 0 weight > spar 1 weight
        self.assertGreater(W[0, 0], W[0, 1])
        # Point 1 is midway -> equal weights
        self.assertAlmostEqual(W[1, 0], W[1, 1], places=4)
        # Point 2 is near spar 1 -> spar 1 weight > spar 0 weight
        self.assertGreater(W[2, 1], W[2, 0])

    def test_filter_part_weights_deny_and_allow(self):
        bone_names = ["arm_1.L", "arm_2.L", "wing_1.L", "wing_2.L", "spine_2"]
        # 2 vertices: vert 0 belongs to forelimb, vert 1 belongs to wing
        weights = np.array([
            [0.6, 0.2, 0.15, 0.0, 0.05],  # arm vertex with accidental wing bleed
            [0.05, 0.0, 0.7, 0.2, 0.05],  # wing vertex with slight arm bleed
        ])
        parts_rules = [
            {
                "name": "forelimb.L",
                "bones": ["arm_1.L", "arm_2.L"],
                "deny": ["wing_*"],
            },
            {
                "name": "wing.L",
                "bones": ["wing_1.L", "wing_2.L"],
                "deny": ["arm_*"],
            }
        ]
        cleaned = placed_rules.filter_part_weights(weights, bone_names, parts_rules)
        # Vertex 0: wing weights must be zeroed out
        self.assertEqual(cleaned[0, 2], 0.0)
        self.assertEqual(cleaned[0, 3], 0.0)
        self.assertAlmostEqual(cleaned[0].sum(), 1.0, places=5)
        # Vertex 1: arm weights must be zeroed out
        self.assertEqual(cleaned[1, 0], 0.0)
        self.assertEqual(cleaned[1, 1], 0.0)
        self.assertAlmostEqual(cleaned[1].sum(), 1.0, places=5)

    def test_compute_join_blend(self):
        joint_pos = np.array([0.0, 0.0, 0.0])
        child_dir = np.array([1.0, 0.0, 0.0])
        radius = 1.0
        coords = np.array([
            [-0.5, 0.0, 0.0],  # Behind joint along parent side
            [0.0, 0.0, 0.0],   # At joint
            [0.5, 0.0, 0.0],   # Along child side
            [2.0, 0.0, 0.0],   # Outside radius
        ])
        blend = placed_rules.compute_join_blend(coords, joint_pos, child_dir, radius, fade=0.4)
        self.assertEqual(len(blend), 4)
        # Behind joint -> factor near 0 (parent dominant)
        self.assertAlmostEqual(blend[0], 0.0, places=3)
        # Along child -> factor near 1 (child dominant)
        self.assertAlmostEqual(blend[2], 1.0, places=3)
        # Outside radius -> 0
        self.assertEqual(blend[3], 0.0)


if "bpy" not in sys.modules:
    class TestPlacedRulesUnderBlender(unittest.TestCase):
        @unittest.skipUnless(blender.find(required=False), "Blender not found")
        def test_run_math_under_blender(self):
            blender_bin = blender.find()
            script = os.path.abspath(__file__)
            r = subprocess.run([blender_bin, "-b", "--factory-startup", "--python", script, "--", "-v"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stderr + r.stdout)


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

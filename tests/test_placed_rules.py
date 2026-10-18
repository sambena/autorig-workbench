# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for body-part rules and mathematical helpers in placed_rules.py.
import os, subprocess, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]
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
    def test_limb_piece_weights_keeps_a_sleeve_on_its_arm(self):
        bones = ["Hips", "Spine2", "LeftArm", "LeftForeArm", "LeftUpLeg", "RightArm"]
        W = np.array([[0, 0, 0.6, 0.4, 0, 0],        # sleeve near the shoulder: all arm
                      [0.3, 0, 0.1, 0.3, 0.3, 0],    # sleeve hem by the hips: hips and a thigh crept in
                      [0.5, 0, 0, 0, 0.5, 0]], float)  # a hem vertex the arm lost altogether
        got, limbs = placed_rules.limb_piece_weights(W, bones, [np.array([0, 1, 2])])
        self.assertEqual(limbs, ["LeftArm"])
        np.testing.assert_allclose(got[1], [0, 0, 0.25, 0.75, 0, 0])
        np.testing.assert_allclose(got[2], [0, 0, 0, 1.0, 0, 0])   # to the forearm: a sleeve hangs off it
        np.testing.assert_allclose(got.sum(1), 1)

    def test_follow_weights_copies_the_surface_under_each_vertex(self):
        P = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0.1, 0.01, 0], [1.9, 0.01, 0]], float)
        W = np.array([[1, 0], [0.5, 0.5], [0, 1], [0.3, 0.7], [0.3, 0.7]], float)
        got = placed_rules.follow_weights(P, W, np.array([3, 4]), np.array([0, 1, 2]), k=1)
        np.testing.assert_allclose(got[3], [1, 0]); np.testing.assert_allclose(got[4], [0, 1])
        np.testing.assert_allclose(got[:3], W[:3])   # the body itself is untouched
        got = placed_rules.follow_weights(P, W, np.array([3]), np.array([0, 1, 2]), k=2)   # the mean of the 2 nearest
        np.testing.assert_allclose(got[3], [0.75, 0.25])

    def test_floating_pieces_finds_scraps_and_keeps_what_lies_on_the_body(self):
        # a body: a 1 m column of points; a rosary lying on it (5 mm off); two scraps 16 cm and 30 cm out
        body = [(0.1 * np.cos(a), 0.1 * np.sin(a), z) for z in np.linspace(0, 1, 41) for a in np.linspace(0, 6.2, 24)]
        rosary = [(0.105, 0.0, z) for z in np.linspace(0.4, 0.6, 9)]
        scrap = [(0.26 + 0.01 * k, 0.0, 0.7) for k in range(5)]
        far = [(-0.40, 0.0, 0.5 + 0.01 * k) for k in range(3)]
        co = body + rosary + scrap + far
        n0, n1, n2 = len(body), len(body) + len(rosary), len(body) + len(rosary) + len(scrap)
        islands = [list(range(n0)), list(range(n0, n1)), list(range(n1, n2)), list(range(n2, len(co)))]
        got = placed_rules.floating_pieces(co, islands, 0.05)
        self.assertEqual([sorted(p) for p, _ in got], [islands[3], islands[2]])   # farthest first; the rosary stays
        self.assertAlmostEqual(got[0][1], 0.30, delta=0.01)
        self.assertAlmostEqual(got[1][1], 0.16, delta=0.01)
        self.assertEqual(placed_rules.floating_pieces(co, islands[:2], 0.05), [])
        self.assertEqual(placed_rules.floating_pieces(co[:n0], islands[:1], 0.05), [])
        # a model of separate parts: a hand 40 cm out, but on a sleeve that touches the body, is not floating
        sleeve = [(0.12 + 0.02 * k, 0.0, 0.8) for k in range(16)]
        hand = [(0.44 + 0.01 * k, 0.0, 0.8) for k in range(4)]
        co2 = body + sleeve + hand
        isl2 = [list(range(n0)), list(range(n0, n0 + 16)), list(range(n0 + 16, len(co2)))]
        self.assertEqual(placed_rules.floating_pieces(co2, isl2, 0.05), [])
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


    def test_geodesic_skin_barrier_crotch_and_armpit(self):
        bone_names = ["LeftUpLeg", "RightUpLeg", "Spine", "LeftForeArm"]
        # Vert 0: left leg (X=0.2), Vert 1: right leg (X=-0.2), Vert 2: torso center (X=0.0)
        coords = np.array([
            [0.2, 0.0, 0.3],
            [-0.2, 0.0, 0.3],
            [0.0, 0.0, 0.5],
        ])
        weights = np.array([
            [0.7, 0.2, 0.1, 0.0],    # Left leg vert with RightUpLeg cross-bleed
            [0.2, 0.7, 0.1, 0.0],    # Right leg vert with LeftUpLeg cross-bleed
            [0.0, 0.0, 0.85, 0.15],  # Torso vert with LeftForeArm cross-bleed
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, sym_plane=0.0, crotch_threshold=0.04
        )
        self.assertEqual(cleaned.shape, (3, 4))
        # Opposite-leg cross bleed must be completely eliminated
        self.assertEqual(cleaned[0, 1], 0.0)  # No RightUpLeg on Left leg
        self.assertEqual(cleaned[1, 0], 0.0)  # No LeftUpLeg on Right leg
        # Distal arm bleed on torso must be eliminated
        self.assertEqual(cleaned[2, 3], 0.0)  # No LeftForeArm on Torso
        # All rows must remain normalized to 1.0
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(3), rtol=1e-5)

    def test_geodesic_skin_barrier_shoulder_isolation(self):
        bone_names = ["LeftShoulder", "RightShoulder", "Chest"]
        # Vert 0: left shoulder (X=0.25), Vert 1: right shoulder (X=-0.25)
        coords = np.array([
            [0.25, 0.0, 1.4],
            [-0.25, 0.0, 1.4],
        ])
        weights = np.array([
            [0.6, 0.2, 0.2],   # Left shoulder vert with RightShoulder cross-bleed
            [0.2, 0.6, 0.2],   # Right shoulder vert with LeftShoulder cross-bleed
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, sym_plane=0.0, crotch_threshold=0.04
        )
        # Contralateral shoulder bleed must be zeroed out
        self.assertEqual(cleaned[0, 1], 0.0)  # No RightShoulder on Left side
        self.assertEqual(cleaned[1, 0], 0.0)  # No LeftShoulder on Right side
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_geodesic_skin_barrier_distal_leg_height(self):
        bone_names = ["LeftUpLeg", "LeftFoot", "LeftToeBase"]
        bone_heads = {
            "LeftUpLeg": [0.1, 0.0, 0.9],
            "LeftFoot": [0.1, 0.0, 0.1],
            "LeftToeBase": [0.1, 0.0, 0.02],
        }
        # Vert 0: foot height (Z=0.05), Vert 1: thigh height (Z=0.8)
        coords = np.array([
            [0.1, 0.0, 0.05],
            [0.1, 0.0, 0.8],
        ])
        weights = np.array([
            [0.1, 0.6, 0.3],   # Foot vert
            [0.6, 0.3, 0.1],   # Thigh vert with foot and toe bleed
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.8
        )
        # Distal leg weights on thigh vertex (Z=0.8) must be eliminated
        self.assertEqual(cleaned[1, 1], 0.0)  # LeftFoot eliminated
        self.assertEqual(cleaned[1, 2], 0.0)  # LeftToeBase eliminated
        self.assertAlmostEqual(cleaned[1, 0], 1.0, places=5)
        # Foot vert retains distal weights
        self.assertGreater(cleaned[0, 1], 0.0)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_geodesic_skin_barrier_arm_neck_isolation(self):
        bone_names = ["Neck", "Head", "LeftArm", "LeftForeArm"]
        bone_heads = {
            "Neck": [0.0, 0.0, 1.4],
            "Head": [0.0, 0.0, 1.55],
            "LeftArm": [0.2, 0.0, 1.4],
            "LeftForeArm": [0.4, 0.0, 1.4],
        }
        # Vert 0: neck center (X=0.02, Z=1.45), Vert 1: arm (X=0.3, Z=1.4)
        coords = np.array([
            [0.02, 0.0, 1.45],
            [0.30, 0.0, 1.40],
        ])
        weights = np.array([
            [0.5, 0.1, 0.25, 0.15],  # Neck vert with arm bleed
            [0.0, 0.0, 0.70, 0.30],  # Arm vert
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.8
        )
        # Arm weights on central neck vert must be eliminated
        self.assertEqual(cleaned[0, 2], 0.0)  # LeftArm eliminated
        self.assertEqual(cleaned[0, 3], 0.0)  # LeftForeArm eliminated
        self.assertAlmostEqual(cleaned[0, 0] + cleaned[0, 1], 1.0, places=5)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_geodesic_skin_barrier_crotch_transfer_to_hips(self):
        bone_names = ["Hips", "LeftUpLeg", "RightUpLeg"]
        # Vert 0 is slightly left of midline (X=0.02, crotch_threshold=0.04)
        coords = np.array([
            [0.02, 0.0, 0.5],
        ])
        weights = np.array([
            [0.2, 0.4, 0.4],  # Hips: 0.2, LeftUpLeg: 0.4, RightUpLeg: 0.4
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, sym_plane=0.0, crotch_threshold=0.04
        )
        # RightUpLeg is faded because X > 0; freed weight must be transferred to Hips
        self.assertLess(cleaned[0, 2], 0.4)
        self.assertGreater(cleaned[0, 0], 0.2)  # Hips received the freed weight
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(1), rtol=1e-5)

    def test_geodesic_skin_barrier_arm_vertical_isolation(self):
        bone_names = ["Hips", "LeftArm", "LeftUpLeg"]
        bone_heads = {
            "Hips": [0.0, 0.0, 0.5],
            "LeftArm": [0.25, 0.0, 0.8],
            "LeftUpLeg": [0.1, 0.0, 0.45],
        }
        # Vert 0: low on thigh (Z=0.3, X=0.15) with LeftArm bleed
        coords = np.array([
            [0.15, 0.0, 0.3],
        ])
        weights = np.array([
            [0.1, 0.4, 0.5],
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        # LeftArm must be 0 on thigh; transferred to LeftUpLeg
        self.assertEqual(cleaned[0, 1], 0.0)
        self.assertGreater(cleaned[0, 2], 0.5)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(1), rtol=1e-5)

    def test_geodesic_skin_barrier_shoulder_containment_and_head_routing(self):
        bone_names = ["LeftShoulder", "Neck", "Head", "Hips"]
        bone_heads = {
            "Hips": [0.0, 0.0, 0.5],
            "Neck": [0.0, 0.0, 0.80],
            "Head": [0.0, 0.0, 0.88],
            "LeftShoulder": [0.12, 0.0, 0.78],
        }
        # Vert 0: neck zone (Z=0.81, X=0.01) with shoulder bleed -> routes to Neck
        # Vert 1: upper head (Z=0.92, X=0.01) with shoulder bleed -> routes to Head
        coords = np.array([
            [0.01, 0.0, 0.81],
            [0.01, 0.0, 0.92],
        ])
        weights = np.array([
            [0.5, 0.3, 0.2, 0.0],
            [0.6, 0.0, 0.4, 0.0],
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        self.assertEqual(cleaned[0, 0], 0.0)  # Shoulder eliminated
        self.assertEqual(cleaned[1, 0], 0.0)  # Shoulder eliminated
        self.assertGreater(cleaned[0, 1], 0.3)  # Transferred to Neck
        self.assertGreater(cleaned[1, 2], 0.4)  # Transferred to Head
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_geodesic_skin_barrier_upper_torso_to_leg(self):
        bone_names = ["Hips", "Spine1", "LeftUpLeg"]
        bone_heads = {"Hips": [0.0, 0.0, 0.5]}
        # Vert on thigh (Z=0.4, X=0.15) with Spine1 bleed
        coords = np.array([[0.15, 0.0, 0.4]])
        weights = np.array([[0.2, 0.3, 0.5]])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        self.assertEqual(cleaned[0, 1], 0.0)  # Spine1 zeroed on leg
        self.assertAlmostEqual(cleaned[0, 0], 0.5, places=5)  # Spine1 transferred to Hips
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(1), rtol=1e-5)

    def test_geodesic_skin_barrier_appendage_isolation(self):
        bone_names = ["Hips", "LeftEar", "RightEar"]
        bone_heads = {
            "Hips": [0.0, 0.0, 0.5],
            "LeftEar": [0.15, 0.0, 0.95],
            "RightEar": [-0.15, 0.0, 0.95],
        }
        # Vert on body torso (Z=0.55, X=0.05), dist from LeftEar > 0.28 * 1.0
        coords = np.array([[0.05, 0.0, 0.55]])
        weights = np.array([[0.7, 0.3, 0.0]])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        self.assertEqual(cleaned[0, 1], 0.0)  # LeftEar zeroed on torso
        self.assertAlmostEqual(cleaned[0, 0], 1.0, places=5)  # Transferred to Hips
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(1), rtol=1e-5)

    def test_geodesic_skin_barrier_arm_leg_separation_low_hands(self):
        bone_names = ["Hips", "LeftArm", "LeftForeArm", "LeftUpLeg"]
        bone_heads = {
            "Hips": [0.0, 0.0, 0.5],
            "LeftArm": [0.35, 0.0, 0.75],
            "LeftForeArm": [0.38, 0.0, 0.40],
            "LeftUpLeg": [0.12, 0.0, 0.45],
        }
        # Vert 0: low-hanging hand/wrist (Z=0.35, X=0.38) -> closer to ForeArm than UpLeg
        # Vert 1: outer thigh (Z=0.35, X=0.14) -> closer to UpLeg than ForeArm
        coords = np.array([
            [0.38, 0.0, 0.35],
            [0.14, 0.0, 0.35],
        ])
        weights = np.array([
            [0.0, 0.1, 0.6, 0.3],   # Hand vert with accidental leg bleed
            [0.1, 0.2, 0.2, 0.5],   # Thigh vert with accidental arm bleed
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        # Vert 0 is on hand: leg weights quenched, restored to arm
        self.assertEqual(cleaned[0, 3], 0.0)
        self.assertAlmostEqual(cleaned[0, 1] + cleaned[0, 2], 1.0, places=4)
        # Vert 1 is on thigh: arm weights quenched, restored to leg
        self.assertEqual(cleaned[1, 1], 0.0)
        self.assertEqual(cleaned[1, 2], 0.0)
        self.assertGreater(cleaned[1, 3], 0.5)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_geodesic_skin_barrier_head_paw_isolation(self):
        bone_names = ["Head", "Neck", "LeftUpLeg", "LeftFoot"]
        bone_heads = {
            "Head": [0.0, 0.0, 0.90],
            "Neck": [0.0, 0.0, 0.80],
            "LeftUpLeg": [0.15, 0.0, 0.45],
            "LeftFoot": [0.15, 0.0, 0.05],
        }
        # Vert 0: snout/head (Z=0.88, X=0.0) with accidental LeftFoot bleed
        # Vert 1: paw/foot (Z=0.04, X=0.15) with accidental Head bleed
        coords = np.array([
            [0.0, 0.0, 0.88],
            [0.15, 0.0, 0.04],
        ])
        weights = np.array([
            [0.6, 0.1, 0.0, 0.3],  # Head with paw bleed
            [0.3, 0.0, 0.1, 0.6],  # Paw with head bleed
        ])
        cleaned = placed_rules.apply_geodesic_skin_barrier(
            weights, coords, bone_names, bone_heads=bone_heads, sym_plane=0.0, height_span=1.0
        )
        # Foot weights on head vert must be eliminated
        self.assertEqual(cleaned[0, 3], 0.0)
        self.assertGreater(cleaned[0, 0], 0.6)
        # Head weights on paw vert must be eliminated
        self.assertEqual(cleaned[1, 0], 0.0)
        self.assertGreater(cleaned[1, 3], 0.6)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_compute_sibling_appendage_isolation(self):
        bone_names = ["tentacle1_base.L", "tentacle1_mid.L", "tentacle1_tip.L",
                      "tentacle2_base.L", "tentacle2_mid.L", "tentacle2_tip.L"]
        bone_heads = {
            "tentacle1_base.L": [0.2, 0.0, 1.0],
            "tentacle1_mid.L": [0.2, 0.3, 1.0],
            "tentacle1_tip.L": [0.2, 0.6, 1.0],
            "tentacle2_base.L": [0.4, 0.0, 1.0],
            "tentacle2_mid.L": [0.4, 0.3, 1.0],
            "tentacle2_tip.L": [0.4, 0.6, 1.0],
        }
        chains = [
            {"role": "tentacle", "bones": ["tentacle1_base.L", "tentacle1_mid.L", "tentacle1_tip.L"]},
            {"role": "tentacle", "bones": ["tentacle2_base.L", "tentacle2_mid.L", "tentacle2_tip.L"]},
        ]
        # Vert 0: on tentacle 1 distal segment (X=0.2, Y=0.5) with tentacle 2 bleed
        # Vert 1: on tentacle 2 distal segment (X=0.4, Y=0.5) with tentacle 1 bleed
        coords = np.array([
            [0.21, 0.5, 1.0],
            [0.39, 0.5, 1.0],
        ])
        weights = np.array([
            [0.0, 0.5, 0.2, 0.0, 0.2, 0.1],  # tentacle 1 with tentacle 2 bleed
            [0.0, 0.2, 0.1, 0.0, 0.5, 0.2],  # tentacle 2 with tentacle 1 bleed
        ])
        cleaned = placed_rules.compute_sibling_appendage_isolation(
            weights, coords, bone_names, bone_heads=bone_heads, chains=chains
        )
        # Vert 0: tentacle 2 distal weights must be zeroed
        self.assertEqual(cleaned[0, 4], 0.0)
        self.assertEqual(cleaned[0, 5], 0.0)
        self.assertAlmostEqual(cleaned[0, 1] + cleaned[0, 2], 1.0, places=4)

        # Vert 1: tentacle 1 distal weights must be zeroed
        self.assertEqual(cleaned[1, 1], 0.0)
        self.assertEqual(cleaned[1, 2], 0.0)
        self.assertAlmostEqual(cleaned[1, 4] + cleaned[1, 5], 1.0, places=4)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_compute_closed_loop_laplacian_healing(self):
        # 3 vertices connected in a line: 0 -- 1 -- 2
        # Vertex 0 and 1 have a steep weight step: w[0] = [1.0, 0.0], w[1] = [0.0, 1.0] (diff = 1.0 > 0.20)
        coords = np.array([
            [0.0, 0.0, 0.0],
            [0.1, 0.0, 0.0],
            [0.2, 0.0, 0.0],
        ])
        weights = np.array([
            [1.0, 0.0],
            [0.0, 1.0],
            [0.0, 1.0],
        ])
        edges = [(0, 1), (1, 2)]
        healed, count = placed_rules.compute_closed_loop_laplacian_healing(
            weights, coords, edges, max_gradient=0.20, passes=6, blend_factor=0.5
        )
        self.assertGreater(count, 0)
        # After healing, the maximum gradient across edge (0, 1) must be significantly reduced
        max_diff = float(np.max(np.abs(healed[0] - healed[1])))
        self.assertLess(max_diff, 0.50)
        np.testing.assert_allclose(healed.sum(axis=1), np.ones(3), rtol=1e-5)

    def test_compute_hinge_laplacian_smoothing(self):
        bone_names = ["LeftArm", "LeftForeArm", "Spine"]
        hinge_pairs = [("LeftArm", "LeftForeArm")]
        bone_heads = {
            "LeftArm": [0.2, 0.0, 1.4],
            "LeftForeArm": [0.4, 0.0, 1.4],  # Elbow joint at (0.4, 0.0, 1.4)
        }
        bone_tails = {
            "LeftArm": [0.4, 0.0, 1.4],
            "LeftForeArm": [0.6, 0.0, 1.4],
        }
        # 4 vertices:
        # v0: slightly left of elbow (0.38, 0, 1.4) with mostly LeftArm
        # v1: slightly right of elbow (0.42, 0, 1.4) with mostly LeftForeArm (steep cliff with v0)
        # v2: far along forearm (0.58, 0, 1.4)
        # v3: far on body (0.0, 0, 1.0) - outside hinge zone
        coords = np.array([
            [0.38, 0.0, 1.4],
            [0.42, 0.0, 1.4],
            [0.58, 0.0, 1.4],
            [0.0, 0.0, 1.0],
        ])
        edges = np.array([
            [0, 1],  # Edge spanning across the elbow hinge with 0.8 weight cliff
            [1, 2],  # Edge along forearm
        ])
        weights = np.array([
            [0.9, 0.1, 0.0],  # v0: LeftArm 0.9, LeftForeArm 0.1
            [0.1, 0.9, 0.0],  # v1: LeftArm 0.1, LeftForeArm 0.9 -> initial gradient = 0.8!
            [0.0, 1.0, 0.0],  # v2: LeftForeArm 1.0
            [0.0, 0.0, 1.0],  # v3: Spine 1.0 (outside zone)
        ])

        smoothed = placed_rules.compute_hinge_laplacian_smoothing(
            weights, coords, edges, hinge_pairs, bone_names,
            bone_heads=bone_heads, bone_tails=bone_tails,
            passes=8, max_gradient=0.30, radius_scale=0.6, model_size=[1.0, 1.0, 2.0]
        )

        # Gradient across edge (0, 1) must be relaxed significantly
        initial_grad = abs(weights[0, 1] - weights[1, 1])
        final_grad = abs(smoothed[0, 1] - smoothed[1, 1])
        self.assertLess(final_grad, initial_grad)
        self.assertLessEqual(final_grad, 0.45)

        # Outside vertex (v3) must remain 100% untouched
        self.assertEqual(smoothed[3, 2], 1.0)
        self.assertEqual(smoothed[3, 0], 0.0)
        self.assertEqual(smoothed[3, 1], 0.0)

        # All rows must remain normalized to 1.0
        np.testing.assert_allclose(smoothed.sum(axis=1), np.ones(4), rtol=1e-5)


    def test_find_nearest_bone_segment(self):
        # Two bone segments:
        # spine: from (0, 0, 0) to (0, 0, 1)
        # arm.L: from (0, 0, 1) to (1, 0, 1)
        segments = [
            ("spine", np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])),
            ("arm.L", np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 1.0])),
        ]
        # Point A (0.8, 0.1, 1.0) is closest to arm.L
        bone, dist = placed_rules.find_nearest_bone_segment([0.8, 0.1, 1.0], segments)
        self.assertEqual(bone, "arm.L")
        self.assertAlmostEqual(dist, 0.1, places=5)

        # Point B (0.05, 0.0, 0.4) is closest to spine
        bone, dist = placed_rules.find_nearest_bone_segment([0.05, 0.0, 0.4], segments)
        self.assertEqual(bone, "spine")
        self.assertAlmostEqual(dist, 0.05, places=5)

    def test_bind_rigid_armor_islands(self):
        # 4 vertices, 3 bones (bones 0, 1, 2)
        weights = np.array([
            [0.5, 0.5, 0.0],
            [0.4, 0.6, 0.0],
            [0.1, 0.2, 0.7],
            [0.0, 0.0, 1.0],
        ])
        # Island A (verts 0, 1) bound 100% to bone 1 (e.g. pauldron to shoulder)
        # Island B (vert 3) bound 100% to bone 0 (e.g. scabbard to hip)
        # Vert 2 is part of body mesh, not in any rigid island
        islands = [[0, 1], [3]]
        host_bones = [1, 0]
        bound = placed_rules.bind_rigid_armor_islands(weights, islands, host_bones)

        # Island A: 100% bone 1
        self.assertEqual(bound[0, 1], 1.0)
        self.assertEqual(bound[0, 0], 0.0)
        self.assertEqual(bound[1, 1], 1.0)
        self.assertEqual(bound[1, 0], 0.0)

        # Vert 2 untouched
        np.testing.assert_allclose(bound[2], [0.1, 0.2, 0.7])

        # Island B: 100% bone 0
        self.assertEqual(bound[3, 0], 1.0)
        self.assertEqual(bound[3, 2], 0.0)

    def test_compute_twist_shaft_relaxation(self):
        bone_names = ["Spine", "Spine1", "Spine2"]
        shaft_pairs = [("Spine", "Spine1"), ("Spine1", "Spine2")]
        bone_heads = {
            "Spine": [0.0, 0.0, 0.0],
            "Spine1": [0.0, 0.0, 1.0],
            "Spine2": [0.0, 0.0, 2.0],
        }
        bone_tails = {
            "Spine": [0.0, 0.0, 1.0],
            "Spine1": [0.0, 0.0, 2.0],
            "Spine2": [0.0, 0.0, 3.0],
        }
        # 3 vertices along spine shaft around joint Spine1 (Z=1.0)
        coords = np.array([
            [0.1, 0.0, 0.95],
            [0.1, 0.0, 1.05],
            [0.0, 0.0, 2.80],
        ])
        weights = np.array([
            [0.9, 0.1, 0.0],
            [0.1, 0.9, 0.0],
            [0.0, 0.0, 1.0],
        ])
        edges = np.array([
            [0, 1],  # steep step cliff of 0.8
        ])
        relaxed = placed_rules.compute_twist_shaft_relaxation(
            weights, coords, edges, shaft_pairs, bone_names,
            bone_heads=bone_heads, bone_tails=bone_tails,
            max_twist_gradient=0.25, passes=15
        )
        # Gradient across edge (0, 1) on Spine1 must be relaxed to <= 0.25
        diff = abs(relaxed[0, 1] - relaxed[1, 1])
        self.assertLessEqual(diff, 0.26)
        # Far vertex v2 untouched
        np.testing.assert_allclose(relaxed[2], [0.0, 0.0, 1.0])
        # Partition of unity preserved
        np.testing.assert_allclose(relaxed.sum(axis=1), np.ones(3), rtol=1e-5)

    def test_longitudinal_flank_barrier(self):
        bone_names = ["leg_front_1.L", "leg_hind_1.L", "hips", "spine_2"]
        bone_heads = {
            "leg_front_1.L": [0.3, -0.5, 0.5],
            "leg_hind_1.L": [0.3, 0.5, 0.5],
            "hips": [0.0, 0.4, 0.6],
            "spine_2": [0.0, -0.3, 0.6],
        }
        # Point 0: at Y=0.65 (rear flank/rump) with accidental front leg bleed
        # Point 1: at Y=-0.65 (chest/shoulder) with accidental hind leg bleed
        # Point 2: at Y=-0.45 (legitimate front leg vertex)
        coords = np.array([
            [0.25, 0.65, 0.4],
            [0.25, -0.65, 0.4],
            [0.3, -0.45, 0.3],
        ])
        weights = np.array([
            [0.4, 0.1, 0.5, 0.0],   # v0: front bleed on rear flank
            [0.1, 0.5, 0.0, 0.4],   # v1: hind bleed on chest
            [0.8, 0.0, 0.0, 0.2],   # v2: valid front leg vertex
        ])
        cleaned = placed_rules.apply_longitudinal_flank_barrier(weights, coords, bone_names, bone_heads=bone_heads)
        # v0: leg_front_1.L must be zeroed, transferred to hips
        self.assertEqual(cleaned[0, 0], 0.0)
        self.assertAlmostEqual(cleaned[0, 2], 0.9, places=5)
        # v1: leg_hind_1.L must be zeroed, transferred to spine_2 (chest)
        self.assertEqual(cleaned[1, 1], 0.0)
        self.assertAlmostEqual(cleaned[1, 3], 0.9, places=5)
        # v2: valid front leg vertex untouched
        self.assertAlmostEqual(cleaned[2, 0], 0.8, places=5)
        # Partition of unity preserved
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(3), rtol=1e-5)

    def test_tail_isolation_barrier(self):
        bone_names = ["tail_1", "tail_2", "leg_hind_1.L", "hips"]
        bone_heads = {
            "tail_1": [0.0, 0.6, 0.7],
            "tail_2": [0.0, 0.8, 0.75],
            "leg_hind_1.L": [0.25, 0.5, 0.5],
            "hips": [0.0, 0.4, 0.6],
        }
        # Point 0: thigh vertex (dominant on leg_hind_1.L) with accidental distal tail_2 bleed
        # Point 1: tail tip vertex (dominant on tail_2)
        coords = np.array([
            [0.22, 0.52, 0.45],
            [0.0, 0.85, 0.78],
        ])
        weights = np.array([
            [0.0, 0.35, 0.55, 0.10], # v0: thigh with 0.35 tail_2 weight
            [0.1, 0.90, 0.0, 0.0],   # v1: tail tip
        ])
        cleaned = placed_rules.apply_tail_isolation_barrier(weights, coords, bone_names, bone_heads=bone_heads)
        # v0: tail_2 must be zeroed, transferred to hips
        self.assertEqual(cleaned[0, 1], 0.0)
        self.assertAlmostEqual(cleaned[0, 3], 0.45, places=5)
        # v1: tail tip vertex untouched
        self.assertAlmostEqual(cleaned[1, 1], 0.90, places=5)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(2), rtol=1e-5)

    def test_radial_limb_sector_isolation(self):
        bone_names = ["leg1.L", "leg2.L", "leg3.L", "leg1.R", "leg2.R", "leg3.R", "body"]
        bone_heads = {
            "leg1.L": [0.4, 0.4, 0.3],    # front left
            "leg2.L": [0.5, 0.0, 0.3],    # mid left
            "leg3.L": [0.4, -0.4, 0.3],   # rear left
            "leg1.R": [-0.4, 0.4, 0.3],   # front right
            "leg2.R": [-0.5, 0.0, 0.3],   # mid right
            "leg3.R": [-0.4, -0.4, 0.3],  # rear right
            "body": [0.0, 0.0, 0.35],
        }
        # Point 0: near rear left leg (X=0.45, Y=-0.45), accidental bleed from front left leg1.L
        coords = np.array([
            [0.45, -0.45, 0.25],
        ])
        weights = np.array([
            [0.35, 0.1, 0.45, 0.0, 0.0, 0.0, 0.1], # leg1.L bleed of 0.35
        ])
        cleaned = placed_rules.apply_radial_limb_sector_isolation(weights, coords, bone_names, bone_heads=bone_heads, center_xy=(0.0, 0.0))
        # leg1.L must be zeroed and transferred to body
        self.assertEqual(cleaned[0, 0], 0.0)
        self.assertAlmostEqual(cleaned[0, 6], 0.45, places=5)
        np.testing.assert_allclose(cleaned.sum(axis=1), np.ones(1), rtol=1e-5)



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
else:
    import bpy
    from mathutils import Vector

    class TestRigidIslandsBlender(unittest.TestCase):
        def test_auto_isolate_disconnected_islands_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            # Create a main body cylinder
            bpy.ops.mesh.primitive_cylinder_add(vertices=64, depth=2.0, radius=0.3, location=(0, 0, 1.0))
            body = bpy.context.active_object
            # Create a separate floating pauldron armor box near X=0.8, Z=1.5
            bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.8, 0, 1.5))
            pauldron = bpy.context.active_object

            # Join pauldron into body mesh so it's a single mesh with 2 disconnected islands
            body.select_set(True)
            pauldron.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.join()
            mesh = body
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # Create armature with spine (0,0,0)->(0,0,1) and arm.L (0,0,1.5)->(1,0,1.5)
            arm_data = bpy.data.armatures.new("Armature")
            arm = bpy.data.objects.new("Armature", arm_data)
            bpy.context.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b1 = arm.data.edit_bones.new("spine")
            b1.head = (0, 0, 0); b1.tail = (0, 0, 1.0)
            b2 = arm.data.edit_bones.new("arm.L")
            b2.head = (0, 0, 1.5); b2.tail = (1.0, 0, 1.5)
            bpy.ops.object.mode_set(mode='OBJECT')

            # Parent with automatic weights (bone heat will bleed across gap)
            mesh.select_set(True)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.parent_set(type='ARMATURE_AUTO')

            # Run rigid_islands_pass with rigid_islands="auto"
            log = {}
            chains = [{"name": "arm.L", "role": "arm", "bones": ["arm.L"]}]
            spec = {"rigid_islands": "auto"}
            placed_rules.rigid_islands_pass(mesh, arm, chains, spec, Vector((2, 2, 2)), log)

            self.assertEqual(log.get("auto_rigid_islands"), 1)
            self.assertEqual(log.get("rigid_islands_assigned"), 1)

            # Check that pauldron vertices are 100% bound to arm.L
            vg_arm = mesh.vertex_groups.get("arm.L")
            vg_spine = mesh.vertex_groups.get("spine")
            for v in mesh.data.vertices:
                if v.co.x > 0.5:  # pauldron vertex
                    arm_weight = sum(g.weight for g in v.groups if g.group == vg_arm.index)
                    spine_weight = sum(g.weight for g in v.groups if g.group == vg_spine.index)
                    self.assertAlmostEqual(arm_weight, 1.0, places=4)
                    self.assertEqual(spine_weight, 0.0)

        def test_armor_and_accessories_spec_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, depth=2.0, radius=0.3, location=(0, 0, 1.0))
            body = bpy.context.active_object
            # Create a floating scabbard accessory near X=0.5, Z=0.6
            bpy.ops.mesh.primitive_cube_add(size=0.15, location=(0.5, 0, 0.6))
            scabbard = bpy.context.active_object

            body.select_set(True)
            scabbard.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.join()
            mesh = body
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            arm_data = bpy.data.armatures.new("Armature")
            arm = bpy.data.objects.new("Armature", arm_data)
            bpy.context.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b_hips = arm.data.edit_bones.new("Hips")
            b_hips.head = (0, 0, 0.5); b_hips.tail = (0, 0, 0.8)
            b_leg = arm.data.edit_bones.new("LeftUpLeg")
            b_leg.head = (0.2, 0, 0.5); b_leg.tail = (0.2, 0, 0.1)
            bpy.ops.object.mode_set(mode='OBJECT')

            mesh.select_set(True)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.parent_set(type='ARMATURE_AUTO')

            log = {}
            chains = [{"name": "spine", "role": "spine", "bones": ["Hips"]}]
            spec = {"armor": [{"bone": "Hips", "at": [0.75, 0.5, 0.3]}]}
            placed_rules.rigid_islands_pass(mesh, arm, chains, spec, Vector((2, 2, 2)), log)

            self.assertGreaterEqual(log.get("rigid_islands_assigned", 0), 1)
            vg_hips = mesh.vertex_groups.get("Hips")
            for v in mesh.data.vertices:
                if v.co.x > 0.4:
                    hips_weight = sum(g.weight for g in v.groups if g.group == vg_hips.index)
                    self.assertAlmostEqual(hips_weight, 1.0, places=4)

        def test_centerline_armor_pass_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            # Create a body cylinder centered at origin, height 2.0 (Z from 0 to 2)
            bpy.ops.mesh.primitive_cylinder_add(vertices=64, depth=2.0, radius=0.4, location=(0, 0, 1.0))
            body = bpy.context.active_object
            # Create a centerline armor skirt/fauld box centered at X=0, spanning X in [-0.15, 0.15], Z=0.9
            bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, -0.6, 0.9))
            skirt = bpy.context.active_object

            body.select_set(True)
            skirt.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.join()
            mesh = body
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # Armature with Hips and two legs
            arm_data = bpy.data.armatures.new("Armature")
            arm = bpy.data.objects.new("Armature", arm_data)
            bpy.context.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b_hips = arm.data.edit_bones.new("Hips")
            b_hips.head = (0, 0, 1.0); b_hips.tail = (0, 0, 1.3)

            b_lleg = arm.data.edit_bones.new("LeftUpLeg")
            b_lleg.parent = b_hips
            b_lleg.head = (0.2, 0, 1.0); b_lleg.tail = (0.2, 0, 0.4)

            b_rleg = arm.data.edit_bones.new("RightUpLeg")
            b_rleg.parent = b_hips
            b_rleg.head = (-0.2, 0, 1.0); b_rleg.tail = (-0.2, 0, 0.4)
            bpy.ops.object.mode_set(mode='OBJECT')

            # Parent with automatic weights
            mesh.select_set(True)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.parent_set(type='ARMATURE_AUTO')

            # Ensure Hips vertex group exists
            if not mesh.vertex_groups.get("Hips"):
                mesh.vertex_groups.new(name="Hips")

            log = {}
            chains = [
                {"name": "spine", "role": "spine", "bones": ["Hips"]},
                {"name": "leg.L", "role": "leg", "bones": ["LeftUpLeg"]},
                {"name": "leg.R", "role": "leg", "bones": ["RightUpLeg"]}
            ]
            placed_rules.centerline_armor_pass(mesh, arm, chains, {}, Vector((1.0, 1.0, 2.0)), log)

            self.assertGreaterEqual(log.get("centerline_islands_bound", 0), 1)

            # Centerline skirt piece (near Y=-0.6) must be 100% bound to Hips
            vg_hips = mesh.vertex_groups.get("Hips")
            vg_lleg = mesh.vertex_groups.get("LeftUpLeg")
            vg_rleg = mesh.vertex_groups.get("RightUpLeg")

            for v in mesh.data.vertices:
                if v.co.y < -0.45:  # skirt vertex
                    w_hips = sum(g.weight for g in v.groups if g.group == vg_hips.index)
                    w_lleg = sum(g.weight for g in v.groups if vg_lleg and g.group == vg_lleg.index)
                    w_rleg = sum(g.weight for g in v.groups if vg_rleg and g.group == vg_rleg.index)
                    self.assertAlmostEqual(w_hips, 1.0, places=4)
                    self.assertEqual(w_lleg, 0.0)
                    self.assertEqual(w_rleg, 0.0)

        def test_sibling_appendage_pass_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            # Create two cylinders side by side: cyl1 at X=0.2, cyl2 at X=0.6
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, depth=1.0, radius=0.1, location=(0.2, 0, 0.5))
            c1 = bpy.context.active_object
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, depth=1.0, radius=0.1, location=(0.6, 0, 0.5))
            c2 = bpy.context.active_object

            c1.select_set(True)
            c2.select_set(True)
            bpy.context.view_layer.objects.active = c1
            bpy.ops.object.join()
            mesh = c1
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            arm_data = bpy.data.armatures.new("Armature")
            arm = bpy.data.objects.new("Armature", arm_data)
            bpy.context.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b1_base = arm.data.edit_bones.new("tentacle1_base.L")
            b1_base.head = (0.2, 0, 0.0); b1_base.tail = (0.2, 0, 0.2)
            b1 = arm.data.edit_bones.new("tentacle1_tip.L")
            b1.head = (0.2, 0, 0.2); b1.tail = (0.2, 0, 0.8)
            b1.parent = b1_base

            b2_base = arm.data.edit_bones.new("tentacle2_base.L")
            b2_base.head = (0.6, 0, 0.0); b2_base.tail = (0.6, 0, 0.2)
            b2 = arm.data.edit_bones.new("tentacle2_tip.L")
            b2.head = (0.6, 0, 0.2); b2.tail = (0.6, 0, 0.8)
            b2.parent = b2_base
            bpy.ops.object.mode_set(mode='OBJECT')

            vg1 = mesh.vertex_groups.new(name="tentacle1_tip.L")
            vg2 = mesh.vertex_groups.new(name="tentacle2_tip.L")
            for v in mesh.data.vertices:
                if v.co.x < 0.4:
                    vg1.add([v.index], 0.8, 'REPLACE')
                    vg2.add([v.index], 0.2, 'REPLACE')  # accidental cross-bleed
                else:
                    vg1.add([v.index], 0.2, 'REPLACE')  # accidental cross-bleed
                    vg2.add([v.index], 0.8, 'REPLACE')

            log = {}
            chains = [
                {"role": "tentacle", "bones": ["tentacle1_base.L", "tentacle1_tip.L"]},
                {"role": "tentacle", "bones": ["tentacle2_base.L", "tentacle2_tip.L"]},
            ]
            placed_rules.sibling_appendage_pass(mesh, arm, chains, {}, Vector((1.0, 1.0, 1.0)), log)
            self.assertGreater(log.get("sibling_appendage_fixed_verts", 0), 0)

            # Check that cross-bleed has been eliminated
            for v in mesh.data.vertices:
                if v.co.x < 0.4:
                    w2 = sum(g.weight for g in v.groups if g.group == vg2.index)
                    self.assertEqual(w2, 0.0)
                else:
                    w1 = sum(g.weight for g in v.groups if g.group == vg1.index)
                    self.assertEqual(w1, 0.0)

        def test_closed_loop_healing_pass_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            # Create a subdivided plane with connected vertices
            bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=1.0)
            mesh = bpy.context.active_object

            arm_data = bpy.data.armatures.new("Armature")
            arm = bpy.data.objects.new("Armature", arm_data)
            bpy.context.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b1 = arm.data.edit_bones.new("Bone1")
            b1.head = (-0.25, 0, 0); b1.tail = (-0.25, 0, 0.5)
            b2 = arm.data.edit_bones.new("Bone2")
            b2.head = (0.25, 0, 0); b2.tail = (0.25, 0, 0.5)
            bpy.ops.object.mode_set(mode='OBJECT')

            vg1 = mesh.vertex_groups.new(name="Bone1")
            vg2 = mesh.vertex_groups.new(name="Bone2")
            # Step discontinuity: left half 100% Bone1, right half 100% Bone2 (steep cliff across centerline)
            for v in mesh.data.vertices:
                if v.co.x <= 0.0:
                    vg1.add([v.index], 1.0, 'REPLACE')
                else:
                    vg2.add([v.index], 1.0, 'REPLACE')

            log = {}
            spec = {"heal_max_gradient": 0.20, "heal_passes": 4, "heal_blend": 0.5}
            healed = placed_rules.closed_loop_healing_pass(mesh, arm, spec, Vector((1.0, 1.0, 1.0)), log)
            self.assertGreater(log.get("closed_loop_healed_verts", 0), 0)
            self.assertGreater(healed, 0)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed in system Python (available in Blender)")
class TestBarrierFadesNotCuts(unittest.TestCase):
    """The skin barrier's limits fade over a band: a hard line between two owners tears when the joint bends."""
    NAMES = ["Hips", "Spine", "Spine2", "Neck", "Head", "LeftShoulder", "RightShoulder", "LeftArm", "RightArm",
             "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg"]
    HEADS = {"Hips": [0, 0, 1.0], "Spine": [0, 0, 1.1], "Spine2": [0, 0, 1.3], "Neck": [0, 0, 1.5],
             "Head": [0, 0, 1.6], "LeftShoulder": [0.05, 0, 1.45], "RightShoulder": [-0.05, 0, 1.45],
             "LeftArm": [0.2, 0, 1.45], "RightArm": [-0.2, 0, 1.45], "LeftUpLeg": [0.1, 0, 0.95],
             "RightUpLeg": [-0.1, 0, 0.95], "LeftLeg": [0.1, 0, 0.5], "RightLeg": [-0.1, 0, 0.5]}

    def barrier(self, P, W, heads=None):
        return placed_rules.apply_geodesic_skin_barrier(W, P, self.NAMES, bone_heads=heads or self.HEADS,
                                                        height_span=1.8, plan="biped")

    def test_shoulder_leaves_the_neck_column_gradually(self):
        # a row of vertices at the neck's height, from the middle out to the shoulder, all the left shoulder's
        xs = np.linspace(0.0, 0.3, 61)
        P = np.stack([xs, np.zeros_like(xs), np.full_like(xs, 1.5)], axis=1)
        W = np.zeros((len(xs), len(self.NAMES))); W[:, self.NAMES.index("LeftShoulder")] = 1.0
        sh = self.barrier(P, W)[:, self.NAMES.index("LeftShoulder")]
        self.assertLess(sh[0], 0.05)                      # the neck column's middle is the neck's
        self.assertGreater(sh[-1], 0.95)                  # the shoulder keeps its own
        self.assertTrue(np.all(np.diff(sh) >= -1e-9))     # rising outwards, never back
        self.assertLess(np.abs(np.diff(sh)).max(), 0.1)   # no step anywhere: 5 mm apart, under a tenth

    def test_a_horizontal_torso_keeps_its_chest_below_hip_height(self):
        # a wyvern-like body: the chest starts at the hips' height and runs forward, level
        heads = dict(self.HEADS, Spine2=[0, -0.3, 1.0], Spine=[0, -0.1, 1.0])
        P = np.array([[0.15, -0.4, 1.02], [0.15, -0.4, 0.98]])
        W = np.zeros((2, len(self.NAMES))); W[:, self.NAMES.index("Spine2")] = 1.0
        out = self.barrier(P, W, heads)
        self.assertTrue(np.allclose(out[:, self.NAMES.index("Spine2")], 1.0))
        self.assertTrue(np.allclose(out[:, self.NAMES.index("Hips")], 0.0))

    def test_an_upright_torso_still_gives_its_chest_up_below_the_hips(self):
        P = np.array([[0.15, 0.0, 0.9]])
        W = np.zeros((1, len(self.NAMES))); W[0, self.NAMES.index("Spine2")] = 1.0
        out = self.barrier(P, W)
        self.assertAlmostEqual(out[0, self.NAMES.index("Spine2")], 0.0)

    def test_soft_nearest_shares_near_ties_and_keeps_clear_winners(self):
        w = placed_rules.soft_nearest(np.array([[0.10, 0.10], [0.10, 0.50], [0.10, 0.12]]), 0.05)
        self.assertTrue(np.allclose(w.sum(1), 1.0))
        self.assertTrue(np.allclose(w[0], [0.5, 0.5]))
        self.assertTrue(np.allclose(w[1], [1.0, 0.0]))
        self.assertTrue(0.5 < w[2, 0] < 0.8)
        self.assertTrue(np.allclose(placed_rules.soft_nearest(np.array([[0.2, 0.1]]), 0.0), [[0.0, 1.0]]))

    def test_freed_weight_follows_distance_when_the_vertex_has_only_a_trace(self):
        # a row of vertices across the seam between two torso bones; each holds a trace of torso weight whose ratio
        # flips halfway (0.01 of bone 0 against 0.002 of bone 1, then the other way round), and 0.9 to hand over
        n = 41
        x = np.linspace(-0.2, 0.2, n)
        D = np.stack([np.abs(x + 0.1), np.abs(x - 0.1)], axis=1)          # bone 0 at -0.1, bone 1 at +0.1
        T = np.where((x < 0)[:, None], [[0.010, 0.002]], [[0.002, 0.010]])
        add = placed_rules.share_freed(T, np.full(n, 0.9), D, 0.05)
        self.assertTrue(np.allclose(add.sum(1), 0.9))                     # all of it handed over
        total = T + add
        self.assertLess(np.abs(np.diff(total[:, 0])).max(), 0.15)         # no flip at the seam
        # what the vertex already has keeps its say: half its weight on bone 1 and a little to hand over stays there
        keep = placed_rules.share_freed(np.array([[0.0, 0.5]]), np.array([0.05]), np.array([[0.0, 1.0]]), 0.05)
        self.assertGreater(keep[0, 1], 0.04)

    def test_root_fade_is_never_shorter_than_a_torso_bone(self):
        # a limb whose first bone is 2 cm long, on a body whose torso bones are 30 cm: the fade spans the torso bone
        s = np.linspace(-0.05, 0.15, 201)
        w = placed_rules.root_fade(s, 0.02, 0.3)
        self.assertAlmostEqual(w[0], 0.0)
        self.assertAlmostEqual(w[-1], 1.0)
        self.assertLess(np.abs(np.diff(w)).max(), 0.02)                    # 1 mm apart, never a step
        self.assertTrue(np.all(np.diff(w) >= -1e-12))
        # a long first bone keeps its own length
        self.assertAlmostEqual(float(placed_rules.root_fade(np.array([0.5]), 1.5, 0.3)[0]),
                               float(placed_rules._smooth(-0.15, 0.5, np.array([0.5]))[0]))


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed in system Python (available in Blender)")
class TestFlankBarrierFades(unittest.TestCase):
    """The flank barrier hands the front limbs' weight off over a band across the middle of the body."""
    NAMES = ["hips", "spine_1", "chest", "leg_front.L", "leg_hind.L"]
    HEADS = {"hips": [0, 0.5, 0.5], "spine_1": [0, 0.0, 0.5], "chest": [0, -0.5, 0.5],
             "leg_front.L": [0.2, -0.5, 0.3], "leg_hind.L": [0.2, 0.5, 0.3]}
    TAILS = {"hips": [0, 0.0, 0.5], "spine_1": [0, -0.5, 0.5], "chest": [0, -0.7, 0.5],
             "leg_front.L": [0.2, -0.5, 0.0], "leg_hind.L": [0.2, 0.5, 0.0]}

    def flank(self, P, W, tails=True):
        return placed_rules.apply_longitudinal_flank_barrier(W, P, self.NAMES, bone_heads=self.HEADS,
                                                             front_bones=["leg_front.L"], hind_bones=["leg_hind.L"],
                                                             bone_tails=self.TAILS if tails else None)

    def test_front_weight_fades_out_along_the_body(self):
        ys = np.linspace(-0.5, 0.5, 101)                     # from the front legs to the hind legs, 1 cm apart
        P = np.stack([np.full_like(ys, 0.2), ys, np.full_like(ys, 0.4)], axis=1)
        W = np.zeros((len(ys), len(self.NAMES))); W[:, 3] = 0.5; W[:, 1] = 0.5
        front = self.flank(P, W)[:, 3]
        self.assertAlmostEqual(front[0], 0.5)                # the front leg keeps its own
        self.assertAlmostEqual(front[-1], 0.0)               # and none of the hindquarters
        self.assertTrue(np.all(np.diff(front) <= 1e-12))
        self.assertLess(np.abs(np.diff(front)).max(), 0.05)  # no step: the old rule dropped 0.5 to 0 at one line

    def test_freed_weight_goes_to_the_torso_bone_beside_it(self):
        P = np.array([[0.0, 0.45, 0.5]])                     # at the rump, on the hips bone
        W = np.zeros((1, len(self.NAMES))); W[0, 3] = 0.6; W[0, 0] = 0.4
        out = self.flank(P, W)
        self.assertAlmostEqual(out[0, 3], 0.0)
        self.assertGreater(out[0, 0], 0.95)                  # the hips, not the chest at the far end
        self.assertTrue(np.allclose(out.sum(1), 1.0))


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

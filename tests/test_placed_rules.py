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
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, depth=2.0, radius=0.3, location=(0, 0, 1.0))
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

        def test_centerline_armor_pass_blender(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            # Create a body cylinder centered at origin, height 2.0 (Z from 0 to 2)
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, depth=2.0, radius=0.4, location=(0, 0, 1.0))
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


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for skeleton suggestion heuristics (autorig/core/suggest.py).
import math, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "gui")]
import suggest
import spec_api


class TestSuggestHeuristics(unittest.TestCase):
    def test_pair_tips_symmetry(self):
        # Tips in 0..1 bounds: 1 snout, 1 tail, 2 pairs of legs
        tips = [
            [0.5, 0.12, 0.4],     # snout (centerline)
            [0.5, 0.88, 0.5],     # tail (centerline)
            [0.8, 0.3, 0.1],      # front left foot
            [0.2, 0.3, 0.1],      # front right foot
            [0.78, 0.75, 0.1],    # hind left foot
            [0.22, 0.75, 0.1],    # hind right foot
        ]
        centerline, pairs, unpaired = suggest.pair_tips(tips, sym_center=0.5)
        self.assertEqual(len(centerline), 2)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(len(unpaired), 0)

        # Centerline classification
        c_info = suggest.classify_centerline(centerline)
        self.assertIsNotNone(c_info["head"])
        self.assertEqual(c_info["head"][1], 0.12)
        self.assertIsNotNone(c_info["tail"])
        self.assertEqual(c_info["tail"][1], 0.88)

        # Limb classification
        limbs = suggest.classify_limbs(pairs)
        self.assertEqual(len(limbs["legs"]), 2)
        self.assertEqual(len(limbs["wings"]), 0)

    def test_propose_archetypes(self):
        # 4 leg pairs -> octopod
        octo_limbs = {"legs": [{}] * 4, "wings": []}
        arch, conf, _ = suggest.propose_archetype([1, 1, 0.5], octo_limbs, {})
        self.assertEqual(arch, "octopod")
        self.assertEqual(conf, "high")

        # 3 leg pairs -> hexapod
        hexa_limbs = {"legs": [{}] * 3, "wings": []}
        arch, conf, _ = suggest.propose_archetype([0.8, 1.2, 0.4], hexa_limbs, {})
        self.assertEqual(arch, "hexapod")
        self.assertEqual(conf, "high")

        # 2 leg pairs -> quadruped
        quad_limbs = {"legs": [{}] * 2, "wings": []}
        arch, conf, _ = suggest.propose_archetype([0.6, 1.4, 0.8], quad_limbs, {})
        self.assertEqual(arch, "quadruped")
        self.assertEqual(conf, "high")

        # 2 leg pairs + wings -> winged
        winged_limbs = {"legs": [{}] * 2, "wings": [{}]}
        arch, conf, _ = suggest.propose_archetype([1.5, 1.2, 0.8], winged_limbs, {})
        self.assertEqual(arch, "winged")
        self.assertEqual(conf, "high")

        # 1 leg pair + tall -> humanoid
        human_limbs = {"legs": [{}], "wings": []}
        arch, conf, _ = suggest.propose_archetype([0.5, 0.3, 1.8], human_limbs, {})
        self.assertEqual(arch, "humanoid")

        # 0 leg pairs + long Y -> serpent
        serp_limbs = {"legs": [], "wings": []}
        arch, conf, _ = suggest.propose_archetype([0.3, 2.5, 0.3], serp_limbs, {})
        self.assertEqual(arch, "serpent")

    def test_suggest_skeleton_produces_valid_placed_spec(self):
        # Dragon/winged tips
        tips = [
            [0.5, 0.1, 0.55],     # snout
            [0.5, 0.15, 0.3],     # jaw
            [0.85, 0.35, 0.85],   # left wing tip
            [0.15, 0.35, 0.85],   # right wing tip
            [0.75, 0.3, 0.15],    # front left foot
            [0.25, 0.3, 0.15],    # front right foot
            [0.78, 0.75, 0.15],   # hind left foot
            [0.22, 0.75, 0.15],   # hind right foot
            [0.5, 0.95, 0.45],    # tail tip
        ]
        res = suggest.suggest_skeleton("test_dragon", tips=tips, proportions=[1.8, 1.5, 1.0])
        self.assertEqual(res["archetype"], "winged")
        self.assertIn("rig", res)
        rig = res["rig"]
        self.assertEqual(rig["kind"], "placed")
        self.assertEqual(rig["skeleton"], "winged")
        self.assertIn("chains", rig)
        self.assertIn("jaw", rig)
        self.assertIn("membranes", rig)

        # Validate with spec_api.check
        full_spec = {"schema": "autorig-spec/1", "rig": rig}
        errs, warns = spec_api.check(full_spec)
        self.assertEqual(errs, [], f"Suggested spec had check errors: {errs}")

    def test_suggest_mixamo_source(self):
        source = {
            "joints": [
                {"name": "mixamorig:Hips", "head": [0, 0, 1]},
                {"name": "mixamorig:Head", "head": [0, 0, 1.8]},
                {"name": "mixamorig:LeftArm", "head": [0.4, 0, 1.5]}
            ]
        }
        res = suggest.suggest_skeleton("test_human", source_data=source)
        self.assertEqual(res["archetype"], "humanoid")
        self.assertEqual(res["rig"]["kind"], "humanoid")
        self.assertIn("humanoid", res["spec"])

    def test_suggest_apose_humanoid(self):
        # A-pose upright character tips: feet on floor, hands at side/waist level, tall Z
        tips = [
            [0.5, 0.45, 0.95],    # head
            [0.65, 0.5, 0.05],    # left foot
            [0.35, 0.5, 0.05],    # right foot
            [0.80, 0.5, 0.50],    # left hand
            [0.20, 0.5, 0.50],    # right hand
        ]
        res = suggest.suggest_skeleton("test_guard", tips=tips, proportions=[0.9, 0.4, 1.9])
        self.assertEqual(res["archetype"], "humanoid")
        chain_names = [c["name"] for c in res["rig"]["chains"]]
        self.assertIn("arm.L", chain_names)
        self.assertIn("arm.R", chain_names)
        self.assertIn("leg.L", chain_names)
        self.assertIn("leg.R", chain_names)
        self.assertNotIn("leg_front.L", chain_names)
        self.assertNotIn("leg_hind.L", chain_names)

    def test_suggest_tripo_source(self):
        source = {
            "lo": [-1, -1, 0], "hi": [1, 1, 1], "size": [2, 2, 1],
            "joints": [
                {"name": "bone_0", "head": [0, -0.5, 0.5], "children": ["bone_1", "bone_2"]},
                {"name": "bone_1", "head": [0, 0.5, 0.5], "children": ["bone_3", "bone_4"]},
                {"name": "bone_2", "head": [0.4, -0.5, 0.2], "children": []},
                {"name": "bone_3", "head": [-0.4, 0.5, 0.2], "children": []},
                {"name": "bone_4", "head": [0.4, 0.5, 0.2], "children": []},
            ]
        }
        res = suggest.suggest_skeleton("test_creature", source_data=source, survey_data={"skeleton": "tripo"})
        self.assertIn("rig", res)
        self.assertEqual(res["rig"]["kind"], "tripo")
        self.assertEqual(res["rig"]["head"], "bone_0")
        self.assertEqual(res["rig"]["hips"], "bone_1")


    def test_suggest_pinch_refinement(self):
        tips = [
            [0.5, 0.15, 0.6],    # head/snout
            [0.7, 0.4, 0.1],     # front left foot
            [0.3, 0.4, 0.1],     # front right foot
            [0.7, 0.8, 0.1],     # hind left foot
            [0.3, 0.8, 0.1],     # hind right foot
            [0.5, 0.95, 0.5],    # tail tip
        ]
        verts = []
        for z in [i / 20.0 for i in range(21)]:
            r = 0.08 - 0.04 * math.sin(z * math.pi)
            for theta in [0, math.pi / 2, math.pi, 3 * math.pi / 2]:
                verts.append([0.7 + r * math.cos(theta), 0.4 + r * math.sin(theta), z])
                verts.append([0.3 + r * math.cos(theta), 0.4 + r * math.sin(theta), z])

        res = suggest.suggest_skeleton("test_beast", tips=tips, proportions=[1.0, 1.5, 0.8], vertices=verts)
        self.assertEqual(res["archetype"], "quadruped")
        chains = {c["name"]: c for c in res["rig"]["chains"]}
        self.assertIn("leg_front.L", chains)
        self.assertIn("points", chains["leg_front.L"])
        self.assertEqual(len(chains["leg_front.L"]["points"]), 3)
        self.assertIn("tail", chains)
        self.assertTrue(chains["tail"].get("medial"))

    def test_suggest_unreal_and_unity_sources(self):
        # Unreal Mannequin source
        unreal_source = {
            "joints": [
                {"name": "pelvis", "head": [0.0, 0.0, 1.0], "tail": [0.0, 0.0, 1.1]},
                {"name": "spine_01", "head": [0.0, 0.0, 1.2]},
                {"name": "neck_01", "head": [0.0, 0.0, 1.5]},
                {"name": "head", "head": [0.0, 0.0, 1.65], "tail": [0.0, 0.0, 1.8]},
                {"name": "clavicle_l", "head": [0.1, 0.0, 1.45]},
                {"name": "upperarm_l", "head": [0.25, 0.0, 1.45]},
                {"name": "lowerarm_l", "head": [0.5, 0.0, 1.45]},
                {"name": "hand_l", "head": [0.75, 0.0, 1.45]},
                {"name": "thigh_l", "head": [0.15, 0.0, 0.95]},
                {"name": "calf_l", "head": [0.15, 0.0, 0.5]},
                {"name": "foot_l", "head": [0.15, 0.0, 0.1]},
            ]
        }
        res_u = suggest.suggest_skeleton("test_ue", source_data=unreal_source)
        self.assertEqual(res_u["archetype"], "humanoid")
        self.assertEqual(res_u["rig"]["kind"], "placed")
        u_chains = {c["name"]: c for c in res_u["rig"]["chains"]}
        self.assertIn("spine", u_chains)
        self.assertIn("arm.L", u_chains)
        self.assertIn("leg.L", u_chains)

        # Unity Humanoid source
        unity_source = {
            "joints": [
                {"name": "Hips", "head": [0.0, 0.0, 1.0]},
                {"name": "Spine", "head": [0.0, 0.0, 1.2]},
                {"name": "Neck", "head": [0.0, 0.0, 1.5]},
                {"name": "Head", "head": [0.0, 0.0, 1.65], "tail": [0.0, 0.0, 1.8]},
                {"name": "LeftUpperArm", "head": [0.25, 0.0, 1.45]},
                {"name": "LeftLowerArm", "head": [0.5, 0.0, 1.45]},
                {"name": "LeftHand", "head": [0.75, 0.0, 1.45]},
                {"name": "LeftUpperLeg", "head": [0.15, 0.0, 0.95]},
                {"name": "LeftLowerLeg", "head": [0.15, 0.0, 0.5]},
                {"name": "LeftFoot", "head": [0.15, 0.0, 0.1]},
            ]
        }
        res_unity = suggest.suggest_skeleton("test_unity", source_data=unity_source)
        self.assertEqual(res_unity["archetype"], "humanoid")
        self.assertEqual(res_unity["rig"]["kind"], "placed")


if __name__ == "__main__":
    unittest.main()

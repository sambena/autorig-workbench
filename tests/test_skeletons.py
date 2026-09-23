# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for the universal semantic bone dictionary and convention detection.
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))

import skeletons


class TestSemanticBoneDictionary(unittest.TestCase):
    def test_detect_conventions(self):
        # Unreal Mannequin
        unreal_bones = ["pelvis", "spine_01", "spine_02", "neck_01", "head", "upperarm_l", "thigh_l", "calf_l", "foot_l"]
        conv, conf = skeletons.detect_convention(unreal_bones)
        self.assertEqual(conv, "unreal")
        self.assertGreaterEqual(conf, 0.5)

        # Unity Humanoid
        unity_bones = ["Hips", "Spine", "Chest", "Neck", "Head", "LeftUpperArm", "LeftLowerArm", "LeftUpperLeg", "LeftLowerLeg"]
        conv, conf = skeletons.detect_convention(unity_bones)
        self.assertEqual(conv, "unity")
        self.assertGreaterEqual(conf, 0.5)

        # Blender Rigify
        rigify_bones = ["DEF-spine", "DEF-spine.001", "DEF-neck", "DEF-head", "DEF-upper_arm.L", "DEF-thigh.L", "DEF-shin.L"]
        conv, conf = skeletons.detect_convention(rigify_bones)
        self.assertEqual(conv, "rigify")
        self.assertGreaterEqual(conf, 0.5)

        # 3ds Max Biped
        biped_bones = ["Bip01 Pelvis", "Bip01 Spine", "Bip01 Neck", "Bip01 Head", "Bip01 L UpperArm", "Bip01 L Thigh"]
        conv, conf = skeletons.detect_convention(biped_bones)
        self.assertEqual(conv, "biped")
        self.assertGreaterEqual(conf, 0.5)

        # AccuRig / Character Creator
        accurig_bones = ["CC_Base_Pelvis", "CC_Base_Waist", "CC_Base_Head", "CC_Base_L_Upperarm", "CC_Base_L_Thigh", "CC_Base_L_Calf"]
        conv, conf = skeletons.detect_convention(accurig_bones)
        self.assertEqual(conv, "accurig")
        self.assertGreaterEqual(conf, 0.5)

        # Valve / Source Engine
        valve_bones = ["ValveBiped.Bip01_Pelvis", "ValveBiped.Bip01_Spine", "ValveBiped.Bip01_Head1", "ValveBiped.Bip01_L_UpperArm"]
        conv, conf = skeletons.detect_convention(valve_bones)
        self.assertEqual(conv, "valve")
        self.assertGreaterEqual(conf, 0.5)

        # Mixamo
        mixamo_bones = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:LeftArm", "mixamorig:LeftUpLeg"]
        conv, conf = skeletons.detect_convention(mixamo_bones)
        self.assertEqual(conv, "mixamo")
        self.assertEqual(conf, 1.0)

        # Tripo
        tripo_bones = ["bone_0", "bone_1", "bone_2", "bone_3"]
        conv, conf = skeletons.detect_convention(tripo_bones)
        self.assertEqual(conv, "tripo")
        self.assertEqual(conf, 1.0)

        # None / Empty
        conv, conf = skeletons.detect_convention([])
        self.assertEqual(conv, "none")
        self.assertEqual(conf, 0.0)

    def test_map_bone_to_canonical(self):
        # Unreal
        self.assertEqual(skeletons.map_bone_to_canonical("pelvis"), "hips")
        self.assertEqual(skeletons.map_bone_to_canonical("spine_01"), "spine")
        self.assertEqual(skeletons.map_bone_to_canonical("upperarm_l"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("lowerarm_l"), "forearm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("thigh_r"), "thigh.R")
        self.assertEqual(skeletons.map_bone_to_canonical("calf_r"), "shin.R")
        self.assertEqual(skeletons.map_bone_to_canonical("ball_l"), "toe.L")

        # Unity
        self.assertEqual(skeletons.map_bone_to_canonical("LeftUpperArm"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("LeftLowerArm"), "forearm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("RightUpperLeg"), "thigh.R")
        self.assertEqual(skeletons.map_bone_to_canonical("LeftIndexProximal"), "index1.L")
        self.assertEqual(skeletons.map_bone_to_canonical("RightThumbDistal"), "thumb3.R")

        # Rigify
        self.assertEqual(skeletons.map_bone_to_canonical("DEF-upper_arm.L"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("DEF-forearm.L"), "forearm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("DEF-thigh.R"), "thigh.R")

        # Biped
        self.assertEqual(skeletons.map_bone_to_canonical("Bip01 L UpperArm"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("Bip01 R Calf"), "shin.R")

        # AccuRig
        self.assertEqual(skeletons.map_bone_to_canonical("CC_Base_L_Upperarm"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("CC_Base_L_Thigh"), "thigh.L")

        # Tails
        self.assertEqual(skeletons.map_bone_to_canonical("tail_01"), "tail_1")
        self.assertEqual(skeletons.map_bone_to_canonical("tail_02"), "tail_2")

    def test_extract_chains_from_unreal_joints(self):
        mock_unreal_joints = [
            {"name": "pelvis", "head": [0.0, 0.0, 1.0], "tail": [0.0, 0.0, 1.1], "parent": None},
            {"name": "spine_01", "head": [0.0, 0.0, 1.2], "parent": "pelvis"},
            {"name": "neck_01", "head": [0.0, 0.0, 1.5], "parent": "spine_01"},
            {"name": "head", "head": [0.0, 0.0, 1.65], "tail": [0.0, 0.0, 1.8], "parent": "neck_01"},
            {"name": "clavicle_l", "head": [0.1, 0.0, 1.45], "parent": "spine_01"},
            {"name": "upperarm_l", "head": [0.25, 0.0, 1.45], "parent": "clavicle_l"},
            {"name": "lowerarm_l", "head": [0.5, 0.0, 1.45], "parent": "upperarm_l"},
            {"name": "hand_l", "head": [0.75, 0.0, 1.45], "parent": "lowerarm_l"},
            {"name": "clavicle_r", "head": [-0.1, 0.0, 1.45], "parent": "spine_01"},
            {"name": "upperarm_r", "head": [-0.25, 0.0, 1.45], "parent": "clavicle_r"},
            {"name": "lowerarm_r", "head": [-0.5, 0.0, 1.45], "parent": "upperarm_r"},
            {"name": "hand_r", "head": [-0.75, 0.0, 1.45], "parent": "lowerarm_r"},
            {"name": "thigh_l", "head": [0.15, 0.0, 0.95], "parent": "pelvis"},
            {"name": "calf_l", "head": [0.15, 0.0, 0.5], "parent": "thigh_l"},
            {"name": "foot_l", "head": [0.15, 0.0, 0.1], "parent": "calf_l"},
            {"name": "thigh_r", "head": [-0.15, 0.0, 0.95], "parent": "pelvis"},
            {"name": "calf_r", "head": [-0.15, 0.0, 0.5], "parent": "thigh_r"},
            {"name": "foot_r", "head": [-0.15, 0.0, 0.1], "parent": "calf_r"},
        ]
        res = skeletons.suggest_from_known_skeleton(mock_unreal_joints, "unreal")
        self.assertEqual(res["archetype"], "humanoid")
        self.assertEqual(res["rig"]["kind"], "placed")
        chain_map = {c["name"]: c for c in res["rig"]["chains"]}
        self.assertIn("spine", chain_map)
        self.assertIn("arm.L", chain_map)
        self.assertIn("arm.R", chain_map)
        self.assertIn("leg.L", chain_map)
        self.assertIn("leg.R", chain_map)
        self.assertTrue(chain_map["arm.L"].get("girdle"))
        self.assertTrue(chain_map["leg.L"].get("ik"))
        self.assertIn("humanoid", res["spec"])


if __name__ == "__main__":
    unittest.main()

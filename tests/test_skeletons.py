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

        # Mixamo
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:Hips"), "hips")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:Spine"), "spine")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:Spine1"), "spine1")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:Spine2"), "spine2")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftArm"), "arm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftForeArm"), "forearm.L")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftHandIndex1"), "index1.L")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftHandIndex4"), "index4.L")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftToeBase"), "toe.L")
        self.assertEqual(skeletons.map_bone_to_canonical("mixamorig:LeftToe_End"), "toetip.L")

        # Tails
        self.assertEqual(skeletons.map_bone_to_canonical("tail_01"), "tail_1")
        self.assertEqual(skeletons.map_bone_to_canonical("tail_02"), "tail_2")

    def test_extract_chains_from_mixamo_joints(self):
        mock_mixamo_joints = [
            {"name": "mixamorig:Hips", "head": [0.0, 0.0, 1.0], "parent": None},
            {"name": "mixamorig:Spine", "head": [0.0, 0.0, 1.15], "parent": "mixamorig:Hips"},
            {"name": "mixamorig:Spine1", "head": [0.0, 0.0, 1.3], "parent": "mixamorig:Spine"},
            {"name": "mixamorig:Spine2", "head": [0.0, 0.0, 1.45], "parent": "mixamorig:Spine1"},
            {"name": "mixamorig:Neck", "head": [0.0, 0.0, 1.6], "parent": "mixamorig:Spine2"},
            {"name": "mixamorig:Head", "head": [0.0, 0.0, 1.75], "tail": [0.0, 0.0, 1.9], "parent": "mixamorig:Neck"},
            {"name": "mixamorig:LeftShoulder", "head": [0.1, 0.0, 1.5], "parent": "mixamorig:Spine2"},
            {"name": "mixamorig:LeftArm", "head": [0.25, 0.0, 1.5], "parent": "mixamorig:LeftShoulder"},
            {"name": "mixamorig:LeftForeArm", "head": [0.5, 0.0, 1.5], "parent": "mixamorig:LeftArm"},
            {"name": "mixamorig:LeftHand", "head": [0.75, 0.0, 1.5], "parent": "mixamorig:LeftForeArm"},
            {"name": "mixamorig:LeftHandIndex1", "head": [0.8, 0.0, 1.5], "parent": "mixamorig:LeftHand"},
            {"name": "mixamorig:LeftHandIndex2", "head": [0.85, 0.0, 1.5], "parent": "mixamorig:LeftHandIndex1"},
            {"name": "mixamorig:LeftHandIndex3", "head": [0.9, 0.0, 1.5], "parent": "mixamorig:LeftHandIndex2"},
            {"name": "mixamorig:LeftHandIndex4", "head": [0.95, 0.0, 1.5], "parent": "mixamorig:LeftHandIndex3"},
            {"name": "mixamorig:RightShoulder", "head": [-0.1, 0.0, 1.5], "parent": "mixamorig:Spine2"},
            {"name": "mixamorig:RightArm", "head": [-0.25, 0.0, 1.5], "parent": "mixamorig:RightShoulder"},
            {"name": "mixamorig:RightForeArm", "head": [-0.5, 0.0, 1.5], "parent": "mixamorig:RightArm"},
            {"name": "mixamorig:RightHand", "head": [-0.75, 0.0, 1.5], "parent": "mixamorig:RightForeArm"},
            {"name": "mixamorig:LeftUpLeg", "head": [0.15, 0.0, 0.95], "parent": "mixamorig:Hips"},
            {"name": "mixamorig:LeftLeg", "head": [0.15, 0.0, 0.5], "parent": "mixamorig:LeftUpLeg"},
            {"name": "mixamorig:LeftFoot", "head": [0.15, 0.0, 0.15], "parent": "mixamorig:LeftLeg"},
            {"name": "mixamorig:LeftToeBase", "head": [0.15, -0.05, 0.05], "parent": "mixamorig:LeftFoot"},
            {"name": "mixamorig:LeftToe_End", "head": [0.15, -0.15, 0.05], "parent": "mixamorig:LeftToeBase"},
            {"name": "mixamorig:RightUpLeg", "head": [-0.15, 0.0, 0.95], "parent": "mixamorig:Hips"},
            {"name": "mixamorig:RightLeg", "head": [-0.15, 0.0, 0.5], "parent": "mixamorig:RightUpLeg"},
            {"name": "mixamorig:RightFoot", "head": [-0.15, 0.0, 0.15], "parent": "mixamorig:RightLeg"},
            {"name": "mixamorig:RightToeBase", "head": [-0.15, -0.05, 0.05], "parent": "mixamorig:RightFoot"},
            {"name": "mixamorig:RightToe_End", "head": [-0.15, -0.15, 0.05], "parent": "mixamorig:RightToeBase"},
        ]
        res = skeletons.suggest_from_known_skeleton(mock_mixamo_joints, "mixamo")
        self.assertEqual(res["archetype"], "humanoid")
        chain_map = {c["name"]: c for c in res["rig"]["chains"]}
        self.assertIn("spine", chain_map)
        # Spine should contain hips, spine, spine1, spine2, neck, head (+ tail if present)
        self.assertGreaterEqual(len(chain_map["spine"]["points"]), 6)
        self.assertIn("f_index.L", chain_map)
        # Finger chain should include joint 4 (tip)
        self.assertEqual(len(chain_map["f_index.L"]["points"]), 4)
        # Leg chain should include toe tip
        self.assertEqual(len(chain_map["leg.L"]["points"]), 5)
        self.assertIn("humanoid", res["spec"])
        h_z = res["spec"]["humanoid"]["z"]
        self.assertTrue(0.01 <= h_z["ankle"] < h_z["knee"] < h_z["hip"] < h_z["spine"] < h_z["spine1"] < h_z["spine2"] <= h_z["arm"] < h_z["neck"] < h_z["head"] <= h_z["top"] <= 1.0)

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
        h_x = res["spec"]["humanoid"]["x"]
        self.assertTrue(0.0 <= h_x["tip"] <= h_x["knuckle"] <= h_x["wrist"] <= h_x["elbow"] <= h_x["shoulder"] <= 0.49)
        h_z = res["spec"]["humanoid"]["z"]
        self.assertTrue(0.01 <= h_z["ankle"] < h_z["knee"] < h_z["hip"] < h_z["spine"] < h_z["spine1"] < h_z["spine2"] <= h_z["arm"] < h_z["neck"] < h_z["head"] <= h_z["top"] <= 1.0)


if __name__ == "__main__":
    unittest.main()

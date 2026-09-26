# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit and integration tests for external mocap / BVH / FBX retargeting.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import retargeter
import skeletons
import layout
import spec_store
import blender

SAMPLE_BVH = """HIERARCHY
ROOT Hips
{
	OFFSET 0.0 0.0 0.0
	CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
	JOINT Spine
	{
		OFFSET 0.0 1.2 0.0
		CHANNELS 3 Zrotation Xrotation Yrotation
		JOINT Head
		{
			OFFSET 0.0 1.0 0.0
			CHANNELS 3 Zrotation Xrotation Yrotation
			End Site
			{
				OFFSET 0.0 0.5 0.0
			}
		}
	}
	JOINT LeftArm
	{
		OFFSET 1.0 1.0 0.0
		CHANNELS 3 Zrotation Xrotation Yrotation
		JOINT LeftForeArm
		{
			OFFSET 1.0 0.0 0.0
			CHANNELS 3 Zrotation Xrotation Yrotation
			End Site
			{
				OFFSET 0.5 0.0 0.0
			}
		}
	}
	JOINT RightArm
	{
		OFFSET -1.0 1.0 0.0
		CHANNELS 3 Zrotation Xrotation Yrotation
		JOINT RightForeArm
		{
			OFFSET -1.0 0.0 0.0
			CHANNELS 3 Zrotation Xrotation Yrotation
			End Site
			{
				OFFSET -0.5 0.0 0.0
			}
		}
	}
	JOINT LeftUpLeg
	{
		OFFSET 0.5 -0.5 0.0
		CHANNELS 3 Zrotation Xrotation Yrotation
		JOINT LeftLeg
		{
			OFFSET 0.0 -1.5 0.0
			CHANNELS 3 Zrotation Xrotation Yrotation
			End Site
			{
				OFFSET 0.0 -0.5 0.0
			}
		}
	}
	JOINT RightUpLeg
	{
		OFFSET -0.5 -0.5 0.0
		CHANNELS 3 Zrotation Xrotation Yrotation
		JOINT RightLeg
		{
			OFFSET 0.0 -1.5 0.0
			CHANNELS 3 Zrotation Xrotation Yrotation
			End Site
			{
				OFFSET 0.0 -0.5 0.0
			}
		}
	}
}
MOTION
Frames: 3
Frame Time: 0.0333333
0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0
0.0 0.05 0.0 2.0 0.0 0.0 1.0 0.0 0.0 1.0 0.0 0.0 2.0 0.0 0.0 3.0 0.0 0.0 -2.0 0.0 0.0 -3.0 0.0 0.0 2.0 0.0 0.0 4.0 0.0 0.0 -2.0 0.0 0.0 -4.0 0.0 0.0
0.0 0.10 0.0 4.0 0.0 0.0 2.0 0.0 0.0 2.0 0.0 0.0 4.0 0.0 0.0 6.0 0.0 0.0 -4.0 0.0 0.0 -6.0 0.0 0.0 4.0 0.0 0.0 8.0 0.0 0.0 -4.0 0.0 0.0 -8.0 0.0 0.0
"""


class RetargeterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-retarget-")
        cls.orig_models = os.environ.get("AUTORIG_MODELS")
        cls.orig_work = os.environ.get("AUTORIG_WORK")
        cls.test_models = os.path.join(cls.tmp, "models")
        cls.test_work = os.path.join(cls.tmp, "work")
        shutil.copytree(os.path.join(REPO, "samples"), cls.test_models)
        os.environ["AUTORIG_MODELS"] = cls.test_models
        os.environ["AUTORIG_WORK"] = cls.test_work
        layout.ROOT = os.path.abspath(cls.test_models)
        layout.WORK = os.path.abspath(cls.test_work)
        spec_store.reload()

        cls.bvh_file = os.path.join(cls.tmp, "sample_walk.bvh")
        with open(cls.bvh_file, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE_BVH)

        # An FBX clip to retarget, when the machine has one to offer (AUTORIG_TEST_FBX); else that test skips
        cls.sample_fbx = os.environ.get("AUTORIG_TEST_FBX") or ""

        # plan_retarget reads the rigged .blend's bones under Blender: rig the biped once here when Blender is
        # installed (rigged/ is not committed), else the plan and retarget tests skip
        cls.blend = os.path.join(cls.test_models, "biped", "rigged", "biped.blend")
        cls.blender_exe = blender.find(required=False)
        if cls.blender_exe and not os.path.exists(cls.blend):
            r = blender.run("rerig.py", "-only", "biped", "-qa", os.path.join(cls.test_work, "qa"))
            if r.returncode != 0:
                cls.blend = None
        elif not cls.blender_exe:
            cls.blend = None

    @classmethod
    def tearDownClass(cls):
        if cls.orig_models:
            os.environ["AUTORIG_MODELS"] = cls.orig_models
        elif "AUTORIG_MODELS" in os.environ:
            del os.environ["AUTORIG_MODELS"]
        if cls.orig_work:
            os.environ["AUTORIG_WORK"] = cls.orig_work
        elif "AUTORIG_WORK" in os.environ:
            del os.environ["AUTORIG_WORK"]
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_parse_bvh_header(self):
        meta = retargeter.parse_bvh_header(self.bvh_file)
        self.assertEqual(meta["format"], "BVH")
        self.assertEqual(meta["root"], "Hips")
        self.assertEqual(meta["frames"], 3)
        self.assertEqual(meta["fps"], 30.0)
        self.assertIn("Hips", meta["channels"])
        self.assertEqual(len(meta["channels"]["Hips"]), 6)
        self.assertIn("LeftForeArm", meta["joints"])

    def test_build_retarget_mapping_semantic(self):
        source_bones = [
            "Hips", "Spine", "Head",
            "LeftArm", "LeftForeArm", "RightArm", "RightForeArm",
            "LeftUpLeg", "LeftLeg", "RightUpLeg", "RightLeg"
        ]
        target_bones = [
            "root", "spine", "chest", "head",
            "arm.L_1.L", "arm.L_2.L", "arm.R_1.R", "arm.R_2.R",
            "leg.L_1.L", "leg.L_2.L", "leg.R_1.R", "leg.R_2.R"
        ]
        res = retargeter.build_retarget_mapping(source_bones, target_bones)
        mapping = res["mapping"]

        self.assertEqual(mapping.get("Hips"), "root")
        self.assertEqual(mapping.get("Spine"), "spine")
        self.assertEqual(mapping.get("Head"), "head")
        self.assertEqual(mapping.get("LeftArm"), "arm.L_1.L")
        self.assertEqual(mapping.get("LeftForeArm"), "arm.L_2.L")
        self.assertEqual(mapping.get("RightArm"), "arm.R_1.R")
        self.assertEqual(mapping.get("RightForeArm"), "arm.R_2.R")
        self.assertEqual(mapping.get("LeftUpLeg"), "leg.L_1.L")
        self.assertEqual(mapping.get("LeftLeg"), "leg.L_2.L")
        self.assertEqual(mapping.get("RightUpLeg"), "leg.R_1.R")
        self.assertEqual(mapping.get("RightLeg"), "leg.R_2.R")

        self.assertEqual(res["confidence"], 1.0)
        self.assertEqual(res["root_pair"], ("Hips", "root"))

    def test_build_retarget_mapping_overrides(self):
        source_bones = ["Hips", "Spine"]
        target_bones = ["root", "spine", "chest"]
        res = retargeter.build_retarget_mapping(source_bones, target_bones, overrides={"Spine": "chest"})
        self.assertEqual(res["mapping"]["Spine"], "chest")

    def test_plan_and_format_summary(self):
        plan = retargeter.plan_retarget("biped", self.bvh_file, clip_name="walk_cycle")
        self.assertEqual(plan["clip_name"], "walk_cycle")
        self.assertEqual(plan["model"], "biped")
        self.assertTrue(plan["root_motion"])
        self.assertTrue(plan["solve_offsets"])
        self.assertGreater(plan["mapping_result"]["mapped_count"], 5)

        summary = retargeter.format_retarget_summary(plan)
        self.assertIn("RETARGET PLAN", summary)
        self.assertIn("walk_cycle", summary)
        self.assertIn("LeftArm", summary)
        self.assertIn("arm.L_1.L", summary)

    def test_retarget_clip_headless_blender(self):
        res = retargeter.retarget_clip(
            model_name="biped",
            mocap_file=self.bvh_file,
            clip_name="test_walk",
            root_motion=True,
            scale_proportions=True,
            solve_offsets=True,
            export_glb=False,
        )
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["clip_name"], "test_walk")
        self.assertEqual(res["frames"], 3)
        self.assertEqual(res["fps"], 30)
        self.assertGreater(res["mapped_bones"], 5)

        # Verify action exists in target .blend via Blender inspect
        verify_cmd = [
            "blender", "-b", res["target_blend"],
            "--python-expr",
            """
import bpy
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
action_names = [a.name for a in bpy.data.actions]
print("__ACTIONS__" + str(action_names))
"""
        ]
        r = subprocess.run(verify_cmd, capture_output=True, text=True)
        self.assertIn("'test_walk'", r.stdout)

    def test_retarget_fbx_and_batch(self):
        if not os.path.isfile(self.sample_fbx):
            self.skipTest("Sample FBX not found on system")

        meta = retargeter.inspect_mocap_file(self.sample_fbx)
        self.assertEqual(meta["format"], "FBX")
        self.assertEqual(meta["convention"], "unity")
        self.assertGreater(meta["frames"], 100)

        # Test single FBX retarget with frame range
        res = retargeter.retarget_clip(
            model_name="biped",
            mocap_file=self.sample_fbx,
            clip_name="test_fbx_victory",
            frame_range=[1, 5],
            solve_offsets=True,
            root_motion=True,
        )
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["frames"], 5)
        self.assertEqual(res["clip_name"], "test_fbx_victory")

        # Test batch retarget
        batch_res = retargeter.retarget_batch(
            models=["biped"],
            mocap_files=[self.bvh_file],
            clip_names=["batch_walk"],
        )
        self.assertEqual(len(batch_res), 1)
        self.assertEqual(batch_res[0]["status"], "OK")

    def test_retarget_cli(self):
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "autorig", "steps", "retarget.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("--no-root-motion", r.stdout)
        self.assertIn("--no-solve-offsets", r.stdout)
        self.assertIn("--dry-run", r.stdout)

        # Dry run CLI execution
        r_dry = subprocess.run(
            [sys.executable, os.path.join(REPO, "autorig", "steps", "retarget.py"),
             "biped", self.bvh_file, "--dry-run"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r_dry.returncode, 0)
        self.assertIn("RETARGET_DRY_RUN", r_dry.stdout)

        # List actions CLI execution
        r_list = subprocess.run(
            [sys.executable, os.path.join(REPO, "autorig", "steps", "retarget.py"),
             "biped", self.bvh_file, "--list-actions"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r_list.returncode, 0)
        self.assertIn("ACTIONS IN", r_list.stdout)
        self.assertIn("sample_walk", r_list.stdout)

    def test_clean_action_name(self):
        self.assertEqual(retargeter.clean_action_name("Armature|Armature|Dance_Loop"), "dance_loop")
        self.assertEqual(retargeter.clean_action_name("mixamo.com/Run_Fwd_Loop"), "run_fwd_loop")
        self.assertEqual(retargeter.clean_action_name("Sword_Attack-01"), "sword_attack_01")
        self.assertEqual(retargeter.clean_action_name(""), "clip")

    def test_plan_with_source_action(self):
        plan = retargeter.plan_retarget("biped", self.bvh_file, source_action="sample_walk")
        self.assertEqual(plan["source_action"], "sample_walk")
        self.assertEqual(plan["clip_name"], "sample_walk")

        with self.assertRaises(ValueError):
            retargeter.plan_retarget("biped", self.bvh_file, source_action="nonexistent_action")


if __name__ == "__main__":
    unittest.main()


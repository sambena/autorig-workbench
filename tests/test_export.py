# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for multi-target engine export presets and packager.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import exporter
import layout


class ExportPresetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-export-")
        cls.orig_models = os.environ.get("AUTORIG_MODELS")
        cls.orig_work = os.environ.get("AUTORIG_WORK")
        # Point to repo samples
        os.environ["AUTORIG_MODELS"] = os.path.join(REPO, "samples")
        os.environ["AUTORIG_WORK"] = os.path.join(cls.tmp, "_autorig")
        layout.ROOT = os.path.abspath(os.environ["AUTORIG_MODELS"])
        layout.WORK = os.path.abspath(os.environ["AUTORIG_WORK"])

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

    def test_list_presets(self):
        presets = exporter.list_presets()
        self.assertEqual(len(presets), 4)
        preset_ids = {p["id"] for p in presets}
        self.assertEqual(preset_ids, {"unreal", "unity", "godot", "web"})
        for p in presets:
            self.assertTrue(p["name"])
            self.assertTrue(p["primary_format"])
            self.assertTrue(p["badge"])
            self.assertTrue(p["description"])
            self.assertTrue(p["doc_file"])

    def test_bone_mappings(self):
        test_bones = ["hips", "spine", "chest", "neck", "head", "LeftArm", "RightArm", "LeftUpLeg", "RightUpLeg"]
        mappings = exporter.compute_bone_mappings(test_bones)
        self.assertIn("canonical", mappings)
        self.assertIn("unreal", mappings)
        self.assertIn("unity", mappings)

        self.assertEqual(mappings["unreal"].get("hips"), "pelvis")
        self.assertEqual(mappings["unreal"].get("spine"), "spine_01")
        self.assertEqual(mappings["unreal"].get("chest"), "spine_02")
        self.assertEqual(mappings["unreal"].get("head"), "head")
        self.assertEqual(mappings["unity"].get("hips"), "Hips")
        self.assertEqual(mappings["unity"].get("spine"), "Spine")
        self.assertEqual(mappings["unity"].get("head"), "Head")

    def test_create_export_package_unreal(self):
        out_dir = os.path.join(self.tmp, "out_unreal")
        res = exporter.create_export_package("biped", target="unreal", out_dir=out_dir)
        self.assertEqual(res["target"], "unreal")
        self.assertTrue(os.path.isfile(res["zip_path"]))
        self.assertGreater(res["zip_size"], 0)

        # Check unzipped contents
        with zipfile.ZipFile(res["zip_path"], "r") as zf:
            names = zf.namelist()
            self.assertIn("biped.fbx", names)
            self.assertIn("unreal_bone_mapping.json", names)
            self.assertIn("unreal_import_preset.json", names)
            self.assertIn("Unreal_Import_Guide.md", names)

            mapping_content = json.loads(zf.read("unreal_bone_mapping.json").decode("utf-8"))
            self.assertEqual(mapping_content["model"], "biped")
            self.assertIn("mannequin_mappings", mapping_content)

    def test_create_export_package_unity(self):
        out_dir = os.path.join(self.tmp, "out_unity")
        res = exporter.create_export_package("biped", target="unity", out_dir=out_dir)
        self.assertEqual(res["target"], "unity")
        self.assertTrue(os.path.isfile(res["zip_path"]))

        with zipfile.ZipFile(res["zip_path"], "r") as zf:
            names = zf.namelist()
            self.assertIn("biped.fbx", names)
            self.assertIn("unity_avatar_definition.json", names)
            self.assertIn("Unity_Import_Guide.md", names)

            avatar = json.loads(zf.read("unity_avatar_definition.json").decode("utf-8"))
            self.assertIn("avatar", avatar)
            self.assertIn("humanDescription", avatar["avatar"])

    def test_create_export_package_godot(self):
        out_dir = os.path.join(self.tmp, "out_godot")
        res = exporter.create_export_package("biped", target="godot", out_dir=out_dir)
        self.assertEqual(res["target"], "godot")
        self.assertTrue(os.path.isfile(res["zip_path"]))

        with zipfile.ZipFile(res["zip_path"], "r") as zf:
            names = zf.namelist()
            self.assertTrue(any(n.endswith(".glb") or n.endswith(".fbx") for n in names))
            self.assertIn("biped.glb.import", names)
            self.assertIn("character_controller.gd", names)
            self.assertIn("Godot_Import_Guide.md", names)

    def test_create_export_package_web(self):
        out_dir = os.path.join(self.tmp, "out_web")
        res = exporter.create_export_package("biped", target="web", out_dir=out_dir)
        self.assertEqual(res["target"], "web")
        self.assertTrue(os.path.isfile(res["zip_path"]))

        with zipfile.ZipFile(res["zip_path"], "r") as zf:
            names = zf.namelist()
            self.assertTrue(any(n.endswith(".glb") or n.endswith(".fbx") for n in names))
            self.assertIn("web_manifest.json", names)
            self.assertIn("index.html", names)
            self.assertIn("Web_Usage_Guide.md", names)

            manifest = json.loads(zf.read("web_manifest.json").decode("utf-8"))
            self.assertEqual(manifest["name"], "biped")

    def test_create_export_package_all(self):
        out_dir = os.path.join(self.tmp, "out_all")
        res = exporter.create_export_package("biped", target="all", out_dir=out_dir)
        self.assertEqual(res["target"], "all")
        self.assertTrue(os.path.isfile(res["zip_path"]))
        self.assertIn("presets", res)
        self.assertEqual(set(res["presets"].keys()), {"unreal", "unity", "godot", "web"})

        with zipfile.ZipFile(res["zip_path"], "r") as zf:
            names = zf.namelist()
            self.assertTrue(any(n.startswith("unreal/") for n in names))
            self.assertTrue(any(n.startswith("unity/") for n in names))
            self.assertTrue(any(n.startswith("godot/") for n in names))
            self.assertTrue(any(n.startswith("web/") for n in names))

    def test_invalid_target_and_model(self):
        with self.assertRaises(ValueError):
            exporter.create_export_package("nonexistent_model_12345", target="unreal")
        with self.assertRaises(ValueError):
            exporter.create_export_package("biped", target="unknown_engine")

    def test_export_cli_step(self):
        out_dir = os.path.join(self.tmp, "cli_out")
        cmd = [sys.executable, os.path.join(REPO, "autorig", "steps", "export.py"), "biped", "--target", "unreal", "--out", out_dir]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.returncode, 0)
        self.assertIn("EXPORT biped unreal", p.stdout)
        self.assertIn("EXPORT_DONE", p.stdout)


if __name__ == "__main__":
    unittest.main()

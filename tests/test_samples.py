# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: validates sample models, their CC0 licenses, and their specs against rig.schema.json.
import json, os, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO, "docs", "rig.schema.json")
SAMPLES_DIR = os.path.join(REPO, "samples")
EXPECTED_SAMPLES = ["pedestal", "canine", "beetle", "biped", "wyvern"]


class TestSampleModels(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            self.schema = json.load(fh)

    def test_samples_exist(self):
        self.assertTrue(os.path.isdir(SAMPLES_DIR), f"Samples directory missing at {SAMPLES_DIR}")
        for slug in EXPECTED_SAMPLES:
            folder = os.path.join(SAMPLES_DIR, slug)
            self.assertTrue(os.path.isdir(folder), f"Sample {slug} directory missing")

    def test_sample_files_and_licenses(self):
        for slug in EXPECTED_SAMPLES:
            folder = os.path.join(SAMPLES_DIR, slug)
            lic_path = os.path.join(folder, "LICENSE.txt")
            obj_path = os.path.join(folder, f"{slug}.obj")
            rig_path = os.path.join(folder, "rig.json")

            self.assertTrue(os.path.isfile(lic_path), f"Missing LICENSE.txt in {slug}")
            with open(lic_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("CC0", content, f"License for {slug} does not mention CC0")

            self.assertTrue(os.path.isfile(obj_path), f"Missing {slug}.obj in {slug}")
            with open(obj_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                v_count = sum(1 for line in lines if line.startswith("v "))
                f_count = sum(1 for line in lines if line.startswith("f "))
                self.assertGreater(v_count, 50, f"{slug}.obj has too few vertices: {v_count}")
                self.assertGreater(f_count, 50, f"{slug}.obj has too few faces: {f_count}")

            self.assertTrue(os.path.isfile(rig_path), f"Missing rig.json in {slug}")

    def test_sample_specs_validate_against_schema(self):
        try:
            import jsonschema
            has_jsonschema = True
        except ImportError:
            has_jsonschema = False

        for slug in EXPECTED_SAMPLES:
            rig_path = os.path.join(SAMPLES_DIR, slug, "rig.json")
            with open(rig_path, "r", encoding="utf-8") as f:
                spec = json.load(f)

            self.assertEqual(spec.get("schema"), "autorig-spec/1")
            self.assertIn("rig", spec)
            self.assertEqual(spec["rig"].get("kind"), "placed")

            if has_jsonschema:
                jsonschema.validate(instance=spec, schema=self.schema)


if __name__ == "__main__":
    unittest.main()

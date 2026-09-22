# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: validates docs/rig.schema.json syntax and checks specs against it.
import json, os, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO, "docs", "rig.schema.json")


class TestRigJsonSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            self.schema = json.load(fh)

    def test_schema_structure(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["title"], "Autorig Model Spec")
        self.assertIn("properties", self.schema)
        self.assertIn("rig", self.schema["properties"])
        self.assertIn("humanoid", self.schema["properties"])
        self.assertIn("clips", self.schema["properties"])

        # Check rig.properties
        rig_props = self.schema["properties"]["rig"]["properties"]
        for key in ("kind", "chains", "parts", "blends", "rip_welds", "membranes", "rigid_islands", "audit"):
            self.assertIn(key, rig_props)

    def test_sample_specs_match_schema(self):
        # 1. Placed rig spec
        placed_spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "chains": [
                    {"name": "spine", "slice": [0.1, 0.9], "bones": 4},
                    {"name": "wing.L", "points": [[0.5, 0.2, 0.5], [0.8, 0.4, 0.8]], "bones": 3}
                ],
                "parts": {
                    "wing": {"bones": ["wing.L_1", "wing.L_2", "wing.L_3"], "deny": ["leg_1.L"]}
                },
                "rip_welds": [["wing.L_1", "flank"]],
                "membranes": [
                    {"name": "membrane.L", "bones": ["wing.L_1", "wing.L_2", "wing.L_3"], "cut_flank": True}
                ]
            }
        }
        self.assertEqual(placed_spec["schema"], "autorig-spec/1")
        self.assertIn(placed_spec["rig"]["kind"], self.schema["properties"]["rig"]["properties"]["kind"]["enum"])

        # 2. Tripo rig spec
        tripo_spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "tripo",
                "head": "bone_3",
                "hips": "bone_1",
                "legs": ["bone_5", "bone_6"]
            }
        }
        self.assertEqual(tripo_spec["schema"], "autorig-spec/1")

        # 3. Humanoid rig spec
        humanoid_spec = {
            "schema": "autorig-spec/1",
            "rig": {"kind": "humanoid"},
            "humanoid": {
                "forward": [0, -1, 0],
                "z": {"top": 1.0, "head": 0.87, "neck": 0.83, "arm": 0.77, "spine2": 0.72,
                      "spine1": 0.65, "spine": 0.57, "hip": 0.47, "knee": 0.28, "ankle": 0.08},
                "x": {"shoulder": 0.38, "elbow": 0.23, "wrist": 0.11, "knuckle": 0.05, "tip": 0.0}
            }
        }
        self.assertEqual(humanoid_spec["schema"], "autorig-spec/1")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# File > Clean Up Generated Files… (core/clean_generated.py) on a scratch models folder.
import os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autorig", "core"))
import clean_generated  # noqa: E402


class CleanGeneratedTest(unittest.TestCase):
    KEEP = ["Pack/Oak/Oak.glb", "Pack/Oak/rig.json", "Pack/Oak/model.json", "Pack/Oak/rigged/Oak.fbx",
            "Pack/Oak/rigged/Oak.blend", "Pack/Oak/clips/Oak_clips.json", "Smeltdown/dragon/rigged_v2/preview.glb"]

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.work = os.path.join(self.root, "_autorig")
        for rel in self.KEEP + ["Pack/Oak/rigged/preview.glb", "Pack/Oak/rigged/preview.json",
                                "_autorig/source/Oak.png", "_autorig/source/Oak/front.png", "_autorig/qa/Oak.json",
                                "_autorig/qa/Oak_bend.png", "_autorig/audit/Oak.json", "_autorig/facing/Oak.png",
                                "_autorig/ai_trials/Oak/Oak.glb", "_autorig/spec_editor/Oak/before.json",
                                "_autorig/mocap/dance.bvh", "_autorig/rigged/preview.glb"]:
            self.write(rel, "x" * 10)

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(text)

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def test_scan_counts_each_kind(self):
        kinds = {k["key"]: k for k in clean_generated.scan(self.root, self.work)}
        self.assertEqual(kinds["source"]["files"], 2)
        self.assertEqual(kinds["previews"]["files"], 2)       # not the Dragon's rigged_v2, not inside the work folder
        self.assertEqual(kinds["renders"]["files"], 1)
        self.assertEqual(kinds["riglogs"]["files"], 1)
        self.assertEqual(kinds["scratch"]["files"], 2)
        self.assertEqual(kinds["source"]["bytes"], 20)
        self.assertFalse(kinds["audit"]["default"] or kinds["riglogs"]["default"] or kinds["undo"]["default"]
                         or kinds["mocap"]["default"])

    def test_the_default_kinds_go_and_the_rest_stay(self):
        keys = [k["key"] for k in clean_generated.scan(self.root, self.work) if k["default"]]
        r = clean_generated.clean(self.root, self.work, keys)
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["deleted"]["previews"]["files"], 2)
        for rel in ("Pack/Oak/rigged/preview.glb", "_autorig/source", "_autorig/qa/Oak_bend.png", "_autorig/facing",
                    "_autorig/ai_trials"):
            self.assertFalse(self.exists(rel), rel)
        for rel in self.KEEP + ["_autorig/qa/Oak.json", "_autorig/audit/Oak.json",
                                "_autorig/spec_editor/Oak/before.json", "_autorig/mocap/dance.bvh"]:
            self.assertTrue(self.exists(rel), rel)

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            clean_generated.clean(self.root, self.work, ["models"])


if __name__ == "__main__":
    unittest.main()

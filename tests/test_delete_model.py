# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: deleting a model removes its folder and the work files named for it, and nothing else.
import os
import shutil
import tempfile
import unittest

from autorig.core import layout


def _model(root, *parts):
    d = os.path.join(root, *parts)
    os.makedirs(d)
    with open(os.path.join(d, "rig.json"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    return d


class DeleteModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-delete-")
        self.root = os.path.join(self.tmp, "models")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.root)
        self._root, self._work = layout.ROOT, layout.WORK
        layout.ROOT, layout.WORK = self.root, self.work

    def tearDown(self):
        layout.ROOT, layout.WORK = self._root, self._work
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, *parts, body="x"):
        path = os.path.join(self.work, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_removes_the_model_and_its_work_files_only(self):
        _model(self.root, "Creatures", "wolf")
        os.makedirs(os.path.join(self.work, "export", "wolf"))
        self._touch("export", "wolf", "note.txt")
        kept = [
            self._touch("audit", "beetle.json"),
            self._touch("qa", "canine_walk_retarget.json"),
            self._touch("export", "canine_unreal.zip"),
            self._touch("source", "can.json"),
        ]
        gone = [
            self._touch("audit", "wolf.json"),
            self._touch("source", "wolf.glb"),
            self._touch("source", "wolf.rig.json"),
            self._touch("qa", "wolf_walk_retarget.json"),
            self._touch("export", "wolf_unreal.zip"),
            self._touch("export", "wolf_all_presets.zip"),
        ]
        # a file sitting directly in the work folder is not a tool's per-model output
        stray = os.path.join(self.work, "wolf.json")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("keep")

        result = layout.delete_model("wolf")

        self.assertEqual(result["group"], "Creatures")
        self.assertFalse(os.path.exists(os.path.join(self.root, "Creatures", "wolf")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Creatures")))
        for path in gone:
            self.assertFalse(os.path.exists(path), path)
        self.assertFalse(os.path.exists(os.path.join(self.work, "export", "wolf")))
        for path in kept + [stray]:
            self.assertTrue(os.path.exists(path), path)

    def test_short_name_does_not_take_a_longer_one(self):
        _model(self.root, "can")
        _model(self.root, "Creatures", "canine")
        canine = self._touch("audit", "canine.json")
        canine_zip = self._touch("export", "canine_unreal.zip")
        can_audit = self._touch("audit", "can.json")

        layout.delete_model("can")

        self.assertFalse(os.path.exists(os.path.join(self.root, "can")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Creatures", "canine")))
        self.assertFalse(os.path.exists(can_audit))
        self.assertTrue(os.path.exists(canine))
        self.assertTrue(os.path.exists(canine_zip))

    def test_duplicate_names_are_left_alone(self):
        _model(self.root, "wolf")
        _model(self.root, "Creatures", "wolf")
        with self.assertRaises(ValueError):
            layout.delete_model("wolf")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "wolf")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Creatures", "wolf")))

    def test_rejects_a_path(self):
        _model(self.root, "wolf")
        with self.assertRaises(ValueError):
            layout.delete_model("../wolf")
        with self.assertRaises(ValueError):
            layout.delete_model("nope")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "wolf")))


if __name__ == "__main__":
    unittest.main()

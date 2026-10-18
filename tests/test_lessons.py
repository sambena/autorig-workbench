# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: lessons (core/lessons.py), a fix to one model kept for the models of its kind.
import os, shutil, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import layout  # noqa: E402
import lessons  # noqa: E402


class LessonsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-lessons-")
        self.saved = (layout.ROOT, layout.WORK)
        layout.ROOT, layout.WORK = os.path.join(self.tmp, "Farm"), os.path.join(self.tmp, "_autorig")
        os.makedirs(layout.ROOT); os.makedirs(layout.WORK)

    def tearDown(self):
        layout.ROOT, layout.WORK = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kind(self):
        self.assertEqual(lessons.kind("Cow_Hereford_Tripo"), "cow")
        self.assertEqual(lessons.kind("cow2-holstein"), "cow")
        self.assertEqual(lessons.kind("Horse_Bay"), "horse")
        self.assertIsNone(lessons.kind("ox"))            # too short to be a kind
        self.assertIsNone(lessons.kind(""))

    def test_learn_and_reapply(self):
        self.assertEqual(lessons.for_model("Cow_Holstein"), {})
        lessons.learn_swing("Cow_Hereford_Tripo", 0.75)
        # the collection's lessons file sits beside its work folder, not in a pack view
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "autorig_lessons.json")))
        got = lessons.for_model("Cow_Holstein")
        self.assertEqual((got["swing"], got["swing_from"]), (0.75, "Cow_Hereford_Tripo"))
        self.assertEqual(lessons.for_model("Horse_Bay"), {})
        lessons.learn_swing("Cow_Holstein", 1.0)         # back to the clips as made: the lesson goes
        self.assertEqual(lessons.for_model("Cow_Hereford_Tripo"), {})


if __name__ == "__main__":
    unittest.main()

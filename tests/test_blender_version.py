# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: testing Blender version detection, tested range checking, and startup warnings.
import io, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender


class TestBlenderVersionCheck(unittest.TestCase):
    def test_current_blender_version(self):
        b_exe = blender.find(required=False)
        if not b_exe:
            self.skipTest("Blender not installed")
        v = blender.version(b_exe)
        self.assertIsNotNone(v)
        self.assertIsInstance(v, tuple)
        self.assertEqual(len(v), 3)
        self.assertGreaterEqual(v[0], 1)

        vs = blender.version_string(b_exe)
        self.assertTrue(vs.startswith(f"{v[0]}.{v[1]}"))

        # The local environment is tested on 5.2 LTS
        if v[:2] == (5, 2):
            self.assertTrue(blender.is_tested_version(b_exe))
            self.assertIsNone(blender.version_warning(b_exe))
            buf = io.StringIO()
            self.assertIsNone(blender.warn_if_untested(b_exe, file=buf))
            self.assertEqual(buf.getvalue(), "")

    def test_untested_versions_warn(self):
        # Test simulated versions outside (5, 2)
        blender._VERSION_CACHE["/fake/blender43"] = (4, 3, 0)
        self.assertFalse(blender.is_tested_version("/fake/blender43"))
        w43 = blender.version_warning("/fake/blender43")
        self.assertIsNotNone(w43)
        self.assertIn("4.3.0", w43)
        self.assertIn("5.2 LTS", w43)

        buf = io.StringIO()
        res = blender.warn_if_untested("/fake/blender43", file=buf)
        self.assertEqual(res, w43)
        self.assertIn("WARNING:", buf.getvalue())

        blender._VERSION_CACHE["/fake/blender53"] = (5, 3, 1)
        self.assertFalse(blender.is_tested_version("/fake/blender53"))
        w53 = blender.version_warning("/fake/blender53")
        self.assertIsNotNone(w53)
        self.assertIn("5.3.1", w53)

    def test_tested_version_passes(self):
        blender._VERSION_CACHE["/fake/blender52"] = (5, 2, 2)
        self.assertTrue(blender.is_tested_version("/fake/blender52"))
        self.assertIsNone(blender.version_warning("/fake/blender52"))
        buf = io.StringIO()
        self.assertIsNone(blender.warn_if_untested("/fake/blender52", file=buf))
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

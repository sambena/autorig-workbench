# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for head_line rule and spine cut handling.
import os, sys, unittest, subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
sys.path.insert(0, os.path.join(REPO, "autorig", "steps"))

if "bpy" not in sys.modules:
    import blender

    class TestHeadLineUnderBlender(unittest.TestCase):
        @unittest.skipUnless(blender.find(required=False), "Blender not found")
        def test_run_head_line_under_blender(self):
            blender_bin = blender.find()
            script = os.path.abspath(__file__)
            r = subprocess.run([blender_bin, "-b", "--factory-startup", "--python", script, "--", "-v"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stderr + r.stdout)
else:
    import bpy
    from mathutils import Vector
    import rerig

    class TestHeadLine(unittest.TestCase):
        def setUp(self):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
            self.mesh = bpy.context.active_object

        def test_head_line_single_bone_spine(self):
            # For body="single", spine has only 1 joint (__body) and 2 points
            chains = [
                {"role": "spine", "joints": ["__body"], "points": [Vector((0, 0, 0)), Vector((0, -0.5, 0))], "parent": None, "ik": False, "single": True},
                {"role": "leg", "joints": ["leg_1"], "points": [Vector((0.5, 0, 0)), Vector((0.5, 0, -1))], "parent": (0, 0), "ik": True},
            ]
            spec = {
                "body": "single",
                "head_line": [[0.5, 0.4, 0.5], [0.5, 0.1, 0.5]]
            }
            # Should not raise ValueError: min() iterable argument is empty
            rerig.head_line(chains, self.mesh, spec)
            self.assertEqual(len(chains[0]["points"]), 3)
            # Child chains attached to body should remain attached to body
            self.assertEqual(chains[1]["parent"], (0, 0))

            # Test naming
            rerig.name_chains(chains, Vector((2, 2, 2)))
            self.assertEqual(chains[0]["bones"], ["body", "head"])

        def test_head_line_multi_point_spine(self):
            # Spine with 4 points (3 bones: hips, spine_1, head)
            chains = [
                {"role": "spine", "joints": ["b1", "b2", "b3"],
                 "points": [Vector((0, 0, 0)), Vector((0, 0, 0.5)), Vector((0, 0, 1.0)), Vector((0, 0, 1.5))],
                 "parent": None, "ik": False, "single": False},
                {"role": "ear", "joints": ["ear_1"], "points": [Vector((0.2, 0, 1.5)), Vector((0.3, 0, 1.7))],
                 "parent": (0, 2), "ik": False}
            ]
            spec = {
                "head_line": [[0.5, 0.5, 0.75], [0.5, 0.2, 0.75]]
            }
            rerig.head_line(chains, self.mesh, spec)
            # Cut at nearest point
            self.assertGreaterEqual(len(chains[0]["points"]), 3)
            rerig.name_chains(chains, Vector((2, 2, 2)))
            self.assertIn("head", chains[0]["bones"])
            self.assertEqual(chains[0]["bones"][0], "hips")


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

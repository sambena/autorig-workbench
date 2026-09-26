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

        def test_shell_preserves_head_weights(self):
            # Create armature with body and head bones
            ad = bpy.data.armatures.new("test_arm")
            arm = bpy.data.objects.new("test_arm", ad)
            bpy.context.scene.collection.objects.link(arm)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            b_body = ad.edit_bones.new("body")
            b_body.head = (0, 0, 0); b_body.tail = (0, 0.5, 0)
            b_head = ad.edit_bones.new("head")
            b_head.head = (0, 0.5, 0); b_head.tail = (0, 1.0, 0)
            b_head.parent = b_body
            bpy.ops.object.mode_set(mode='OBJECT')

            chains = [
                {"role": "spine", "joints": ["__body"], "points": [Vector((0, 0, 0)), Vector((0, 0.5, 0)), Vector((0, 1, 0))],
                 "parent": None, "ik": False, "bones": ["body", "head"]},
            ]
            spec = {"kind": "tripo", "body": "single", "shell": "bone_unmapped"}
            size = Vector((1, 1, 1))
            log = {}
            # Vertex groups on mesh
            vg_head = self.mesh.vertex_groups.new(name="head")
            vg_body = self.mesh.vertex_groups.new(name="body")
            # Assign first half to head, second half to body
            for i, v in enumerate(self.mesh.data.vertices):
                if i < len(self.mesh.data.vertices) // 2:
                    vg_head.add([v.index], 1.0, 'REPLACE')
                else:
                    vg_body.add([v.index], 1.0, 'REPLACE')

            # Run skin pass
            rerig.skin(self.mesh, arm, chains, spec, size, log)
            # Head bone should have preserved its vertices, not wiped by shell
            head_vg = self.mesh.vertex_groups.get("head")
            head_weights = [head_vg.weight(v.index) for v in self.mesh.data.vertices if any(g.group == head_vg.index for g in v.groups)]
            self.assertGreater(len(head_weights), 0)
            self.assertNotIn("head", log.get("bones_without_skin", []))


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

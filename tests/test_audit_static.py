import json, os, shutil, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender, layout

HAVE_BLENDER = bool(blender.find(required=False))


class TestAuditStaticAndCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-static-")
        self.models_dir = os.path.join(self.tmp, "models")
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(self.models_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not installed")
    def test_static_mesh_audit_grades_pass(self):
        """A static prop FBX with no armature grades as PASS with 0 tears and max_influences <= 4."""
        model_name = "test_prop"
        pdir = os.path.join(self.models_dir, "Props", model_name)
        rdir = os.path.join(pdir, "rigged")
        os.makedirs(rdir, exist_ok=True)
        fbx_path = os.path.join(rdir, model_name + ".fbx")

        # Create a static cube FBX using Blender
        make_prop = (
            "import bpy\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            "bpy.ops.mesh.primitive_cube_add(size=1.0)\n"
            "bpy.ops.export_scene.fbx(filepath=%r, object_types={'MESH'})\n" % fbx_path
        )
        res = subprocess.run([blender.find(), "-b", "--python-expr", make_prop], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(fbx_path))

        # Run audit.py on the static prop FBX
        out_audit = os.path.join(self.work_dir, "audit")
        os.makedirs(out_audit, exist_ok=True)
        env = dict(os.environ, AUTORIG_MODELS=self.models_dir, AUTORIG_WORK=self.work_dir)
        audit_res = blender.run("audit.py", "-fbx", fbx_path, "-slug", model_name, "-out", out_audit, "-render", "0", env=env)
        self.assertEqual(audit_res.returncode, 0, audit_res.stdout + audit_res.stderr)
        self.assertIn("AUDIT_PASS test_prop", audit_res.stdout)

        json_path = os.path.join(out_audit, model_name + ".json")
        self.assertTrue(os.path.exists(json_path))
        with open(json_path) as fh:
            data = json.load(fh)
        self.assertEqual(data["verdict"]["grade"], "PASS")
        self.assertEqual(data["combined_pose"]["tear_edges"], 0)
        self.assertEqual(data["bones"], 0)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not installed")
    def test_decimate_exports_fbx_when_target_budget_is_none(self):
        """When budget is None (full resolution) and FBX is missing, decimate produces the FBX."""
        model_name = "test_fullres"
        pdir = os.path.join(self.models_dir, "FullRes", model_name)
        rdir = os.path.join(pdir, "rigged")
        os.makedirs(rdir, exist_ok=True)
        blend_path = os.path.join(rdir, model_name + ".blend")
        fbx_path = os.path.join(rdir, model_name + ".fbx")

        # Create a .blend file with a simple mesh and armature
        make_blend = (
            "import bpy\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            "bpy.ops.mesh.primitive_cube_add(size=1.0)\n"
            "mesh = bpy.context.active_object\n"
            "bpy.ops.object.armature_add()\n"
            "arm = bpy.context.active_object\n"
            "mesh.select_set(True)\n"
            "bpy.context.view_layer.objects.active = arm\n"
            "bpy.ops.object.parent_set(type='ARMATURE_AUTO')\n"
            "bpy.ops.wm.save_as_mainfile(filepath=%r)\n" % blend_path
        )
        res = subprocess.run([blender.find(), "-b", "--python-expr", make_blend], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(blend_path))
        self.assertFalse(os.path.exists(fbx_path))

        # Run decimate.py on the model
        env = dict(os.environ, AUTORIG_MODELS=self.models_dir, AUTORIG_WORK=self.work_dir)
        dec_res = blender.run("decimate.py", "-only", model_name, env=env)
        self.assertEqual(dec_res.returncode, 0, dec_res.stdout + dec_res.stderr)
        self.assertTrue(os.path.exists(fbx_path), "decimate.py did not export FBX when target budget was None")


if __name__ == "__main__":
    unittest.main()

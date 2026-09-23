# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for Mesh Doctor diagnostics and pre-flight healing.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import mesh_doctor
import layout


class MeshDoctorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-doctor-")
        cls.orig_models = os.environ.get("AUTORIG_MODELS")
        cls.orig_work = os.environ.get("AUTORIG_WORK")
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

    def test_clean_mesh_diagnostics(self):
        # Clean tetrahedron
        verts = [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.5, 0.866, 0.0],
            [0.5, 0.288, 0.816],
        ]
        faces = [
            [0, 1, 2],
            [0, 1, 3],
            [1, 2, 3],
            [2, 0, 3],
        ]
        diag = mesh_doctor.diagnose_geometry(verts, faces)
        self.assertEqual(diag["grade"], "HEALTHY")
        self.assertEqual(diag["health_score"], 100)
        self.assertEqual(diag["loose_verts"], 0)
        self.assertEqual(diag["degenerate_faces"], 0)
        self.assertEqual(diag["non_manifold_edges"], 0)
        self.assertTrue(diag["pass"])

    def test_corrupt_mesh_detection(self):
        # Tetrahedron with intentional defects:
        # 1. Floating loose vertex [2.0, 2.0, 2.0]
        # 2. Degenerate zero-area face with duplicate indices [0, 0, 1]
        # 3. Non-manifold edge shared by 3 faces
        # 4. Duplicate vertex at same coordinates as vertex 0
        verts = [
            [0.0, 0.0, 0.0],       # 0
            [1.0, 0.0, 0.0],       # 1
            [0.5, 0.866, 0.0],     # 2
            [0.5, 0.288, 0.816],   # 3
            [2.0, 2.0, 2.0],       # 4 (loose)
            [0.0, 0.0, 0.0],       # 5 (duplicate of 0)
        ]
        faces = [
            [0, 1, 2],
            [0, 1, 3],
            [1, 2, 3],
            [2, 0, 3],
            [0, 1, 5],             # Edge (0, 1) now shared by 3 faces: non-manifold!
            [0, 0, 1],             # Degenerate zero-area face!
        ]
        diag = mesh_doctor.diagnose_geometry(verts, faces)
        self.assertGreater(diag["loose_verts"], 0)
        self.assertGreater(diag["degenerate_faces"], 0)
        self.assertGreater(diag["non_manifold_edges"], 0)
        self.assertGreater(diag["duplicate_verts"], 0)
        self.assertLess(diag["health_score"], 80)
        self.assertIn(diag["grade"], ("WARN", "CRITICAL"))

    def test_inspect_obj_file(self):
        obj_path = os.path.join(self.tmp, "test_cube.obj")
        with open(obj_path, "w") as fh:
            fh.write("""
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 5 5 5
f 1 2 3
f 1 3 4
""")
        diag = mesh_doctor.inspect_source_model(obj_path)
        self.assertEqual(diag["verts"], 5)
        self.assertEqual(diag["faces"], 2)
        self.assertEqual(diag["loose_verts"], 1)

    def test_doctor_sample_models(self):
        biped_src = layout.source_model("biped")
        self.assertTrue(os.path.isfile(biped_src))
        diag = mesh_doctor.inspect_source_model(biped_src)
        self.assertIn("health_score", diag)
        self.assertIn("grade", diag)
        self.assertGreater(diag["verts"], 100)

    def test_doctor_cli_step(self):
        cmd = [sys.executable, os.path.join(REPO, "autorig", "steps", "mesh_doctor.py"), "biped"]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.returncode, 0)
        self.assertIn("DOCTOR biped", p.stdout)
        self.assertIn("DOCTOR_DONE", p.stdout)


if __name__ == "__main__":
    unittest.main()

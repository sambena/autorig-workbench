# SPDX-License-Identifier: GPL-3.0-or-later
# core/mesh_prep.py: the plain-numpy helpers here; the library calls in tests/mesh_prep_libs.py, run with Blender's
# Python and the extras when both are installed (skipped otherwise).
import os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autorig", "core"))
try:
    import numpy as np
except ImportError:      # the plain tests need numpy; Blender's Python has it
    np = None
import extras  # noqa: E402
import mesh_prep as mp  # noqa: E402


@unittest.skipIf(np is None, "numpy is not installed for this Python")
class Plain(unittest.TestCase):
    def test_weld_merges_split_seams(self):
        V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], float)
        F = np.array([[0, 1, 2], [3, 5, 4]])
        V2, F2, old = mp.weld(V, F)
        self.assertEqual(len(V2), 4)
        self.assertEqual(F2[0, 1], F2[1, 0])

    def test_components(self):
        F = np.array([[0, 1, 2], [2, 3, 4], [5, 6, 7], [7, 8, 5], [9, 10, 11]])
        lab = mp.components(F, 12)
        self.assertEqual(len(set(lab[[0, 1, 2, 3, 4]])), 1)
        self.assertEqual(len(set(lab[[5, 6, 7, 8]])), 1)
        self.assertEqual(len(set(lab)), 3)
        # a long chain, numbered against the grain, still ends as one label
        n = 200
        chain = np.array([[i + 2, i + 1, i] for i in range(n - 2)][::-1])
        self.assertEqual(len(set(mp.components(chain, n))), 1)

    def test_face_components(self):
        F = np.array([[0, 1, 2], [3, 4, 5], [2, 1, 6]])
        fc = mp.face_components(F, 7)
        self.assertEqual(fc[0], fc[2])
        self.assertNotEqual(fc[0], fc[1])

    def test_solids_contain(self):
        box = mp.Solid.box((0, 0, 0), (1, 2, 3))
        self.assertEqual(box.contains([[0.5, 1, 1], [2, 0, 0]]).tolist(), [True, False])
        disc = mp.Solid.disc((0, 0, 0), (1, 0, 0), 1.0, (-0.1, 0.1))
        self.assertEqual(disc.contains([[0, 0.5, 0.5], [0.5, 0, 0], [0, 1.2, 0]]).tolist(), [True, False, False])

    def test_hausdorff_of_a_shifted_triangle(self):
        V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float); F = np.array([[0, 1, 2]])
        self.assertAlmostEqual(mp.hausdorff(V, F, V, F), 0.0)
        d = mp.hausdorff(V, F, V + [0, 0, 0.1], F)
        self.assertAlmostEqual(d, 0.1 / np.sqrt(2), places=6)

    def test_collider_names(self):
        self.assertEqual(mp.collider_names("Barn", 2), ["Barn_Collider_00", "Barn_Collider_01"])


class WithLibraries(unittest.TestCase):
    def test_libraries(self):
        if extras.blender_python() is None:
            self.skipTest("Blender's Python not found")
        st = extras.status()
        missing = [e["name"] for e in st["extras"]
                   if e["name"] in ("pymeshlab", "pymeshfix", "xatlas", "coacd", "manifold3d") and not e["installed"]]
        if missing:
            self.skipTest("extras not installed: " + ", ".join(missing))
        r = extras.run_python([os.path.join(HERE, "mesh_prep_libs.py")], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=600)
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))


if __name__ == "__main__":
    unittest.main()

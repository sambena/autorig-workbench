# SPDX-License-Identifier: GPL-3.0-or-later
# core/mesh_prep.py with the real libraries. Runs in Blender's Python with the extras folder on its path (no bpy):
# tests/test_mesh_prep.py starts it through extras.run_python when Blender and the extras are installed.
#
#   python autorig/cli/run.py extras install pymeshlab pymeshfix xatlas coacd manifold3d
#   python -m unittest tests.test_mesh_prep
import math, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "autorig", "core")]
import numpy as np
import mesh_prep as mp


def sphere(n=48, r=1.0, centre=(0, 0, 0)):
    """A UV sphere with a real seam: per-corner UVs, u wrapping from 1 back to 0 on the seam."""
    V, F, UV = [], [], []
    rings, segs = n // 2, n
    for i in range(rings + 1):
        th = math.pi * i / rings
        for j in range(segs):
            ph = 2 * math.pi * j / segs
            V.append((r * math.sin(th) * math.cos(ph) + centre[0], r * math.sin(th) * math.sin(ph) + centre[1],
                      r * math.cos(th) + centre[2]))
    idx = lambda i, j: i * segs + (j % segs)
    for i in range(rings):
        for j in range(segs):
            a, b, c, d = idx(i, j), idx(i, j + 1), idx(i + 1, j + 1), idx(i + 1, j)
            u0, u1, v0, v1 = j / segs, (j + 1) / segs, i / rings, (i + 1) / rings
            for tri, uv in (((a, d, c), ((u0, v0), (u0, v1), (u1, v1))), ((a, c, b), ((u0, v0), (u1, v1), (u1, v0)))):
                if len(set(tri)) == 3 and not (i == 0 and tri == (a, c, b)) and not (i == rings - 1 and tri == (a, d, c)):
                    F.append(tri); UV.extend(uv)
    return np.array(V), np.array(F), np.array(UV)


def cube(lo=(0, 0, 0), hi=(1, 1, 1)):
    lo, hi = np.array(lo, float), np.array(hi, float)
    V = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float) * (hi - lo) + lo
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def volume(V, F):
    T = np.asarray(V)[np.asarray(F)]
    return float(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum() / 6.0)


class Decimate(unittest.TestCase):
    def test_uvs_and_shape_survive(self):
        V, F, UV = sphere(96)
        FD = np.zeros(len(F), dtype=np.int64); FD[V[F].mean(1)[:, 2] > 0] = 1    # two materials, top and bottom
        V2, F2, UV2, FD2 = mp.decimate(V, F, UV, FD, 1000)
        self.assertLessEqual(len(F2), 1010)
        self.assertEqual(UV2.shape, (len(F2) * 3, 2))
        self.assertEqual(set(FD2.tolist()), {0, 1})
        self.assertLess(mp.hausdorff(V, F, V2, F2), 0.02)
        # the texture is not dragged: every corner's UV still matches where it now stands on the sphere (u around,
        # v down from the pole), away from the seam (u 0 or 1) and the poles where u is undefined
        P = V2[F2].reshape(-1, 3)
        u = (np.arctan2(P[:, 1], P[:, 0]) / (2 * math.pi)) % 1.0
        v = np.arccos(np.clip(P[:, 2] / np.linalg.norm(P, axis=1), -1, 1)) / math.pi
        ok = (UV2[:, 0] > 0.02) & (UV2[:, 0] < 0.98) & (v > 0.05) & (v < 0.95)
        self.assertGreater(ok.mean(), 0.8)
        self.assertLess(np.median(np.abs(UV2[ok, 0] - u[ok])), 0.01)
        self.assertLess(np.median(np.abs(UV2[ok, 1] - v[ok])), 0.01)

    def test_without_uvs(self):
        V, F, _ = sphere(64)
        V2, F2, UV2, FD2 = mp.decimate(V, F, None, None, 500)
        self.assertLessEqual(len(F2), 510)
        self.assertIsNone(UV2)


class Repair(unittest.TestCase):
    def test_duplicates_and_non_manifold_edges(self):
        V, F = cube()
        # a duplicate face and a fin: a third face on an edge
        V = np.vstack([V, [[0.5, -1, 0.5]]])
        F = np.vstack([F, F[:1], [[0, 1, 8]]])
        before = mp.topology(V, F)
        self.assertGreater(before["non_manifold_edges"], 0)
        V2, F2, _, _, rep = mp.repair(V, F)
        self.assertEqual(rep["non_manifold_edges"][1], 0)
        self.assertEqual(mp.topology(V2, F2)["non_manifold_edges"], 0)

    def test_closes_a_small_hole(self):
        V, F = cube()
        V2, F2, _, _, rep = mp.repair(V, F[:-1])
        self.assertEqual(rep["boundary_edges"][1], 0)
        self.assertEqual(len(F2), 12)


class Watertight(unittest.TestCase):
    def test_open_multi_shell_mesh_becomes_closed_and_keeps_both(self):
        a, fa, _ = sphere(32)
        b, fb, _ = sphere(16, 0.3, (3, 0, 0))
        V = np.vstack([a, b]); F = np.vstack([fa[:-40], fb + len(a)])     # the big one open at a pole
        V2, F2, rep = mp.watertight(V, F)
        self.assertTrue(rep["joined"])
        t = mp.topology(V2, F2)
        self.assertTrue(t["two_manifold"])
        self.assertEqual(t["boundary_edges"], 0)
        self.assertGreater(V2[:, 0].max(), 3.0)                            # the small shell is still there


class Unwrap(unittest.TestCase):
    def test_atlas_in_unit_square_per_corner(self):
        V, F, _ = sphere(32)
        UV = mp.unwrap(V, F, 512, 2)
        self.assertEqual(UV.shape, (len(F) * 3, 2))
        self.assertGreaterEqual(UV.min(), 0.0)
        self.assertLessEqual(UV.max(), 1.0)
        t = UV.reshape(-1, 3, 2); e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
        self.assertGreater((area > 1e-7).mean(), 0.99)        # no triangle squashed to nothing
        self.assertLess(area.sum(), 1.0)                       # charts do not overlap past the square


class Colliders(unittest.TestCase):
    def test_l_shape_needs_two_hulls_under_unitys_limit(self):
        a, fa = cube((0, 0, 0), (3, 1, 1)); b, fb = cube((0, 0, 1), (1, 1, 3))
        V = np.vstack([a, b]); F = np.vstack([fa, fb + 8])
        hulls = mp.convex_parts(V, F, threshold=0.05, max_hulls=8)
        self.assertGreaterEqual(len(hulls), 2)
        for hv, hf in hulls:
            self.assertLessEqual(len(hf), 255)
        lo = np.min([h[0].min(0) for h in hulls], axis=0); hi = np.max([h[0].max(0) for h in hulls], axis=0)
        np.testing.assert_allclose(lo, [0, 0, 0], atol=0.1); np.testing.assert_allclose(hi, [3, 1, 3], atol=0.1)


class Split(unittest.TestCase):
    def test_closed_shell_cut_exactly_with_caps(self):
        V, F, UV = sphere(48)
        FD = np.full(len(F), 2)
        (iV, iF, iUV, iFD), (oV, oF, oUV, oFD), rep = mp.split(V, F, UV, FD, mp.Solid.box((-2, -2, 0), (2, 2, 2)))
        self.assertEqual(rep["cut_exactly"], 1)
        # each half is closed and holds about half the volume
        self.assertEqual(mp.topology(iV, iF)["boundary_edges"], 0)
        self.assertEqual(mp.topology(oV, oF)["boundary_edges"], 0)
        whole = volume(V, F)
        self.assertAlmostEqual(volume(iV, iF) / whole, 0.5, delta=0.03)
        self.assertAlmostEqual(volume(oV, oF) / whole, 0.5, delta=0.03)
        self.assertEqual(set(iFD.tolist()), {2})
        self.assertEqual(iUV.shape, (len(iF) * 3, 2))

    def test_disc_cut(self):
        V, F, UV = sphere(48)
        (iV, iF, _, _), _, rep = mp.split(V, F, UV, None, mp.Solid.disc((0, 0, 0), (1, 0, 0), 0.5, (-2, 2)))
        self.assertEqual(rep["cut_exactly"], 1)
        r = np.linalg.norm(iV[:, 1:], axis=1)
        self.assertLessEqual(r.max(), 0.5 + 1e-3)

    def test_open_shell_falls_back_to_face_centres(self):
        V, F, UV = sphere(32)
        F2, UV2 = F[:-30], UV[:-90]
        (iV, iF, _, _), (oV, oF, _, _), rep = mp.split(V, F2, UV2, None, mp.Solid.box((-2, -2, 0), (2, 2, 2)))
        self.assertEqual(rep["sorted_by_faces"], 1)
        self.assertEqual(len(iF) + len(oF), len(F2))

    def test_untouched_shells_go_whole(self):
        a, fa = cube((0, 0, 0), (1, 1, 1)); b, fb = cube((5, 0, 0), (6, 1, 1))
        V = np.vstack([a, b]); F = np.vstack([fa, fb + 8])
        (iV, iF, _, _), (oV, oF, _, _), rep = mp.split(V, F, None, None, mp.Solid.box((4, -1, -1), (7, 2, 2)))
        self.assertEqual((rep["whole_inside"], rep["whole_outside"], rep["cut_exactly"]), (1, 1, 0))
        self.assertEqual(len(iF), 12)


if __name__ == "__main__":
    unittest.main()

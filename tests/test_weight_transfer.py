# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the robust skin-weight transfer (autorig/core/weight_transfer.py) that `skinning:
# "proxy-transfer"` uses: closest points, matching, inpainting the unmatched vertices, normalising.
# Needs numpy: run with Blender's own Python where the system one has none.
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

try:
    import numpy as np
    import weight_transfer as WT
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


def grid(nx, ny, z=0.0, size=1.0):
    """A flat nx x ny vertex grid in the XY plane at height z, triangulated."""
    xs, ys = np.meshgrid(np.linspace(0, size, nx), np.linspace(0, size, ny), indexing="ij")
    V = np.stack([xs.ravel(), ys.ravel(), np.full(nx * ny, z)], 1)
    F = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a, b, c, d = i * ny + j, (i + 1) * ny + j, (i + 1) * ny + j + 1, i * ny + j + 1
            F += [(a, b, c), (a, c, d)]
    return V, np.array(F)


def ramp(V):
    """Two bones blending along x: [1 - x, x]."""
    return np.stack([1 - V[:, 0], V[:, 0]], 1)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class ClosestPoint(unittest.TestCase):
    def test_regions(self):
        A, B, C = np.array([[0, 0, 0.]]), np.array([[1, 0, 0.]]), np.array([[0, 1, 0.]])
        for p, q in (([0.2, 0.2, 1], [0.2, 0.2, 0]),     # over the face
                     ([-1, -1, 0], [0, 0, 0]),            # past vertex A
                     ([2, -0.5, 0], [1, 0, 0]),           # past vertex B
                     ([0.5, -1, 0], [0.5, 0, 0]),         # off edge AB
                     ([1, 1, 0], [0.5, 0.5, 0])):         # off edge BC
            Q, bary = WT.closest_on_triangles(np.array([p], float), A, B, C)
            np.testing.assert_allclose(Q[0], q, atol=1e-9)
            self.assertAlmostEqual(bary.sum(), 1.0)
            self.assertTrue((bary >= -1e-9).all())

    def test_brute_force_nearest(self):
        V, F = grid(4, 4)
        f, q = WT.nearest_brute(np.array([[0.5, 0.5, 0.3], [2, 0.5, 0]]), V, F)
        np.testing.assert_allclose(q, [[0.5, 0.5, 0], [1, 0.5, 0]], atol=1e-9)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Transfer(unittest.TestCase):
    def test_close_surface_copies_the_weights(self):
        # a coarse source and a fine target lying just above it: every target vertex matches and gets the source's
        # (linear, so exactly interpolated) ramp
        V, F = grid(3, 3)
        P, Ft = grid(9, 9, z=0.01)
        f, q = WT.nearest_brute(P, V, F)
        W, st = WT.robust_transfer(P, WT.vertex_normals(P, Ft), Ft, V, F, ramp(V), f, q, max_dist=0.05)
        self.assertEqual(st["matched"], len(P))
        np.testing.assert_allclose(W, ramp(P), atol=1e-6)

    def test_flipped_normals_still_match(self):
        V, F = grid(3, 3)
        P, Ft = grid(5, 5, z=0.01)
        f, q = WT.nearest_brute(P, V, F)
        N = -WT.vertex_normals(P, Ft)
        _, ok = WT.match(P, N, V, F, ramp(V), f, q, 0.05, flip=True)
        self.assertTrue(ok.all())
        _, ok = WT.match(P, N, V, F, ramp(V), f, q, 0.05, flip=False)
        self.assertFalse(ok.any())

    def test_far_vertices_are_inpainted_smoothly(self):
        # the middle of the target bulges far off the source, as an inner shell or a fold does: those vertices
        # fail the distance test and are inpainted from the matched ring round them; the ramp is linear, so the
        # smooth fill reproduces it closely, where the nearest point would not hold it either way
        V, F = grid(3, 3)
        P, Ft = grid(11, 11)
        mid = np.linalg.norm(P[:, :2] - 0.5, axis=1) < 0.25
        P = P.copy(); P[mid, 2] = 0.3
        f, q = WT.nearest_brute(P, V, F)
        W, st = WT.robust_transfer(P, WT.vertex_normals(P, Ft), Ft, V, F, ramp(V), f, q, max_dist=0.05)
        # the raised vertices and the slope round them (its normals tilt past 30 degrees) are unknown
        self.assertGreaterEqual(st["unknown"], int(mid.sum()))
        self.assertLess(st["residual"], 1e-4)
        np.testing.assert_allclose(W.sum(1), 1.0, atol=1e-9)
        self.assertLess(np.abs(W[mid] - ramp(P)[mid]).max(), 0.15)

    def test_inpaint_reproduces_a_linear_field(self):
        # on a flat mesh a linear field is harmonic, so the inpainting restores it exactly from a cold start
        P, Ft = grid(11, 11)
        hole = np.linalg.norm(P[:, :2] - 0.5, axis=1) < 0.3
        W0 = ramp(P); W0[hole] = 0.0
        W, st = WT.inpaint(P, Ft, W0, ~hole)
        self.assertEqual(st["unknown"], int(hole.sum()))
        np.testing.assert_allclose(W, ramp(P), atol=1e-4)

    def test_unanchored_piece_keeps_nearest(self):
        # a second, separate piece far off the source matches nothing: it keeps the nearest-point weights
        V, F = grid(3, 3)
        P1, F1 = grid(4, 4, z=0.01)
        P2, F2 = grid(3, 3, z=0.5, size=0.2)
        P = np.vstack([P1, P2]); Ft = np.vstack([F1, F2 + len(P1)])
        f, q = WT.nearest_brute(P, V, F)
        W, st = WT.robust_transfer(P, WT.vertex_normals(P, Ft), Ft, V, F, ramp(V), f, q, max_dist=0.05)
        self.assertEqual(st["orphan"], len(P2))
        Wn, _ = WT.match(P, WT.vertex_normals(P, Ft), V, F, ramp(V), f, q, 0.05)
        np.testing.assert_allclose(W[len(P1):], WT.normalise(Wn[len(P1):]), atol=1e-9)

    def test_normalise(self):
        W = WT.normalise(np.array([[0.5, 0.5, -0.1], [0.00001, 2.0, 0.0], [0, 0, 0]]))
        np.testing.assert_allclose(W, [[0.5, 0.5, 0], [0, 1, 0], [0, 0, 0]])


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class SparseMatrix(unittest.TestCase):
    def test_dot_and_diagonal(self):
        S = WT.Sparse(3, [0, 0, 2, 2, 0], [0, 2, 1, 1, 0], [1., 2., 3., 4., 5.])   # duplicates summed
        dense = np.array([[6., 0, 2], [0, 0, 0], [0, 7, 0]])
        X = np.arange(6.).reshape(3, 2)
        np.testing.assert_allclose(S.dot(X), dense @ X)
        np.testing.assert_allclose(S.diagonal(), [6, 0, 0])

    def test_stiffness_is_a_laplacian(self):
        V, F = grid(4, 4)
        K, M = WT.cotan_stiffness(V, F)
        np.testing.assert_allclose(K.dot(np.ones(len(V))), 0, atol=1e-9)       # constants in the null space
        x = np.random.default_rng(1).normal(size=len(V))
        self.assertGreaterEqual(float(x @ K.dot(x)), -1e-9)                     # positive semi-definite
        self.assertAlmostEqual(M.sum(), 1.0, places=6)                          # mass = area


if __name__ == "__main__":
    unittest.main()

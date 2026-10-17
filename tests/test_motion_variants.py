# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: clip variants (autorig/core/motion_variants.py) and the GenMM port (autorig/core/genmm).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
try:
    import numpy as np
    import genmm
    import motion_variants as mv
except ImportError:                       # no numpy in this Python (Blender's has it: CONTRIBUTING.md)
    np = None


def idle(T=48):
    """A two-bone idle: a breathing body (up and down) and a head turning, as an authored loop has them."""
    t = np.arange(T) / float(T) * 2 * math.pi
    body = np.stack([np.zeros(T), np.zeros(T), 0.02 * np.sin(t)], axis=1)
    ang = 0.3 * np.sin(t) + 0.1 * np.sin(3 * t)
    head = np.stack([np.cos(ang / 2), np.zeros(T), np.zeros(T), np.sin(ang / 2)], axis=1)
    return {("body", "location"): ("loc", body), ("head", "rotation_quaternion"): ("quat", head)}


@unittest.skipIf(np is None, "numpy is not installed")
class TestGenMMPort(unittest.TestCase):
    def test_patches_round_trip(self):
        x = np.random.default_rng(1).standard_normal((3, 20))
        for loop in (False, True):
            p = genmm.extract_patches(x, 5, loop=loop)
            self.assertEqual(p.shape, (20 if loop else 16, 15))
            back = genmm.combine_patches(x.shape, p, 5, loop=loop)
            self.assertTrue(np.allclose(back, x))

    def test_nearest_neighbours(self):
        X = np.array([[0.0, 0.0], [1.0, 1.0]])
        Y = np.array([[1.0, 1.1], [0.0, 0.1], [5.0, 5.0]])
        nn, d = genmm.get_nns_dists(X, Y)
        self.assertEqual(list(nn), [1, 0])
        self.assertTrue(np.allclose(genmm.efficient_cdist(X, Y)[0, 1], 0.005))

    def test_pyramid_lengths(self):
        self.assertEqual(genmm.pyramid_lengths(100, 0.2, 0.75)[-1], 100)
        ls = genmm.pyramid_lengths(100, 0.2, 0.75)
        self.assertEqual(ls, sorted(ls))
        self.assertEqual(ls[0], 20)

    def test_interpolate_matches_torch_linear(self):
        x = np.arange(4.0)[None, :]
        self.assertTrue(np.allclose(genmm.interpolate(x, 7, align_corners=True), np.linspace(0, 3, 7)[None, :]))
        self.assertTrue(np.allclose(genmm.interpolate(x, 2), [[0.5, 2.5]]))

    def test_no_noise_reproduces_the_example(self):
        x = np.sin(np.linspace(0, 2 * math.pi, 40, endpoint=False))[None, :]
        crit = genmm.PatchCoherentLoss(5, alpha=0.01, loop=True)
        y = genmm.GenMM(0).run([x], crit, 40, noise_sigma=0.0, coarse_ratio=0.6)
        self.assertLess(np.abs(y - x).max(), 0.05)


@unittest.skipIf(np is None, "numpy is not installed")
class TestVariants(unittest.TestCase):
    def test_6d_round_trip(self):
        rng = np.random.default_rng(3)
        q = rng.standard_normal((50, 4)); q /= np.linalg.norm(q, axis=1, keepdims=True)
        back = mv.sixd_to_quat(mv.quat_to_6d(q))
        self.assertTrue(np.allclose(np.abs((q * back).sum(1)), 1.0, atol=1e-9))

    def test_patch_size(self):
        self.assertEqual(mv.patch_size_for(16), 3)
        self.assertEqual(mv.patch_size_for(48), 7)
        self.assertEqual(mv.patch_size_for(400), 11)

    def test_variant_is_longer_varied_and_plausible(self):
        src = idle()
        out = mv.synthesize(src, 96, loop=True, seed=5)
        body = out[("body", "location")]
        head = out[("head", "rotation_quaternion")]
        self.assertEqual(body.shape, (96, 3))
        self.assertTrue(np.allclose(np.linalg.norm(head, axis=1), 1.0))
        # not the example twice over
        self.assertGreater(mv.difference(body[:48, 2], body[48:, 2]), 1e-4)
        # but made of it: within the example's range
        self.assertLess(np.abs(body[:, 2]).max(), 0.025)
        # loops: the seam step is no bigger than the steps inside
        steps = np.abs(np.diff(body[:, 2]))
        self.assertLessEqual(abs(body[0, 2] - body[-1, 2]), steps.max() * 1.5 + 1e-6)

    def test_seeds_differ_and_repeat(self):
        a = mv.synthesize(idle(), 96, seed=1)[("body", "location")]
        b = mv.synthesize(idle(), 96, seed=2)[("body", "location")]
        c = mv.synthesize(idle(), 96, seed=1)[("body", "location")]
        self.assertGreater(mv.difference(a, b), 1e-5)
        self.assertTrue(np.allclose(a, c))


if __name__ == "__main__":
    unittest.main()

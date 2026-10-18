# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: core/skin_fade.py (the weights faded across each joint as far as a bend there needs). Plain numpy.
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autorig", "core"))
import skin_fade as SF  # noqa: E402


def tube(rings=41, around=16, length=2.0, radius=0.3):
    """A closed-ish tube along +Y (rings x around vertices) and its edges."""
    P, E = [], []
    for i in range(rings):
        y = length * i / (rings - 1)
        for j in range(around):
            a = 2 * math.pi * j / around
            P.append((radius * math.cos(a), y, radius * math.sin(a)))
    idx = lambda i, j: i * around + j % around
    for i in range(rings):
        for j in range(around):
            E.append((idx(i, j), idx(i, j + 1)))
            if i + 1 < rings:
                E.append((idx(i, j), idx(i + 1, j)))
                E.append((idx(i, j), idx(i + 1, j + 1)))
    return np.array(P), np.array(E)


# root (no skin) -> upper (0..1) -> lower (1..2)
PARENT = [-1, 0, 1]
DEFORM = [False, True, True]
HEADS = np.array([(0, 0, 0), (0, 0, 0), (0, 1.0, 0)], float)


def hard_weights(P):
    W = np.zeros((len(P), 3))
    W[P[:, 1] < 1.0, 1] = 1.0
    W[P[:, 1] >= 1.0, 2] = 1.0
    return W


def tears_bending(P, E, W, deg=40):
    """Edges stretched past 2x when the lower bone turns deg about X at its head (linear blend skinning)."""
    a = math.radians(deg)
    R = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    h = HEADS[2]
    moved = (P - h) @ R.T + h
    Q = P * (1 - W[:, 2:3]) + moved * W[:, 2:3]
    L0 = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
    L1 = np.linalg.norm(Q[E[:, 0]] - Q[E[:, 1]], axis=1)
    return int((L1 > 2 * L0).sum())


class Fade(unittest.TestCase):
    def setUp(self):
        self.P, self.E = tube()
        self.W = hard_weights(self.P)

    def test_hard_step_tears_and_the_fade_does_not(self):
        self.assertGreater(tears_bending(self.P, self.E, self.W), 0)
        out, it = SF.fade(self.P, self.E, self.W, PARENT, DEFORM, HEADS, angle=40)
        self.assertGreater(it, 0)
        self.assertEqual(tears_bending(self.P, self.E, out), 0)

    def test_weights_are_a_partition_with_nothing_on_the_root(self):
        out, _ = SF.fade(self.P, self.E, self.W, PARENT, DEFORM, HEADS, angle=40)
        self.assertTrue(np.all(out >= 0))
        np.testing.assert_allclose(out.sum(1), 1.0, atol=1e-9)
        self.assertEqual(float(np.abs(out[:, 0]).max()), 0.0)

    def test_the_fade_runs_along_the_tube_and_ends_where_it_was(self):
        out, _ = SF.fade(self.P, self.E, self.W, PARENT, DEFORM, HEADS, angle=40)
        ys = np.round(self.P[:, 1], 6)
        means = [float(out[ys == y, 2].mean()) for y in sorted(set(ys))]
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(means, means[1:])))      # rises along the tube
        self.assertLess(means[0], 0.05)
        self.assertGreater(means[-1], 0.95)
        mid = [m for y, m in zip(sorted(set(ys)), means) if abs(y - 1.0) < 0.06]
        self.assertTrue(all(0.2 < m < 0.8 for m in mid))                           # the joint is mixed

    def test_a_bone_that_deforms_nothing_gets_nothing(self):
        # root -> girdle (no skin) -> leg: the girdle's column stays 0, the leg's F fades
        deform = [False, False, True]
        W = np.zeros((len(self.P), 3)); W[:, 2] = 1.0
        W[self.P[:, 1] < 1.0, 2] = 0.0
        # the rest belongs to nothing: put it on a body bone instead, a fourth bone off the root
        W4 = np.zeros((len(self.P), 4)); W4[:, 2] = W[:, 2]; W4[:, 3] = 1 - W[:, 2]
        out, _ = SF.fade(self.P, self.E, W4, [-1, 0, 1, 0], deform + [True],
                         np.vstack([HEADS, [(0, 0, 0)]]), angle=40)
        self.assertEqual(float(out[:, 1].max()), 0.0)
        np.testing.assert_allclose(out.sum(1), 1.0, atol=1e-9)

    def test_angle_zero_changes_nothing(self):
        out, it = SF.fade(self.P, self.E, self.W, PARENT, DEFORM, HEADS, angle=0)
        np.testing.assert_array_equal(out, self.W)
        self.assertEqual(it, 0)


CHAIN = [-1, 0, 1, 2, 3, 4]          # root -> a -> b -> c -> d -> e
CHAIN_DEFORM = [False] + [True] * 5


class LimitUp(unittest.TestCase):
    def test_extra_influences_go_up_the_chain(self):
        W = np.array([[0, 0.3, 0.25, 0.2, 0.15, 0.1]])
        out = SF.limit_up(W, CHAIN, CHAIN_DEFORM, most=4)
        self.assertEqual(int((out > 0).sum()), 4)
        np.testing.assert_allclose(out.sum(), 1.0)
        # e (0.1) went to its parent d, so the subtree sums above d are unchanged
        np.testing.assert_allclose(out[0, 4], 0.25)
        np.testing.assert_allclose(out[0, 1:4], W[0, 1:4])

    def test_tiny_weights_go_too(self):
        out = SF.limit_up(np.array([[0, 0.995, 0.005]]), [-1, 0, 1], [False, True, True], most=4, least=0.01)
        np.testing.assert_allclose(out, [[0, 1.0, 0]])

    def test_rows_within_the_limit_are_untouched(self):
        W = np.array([[0, 0.5, 0.5, 0, 0, 0], [0, 0.25, 0.25, 0.25, 0.25, 0]])
        np.testing.assert_allclose(SF.limit_up(W, CHAIN, CHAIN_DEFORM, most=4), W)


class FadeRounds(unittest.TestCase):
    def test_four_influences_and_no_tear(self):
        # a tube of five short bones, hard steps between them: the rounds end on four influences or fewer and the
        # 40-degree bend of each joint tears nothing
        P, E = tube(rings=61, length=3.0)
        heads = np.array([(0, 0, 0)] + [(0, 0.5 * i, 0) for i in range(5)], float)
        W = np.zeros((len(P), 6))
        for i in range(5):
            W[(P[:, 1] >= 0.5 * i) & ((P[:, 1] < 0.5 * (i + 1)) | (i == 4)), i + 1] = 1.0
        self.assertGreater(sum(SF.over_bound(P, E, W, CHAIN, heads).values()), 0)
        out, passes = SF.fade_rounds(P, E, W, CHAIN, CHAIN_DEFORM, heads, rounds=5)
        self.assertGreater(passes, 0)
        self.assertTrue(np.all((out > 0).sum(1) <= 4))
        np.testing.assert_allclose(out.sum(1), 1.0, atol=1e-9)
        self.assertEqual(SF.over_bound(P, E, out, CHAIN, heads), {})


class OverBound(unittest.TestCase):
    def test_the_hard_step_is_over_and_the_fade_is_not(self):
        P, E = tube()
        W = hard_weights(P)
        self.assertGreater(sum(SF.over_bound(P, E, W, PARENT, HEADS).values()), 0)
        out, _ = SF.fade(P, E, W, PARENT, DEFORM, HEADS, angle=40)
        self.assertEqual(SF.over_bound(P, E, out, PARENT, HEADS), {})


if __name__ == "__main__":
    unittest.main()

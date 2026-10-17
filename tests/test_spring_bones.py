# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: secondary motion springs (autorig/core/spring_bones.py, after Wiggle 2).
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import spring_bones as sb


def tail(n=24, sway=0.2):
    """A three-joint tail, horizontal along +Y, whose root sways side to side once a cycle."""
    out = []
    for f in range(n):
        x = sway * math.sin(2 * math.pi * f / n)
        out.append([(x, 0.0, 1.0), (x, 0.3, 1.0), (x, 0.6, 1.0)])
    return out


class TestSprings(unittest.TestCase):
    def test_still_chain_stays_put_without_gravity(self):
        frames = [[(0, 0, 1), (0, 0.3, 1), (0, 0.6, 1)]] * 10
        out = sb.simulate(frames, gravity=0.0, loop=False)
        for f in out:
            for a, b in zip(f, frames[0]):
                self.assertLess(math.dist(a, b), 1e-9)

    def test_keeps_lengths_and_root(self):
        frames = tail()
        out = sb.simulate(frames, stiffness=200)
        for f, anim in zip(out, frames):
            self.assertEqual(f[0], anim[0])
            self.assertAlmostEqual(math.dist(f[0], f[1]), 0.3, places=6)
            self.assertAlmostEqual(math.dist(f[1], f[2]), 0.3, places=6)

    def test_tip_lags_the_root(self):
        frames = tail()
        out = sb.simulate(frames, stiffness=150, gravity=0.0)
        # the tip's sway peaks later than the root's
        root = [f[0][0] for f in frames]
        tip = [f[2][0] for f in out]
        self.assertGreater(tip.index(max(tip)), root.index(max(root)))

    def test_loop_closes(self):
        out = sb.simulate(tail(), stiffness=150)
        # frame 0 follows the last frame as smoothly as any two frames follow each other
        steps = [math.dist(out[f][2], out[f + 1][2]) for f in range(len(out) - 1)]
        self.assertLessEqual(math.dist(out[-1][2], out[0][2]), max(steps) * 1.5)

    def test_gravity_droops_a_soft_chain(self):
        frames = [[(0, 0, 1), (0, 0.3, 1), (0, 0.6, 1)]] * 48
        out = sb.simulate(frames, stiffness=20, gravity=1.0, loop=False)
        self.assertLess(out[-1][2][2], 0.99)

    def test_stiff_chain_follows(self):
        frames = tail()
        out = sb.simulate(frames, stiffness=100000, gravity=0.0)
        for f, anim in zip(out, frames):
            self.assertLess(math.dist(f[2], anim[2]), 0.02)

    def test_turn(self):
        v = sb._turn((1, 0, 0), (0, 1, 0), (1, 0, 0))
        self.assertAlmostEqual(v[1], 1.0)
        v = sb._turn((1, 0, 0), (-1, 0, 0), (1, 0, 0))
        self.assertAlmostEqual(v[0], -1.0)


if __name__ == "__main__":
    unittest.main()

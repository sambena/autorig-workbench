# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the clip audit's grades (core/clip_grades.py) on synthetic motion: a clean walk passes, and a
# slipping foot, a pop, a hitched loop seam, a foot through the floor and lopsided strides each fail their check.
import math, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

try:
    import numpy as np
    import clip_grades as CG
    from placed_rules import bone_side
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

FPS, N, SPEED, STRIDE, H = 24, 24, 1.0, None, 1.0


def walk(slip=0.0, reach=(1.0, 1.0), lift=0.08, frames=N, speed=SPEED, duty=0.5):
    """Two feet stepping in place (the engine moves the body at `speed` along -Y): in stance a foot goes back at
    exactly the body's speed (plus `slip` x speed if it slides), in swing it comes forward, lifted."""
    T = frames / float(FPS)
    stride = speed * duty * T
    feet = {}
    for side, (name, off, r) in enumerate((("foot.L", 0.0, reach[0]), ("foot.R", 0.5, reach[1]))):
        p = np.zeros((frames + 1, 3))
        for f in range(frames + 1):
            ph = (f / float(frames) + off) % 1.0
            if ph < duty:
                s = ph / duty
                y = -stride * r * (0.5 - s) * (1.0 - slip)       # forward (-Y) to back (+Y), short by `slip`
                z = 0.0
            else:
                s = (ph - duty) / (1.0 - duty)
                y = -stride * r * (-0.5 + s) * (1.0 - slip)
                z = lift * math.sin(math.pi * s)
            p[f] = (0.15 if side == 0 else -0.15, y, z)
        feet[name] = p
    rest = {n: np.array([p[0][0], 0.0, 0.0]) for n, p in feet.items()}
    root = np.zeros((frames + 1, 3))
    return feet, rest, root


def turning(frames=N, bones=3, amp=20.0, jump_at=None, jump=0.0, loop=True):
    """Bones swinging smoothly about X (periodic over the clip when `loop`); `jump` degrees added from frame
    `jump_at` on (a pop)."""
    q = np.zeros((frames + 1, bones, 4))
    for f in range(frames + 1):
        for b in range(bones):
            a = amp * math.sin(2 * math.pi * f / frames + b) if loop else amp * f / frames
            if jump_at is not None and f >= jump_at: a += jump
            h = math.radians(a) / 2
            q[f, b] = (math.cos(h), math.sin(h), 0, 0)
    return q


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class ClipGrades(unittest.TestCase):
    def grade(self, feet, rest, root, rots=None, slot="locomotion", loops=True, follows=True, speed=SPEED):
        return CG.grade_clip(slot, loops, follows, FPS, turning() if rots is None else rots, feet=feet, rest=rest,
                             root=root, speed_units=speed, height=H, side_of=bone_side)

    def test_clean_walk_passes(self):
        g = self.grade(*walk())
        self.assertEqual(g["grade"], "PASS", g)
        self.assertLess(g["checks"]["slide"]["value"], 0.02)
        self.assertFalse(g["checks"]["slide"]["root_motion"])

    def test_sliding_feet_fail(self):
        g = self.grade(*walk(slip=0.4))                       # feet cover 60% of the ground the body does
        self.assertEqual(g["checks"]["slide"]["grade"], "FAIL")
        self.assertAlmostEqual(g["checks"]["slide"]["value"], 0.4, delta=0.05)
        self.assertEqual(self.grade(*walk(slip=0.15))["checks"]["slide"]["grade"], "CHECK")

    def test_moonwalk_is_caught(self):
        # planted feet moving forward with the body (the legless heave, or a stride authored backwards): double
        feet, rest, root = walk()
        feet = {n: p * np.array([1, -1, 1]) for n, p in feet.items()}
        self.assertGreater(self.grade(feet, rest, root)["checks"]["slide"]["value"], 1.5)

    def test_clip_carrying_its_own_travel(self):
        feet, rest, root = walk()
        t = np.arange(N + 1) / FPS
        move = np.outer(t * SPEED, [0, -1, 0])
        feet = {n: p + move for n, p in feet.items()}
        g = self.grade(feet, rest, root + move)
        self.assertTrue(g["checks"]["slide"]["root_motion"])
        self.assertLess(g["checks"]["slide"]["value"], 0.02)

    def test_pops(self):
        self.assertEqual(CG.pops(turning())[0], 0)
        n, worst, at = CG.pops(turning(jump_at=10, jump=40.0))
        self.assertEqual(n, 1)
        self.assertEqual(at[0], 10)
        g = self.grade(*walk(), rots=turning(jump_at=10, jump=40.0))
        self.assertEqual(g["checks"]["pops"]["grade"], "CHECK")
        # a hit or a death is meant to be sudden: 40 degrees in a frame is not a pop there
        self.assertEqual(CG.pops(turning(jump_at=10, jump=40.0), sudden=True)[0], 0)

    def test_loop_seam(self):
        pose, jump = CG.seam(turning())
        self.assertLess(pose, 0.01)
        self.assertLess(jump, 1.0)
        hitch = turning(loop=False, amp=60.0)                  # keeps turning one way: the seam snaps back
        pose, jump = CG.seam(hitch)
        self.assertGreater(pose, 5.0)
        g = self.grade(*walk(), rots=hitch)
        self.assertEqual(g["checks"]["seam"]["grade"], "FAIL")

    def test_floor(self):
        feet, rest, root = walk()
        feet["foot.L"][:, 2] -= 0.05                           # 5% of the height under the floor
        g = self.grade(feet, rest, root)
        self.assertEqual(g["checks"]["floor"]["grade"], "FAIL")
        feet, rest, root = walk()
        hover = {n: p + np.array([0, 0, 0.04]) for n, p in feet.items()}
        g = self.grade(hover, rest, root)
        self.assertEqual(g["checks"]["floor"]["hover_pct"], 4.0)
        self.assertEqual(g["checks"]["floor"]["grade"], "CHECK")

    def test_a_flyers_fly_and_hover_are_not_held_to_the_floor(self):
        feet, rest, root = walk()
        up = {n: p + np.array([0, 0, 0.5]) for n, p in feet.items()}
        g = self.grade(up, rest, root, follows=False)          # fly: locomotion at its own rate
        self.assertNotIn("floor", g["checks"])
        self.assertNotIn("slide", g["checks"])
        g = CG.grade_clip("idle", True, False, FPS, turning(), feet=up, rest=rest, root=root, height=H, grounded=False)
        self.assertEqual(g["checks"]["floor"]["hover_pct"], None)
        self.assertEqual(g["grade"], "PASS")

    def test_symmetry(self):
        self.assertLess(self.grade(*walk())["checks"]["symmetry"]["value"], 0.02)
        g = self.grade(*walk(reach=(1.0, 0.5)))
        self.assertEqual(g["checks"]["symmetry"]["grade"], "FAIL")

    def test_model_grade_ignores_extra_clips(self):
        clips = [{"slot": "locomotion", "grade": "PASS"}, {"slot": "extra", "grade": "FAIL"}]
        self.assertEqual(CG.grade_model(clips), "PASS")
        self.assertEqual(CG.grade_model(clips + [{"slot": "attack", "grade": "CHECK"}]), "CHECK")


if __name__ == "__main__":
    unittest.main()

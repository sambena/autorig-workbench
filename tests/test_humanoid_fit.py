# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a person's humanoid section measured from the mesh (autorig/core/humanoid_fit.py), on a body
# made of cylinders and boxes: a T-pose, the same body in a long robe and an A-pose. Needs numpy (Blender's Python
# where the system one has none).
import math, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

try:
    import numpy as np
    import humanoid_fit as F
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

RNG = None


def cylinder(a, b, r, n):
    """n points on the side of a cylinder from a to b."""
    a, b = np.array(a, float), np.array(b, float)
    d = b - a; L = np.linalg.norm(d); d /= L
    u = np.cross(d, (0, 0, 1) if abs(d[2]) < 0.9 else (1, 0, 0)); u /= np.linalg.norm(u)
    v = np.cross(d, u)
    t = RNG.random(n) * L; th = RNG.random(n) * 2 * math.pi
    return a + t[:, None] * d + r * (np.cos(th)[:, None] * u + np.sin(th)[:, None] * v)


def box(lo, hi, n):
    return np.array(lo) + RNG.random((n, 3)) * (np.array(hi) - np.array(lo))


def cone(z0, z1, r0, r1, n):
    t = RNG.random(n); th = RNG.random(n) * 2 * math.pi
    r = r0 + (r1 - r0) * t
    return np.c_[r * np.cos(th), r * np.sin(th) * 0.7, z0 + (z1 - z0) * t]


def person(arms="t", robe=False):
    """A body 1 tall, facing -Y: legs 0.06 either side of the middle, hips at 0.52, shoulders 0.1 out at 0.79."""
    parts = []
    for s in (1, -1):
        parts.append(cylinder((0.06 * s, 0, 0.05), (0.06 * s, 0, 0.52), 0.035, 6000))     # leg
        parts.append(box((0.06 * s - 0.03, -0.09, 0.0), (0.06 * s + 0.03, 0.03, 0.05), 1500))   # foot
        if arms == "t":
            parts.append(cylinder((0.1 * s, 0, 0.79), (0.47 * s, 0, 0.79), 0.028, 5000))
        else:   # 45 degrees down
            parts.append(cylinder((0.1 * s, 0, 0.79), ((0.1 + 0.3) * s, 0, 0.49), 0.028, 5000))
    parts.append(box((-0.1, -0.06, 0.5), (0.1, 0.06, 0.82), 12000))       # torso
    parts.append(cylinder((0, 0, 0.82), (0, 0, 0.86), 0.035, 800))         # neck
    parts.append(box((-0.06, -0.07, 0.86), (0.06, 0.07, 1.0), 3000))       # head
    if robe:
        parts.append(cone(0.12, 0.55, 0.24, 0.11, 15000))
    pts = np.vstack(parts)
    lo = pts.min(axis=0); size = pts.max(axis=0) - lo
    return pts, lo, size


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Fit(unittest.TestCase):
    def setUp(self):
        global RNG
        RNG = np.random.default_rng(7)

    def test_wants_auto(self):
        self.assertTrue(F.wants_auto(F.DEFAULT_HUMANOID))
        tuned = {"z": dict(F.DEFAULT_HUMANOID["z"], hip=0.56), "x": dict(F.DEFAULT_HUMANOID["x"])}
        self.assertFalse(F.wants_auto(tuned))
        self.assertTrue(F.wants_auto(dict(tuned, auto=True)))
        self.assertFalse(F.wants_auto(dict(F.DEFAULT_HUMANOID, auto=False)))
        self.assertFalse(F.wants_auto(None))

    def check_body(self, out, log):
        z, x = out["z"], out["x"]
        H = 1.0
        self.assertAlmostEqual(z["arm"], 0.79, delta=0.03, msg=log)
        self.assertGreater(z["neck"], z["arm"])
        self.assertAlmostEqual(z["hip"], z["ankle"] + F.AUTO["hip_share"] * (z["neck"] - z["ankle"]), delta=1e-3)
        self.assertTrue(z["ankle"] < z["knee"] < z["hip"] < z["spine"] < z["spine1"] < z["spine2"] < z["neck"] < z["head"])
        self.assertAlmostEqual(log["arms"]["shoulder_half"], 0.1, delta=0.03, msg=log)
        return z, x

    def test_t_pose(self):
        pts, lo, size = person("t")
        log = {}
        out = F.auto_landmarks(pts, lo, size, None, None, log)
        z, x = self.check_body(out, log)
        self.assertTrue(log["legs"]["seen"], log)
        wx = size[0] / size[2]
        self.assertAlmostEqual(x["leg"] * wx, 0.06, delta=0.015)        # each leg 0.06 out, in height units
        self.assertLess(x["tip"], 0.03)                                  # the fingertips are the bounds' edges
        self.assertTrue(x["tip"] < x["knuckle"] < x["wrist"] < x["elbow"] < x["shoulder"] < 0.5)

    def test_robe_keeps_the_legs_under_the_body(self):
        """A robe flaring to 0.24 either side: the legs are read from the feet under it, never out at the hem."""
        pts, lo, size = person("t", robe=True)
        log = {}
        out = F.auto_landmarks(pts, lo, size, None, None, log)
        self.check_body(out, log)
        wx = size[0] / size[2]
        self.assertAlmostEqual(out["x"]["leg"] * wx, 0.06, delta=0.015, msg=log)
        self.assertTrue(out.get("robe"), log)                            # covered from the hem up
        self.assertLess(float(out["robe"]), 0.2)

    def test_a_pose(self):
        pts, lo, size = person("a")
        log = {}
        out = F.auto_landmarks(pts, lo, size, None, None, log)
        self.check_body(out, log)

    def test_auto_section_keeps_what_it_does_not_measure(self):
        pts, lo, size = person("t")
        h = dict(F.DEFAULT_HUMANOID, forward=[0, 1, 0], rest_arms="keep")
        out = F.auto_section(h, pts, lo, size)
        self.assertEqual(out["forward"], [0, 1, 0])
        self.assertEqual(out["rest_arms"], "keep")
        self.assertNotIn("straight_arms", out)                          # the sleeve tracker places the arm's joints
        self.assertNotEqual(out["z"]["hip"], F.DEFAULT_HUMANOID["z"]["hip"])
        self.assertEqual(F.DEFAULT_HUMANOID["z"]["hip"], 0.47)           # the default itself is untouched


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: autorig/core/m2m_skin.py, the NumPy port of Mesh2Motion's skin solver. Small hand-made cases for
# the rules it ports, and (when Node and the submodule's npm packages are there) the parity run against the original
# TypeScript on M2M's sample models, scripts/m2m_skin_parity.py.
# Needs numpy: run with Blender's own Python where the system one has none.
import os, shutil, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "scripts")]

try:
    import numpy as np
    import m2m_skin as S
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

M2M = os.path.join(REPO, "third_party", "mesh2motion-app")
HAVE_NODE = bool(shutil.which("node") or shutil.which("node.exe")) and \
    os.path.exists(os.path.join(M2M, "node_modules", "vitest", "vitest.mjs"))


# every template but the snake needs a hips bone (M2M's position tracking bone); this one is out of the way
FAR_HIPS = ("hips", (0, 50, 0), "root")


def strip(n, x0=0.0, x1=1.0):
    """A 2 x n grid of vertices along x (y 0 and 0.1), as triangles."""
    xs = np.linspace(x0, x1, n)
    V = np.array([(x, y, 0.0) for x in xs for y in (0.0, 0.1)])
    F = []
    for i in range(n - 1):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        F += [(a, c, b), (b, c, d)]
    return V, np.array(F)


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class TestRig(unittest.TestCase):
    def test_order_is_depth_first_children_as_given(self):
        r = S.Rig([("b", (0, 1, 0), "root"), ("root", (0, 0, 0), None), ("c", (0, 2, 0), "b"), ("d", (1, 1, 0), "root")])
        self.assertEqual(r.names, ["root", "b", "c", "d"])
        self.assertEqual(r.parent.tolist(), [-1, 0, 1, 0])
        np.testing.assert_allclose(r.midpoints()[0], (0, 0.5, 0))   # halfway to the first child
        np.testing.assert_allclose(r.midpoints()[2], (0, 2, 0))     # childless: its own head

    def test_categories(self):
        r = S.Rig([("root", (0, 0, 0), None), ("hips", (0, 1, 0), "root"), ("upperarm_l", (0, 1, 0), "hips"),
                   ("hand_l", (0, 1, 0), "upperarm_l"), ("thing", (0, 1, 0), "hips"), ("index_leaf_l", (0, 1, 0), "hand_l")])
        cat = {n: r.category(i) for i, n in enumerate(r.names)}
        self.assertEqual(cat, {"root": S.ROOT, "hips": S.TORSO, "upperarm_l": S.LIMB, "hand_l": S.EXTREMITY,
                               "thing": S.OTHER, "index_leaf_l": S.EXTREMITY})
        self.assertTrue(r.is_leaf(r.names.index("index_leaf_l")))
        self.assertFalse(r.is_leaf(r.names.index("hand_l")))

    def test_unknown_template(self):
        with self.assertRaises(ValueError): S.template_type("cow")
        self.assertEqual(S.template_type("Shark"), "fish")


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class TestRules(unittest.TestCase):
    def test_to_fixed_rounds_ties_away_from_zero(self):
        self.assertEqual(S._js_fixed6(0.0078125), "0.007813")      # JS; Python's format gives 0.007812
        self.assertEqual(S._js_fixed6(-0.0078125), "-0.007813")
        self.assertEqual(S._js_fixed6(-0.0), "0.000000")
        self.assertEqual(S._js_fixed6(-1e-7), "-0.000000")

    def test_limb_blends_the_child_side_only(self):
        V, F = strip(11, 0.0, 1.0)
        r = S.Rig([("root", (0, 0, 0), None), FAR_HIPS, ("leg_1", (0, 0, 0), "root"), ("leg_2", (0.5, 0, 0), "leg_1"),
                   ("leg_tip", (1.0, 0, 0), "leg_2")])
        idx, w = S.solve(r, V, F, "fox")
        np.testing.assert_allclose(w.sum(axis=1), 1.0)
        own = idx[:, 0]
        blended = w[:, 0] < 1.0
        self.assertTrue(blended.any())
        self.assertTrue(np.all(own[blended] == r.names.index("leg_2")))   # the child's side
        self.assertNotIn(0, own.tolist())                                 # never the root
        self.assertNotIn(r.names.index("leg_tip"), own.tolist())          # nor the tip

    def test_torso_rings(self):
        V, F = strip(21, 0.0, 2.0)
        r = S.Rig([("root", (0, 0, 0), None), FAR_HIPS, ("spine_1", (0, 0, 0), "root"), ("spine_2", (1.0, 0, 0), "spine_1"),
                   ("spine_tip", (2.0, 0, 0), "spine_2")])
        idx, w = S.solve(r, V, F, "fox")
        second = sorted(set(np.round(w[:, 1], 6)) - {0.0})
        self.assertEqual(second, [0.1, 0.25, 0.5])

    def test_crotch_rule(self):
        # a human: hips (pelvis) high, the legs below; a vertex above the crotch depth may not go to the hips
        V, F = strip(5, -0.2, 0.2)
        V = np.r_[V + (0, 0.9, 0), [[0.0, 0.05, 0.0]]]
        F = np.r_[F, [[0, 2, 1]]]
        r = S.Rig([("root", (0, 0, 0), None), ("pelvis", (0, 1.0, 0), "root"), ("spine", (0, 1.2, 0), "pelvis"),
                   ("head_leaf", (0, 1.4, 0), "spine"), ("thigh_l", (0.1, 0.9, 0), "pelvis"),
                   ("calf_l", (0.1, 0.5, 0), "thigh_l"), ("foot_leaf_l", (0.1, 0.0, 0), "calf_l")])
        idx, _ = S.solve(r, V, F, "human")
        self.assertNotIn(r.names.index("pelvis"), idx[:, 0].tolist())


@unittest.skipUnless(HAVE_NUMPY and HAVE_NODE, "needs numpy, node and npm ci in third_party/mesh2motion-app")
class TestParity(unittest.TestCase):
    def test_matches_typescript_on_m2m_samples(self):
        import m2m_skin_parity as P
        rows = P.run(list(P.MODELS), os.path.join(tempfile.gettempdir(), "m2m_skin_parity_test"))
        self.assertTrue(rows)
        bad = [r for r in rows if not r["ok"]]
        self.assertFalse(bad, bad)


if __name__ == "__main__":
    unittest.main()

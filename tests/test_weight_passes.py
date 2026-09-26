# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the R3 fixes to the skin passes (autorig/core/placed_rules.py), with the bone names the tool's
# own builders produce (rerig.name_chains, rerig_humanoid.chains_for), not names picked to trigger each rule.
# Needs numpy: run with Blender's own Python where the system one has none.
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

try:
    import numpy as np
    import placed_rules as PR
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

BIPED = ["hips", "spine_1", "spine_2", "neck", "head",
         "leg_1.L", "leg_2.L", "leg_3.L", "leg_1.R", "leg_2.R", "leg_3.R",
         "arm_0.L", "arm_1.L", "arm_2.L", "arm_3.L", "arm_0.R", "arm_1.R", "arm_2.R", "arm_3.R"]
QUAD = ["hips", "spine_1", "spine_2", "neck", "head",
        "leg_front_0.L", "leg_front_1.L", "leg_front_2.L", "leg_front_3.L",
        "leg_front_0.R", "leg_front_1.R", "leg_front_2.R", "leg_front_3.R",
        "leg_hind_1.L", "leg_hind_2.L", "leg_hind_3.L", "leg_hind_1.R", "leg_hind_2.R", "leg_hind_3.R",
        "tail_1", "tail_2", "tail_3"]
HEXA = ["body", "head"] + ["leg%d_%d.%s" % (k, i, s) for k in (1, 2, 3) for s in "LR" for i in (1, 2, 3)]
HUMAN = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
         "RightShoulder", "RightArm", "RightForeArm", "RightHand", "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg",
         "RightLeg", "RightFoot"]


def chains_of(names, roles):
    """Chains as skin() sees them: bones grouped by base and side, with a role."""
    out = {}
    for n in names:
        s = PR.bone_side(n)
        base = n.split(".")[0].rsplit("_", 1)[0] if "_" in n.split(".")[0] else n.split(".")[0]
        out.setdefault((base, s), []).append(n)
    return [{"role": roles(base), "bones": bones} for (base, s), bones in out.items()]


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Names(unittest.TestCase):
    def test_bone_side(self):
        self.assertEqual(PR.bone_side("leg_1.L"), "L")
        self.assertEqual(PR.bone_side("LeftForeArm"), "L")
        self.assertEqual(PR.bone_side("mixamorig:RightHand"), "R")
        self.assertEqual(PR.bone_side("thigh_l"), "L")
        # substrings are not sides: "_l" is in _lower, "_r" in _rear
        self.assertEqual(PR.bone_side("jaw_lower"), "")
        self.assertEqual(PR.bone_side("spine_rear"), "")
        self.assertEqual(PR.bone_side("leg_l_2"), "L")

    def test_tokens(self):
        self.assertEqual(PR.name_tokens("LeftForeArm"), ["left", "fore", "arm"])
        self.assertFalse(PR.has_token("LeftForeArm", "ear"))     # a forearm is not an ear
        self.assertTrue(PR.has_token("ear_1.L", "ear"))
        self.assertTrue(PR.has_token("antennae_2.R", "antenna"))

    def test_body_plan(self):
        role = lambda b: "leg" if b.startswith("leg") else "arm" if b.startswith("arm") else "spine"
        self.assertEqual(PR.body_plan(chains_of(BIPED, role)), "biped")
        self.assertEqual(PR.body_plan(chains_of(QUAD, role)), "quadruped")
        self.assertEqual(PR.body_plan(chains_of(HEXA, role)), "multi")
        human = [{"role": "leg", "bones": ["LeftUpLeg", "LeftLeg"]}, {"role": "leg", "bones": ["RightUpLeg", "RightLeg"]}]
        self.assertEqual(PR.body_plan(human), "biped")
        # a Tripo biped's toe and side branches (roles leg_toe, leg_b) are parts of its two legs
        tripo = human + [{"role": "leg_toe", "bones": ["leg_toe.L"]}, {"role": "leg_toe", "bones": ["leg_toe.R"]},
                         {"role": "leg_b", "bones": ["leg_b.L"]}]
        self.assertEqual(PR.body_plan(tripo), "biped")
        self.assertIsNone(PR.body_plan(None))                  # no chains: the passes keep their legacy rules


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Barrier(unittest.TestCase):
    def heads(self, names, f):
        return {n: f(n) for n in names}

    def test_radial_never_fires_on_a_biped_and_does_on_a_hexapod(self):
        rng = np.random.default_rng(1)
        P = rng.uniform(-1, 1, (200, 3))
        # biped: leg_1.L / leg_2.L / leg_3.L are one leg's links, not three legs
        W = rng.uniform(0, 1, (200, len(BIPED)))
        heads = self.heads(BIPED, lambda n: [0.1 if PR.bone_side(n) == "L" else -0.1, 0.0, 0.5])
        out = PR.apply_radial_limb_sector_isolation(W, P, BIPED, bone_heads=heads)
        np.testing.assert_allclose(out, W)                       # untouched: no multi-legged body here
        # hexapod: six legs, their links grouped per leg
        W = rng.uniform(0, 1, (200, len(HEXA)))
        ang = {1: -0.6, 2: 0.0, 3: 0.6}
        def hx(n):
            if not n.startswith("leg"): return [0.0, 0.0, 0.3]
            k = int(n[3]); s = 1 if n.endswith(".L") else -1
            return [s * 0.4, ang[k] * 0.5, 0.2]
        out = PR.apply_radial_limb_sector_isolation(W, P, HEXA, bone_heads=self.heads(HEXA, hx))
        self.assertFalse(np.allclose(out, W / W.sum(1, keepdims=True)))

    def test_quadruped_chest_is_not_cut_by_height(self):
        # a vertex under the chest, below hip height: on a biped section 5 moves chest weight to the hips; on a
        # quadruped (its ribcage hangs below the hips) it must stay on the chest
        names = QUAD
        heads = {n: [0.0, 0.0, 0.6] for n in names}
        heads["hips"] = [0.0, 0.4, 0.7]
        heads["spine_2"] = [0.0, -0.2, 0.7]
        P = np.array([[0.02, -0.2, 0.45], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        W = np.zeros((3, len(names)))
        W[:, names.index("spine_2")] = 1.0
        quad = PR.apply_geodesic_skin_barrier(W, P, names, bone_heads=heads, crotch_threshold=0.01, plan="quadruped",
                                              flank_barrier=False, tail_barrier=False, radial_barrier=False)
        self.assertAlmostEqual(quad[0, names.index("spine_2")], 1.0)

    def test_forearm_is_not_an_ear(self):
        names = HUMAN
        heads = {n: [0.0, 0.0, 1.0] for n in names}
        heads["LeftForeArm"] = [0.5, 0.0, 1.4]
        P = np.array([[0.8, 0.0, 1.4]])                     # the wrist end of a long forearm, far from its head
        W = np.zeros((1, len(names))); W[0, names.index("LeftForeArm")] = 1.0
        out = PR.apply_geodesic_skin_barrier(W, P, names, bone_heads=heads, height_span=1.8, plan="biped",
                                             flank_barrier=False, tail_barrier=False, radial_barrier=False)
        self.assertAlmostEqual(out[0, names.index("LeftForeArm")], 1.0)

    def test_flank_uses_given_front_and_hind(self):
        names = HEXA
        heads = {n: [0.0, 0.0, 0.3] for n in names}
        for n in names:
            if n.startswith("leg1"): heads[n] = [0.3, -0.5, 0.2]
            if n.startswith("leg3"): heads[n] = [0.3, 0.5, 0.2]
        front = [n for n in names if n.startswith("leg1")]
        hind = [n for n in names if n.startswith("leg3")]
        P = np.array([[0.1, 0.6, 0.3]])                     # at the back
        W = np.zeros((1, len(names))); W[0, names.index("leg1_2.L")] = 0.5; W[0, names.index("body")] = 0.5
        out = PR.apply_longitudinal_flank_barrier(W, P, names, bone_heads=heads, front_bones=front, hind_bones=hind)
        self.assertAlmostEqual(out[0, names.index("leg1_2.L")], 0.0)
        # too close together for the model's size: no barrier
        out = PR.apply_longitudinal_flank_barrier(W, P, names, bone_heads=heads, front_bones=front, hind_bones=hind,
                                                  min_sep=5.0)
        self.assertAlmostEqual(out[0, names.index("leg1_2.L")], 0.5)

    def test_hanging_tail_keeps_its_weight(self):
        names = ["hips", "tail_1", "tail_2", "tail_3"]
        heads = {"hips": [0, 0, 1.0], "tail_1": [0, 0.3, 0.9], "tail_2": [0, 0.35, 0.6], "tail_3": [0, 0.38, 0.3]}
        P = np.array([[0.0, 0.37, 0.45], [0.0, -0.3, 0.95]])   # on the hanging tail, far below its root; on the body
        W = np.zeros((2, 4)); W[:, 2] = 1.0
        out = PR.apply_tail_isolation_barrier(W, P, names, bone_heads=heads)
        self.assertAlmostEqual(out[0, 2], 1.0)                  # the tail keeps the tail
        self.assertAlmostEqual(out[1, 2], 0.0)                  # the body in front of its root does not

    def test_curled_tail_keeps_its_tip(self):
        # a scorpion's tail: back from the rump, then up and forward over the body; its tip is ahead of its root
        names = ["hips", "tail_1", "tail_2", "tail_3", "tail_4"]
        heads = {"hips": [0, 0, 0.5], "tail_1": [0, 0.4, 0.5], "tail_2": [0, 0.6, 0.9], "tail_3": [0, 0.3, 1.3],
                 "tail_4": [0, -0.1, 1.3]}
        P = np.array([[0.0, -0.1, 1.3], [0.0, -0.3, 0.5]])       # the tail's tip; the chest, far from the tail
        W = np.zeros((2, 5)); W[:, 4] = 1.0
        out = PR.apply_tail_isolation_barrier(W, P, names, bone_heads=heads)
        self.assertAlmostEqual(out[0, 4], 1.0)
        self.assertAlmostEqual(out[1, 4], 0.0)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Healing(unittest.TestCase):
    def test_locked_vertices_keep_their_cut(self):
        # a lid: 0/1 across one edge. Unlocked, the healer blurs it; locked, it stays a cut
        W = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        P = np.array([[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]], dtype=float)
        E = [(0, 1), (1, 2), (2, 3)]
        blurred, n = PR.compute_closed_loop_laplacian_healing(W, P, E, max_gradient=0.2, passes=3)
        self.assertGreater(n, 0)
        kept, n = PR.compute_closed_loop_laplacian_healing(W, P, E, max_gradient=0.2, passes=3, locked={0, 1, 2, 3})
        np.testing.assert_allclose(kept, W)

    def test_long_edges_allow_larger_steps(self):
        # only the middle edge carries a step (0.3), and it is 4x the median edge: within 0.2 x 4, so nothing moves
        W = np.array([[1.0, 0.0], [1.0, 0.0], [0.7, 0.3], [0.7, 0.3]])
        P = np.array([[0, 0, 0], [0, 0, 1], [0, 0, 5], [0, 0, 6]], dtype=float)
        E = [(0, 1), (1, 2), (2, 3)]
        out, n = PR.compute_closed_loop_laplacian_healing(W, P, E, max_gradient=0.2, passes=1)
        np.testing.assert_allclose(out, W)
        # the same step over short edges is eased
        P_short = np.array([[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]], dtype=float)
        out, n = PR.compute_closed_loop_laplacian_healing(W, P_short, E, max_gradient=0.2, passes=1)
        self.assertGreater(n, 0)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (use Blender's own Python)")
class Joints(unittest.TestCase):
    def test_each_joint_once(self):
        pairs = [("hips", "spine_1"), ("spine_1", "neck"), ("arm_1.L", "arm_2.L"), ("tail_1", "tail_2"),
                 ("wing_1.L", "wing_2.L"), ("LeftArm", "LeftForeArm")]
        shafts, hinges = PR.split_joint_pairs(pairs)
        self.assertEqual(set(shafts) | set(hinges), set(pairs))
        self.assertFalse(set(shafts) & set(hinges))
        self.assertIn(("tail_1", "tail_2"), hinges)
        self.assertIn(("arm_1.L", "arm_2.L"), shafts)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the skeleton geometry rules in autorig/core/rig_geom.py (plain Python: no Blender, no numpy).
import math, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import rig_geom as G


def dot(a, b): return sum(x * y for x, y in zip(a, b))
def sub(a, b): return tuple(x - y for x, y in zip(a, b))
def norm(a):
    L = math.sqrt(dot(a, a)); return tuple(x / L for x in a)


class Rolls(unittest.TestCase):
    def test_limb_refs_agree_along_the_chain(self):
        # a hind leg: hip, knee (forward of the line), hock (behind it), foot. A per-bone slope threshold flipped the
        # roll between the thigh and the shin; one reference for the chain never does
        leg = [(0.1, 0.0, 1.0), (0.1, -0.12, 0.62), (0.1, 0.05, 0.3), (0.1, -0.02, 0.0)]
        refs = G.roll_refs(leg, limb=True)
        self.assertEqual(len(refs), 3)
        for a, b in zip(refs, refs[1:]):
            self.assertGreater(dot(a, b), 0.0)
        # the reference is the knee's bend: forward (-Y)
        self.assertLess(refs[0][1], 0.0)
        # and each is square to its bone
        for i, r in enumerate(refs):
            d = norm(sub(leg[i + 1], leg[i]))
            self.assertAlmostEqual(dot(r, d), 0.0, places=6)

    def test_mirrored_chains_get_mirrored_refs(self):
        left = [(0.2, 0.0, 1.0), (0.3, -0.1, 0.55), (0.25, 0.0, 0.05)]
        right = [(-x, y, z) for x, y, z in left]
        for rl, rr in zip(G.roll_refs(left, limb=True), G.roll_refs(right, limb=True)):
            self.assertAlmostEqual(rl[0], -rr[0], places=6)
            self.assertAlmostEqual(rl[1], rr[1], places=6)
            self.assertAlmostEqual(rl[2], rr[2], places=6)

    def test_threshold_straddling_chain_does_not_flip(self):
        # a neck rising at about 45 degrees, its links either side of |z| = 0.7
        neck = [(0, 0, 0), (0, -0.5, 0.52), (0, -0.9, 1.3)]
        refs = G.roll_refs(neck)
        self.assertGreater(dot(refs[0], refs[1]), 0.0)

    def test_humanoid_roll_by_bone(self):
        self.assertEqual(G.humanoid_roll_ref("LeftForeArm"), G.UP)
        self.assertEqual(G.humanoid_roll_ref("LeftHandIndex2"), G.UP)
        self.assertEqual(G.humanoid_roll_ref("RightUpLeg"), G.FORWARD)
        self.assertEqual(G.humanoid_roll_ref("Spine1"), G.FORWARD)
        self.assertEqual(G.humanoid_roll_ref("LeftFoot"), G.UP)


class IkAndBend(unittest.TestCase):
    def test_ik_layout(self):
        self.assertEqual(G.ik_layout(3), (True, 0, 2))          # thigh, shin, foot
        self.assertEqual(G.ik_layout(2), (False, 0, 2))         # upper, lower: no foot
        self.assertEqual(G.ik_layout(3, girdle=True), (False, 1, 2))   # girdle + 2: no foot, girdle not in IK
        self.assertEqual(G.ik_layout(4, girdle=True), (True, 1, 2))    # girdle + 3: foot, IK starts after it

    def test_front_is_by_position(self):
        self.assertTrue(G.is_front_limb((0.1, -0.3, 0.5), 0.0))
        self.assertFalse(G.is_front_limb((0.1, 0.3, 0.5), 0.0))
        self.assertTrue(G.is_front_limb((0.1, 0.3, 0.5), 0.0, role="arm"))
        # upright: a biped's spine runs up, so its legs are hind legs wherever their roots sit in Y
        self.assertFalse(G.is_front_limb((0.1, -0.3, 0.5), 0.0, role="leg", upright=True))
        self.assertTrue(G.spine_is_upright([(0, 0, 0.5), (0, 0.02, 1.4)]))
        self.assertFalse(G.spine_is_upright([(0, 0.4, 0.5), (0, -0.4, 0.55)]))

    def test_t_pose_elbow_bends_back_not_up(self):
        arm = [(0.2, 0.0, 1.4), (0.45, 0.0, 1.4), (0.7, 0.0, 1.4)]           # horizontal, straight
        bent = G.pre_bend(arm, front=True, arm=True)
        self.assertGreater(bent[1][1], 0.0)                                 # back (+Y)
        self.assertAlmostEqual(bent[1][2], 1.4, places=6)                   # not up

    def test_pre_bend_directions(self):
        straight_hind = [(0.1, 0.3, 1.0), (0.1, 0.3, 0.5), (0.1, 0.3, 0.0)]
        self.assertLess(G.pre_bend(straight_hind, front=False)[1][1], 0.3)    # knee forward
        self.assertGreater(G.pre_bend(straight_hind, front=True)[1][1], 0.3)  # elbow back
        sprawl = [(0.1, 0.0, 0.3), (0.35, 0.0, 0.3), (0.6, 0.0, 0.3)]         # an insect leg lying flat
        self.assertGreater(G.pre_bend(sprawl, front=False)[1][2], 0.3)        # its knee rises
        bent = [(0, 0, 1.0), (0, -0.2, 0.5), (0, 0, 0.0)]
        self.assertEqual(G.pre_bend(bent, front=True), [tuple(p) for p in bent])   # an existing bend is kept


class Naming(unittest.TestCase):
    def test_mirror_partners(self):
        # a T-posed biped's legs sit near the middle of its arm span, but mirror each other
        xs = [0.0, 0.08, -0.08, 0.45, -0.45, 0.01]
        roles = ["spine", "leg", "leg", "arm", "arm", "tail"]
        self.assertEqual(G.mirror_partners(xs, roles), [False, True, True, True, True, False])
        # two fins a hair either side of the middle stay centred (Rule D), not a pair
        self.assertEqual(G.mirror_partners([0.005, -0.004], ["fin", "fin"], min_abs=0.02), [False, False])

    def test_dedupe_keeps_spec_names(self):
        chains = [{"bones": ["hips", "spine_1", "head"]},
                  {"bones": ["head"], "named": True},
                  {"bones": ["tail_1", "tail_2"]}]
        renames = G.dedupe_names(chains)
        self.assertEqual(chains[1]["bones"], ["head"])          # the spec's own name wins
        self.assertEqual(chains[0]["bones"][2], "head_v2")
        self.assertEqual(renames, [("head", "head_v2")])
        twice = [{"bones": ["jaw"], "named": True}, {"bones": ["jaw"], "named": True}]
        self.assertEqual(G.dedupe_names(twice), [("jaw", "jaw_v2")])   # a spec mistake: logged, the rig still builds
        sided = [{"bones": ["leg_1.L"]}, {"bones": ["leg_1.L"]}]
        G.dedupe_names(sided)
        self.assertEqual(sided[1]["bones"], ["leg_1_v2.L"])


class Centring(unittest.TestCase):
    def ring(self, cx, cy, r=1.0, z=0.0, n=24):
        return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n), z) for k in range(n)]

    def test_stray_vertex_does_not_drag_the_centre(self):
        ring = self.ring(0, 0) + [(0.05, 0.0, 0.0)]            # one vertex poking in from the wall
        c = G.centre_ring(ring, (0.3, 0.0, 0.0), (0, 0, 1))
        self.assertLess(abs(c[0]), 0.2)

    def test_shift_is_capped(self):
        ring = self.ring(0, 0)
        c = G.centre_ring(ring, (0.9, 0.0, 0.0), (0, 0, 1))     # a wild start near the wall
        self.assertLessEqual(math.dist(c, (0.9, 0.0, 0.0)), 0.5 + 1e-6)

    def test_short_chains_keep_their_bends(self):
        knee = [(0, 0, 1.0), (0, -0.2, 0.5), (0, 0, 0.0)]
        self.assertEqual(G.smooth_stations(knee), [tuple(p) for p in knee])
        tail = [(0, 0.1 * k, (0.02 if k % 2 else 0.0)) for k in range(7)]
        eased = G.smooth_stations(tail)
        for a, b in zip(tail, eased):
            self.assertAlmostEqual(a[1], b[1], places=6)        # moved across the chain only, never along it


class Checks(unittest.TestCase):
    def test_mirror_name(self):
        self.assertEqual(G.mirror_name("arm_1.L"), "arm_1.R")
        self.assertEqual(G.mirror_name("LeftForeArm"), "RightForeArm")
        self.assertEqual(G.mirror_name("thigh_r"), "thigh_l")
        self.assertIsNone(G.mirror_name("spine_2"))

    def test_symmetry_and_rolls(self):
        heads = {"leg_1.L": (0.1, 0.0, 1.0), "leg_1.R": (-0.1, 0.0, 1.0), "arm_1.L": (0.3, 0.0, 1.5),
                 "arm_1.R": (-0.3, 0.2, 1.5)}
        issues = G.symmetry_issues(heads, 0.0, 2.0)
        self.assertEqual([i[:2] for i in issues], [("arm_1.L", "arm_1.R")])
        bones = {"leg_1.L": {"parent": None, "z": (0, -1, 0), "chain": ("leg", "L")},
                 "leg_2.L": {"parent": "leg_1.L", "z": (0, 1, 0), "chain": ("leg", "L")},
                 "leg_1.R": {"parent": None, "z": (0, -1, 0), "chain": ("leg", "R")},
                 "leg_2.R": {"parent": "leg_1.R", "z": (0, 1, 0), "chain": ("leg", "R")}}
        flips, mirror = G.roll_issues(bones)
        self.assertEqual(flips, ["leg_2.L", "leg_2.R"])
        self.assertEqual(mirror, [])

    def test_naming_issues(self):
        short, clash = G.naming_issues({"hips": 0.3, "tip": 0.0, "leg_1_v2.L": 0.2, "head.001": 0.1}, 2.0)
        self.assertEqual(short, ["tip"])
        self.assertEqual(clash, ["head.001", "leg_1_v2.L"])


if __name__ == "__main__":
    unittest.main()

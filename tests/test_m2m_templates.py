# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for Mesh2Motion's template metadata and the archetype -> template choice
# (autorig/core/m2m_templates.py). Plain Python; the tests that read the real M2M files skip when none are found.
import json, os, struct, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import m2m_templates as T

ROOT = T.m2m_root()


def write_glb(path, gltf):
    """A GLB holding only a JSON chunk (no binary buffer): enough for the metadata readers."""
    data = json.dumps(gltf).encode("utf-8")
    data += b" " * (-len(data) % 4)
    with open(path, "wb") as fh:
        fh.write(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(data)))
        fh.write(struct.pack("<I4s", len(data), b"JSON") + data)


class TestRootMotion(unittest.TestCase):
    def test_suffix_only(self):
        for n in ("Walk RM", "Roll_RM", "Sword_Dash_RM", "ClimbUp_1m_RM"):
            self.assertTrue(T.is_root_motion(n), n)
        # Mesh2Motion's endsWith('rm') took these for root motion too
        for n in ("Swarm", "Storm", "Alarm", "Walk", "Rm", "Walk rm", "RM", "Farm"):
            self.assertFalse(T.is_root_motion(n), n)
        self.assertFalse(T.is_root_motion(None))


class TestChoose(unittest.TestCase):
    def test_archetypes(self):
        self.assertEqual(T.choose("humanoid")[0], "human")
        self.assertEqual(T.choose("serpent")[0], "snake")
        self.assertEqual(T.choose("swimmer")[0], "shark")
        self.assertEqual(T.choose("hexapod")[0], "spider")
        self.assertEqual(T.choose("octopod")[0], "spider")
        self.assertEqual(T.choose("winged", {"feet": 2})[0], "bird")
        self.assertEqual(T.choose("winged", {"feet": 4})[0], "dragon")

    def test_quadruped_by_size(self):
        self.assertEqual(T.choose("quadruped", {"metres": 1.5, "feet": 4})[0], "horse")
        self.assertEqual(T.choose("quadruped", {"metres": 0.5, "feet": 4})[0], "fox")
        self.assertEqual(T.choose("quadruped")[0], "horse")          # size unknown

    def test_kaiju(self):
        self.assertEqual(T.choose("humanoid", {"tail": 0.8})[0], "kaiju")
        self.assertEqual(T.choose("humanoid", {"tail": 0.05})[0], "human")

    def test_body_overrules_tips(self):
        # Tripo's hen and shark read "quadruped" off their tips; the sea serpent "rigid"
        self.assertEqual(T.choose("quadruped", {"feet": 2, "stand": 0.93})[0], "bird")
        self.assertEqual(T.choose("quadruped", {"feet": 2, "stand": 0.36})[0], "shark")
        self.assertEqual(T.choose("quadruped", {"feet": 2, "stand": 2.5})[0], "human")
        self.assertEqual(T.choose("rigid", {"ground": 0.6, "slender": 8})[0], "snake")
        self.assertEqual(T.choose("rigid", {"ground": 0.6, "slender": 2})[0], "shark")
        self.assertEqual(T.choose("quadruped", {"wings": True, "feet": 4})[0], "dragon")
        t, why = T.choose("quadruped", {"feet": 2, "stand": 0.93})
        self.assertIn("was quadruped", why)

    def test_no_template(self):
        for a in ("floater", "rigid", "turret", "machine", None, ""):
            t, why = T.choose(a)
            self.assertIsNone(t, a)
            self.assertIn("rerig.py", why)

    def test_every_template_is_complete(self):
        for t, info in T.TEMPLATES.items():
            for k in ("rig", "animations", "tracking", "family", "roles", "skin"):
                self.assertIn(k, info, t)
            self.assertIn(info["family"], ("human", "legged", "body", "spider"))
            self.assertIn("idle", info["roles"], t)
            self.assertIn("walk", info["roles"], t)
            self.assertIn(info["skin"], T.SKIN_METHODS)


class TestResolveClips(unittest.TestCase):
    HAVE = ["Idle", "Walk", "Trot", "Run", "Kick", "Rest_Pose", "Side winding"]

    def test_roles_and_names(self):
        self.assertEqual(T.resolve_clips("horse", "", self.HAVE), [("Idle", "idle"), ("Walk", "walk")])
        self.assertEqual(T.resolve_clips("horse", "walk,Trot=trot_fast", self.HAVE),
                         [("Walk", "walk"), ("Trot", "trot_fast")])
        self.assertEqual(T.resolve_clips("horse", "Kick", self.HAVE), [("Kick", "Kick")])      # not a role: its own name
        self.assertEqual(T.resolve_clips("horse", "trot", self.HAVE), [("Trot", "trot")])

    def test_all_skips_the_rest_pose(self):
        got = [c for c, _ in T.resolve_clips("horse", "all", self.HAVE)]
        self.assertNotIn("Rest_Pose", got)
        self.assertIn("Side winding", got)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            T.resolve_clips("horse", "Moonwalk", self.HAVE)

    def test_case_insensitive_name(self):
        self.assertEqual(T.resolve_clips("snake", "side winding", self.HAVE), [("Side winding", "Side winding")])


class TestGltfMetadata(unittest.TestCase):
    def test_synthetic(self):
        g = {"asset": {"version": "2.0"},
             "nodes": [{"name": "root", "children": [1]}, {"name": "hips", "children": [2]}, {"name": "spine"},
                       {"name": "Mesh"}],
             "skins": [{"joints": [0, 1, 2]}],
             "accessors": [{"max": [1.25]}, {"max": [0.5]}],
             "animations": [{"name": "Walk", "samplers": [{"input": 0}, {"input": 1}], "channels": []},
                            {"name": "Walk RM", "samplers": [{"input": 1}], "channels": []}]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.glb")
            write_glb(p, g)
            got = T.read_gltf(p)
        self.assertEqual(T.gltf_bones(got), [("root", None), ("hips", "root"), ("spine", "hips")])
        self.assertEqual(T.gltf_clips(got), [{"name": "Walk", "seconds": 1.25}, {"name": "Walk RM", "seconds": 0.5}])

    def test_layouts(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(T.dirs(d))
            os.makedirs(os.path.join(d, "static", "rigs")); os.makedirs(os.path.join(d, "static", "animations"))
            self.assertEqual(T.dirs(d)[0], os.path.join(d, "static", "rigs"))
        with tempfile.TemporaryDirectory() as d:              # asset-packs' _mesh2motion: rigs/ and animations/
            os.makedirs(os.path.join(d, "rigs")); os.makedirs(os.path.join(d, "animations"))
            self.assertEqual(T.dirs(d)[1], os.path.join(d, "animations"))

    def test_axes(self):
        p = [0.1, -0.2, 1.5]                                  # Blender: z up, facing -Y
        self.assertEqual(T.to_gltf(p), [0.1, 1.5, 0.2])       # glTF: y up, facing +Z
        self.assertEqual([round(c, 6) for c in T.from_gltf(T.to_gltf(p))], p)

    def test_mixamo_names(self):
        self.assertEqual(T.MIXAMO_HUMAN["pelvis"], "Hips")
        self.assertEqual(T.MIXAMO_HUMAN["upperarm_l"], "LeftArm")
        self.assertEqual(T.MIXAMO_HUMAN["index_02_r"], "RightHandIndex2")
        self.assertEqual(T.MIXAMO_HUMAN["ball_leaf_l"], "LeftToe_End")
        self.assertEqual(len(set(T.MIXAMO_HUMAN.values())), len(T.MIXAMO_HUMAN))


@unittest.skipUnless(ROOT, "Mesh2Motion's rigs and animations not found (AUTORIG_M2M)")
class TestRealFiles(unittest.TestCase):
    def test_every_template(self):
        rows = {r["template"]: r for r in T.summary(ROOT)}
        self.assertEqual(set(rows), set(T.TEMPLATES))
        for t, info in T.TEMPLATES.items():
            names = [n for n, _ in T.bones(t, ROOT)]
            self.assertIn(info["tracking"], names, t)
            clips = T.clips(t, ROOT)
            have = [c["name"] for c in clips]
            for role, clip in info["roles"].items():
                self.assertIn(clip, have, "%s %s" % (t, role))
            self.assertTrue(all(c["seconds"] > 0 for c in clips), t)

    def test_human_spans_three_files(self):
        files = {c["file"] for c in T.clips("human", ROOT)}
        self.assertEqual(len(files), 3)
        rm = [c["name"] for c in T.clips("human", ROOT) if c["root_motion"]]
        self.assertIn("Roll_RM", rm)
        self.assertTrue(all(T.is_root_motion(n) for n in rm))


if __name__ == "__main__":
    unittest.main()

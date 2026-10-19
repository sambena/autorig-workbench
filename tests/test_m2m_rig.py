# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: steps/m2m_rig.py under Blender. Each template's own sample mesh (in its animations GLB) is
# fitted with that template: the joints should land about where the template has them, the clips come out, and the
# edited-joints round trip moves the joint it was given. Skips without Blender or Mesh2Motion's files.
import json, os, re, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender
import m2m_templates as T

HAVE_BLENDER = bool(blender.find(required=False))
ROOT = T.m2m_root()


def rig(*argv):
    r = blender.run("m2m_rig.py", *argv, timeout=900)
    m = re.search(r"^M2M_RIG (.*)$", r.stdout or "", re.M)
    if not m: raise AssertionError("no M2M_RIG line:\n" + ((r.stdout or "") + (r.stderr or ""))[-3000:])
    return r.returncode, json.loads(m.group(1))


@unittest.skipUnless(HAVE_BLENDER and ROOT, "Blender or Mesh2Motion's files not found")
class TestSelfFit(unittest.TestCase):
    def fit(self, t, *extra):
        _, anims = T.files(t, ROOT)
        out = tempfile.mkdtemp(prefix="m2m_%s_" % t)
        code, log = rig("--in", anims[0], "--name", t.capitalize(), "--out", out, "--template", t, "--m2m", ROOT,
                        "--no-preview", *extra)
        self.assertEqual(code, 0, log)
        return out, log

    def check(self, t):
        out, log = self.fit(t)
        self.assertEqual(log["template"], t)
        for f in ("glb", "fbx", "blend"):
            self.assertTrue(os.path.exists(log["out"][f]), f)
        g = T.read_gltf(log["out"]["glb"])
        self.assertEqual(sorted(c["name"] for c in T.gltf_clips(g)), ["idle", "walk"])
        self.assertEqual(log["bare_vertices"], 0)
        names = [n for n, _ in T.bones(t, ROOT)]
        m2m = json.load(open(os.path.join(out, "rig.json")))["m2m"]
        self.assertEqual(m2m["axes"], "gltf")
        joints = m2m["joints"]                                  # {bone: [x, y, z]}, as the API and the bridge read it
        self.assertEqual(sorted(joints), sorted(names))
        self.assertTrue(all(len(v) == 3 for v in joints.values()))
        return out, log, joints

    def test_fox(self):
        out, log, joints = self.check("fox")
        self.assertEqual(log["fit"]["legs"], 4)
        card = json.load(open(os.path.join(out, "model.json")))
        self.assertEqual(card["rig"]["kind"], "m2m")
        self.assertIn(card["rig"]["audit"]["grade"], ("PASS", "CHECK", "FAIL"))
        spec = json.load(open(os.path.join(out, "rig.json")))
        self.assertEqual(spec["rig"]["kind"], "m2m")
        self.assertEqual(spec["m2m"]["template"], "fox")
        # edited joints come back, in the API's {template, joints} shape: the left front knee moved 4 cm
        name = "Front_Leg_Lower_L"
        moved = list(spec["m2m"]["joints"][name])
        moved[2] += 0.04
        jf = os.path.join(out, "edit.json")
        json.dump({"template": "fox", "joints": dict(spec["m2m"]["joints"], **{name: moved})}, open(jf, "w"))
        out2 = tempfile.mkdtemp(prefix="m2m_fox_edit_")
        code, log2 = rig("--in", T.files("fox", ROOT)[1][0], "--name", "Fox", "--out", out2, "--template", "fox",
                         "--m2m", ROOT, "--joints", jf, "--no-preview", "--no-audit")
        self.assertEqual(code, 0, log2)
        got = json.load(open(os.path.join(out2, "rig.json")))["m2m"]["joints"][name]
        self.assertLess(max(abs(a - b) for a, b in zip(got, moved)), 1e-3)

    def test_human(self):
        out, log, joints = self.check("human")
        b = joints
        # standing: the head over the hips over the feet, the left hand out to +x (glTF x is Blender's)
        self.assertGreater(b["head"][1], b["pelvis"][1])
        self.assertGreater(b["pelvis"][1], b["foot_l"][1])
        self.assertGreater(b["hand_l"][0], b["upperarm_l"][0])
        self.assertGreater(b["upperarm_l"][0], 0)

    def test_snake(self):
        out, log, joints = self.check("snake")
        self.assertEqual(log["rest"], "template")
        self.assertIn("line", log)

    def test_root_motion_and_mixamo(self):
        out, log = self.fit("human", "--clips", "Roll_RM=roll,idle", "--naming", "mixamo", "--no-audit")
        self.assertEqual([c["root_motion"] for c in log["clips"]], [True, False])
        names = [n for n, _ in T.gltf_bones(T.read_gltf(log["out"]["glb"]))]
        self.assertIn("Hips", names)
        self.assertIn("LeftHandIndex1", names)
        self.assertNotIn("pelvis", names)

    def test_no_template_falls_back(self):
        code, log = rig("--in", T.files("fox", ROOT)[1][0], "--name", "X", "--out", tempfile.mkdtemp(),
                        "--archetype", "turret", "--m2m", ROOT, "--no-preview", "--no-audit")
        self.assertEqual(code, 3)
        self.assertIsNone(log["template"])



def tripo(name):
    """A Tripo animal's source GLB in the collection (AUTORIG_MODELS), or None."""
    root = os.environ.get("AUTORIG_MODELS") or os.path.expanduser("~/src/asset-packs")
    p = os.path.join(root, "TornadoTitans", name, name + ".glb")
    return p if os.path.exists(p) else None


@unittest.skipUnless(HAVE_BLENDER and ROOT and tripo("Horse_Bay_Tripo"), "Blender, Mesh2Motion or the Tripo animals not found")
class TestTripoChoice(unittest.TestCase):
    """template auto on Tripo's animals as they come (about 1 m tall whatever they are, no --height): a horse or cow
    on the horse, a dog or cat on the fox. The API's first live rig put Horse_Bay on the fox by its height."""
    CASES = {"Horse_Bay_Tripo": "horse", "Cow_Holstein_Tripo": "horse", "Dog_BlackTan_Tripo": "fox",
             "Cat_Tabby_Tripo": "fox"}

    def test_choice(self):
        for name, want in self.CASES.items():
            src = tripo(name)
            if src is None: continue
            code, log = rig("--in", src, "--name", name, "--out", tempfile.mkdtemp(), "--m2m", ROOT, "--choose-only")
            self.assertEqual(code, 0, log)
            self.assertEqual(log["template"], want, "%s: %s" % (name, log["choice"]))


@unittest.skipUnless(HAVE_BLENDER and ROOT and tripo("Cat_Tabby_Tripo") and tripo("Rabbit_Tripo"),
                     "Blender, Mesh2Motion or the Tripo animals not found")
class TestTripoLegged(unittest.TestCase):
    """Four-legged rules on Tripo's animals: the cat's hip joint inside her body (the Fox sample's sparse rump read a
    slice 0.3 m low and put it above her back), the legs' swing from their length, and the rabbit's walk a hop."""

    def test_cat_hip_and_swing(self):
        out = tempfile.mkdtemp(prefix="m2m_cat_")
        code, log = rig("--in", tripo("Cat_Tabby_Tripo"), "--name", "Cat_Tabby_Tripo", "--out", out, "--m2m", ROOT,
                        "--no-preview", "--no-audit", "--format", "glb")
        self.assertEqual(code, 0, log)
        self.assertEqual(log["template"], "fox")
        j = log["landmarks"]["mesh"]
        hip = T.from_gltf(json.load(open(os.path.join(out, "rig.json"), encoding="utf-8"))["m2m"]["joints"]["Back_Leg_Upper_L"])
        self.assertLess(hip[2], j["croup"] * 0.85, (hip, j))
        self.assertGreater(hip[2], j["belly"], (hip, j))
        self.assertLess(log["swing_auto"], 1.0)
        self.assertNotIn("gait", log)

    def test_rabbit_hops(self):
        out = tempfile.mkdtemp(prefix="m2m_rabbit_")
        code, log = rig("--in", tripo("Rabbit_Tripo"), "--name", "Rabbit_Tripo", "--out", out, "--m2m", ROOT,
                        "--no-preview", "--no-audit", "--format", "glb")
        self.assertEqual(code, 0, log)
        self.assertIn("hop", log.get("gait", {}))
        self.assertEqual([(c["name"], c["m2m"]) for c in log["clips"]], [("idle", "Idle"), ("walk", "Hop")])
        g = T.read_gltf(os.path.join(out, "Rabbit_Tripo.glb"))
        self.assertEqual(sorted(c["name"] for c in T.gltf_clips(g)), ["idle", "walk"])


if __name__ == "__main__":
    unittest.main()

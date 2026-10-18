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
        joints = json.load(open(os.path.join(out, "rig.json")))["m2m"]["joints"]
        self.assertEqual(joints["axes"], "gltf")
        self.assertEqual(sorted(joints["bones"]), sorted(names))
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
        # edited joints come back: the left front knee moved 4 cm
        j = spec["m2m"]["joints"]
        name = "Front_Leg_Lower_L"
        moved = [c for c in j["bones"][name]["head"]]
        moved[2] += 0.04
        j["bones"][name]["head"] = moved
        jf = os.path.join(out, "edit.json")
        json.dump(j, open(jf, "w"))
        out2 = tempfile.mkdtemp(prefix="m2m_fox_edit_")
        code, log2 = rig("--in", T.files("fox", ROOT)[1][0], "--name", "Fox", "--out", out2, "--template", "fox",
                         "--m2m", ROOT, "--joints", jf, "--no-preview", "--no-audit")
        self.assertEqual(code, 0, log2)
        got = json.load(open(os.path.join(out2, "rig.json")))["m2m"]["joints"]["bones"][name]["head"]
        self.assertLess(max(abs(a - b) for a, b in zip(got, moved)), 1e-3)

    def test_human(self):
        out, log, joints = self.check("human")
        b = joints["bones"]
        # standing: the head over the hips over the feet, the left hand out to +x (glTF x is Blender's)
        self.assertGreater(b["head"]["head"][1], b["pelvis"]["head"][1])
        self.assertGreater(b["pelvis"]["head"][1], b["foot_l"]["head"][1])
        self.assertGreater(b["hand_l"]["head"][0], b["upperarm_l"]["head"][0])
        self.assertGreater(b["upperarm_l"]["head"][0], 0)

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


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the spec editor (gui/spec_api.py, spec_editor.html/.js) and the source view step.
#
#   python -m unittest discover -s tests -v
#
# Without Blender: the rig.json writer keeps a hand-written file byte for byte and puts short lists on one line;
# check() catches what would break a rig (a missing head, a bone the source does not have, an allowance with no
# reason, a build chain placed two ways) and only warns about fields it does not know. Then the server, started on a
# temporary models folder: the page needs the token, its code is static, /api/spec loads a model, check returns the
# diff, a save refuses a stale file and a broken spec, and a good save keeps rig.json.bak and every unknown field.
# With Blender: a generated model with a bone_N skeleton goes through source_preview.py (the joints come back, the
# GLB has the mesh and no skin), then Save and re-rig twice, and the second run has the first run's audit as its
# "before".
import json, os, secrets, shutil, struct, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "gui")]
import blender  # noqa: E402
import spec_api  # noqa: E402

TOKEN = "test-" + secrets.token_hex(4)
HAVE_BLENDER = blender.find(required=False) is not None

HAND_WRITTEN = """{
  "schema": "autorig-spec/1",
  "rig": {
    "kind": "tripo",
    "head": "bone_3",
    "hips": "bone_1",
    "mirror": [["bone_4", "bone_5"]],
    "chains": {"wing": ["bone_4", "bone_4_m"]},
    "future_field": {"kept": true}
  },
  "notes": {
    "rig": "a note"
  }
}
"""
SOURCE = {"joints": [{"name": n, "head": [0, 0, i], "tail": [0, 0, i + 1], "parent": p, "children": []}
                     for i, (n, p) in enumerate([("bone_0", None), ("bone_1", "bone_0"), ("bone_2", "bone_1"),
                                                 ("bone_3", "bone_2"), ("bone_4", "bone_2"), ("bone_5", "bone_4")])]}


def read_glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    n, kind = struct.unpack_from("<I4s", data, 12)
    assert data[:4] == b"glTF" and kind == b"JSON"
    return json.loads(data[20:20 + n])


class WriterAndChecks(unittest.TestCase):
    def test_round_trip_keeps_the_file(self):
        self.assertEqual(spec_api.dumps(json.loads(HAND_WRITTEN)), HAND_WRITTEN)

    def test_long_and_nested_values_break_sensibly(self):
        s = {"schema": "autorig-spec/1", "rig": {"kind": "build", "chains": [{"name": "spine", "slice": [0.1, 0.9]}]}}
        out = spec_api.dumps(s)
        self.assertIn('\n    "chains": [\n      {"name": "spine", "slice": [0.1, 0.9]}\n    ]', out)
        self.assertEqual(json.loads(out), s)

    def test_check_passes_a_good_spec_and_warns_on_unknowns(self):
        errs, warns = spec_api.check(json.loads(HAND_WRITTEN), SOURCE)
        self.assertEqual(errs, [])
        self.assertEqual([w["path"] for w in warns], ["rig.future_field"])

    def test_check_catches_what_breaks_a_rig(self):
        s = json.loads(HAND_WRITTEN)
        del s["rig"]["head"]
        s["rig"]["chains"]["leg"] = ["bone_9"]
        s["rig"]["audit"] = {"bend_tears": 3}
        paths = {e["path"] for e in spec_api.check(s, SOURCE)[0]}
        self.assertEqual(paths, {"rig.head", "rig.chains.leg", "notes.rig.audit"})
        s["notes"]["rig.audit"] = "thin wing tips"
        s["rig"]["head"] = "bone_1"
        paths = {e["path"] for e in spec_api.check(s, SOURCE)[0]}
        self.assertIn("rig.head", paths)                               # the head and hips are one bone

    def test_check_build_chains(self):
        s = {"schema": "autorig-spec/1", "rig": {"kind": "build", "chains": [
            {"name": "spine", "slice": [0.1, 0.9]}, {"name": "leg", "tip": [0.2, 0.3, 0.0], "points": [[0, 0, 0], [1, 1, 1]]},
            {"name": "tail", "tip": [0.5, 0.9, 0.4], "parent": ["nowhere", 0]}]}}
        paths = {e["path"] for e in spec_api.check(s)[0]}
        self.assertEqual(paths, {"rig.chains.1", "rig.chains.2.parent"})

        # Valid stations
        good_st = {"schema": "autorig-spec/1", "rig": {"kind": "build", "chains": [
            {"name": "spine", "slice": [0.1, 0.9], "bones": 3, "stations": [0.1, 0.35, 0.65, 0.9]}
        ]}}
        errs, warns = spec_api.check(good_st)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

        # Invalid stations (wrong type, out of order, outside range)
        bad_st = {"schema": "autorig-spec/1", "rig": {"kind": "build", "chains": [
            {"name": "spine", "slice": [0.1, 0.9], "bones": 3, "stations": "invalid"}
        ]}}
        errs, _ = spec_api.check(bad_st)
        self.assertIn("rig.chains.0.stations", {e["path"] for e in errs})

        warn_st = {"schema": "autorig-spec/1", "rig": {"kind": "build", "chains": [
            {"name": "spine", "slice": [0.1, 0.9], "bones": 3, "stations": [0.1, 0.8, 0.4, 1.2]}
        ]}}
        _, warns = spec_api.check(warn_st)
        warn_paths = {w["path"] for w in warns}
        self.assertIn("rig.chains.0.stations", warn_paths)
        self.assertIn("rig.chains.0.stations.3", warn_paths)

    def test_check_humanoid_spec(self):
        s = {
            "schema": "autorig-spec/1",
            "rig": {"kind": "humanoid"},
            "humanoid": {
                "forward": [0, -1, 0],
                "z": {"top": 1.0, "head": 0.87, "neck": 0.83, "arm": 0.77, "spine2": 0.72,
                      "spine1": 0.65, "spine": 0.57, "hip": 0.47, "knee": 0.28, "ankle": 0.08},
                "x": {"shoulder": 0.38, "elbow": 0.23, "wrist": 0.11, "knuckle": 0.05, "tip": 0.0}
            }
        }
        errs, warns = spec_api.check(s)
        self.assertEqual(errs, [])

        # Missing humanoid section
        bad = {"schema": "autorig-spec/1", "rig": {"kind": "humanoid"}}
        errs, _ = spec_api.check(bad)
        self.assertEqual({e["path"] for e in errs}, {"humanoid"})

        # Missing a required height
        missing = json.loads(json.dumps(s))
        del missing["humanoid"]["z"]["knee"]
        errs, _ = spec_api.check(missing)
        self.assertIn("humanoid.z.knee", {e["path"] for e in errs})

        # Out-of-order heights
        out_of_order = json.loads(json.dumps(s))
        out_of_order["humanoid"]["z"]["ankle"] = 0.99
        _, warns = spec_api.check(out_of_order)
        self.assertIn("humanoid.z", {w["path"] for w in warns})

    def test_check_custom_spec(self):
        s = {"schema": "autorig-spec/1", "rig": {"kind": "custom", "builder": "rig_boss.py"}}
        errs, warns = spec_api.check(s)
        self.assertEqual(errs, [])

        # Missing builder
        bad = {"schema": "autorig-spec/1", "rig": {"kind": "custom"}}
        errs, _ = spec_api.check(bad)
        self.assertEqual({e["path"] for e in errs}, {"rig.builder"})

    def test_check_placed_spec(self):
        s = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "chains": [
                    {"name": "spine", "slice": [0.1, 0.9], "bones": 4},
                    {"name": "wing_arm.L", "tube": [[0.3, 0.3, 0.5], [0.8, 0.4, 0.8]], "bones": 3},
                    {"name": "wing_spar1.L", "points": [[0.8, 0.4, 0.8], [0.95, 0.3, 0.9]], "bones": 2, "parent": ["wing_arm.L", 2]},
                    {"name": "arm.L", "points": [[0.3, 0.2, 0.4], [0.35, 0.15, 0.2]], "bones": 2, "parent": ["spine", 2]},
                ],
                "parts": {
                    "forelimb.L": {"bones": ["arm_1.L", "arm_2.L"], "deny": ["wing_*"]},
                    "wing.L": {"bones": ["wing_1.L", "wing_2.L", "wing_3.L"], "deny": ["arm_*"]},
                },
                "blends": [
                    {"bone": "wing_1.L", "with": "spine_2", "radius": 0.15, "fade": 0.4}
                ],
                "rip_welds": [
                    ["arm_2.L", "leg_1.L"],
                    {"bones": ["wing_3.L", "tail_2"], "dist": 0.05}
                ],
                "membranes": [
                    {
                        "name": "wing_membrane.L",
                        "bones": ["wing_1.L", "wing_2.L", "wing_3.L", "wing_spar1_1.L", "wing_spar1_2.L"],
                        "root_bone": "wing_1.L",
                        "cut_flank": True
                    }
                ],
                "rigid_islands": [
                    {"bone": "spine_2", "at": [0.5, 0.3, 0.7]}
                ],
                "jaw": {
                    "hinge": [0.5, 0.15, 0.6],
                    "tip": [0.5, 0.05, 0.55],
                    "band": 0.08
                }
            }
        }
        errs, warns = spec_api.check(s)
        self.assertEqual(errs, [])

        # Missing chains
        bad = {"schema": "autorig-spec/1", "rig": {"kind": "placed"}}
        errs, _ = spec_api.check(bad)
        self.assertIn("rig.chains", {e["path"] for e in errs})

        # Invalid rip_welds and blends
        bad_rules = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "chains": [{"name": "spine", "slice": [0.1, 0.9]}],
                "rip_welds": ["invalid_pair"],
                "blends": [{"radius": "not_a_num"}],
                "membranes": [{"bones": "not_a_list"}],
            }
        }
        errs, _ = spec_api.check(bad_rules)
        err_paths = {e["path"] for e in errs}
        self.assertIn("rig.rip_welds.0", err_paths)
        self.assertIn("rig.blends.0", err_paths)
        self.assertIn("rig.membranes.0.bones", err_paths)

    def test_schema_includes_humanoid_and_custom(self):
        sch = spec_api.schema()
        self.assertIn("humanoid", sch)
        self.assertTrue(any(f["key"] == "z.top" for f in sch["humanoid"]))
        self.assertTrue(any(f["key"] == "x.shoulder" for f in sch["humanoid"]))
        custom_fields = [f for f in sch["rig"] if f.get("kinds") and "custom" in f["kinds"]]
        self.assertTrue(any(f["key"] == "builder" for f in custom_fields))
        placed_fields = [f for f in sch["rig"] if f.get("kinds") and "placed" in f["kinds"]]
        placed_keys = {f["key"] for f in placed_fields}
        self.assertTrue({"parts", "blends", "rip_welds", "membranes", "rigid_islands"}.issubset(placed_keys))


class EditorServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-spec-test-")
        cls.models = os.path.join(cls.tmp, "models")
        d = os.path.join(cls.models, "Things", "flat")
        os.makedirs(d)
        with open(os.path.join(d, "flat.obj"), "w") as fh: fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        with open(os.path.join(d, "rig.json"), "w", newline="\n") as fh: fh.write(HAND_WRITTEN)
        url_file = os.path.join(cls.tmp, "url.txt")
        env = dict(os.environ, AUTORIG_URL_FILE=url_file)
        cls.proc = subprocess.Popen([sys.executable, "-m", "autorig", "--models", cls.models, "--port", "0",
                                     "--no-browser", "--token", TOKEN], cwd=REPO, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = ""
        for _ in range(100):
            if os.path.exists(url_file):
                with open(url_file) as fh: url = fh.read()
                if url: break
            time.sleep(0.1)
        cls.base = url.split("/?")[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()                                  # the server this test started, by its handle
        cls.proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None, token=TOKEN):
        headers = {"Content-Type": "application/json"}
        if token: headers["X-Autorig-Token"] = token
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET", headers=headers)
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)

    def status(self, path, body=None, token=TOKEN):
        try:
            self.call(path, body, token); return 200
        except urllib.error.HTTPError as e:
            return e.code

    def raw(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.headers.get("Content-Type"), r.read()

    def wait(self, job, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.call("/api/jobs/%d" % job["id"])
            if j["state"] not in ("queued", "running"): return j
            time.sleep(0.5)
        self.fail("job did not finish")

    def test_1_page_and_code(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.raw("/spec_editor.html?model=flat")
        ctype, body = self.raw("/spec_editor.html?model=flat&t=" + TOKEN)
        self.assertIn(TOKEN.encode(), body)
        ctype, body = self.raw("/spec_editor.js")
        self.assertTrue(ctype.startswith("text/javascript"))
        self.assertIn(b"layoutTripo", body)
        self.assertEqual(self.status("/api/spec?name=flat", token=None), 403)

    def test_2_bundle_check_and_save(self):
        b = self.call("/api/spec?name=flat")
        self.assertEqual(b["text"], HAND_WRITTEN)
        self.assertEqual(b["schema"]["schema"], "autorig-spec/1")
        self.assertTrue(any(f["key"] == "head" for f in b["schema"]["rig"]))
        self.assertTrue(any(f["key"] == "builder" for f in b["schema"]["rig"]))
        self.assertIn("humanoid", b["schema"])
        self.assertIn("preview_url", b)
        spec = b["spec"]
        spec["rig"]["legs"] = ["bone_5"]
        c = self.call("/api/spec/check", {"model": "flat", "spec": spec})
        self.assertTrue(c["changed"])
        self.assertIn('+    "legs": ["bone_5"]', c["diff"])
        # a file changed on disk since the page loaded it is not overwritten
        self.assertEqual(self.status("/api/spec/save", {"model": "flat", "spec": spec, "base": "stale"}), 409)
        # nor is a broken spec written
        bad = json.loads(json.dumps(spec)); del bad["rig"]["kind"]
        self.assertEqual(self.status("/api/spec/save", {"model": "flat", "spec": bad, "base": b["base"]}), 400)
        r = self.call("/api/spec/save", {"model": "flat", "spec": spec, "base": b["base"]})
        self.assertTrue(r["saved"])
        d = os.path.join(self.models, "Things", "flat")
        with open(os.path.join(d, "rig.json.bak"), encoding="utf-8") as fh: self.assertEqual(fh.read(), HAND_WRITTEN)
        with open(os.path.join(d, "rig.json"), encoding="utf-8") as fh: saved = json.load(fh)
        self.assertEqual(saved["rig"]["future_field"], {"kept": True})
        self.assertEqual(saved["rig"]["legs"], ["bone_5"])
        self.assertEqual(self.call("/api/spec?name=flat")["base"], r["base"])

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_3_source_view_and_rerig(self):
        d = os.path.join(self.models, "Things", "boned")
        os.makedirs(d)
        fbx = os.path.join(d, "boned.fbx")
        make = (
            "import bpy\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            "bpy.ops.mesh.primitive_cylinder_add(vertices=16, depth=2, radius=0.3, location=(0, 0, 1))\n"
            "me = bpy.context.active_object\n"
            "bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.subdivide(number_cuts=8); bpy.ops.object.mode_set(mode='OBJECT')\n"
            "arm = bpy.data.objects.new('Armature', bpy.data.armatures.new('Armature')); bpy.context.collection.objects.link(arm)\n"
            "bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode='EDIT')\n"
            "prev = None\n"
            "for i, (z0, z1) in enumerate(((0, 0.05), (0.2, 0.8), (0.8, 1.4), (1.4, 1.9))):\n"
            "    b = arm.data.edit_bones.new('bone_%%d' %% i); b.head = (0, 0, z0); b.tail = (0, 0, z1); b.parent = prev; prev = b\n"
            "bpy.ops.object.mode_set(mode='OBJECT')\n"
            "bpy.ops.object.select_all(action='DESELECT'); me.select_set(True); arm.select_set(True)\n"
            "bpy.context.view_layer.objects.active = arm; bpy.ops.object.parent_set(type='ARMATURE_AUTO')\n"
            "bpy.ops.export_scene.fbx(filepath=%r, add_leaf_bones=False)\n" % fbx)
        r = subprocess.run([blender.find(), "-b", "--factory-startup", "--python-expr", make],
                           capture_output=True, text=True, errors="replace", timeout=300)
        self.assertTrue(os.path.exists(fbx), r.stdout[-2000:] + r.stderr[-2000:])

        job = self.wait(self.call("/api/spec/source", {"model": "boned"}))
        self.assertEqual(job["state"], "done", "\n".join(job["log"][-30:]))
        b = self.call("/api/spec?name=boned")
        src = b["source"]
        self.assertEqual(src["format"], "autorig-source/1")
        self.assertEqual(src["skeleton"], "tripo")
        self.assertEqual([j["name"] for j in src["joints"]], ["bone_0", "bone_1", "bone_2", "bone_3"])
        self.assertEqual(src["joints"][2]["parent"], "bone_1")
        glb = os.path.join(self.models, "_autorig", "source", "boned.glb")
        g = read_glb(glb)
        self.assertTrue(g["meshes"])
        self.assertNotIn("skins", g)                      # the mesh as it stands; the editor draws the skeleton
        self.assertIsNone(b["text"])                      # no rig.json yet

        spec = {"schema": "autorig-spec/1", "rig": {"kind": "tripo", "head": "bone_3", "hips": "bone_nine"}}
        self.assertEqual(self.status("/api/spec/rerig", {"model": "boned", "spec": spec, "base": b["base"]}), 400)
        spec["rig"]["hips"] = "bone_1"
        first = self.call("/api/spec/rerig", {"model": "boned", "spec": spec, "base": b["base"]})
        j = self.wait(first["job"])
        self.assertEqual(j["state"], "done", "\n".join(j["log"][-40:]))
        self.assertEqual([l for l in j["log"] if l.startswith("== ") and not l.endswith("done")],
                         ["== rig (tripo)", "== trim to budget", "== audit", "== preview for the viewer"])
        b = self.call("/api/spec?name=boned")
        self.assertIsNotNone(b["audit"])
        self.assertIsNone(b["before"])                    # the first rig had nothing to compare with
        self.assertEqual(b["bone_from"].get("head"), "bone_3")
        second = self.call("/api/spec/rerig", {"model": "boned", "spec": dict(spec, rig=dict(spec["rig"], smooth=2)), "base": first["base"]})
        self.assertEqual(self.wait(second["job"])["state"], "done")
        b = self.call("/api/spec?name=boned")
        self.assertEqual(set(b["before"]["checks"]), set(b["audit"]["checks"]))
        self.assertGreater(b["audit_time"], b["before"]["time"])

    def test_4_suggest_api(self):
        # Suggestion using source from boned
        sug = self.call("/api/spec/suggest", {"model": "boned"})
        self.assertIn("spec", sug)
        self.assertIn("archetype", sug)
        self.assertEqual(sug["spec"]["schema"], "autorig-spec/1")
        self.assertIn("rig", sug["spec"])

        # Suggestion with direct survey payload
        payload = {
            "model": "boned",
            "source": {
                "format": "autorig-source/1",
                "survey": {
                    "probe_tips": [
                        {"pos": [0.0, 0.4, 0.9], "name": "snout"},
                        {"pos": [0.0, -0.5, 0.4], "name": "tail"},
                        {"pos": [0.25, 0.2, 0.0], "name": "foot_FL"},
                        {"pos": [-0.25, 0.2, 0.0], "name": "foot_FR"},
                        {"pos": [0.25, -0.3, 0.0], "name": "foot_BL"},
                        {"pos": [-0.25, -0.3, 0.0], "name": "foot_BR"}
                    ]
                }
            }
        }
        res = self.call("/api/spec/suggest", payload)
        self.assertEqual(res["archetype"], "quadruped")
        self.assertEqual(res["spec"]["rig"]["kind"], "placed")

    def test_5_auto_tune_api(self):
        res = self.call("/api/spec/auto-tune", {"model": "boned", "max_iterations": 1})
        self.assertIn("job", res)
        self.assertEqual(res["job"]["step"], "auto-tune")


if __name__ == "__main__":
    unittest.main()

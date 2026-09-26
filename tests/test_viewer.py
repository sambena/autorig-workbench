# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the results viewer's endpoints, and the preview step on a generated rig.
#
#   python -m unittest discover -s tests -v
#
# Starts the server (no browser) on a temporary models folder holding two "rigged" models made here: one with a
# preview.glb, its preview.json and an audit, one without. Checks the token and the Host rules on the viewer's page,
# its code and the vendored three.js, the /api/previews list with its audit digest, the worst bones an audit
# blames, and that the GLB is served. With Node, runs tests/viewer_logic_test.mjs (gamepads, the scrub bar, the
# bleed rule, islands). With Blender, a two-bone rig with two actions is built in a .blend and preview_glb.py
# turns it into a GLB with a skin and both clips as animations.
import json, os, secrets, shutil, struct, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
sys.path.insert(0, os.path.join(REPO, "autorig", "gui"))
import blender  # noqa: E402
import viewer_api  # noqa: E402

TOKEN = "test-" + secrets.token_hex(4)
HAVE_BLENDER = blender.find(required=False) is not None
NODE = shutil.which("node")

# An audit as steps/audit.py writes it, cut down to what the viewer reads. The bend tears check failed; the
# bleed and head checks passed.
AUDIT = {
    "verdict": {"pass": False, "warnings": ["twist tears 7 (neck)", "leg_2.L reaches 0.31"],
                "checks": {"bleed_pct": {"value": 1.2, "ok": True, "limit": 2.0},
                           "combined_tears": {"value": 0, "ok": True, "limit": 0},
                           "bend_tears": {"value": 12, "ok": False, "limit": 0},
                           "head_pct": {"value": 6.0, "ok": True, "limit": 2.5},
                           "max_influences": {"value": 4, "ok": True, "limit": 4}}},
    "hierarchy": [{"bone": b, "parent": None} for b in ("hips", "neck", "head", "leg_1.L", "leg_2.L")],
    "bends": [{"bone": "leg_1.L", "mode": "bend", "tear_edges": 12, "max_stretch": 3.1, "collateral_pct": 0.4},
              {"bone": "leg_1.L", "mode": "twist", "tear_edges": 2, "max_stretch": 2.2, "collateral_pct": 0},
              {"bone": "neck", "mode": "twist", "tear_edges": 7, "max_stretch": 2.5, "collateral_pct": 0},
              {"bone": "head", "mode": "bend", "tear_edges": 0, "max_stretch": 1.2, "collateral_pct": 3.5}],
    "bleed_pairs": [["leg_2.L", "neck", 0.9], ["hips", "leg_1.L", 0.01]],
    "joints": [{"joint": "leg_2.L", "parent": "leg_1.L", "hard_edges": 4}],
    "meshes": [{"name": "body", "verts": 100, "max_influences": 4, "verts_over_4": 0}],
    "islands": 3, "rigid_islands": 1, "largest_islands": [{"verts": 90, "rigid": False, "bone": "hips", "area_pct": 95.0}],
    "bleed_total_pct": 1.2, "unweighted_verts": 0,
    "tears": {"worst_bone": "leg_1.L"},
}

def glb(path, doc, binary=b""):
    """A GLB file: the JSON chunk (padded with spaces) and an optional binary chunk."""
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    if binary:
        body += struct.pack("<I4s", len(binary), b"BIN\0") + binary
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def read_glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2
    n, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    return json.loads(data[20:20 + n])


def model(root, group, name, preview):
    d = os.path.join(root, group, name)
    os.makedirs(os.path.join(d, "rigged"))
    with open(os.path.join(d, name + ".obj"), "w") as fh:
        fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    with open(os.path.join(d, "rigged", name + ".fbx"), "wb") as fh:
        fh.write(b"not a real fbx")                    # only its presence matters here
    if preview:
        time.sleep(0.01)
        glb(os.path.join(d, "rigged", "preview.glb"), {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0})
        with open(os.path.join(d, "rigged", "preview.json"), "w") as fh:
            json.dump({"format": "autorig-preview/1", "model": name, "clips": [], "bones": 0, "metres": 1.5}, fh)


class ViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-viewer-test-")
        cls.models = os.path.join(cls.tmp, "models")
        model(cls.models, "Things", "shown", preview=True)
        model(cls.models, "Things", "unseen", preview=False)
        os.makedirs(os.path.join(cls.models, "_autorig", "audit"))             # the default work folder
        with open(os.path.join(cls.models, "_autorig", "audit", "shown.json"), "w") as fh:
            json.dump(AUDIT, fh)
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

    def get(self, path, token=TOKEN, host=None):
        headers = {}
        if token: headers["X-Autorig-Token"] = token
        if host: headers["Host"] = host
        req = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_page_needs_the_token(self):
        self.assertEqual(self.get("/viewer.html?model=shown", token=None)[0], 403)
        code, headers, body = self.get("/viewer.html?model=shown&t=" + TOKEN, token=None)
        self.assertEqual(code, 200)
        self.assertIn(TOKEN.encode(), body)                      # the page carries it for its API calls
        self.assertNotIn(b"__AUTORIG_TOKEN__", body)
        self.assertIn("blob:", headers["Content-Security-Policy"])
        self.assertIn(b"importmap", body)

    def test_code_and_vendor_are_static(self):
        for path in ("/viewer.js", "/vendor/three/build/three.module.js", "/vendor/three/build/three.core.js",
                     "/vendor/three/examples/jsm/loaders/GLTFLoader.js",
                     "/vendor/three/examples/jsm/controls/OrbitControls.js",
                     "/vendor/three/examples/jsm/renderers/CSS2DRenderer.js"):
            code, headers, _ = self.get(path, token=None)
            self.assertEqual(code, 200, path)
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"), path)
        self.assertEqual(self.get("/vendor/three/LICENSE", token=None)[0], 404)       # only code types are served
        for bad in ("/vendor/../server.py", "/vendor/%2e%2e/server.py", "/vendor/three/../../viewer_api.py",
                    "/vendor/three/build/../../../server.py"):
            self.assertIn(self.get(bad, token=None)[0], (400, 403, 404), bad)
        self.assertEqual(self.get("/viewer.js", token=None, host="evil.example")[0], 403)

    def test_previews_list(self):
        self.assertEqual(self.get("/api/previews", token=None)[0], 403)
        code, _, body = self.get("/api/previews")
        self.assertEqual(code, 200)
        by = {m["name"]: m for m in json.loads(body)["models"]}
        self.assertEqual(set(by), {"shown", "unseen"})
        s, u = by["shown"], by["unseen"]
        self.assertEqual((s["group"], s["rig_folder"], s["stale"]), ("Things", "rigged", False))
        self.assertRegex(s["preview"], r"^/files/models/Things/shown/rigged/preview\.glb\?v=\d+-\d+$")
        self.assertEqual(s["info"]["metres"], 1.5)
        self.assertIsNone(u["preview"])
        self.assertIsNone(u["info"])
        self.assertIsNone(u["audit"])
        a = s["audit"]
        self.assertIs(a["pass"], False)
        self.assertFalse(a["checks"]["bend_tears"]["ok"])
        self.assertEqual(a["bleed_pairs"][0], ["leg_2.L", "neck", 0.9])
        self.assertEqual((a["islands"], a["rigid_islands"]), (3, 1))
        self.assertEqual(a["worst_bone"], "leg_1.L")
        self.assertEqual(a["bones"][0]["bone"], "leg_1.L")                     # the failed check's bone, first
        self.assertEqual(a["bones"][0]["level"], "bad")
        code, headers, data = self.get(s["preview"])
        self.assertEqual(code, 200)
        self.assertEqual(data[:4], b"glTF")
        self.assertIn("immutable", headers.get("Cache-Control", ""))                 # versioned: cacheable
        self.assertEqual(self.get(s["preview"], token=None)[0], 403)

    def test_viewer_logic_is_static(self):
        code, headers, body = self.get("/viewer_logic.js", token=None)
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertIn(b"export function padStep", body)
        self.assertEqual(self.get("/viewer_api.py", token=None)[0], 403)            # the rest of gui/ is not

    def test_worst_bones(self):
        rows = {r["bone"]: r for r in viewer_api.worst_bones(AUDIT)}
        # bend tears failed: the bone that tears is red, first
        self.assertEqual(rows["leg_1.L"]["level"], "bad")
        self.assertTrue(any("tears 12 edges bent" in x for x in rows["leg_1.L"]["reasons"]))
        self.assertTrue(any("twisted" in x for x in rows["leg_1.L"]["reasons"]))
        # bleed passed: its owner is only amber; so are twist tears, reach, a hard joint and collateral
        self.assertEqual(rows["leg_2.L"]["level"], "warn")
        why = " ".join(rows["leg_2.L"]["reasons"])
        self.assertIn("nearer neck", why); self.assertIn("reaches 0.31", why); self.assertIn("hard joint", why)
        self.assertEqual(rows["neck"]["level"], "warn")
        self.assertIn("drags 3.5%", rows["head"]["reasons"][0])
        self.assertNotIn("hips", rows)                                   # a 0.01% bleed pair is noise
        self.assertEqual(viewer_api.worst_bones(AUDIT)[0]["bone"], "leg_1.L")
        # a failed bleed check turns a large pair red; a failed head share blames the head
        failed = json.loads(json.dumps(AUDIT))
        failed["verdict"]["checks"]["bleed_pct"]["ok"] = False
        failed["verdict"]["checks"]["head_pct"].update(ok=False, value=0.4)
        rows = {r["bone"]: r for r in viewer_api.worst_bones(failed)}
        self.assertEqual(rows["leg_2.L"]["level"], "bad")
        self.assertEqual(rows["head"]["level"], "bad")
        self.assertIn("owns only 0.4%", " ".join(rows["head"]["reasons"]))
        self.assertEqual(len(viewer_api.worst_bones(AUDIT, limit=2)), 2)
        self.assertEqual(viewer_api.worst_bones({}), [])

    @unittest.skipUnless(NODE, "Node not found")
    def test_viewer_logic_under_node(self):
        r = subprocess.run([NODE, os.path.join(REPO, "tests", "viewer_logic_test.mjs")], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        self.assertIn("passed", r.stdout)

    def test_state_offers_the_preview_step(self):
        code, _, body = self.get("/api/state")
        by = {m["name"]: m for m in json.loads(body)["models"]}
        self.assertTrue(by["shown"]["preview"])
        self.assertFalse(by["unseen"]["preview"])
        self.assertIsNone(by["unseen"]["steps"]["preview"])          # rigged: Preview can run

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_preview_step_on_a_generated_rig(self):
        d = os.path.join(self.tmp, "gen")
        os.makedirs(d)
        blend = os.path.join(d, "rig.blend")
        make = (
            "import bpy\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            "bpy.ops.mesh.primitive_cylinder_add(vertices=12, depth=2, location=(0, 0, 1))\n"
            "me = bpy.context.active_object\n"
            "bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.subdivide(number_cuts=6); bpy.ops.object.mode_set(mode='OBJECT')\n"
            "arm = bpy.data.objects.new('rig', bpy.data.armatures.new('rig')); bpy.context.collection.objects.link(arm)\n"
            "bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode='EDIT')\n"
            "a = arm.data.edit_bones.new('base'); a.head = (0, 0, 0); a.tail = (0, 0, 1)\n"
            "b = arm.data.edit_bones.new('top'); b.head = (0, 0, 1); b.tail = (0, 0, 2); b.parent = a; b.use_connect = True\n"
            "bpy.ops.object.mode_set(mode='OBJECT')\n"
            "bpy.ops.object.select_all(action='DESELECT'); me.select_set(True); arm.select_set(True)\n"
            "bpy.context.view_layer.objects.active = arm; bpy.ops.object.parent_set(type='ARMATURE_AUTO')\n"
            "arm.animation_data_create()\n"
            "for name, angle in (('bend', 0.8), ('sway', -0.5)):\n"
            "    act = bpy.data.actions.new(name); act.use_fake_user = True; arm.animation_data.action = act\n"
            "    pb = arm.pose.bones['top']; pb.rotation_mode = 'XYZ'\n"
            "    for f, x in ((0, 0.0), (10, angle), (20, 0.0)):\n"
            "        pb.rotation_euler = (x, 0, 0); pb.keyframe_insert('rotation_euler', frame=f)\n"
            "arm.animation_data.action = None\n"
            "bpy.ops.wm.save_as_mainfile(filepath=%r)\n" % blend)
        r = subprocess.run([blender.find(), "-b", "--factory-startup", "--python-expr", make],
                           capture_output=True, text=True, errors="replace", timeout=300)
        self.assertTrue(os.path.exists(blend), r.stdout[-2000:] + r.stderr[-2000:])
        r = blender.run("preview_glb.py", "-file", blend, timeout=300)
        line = next((l for l in r.stdout.splitlines() if l.startswith("PREVIEW {")), None)
        self.assertIsNotNone(line, r.stdout[-3000:])
        info = json.loads(line[len("PREVIEW "):])
        self.assertNotIn("error", info)
        self.assertEqual(sorted(c["name"] for c in info["clips"]), ["bend", "sway"])
        self.assertEqual(info["bones"], 2)
        g = read_glb(os.path.join(d, "preview.glb"))
        self.assertEqual(len(g["skins"]), 1)
        self.assertEqual(len(g["skins"][0]["joints"]), 2)
        self.assertEqual(sorted(a["name"] for a in g["animations"]), ["bend", "sway"])
        self.assertTrue(all("JOINTS_0" in p["attributes"] for m in g["meshes"] for p in m["primitives"]))
        with open(os.path.join(d, "preview.json"), encoding="utf-8") as fh:
            side = json.load(fh)
        self.assertEqual(side["format"], "autorig-preview/1")
        self.assertAlmostEqual(side["bone_lengths"]["top"], 1.0, places=4)


if __name__ == "__main__":
    unittest.main()

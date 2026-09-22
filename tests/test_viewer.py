# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the results viewer's endpoints, and the preview step on a generated rig.
#
#   python -m unittest discover -s tests -v
#
# Starts the server (no browser) on a temporary models folder holding two "rigged" models made here: one with a
# preview.glb and its preview.json, one without. Checks the token and the Host rules on the viewer's page, its code
# and the vendored three.js, the /api/previews list, and that the GLB is served. With Blender, a two-bone rig with
# two actions is built in a .blend and preview_glb.py turns it into a GLB with a skin and both clips as animations.
import json, os, secrets, shutil, struct, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender  # noqa: E402

TOKEN = "test-" + secrets.token_hex(4)
HAVE_BLENDER = blender.find(required=False) is not None


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
        self.assertEqual(s["preview"], "/files/models/Things/shown/rigged/preview.glb")
        self.assertEqual(s["info"]["metres"], 1.5)
        self.assertIsNone(u["preview"])
        self.assertIsNone(u["info"])
        code, _, data = self.get(s["preview"])
        self.assertEqual(code, 200)
        self.assertEqual(data[:4], b"glTF")
        self.assertEqual(self.get(s["preview"], token=None)[0], 403)

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

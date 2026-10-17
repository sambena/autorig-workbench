# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the export passes (core/export_passes.py, core/fbx_inspect.py, steps/unity_fbx.py).
#
# The Unity check reads the FBX itself, as Unity does: a rig written the old way has its root at -90 on X; after the
# rewrite every root reads rotation (0,0,0) and scale 1, the model still faces +Z, and every bone and the deformed
# mesh are where they were at every sampled frame of every take. gltfpack and the glTF-Validator are tested when they
# are installed (run.py extras install gltfpack gltf-validator); without them the passes must say they were skipped.
import io, json, os, shutil, struct, subprocess, sys, tarfile, tempfile, unittest, zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import blender
import export_passes
import exporter
import fbx_inspect
import layout
import rigged_sample
import spec_store

HAVE_BLENDER = bool(blender.find(required=False))


# ---- a minimal binary FBX writer, for reading tests that need no Blender

def _prop(v):
    if isinstance(v, bool): return b"C" + struct.pack("<?", v)
    if isinstance(v, int): return b"L" + struct.pack("<q", v)
    if isinstance(v, float): return b"D" + struct.pack("<d", v)
    if isinstance(v, str): v = v.encode()
    return b"S" + struct.pack("<I", len(v)) + v


def _node(name, props=(), children=(), offset=0):
    pb = b"".join(_prop(p) for p in props)
    head = 13 + len(name) + len(pb)
    body = b""
    for c in children:
        body += c(offset + head + len(body))
    if children: body += b"\0" * 13
    end = offset + head + len(body)
    return struct.pack("<III", end, len(props), len(pb)) + bytes([len(name)]) + name.encode() + pb + body


def N(name, *props, children=()):
    return lambda off: _node(name, props, children, off)


def P(name, kind, *vals):
    return N("P", name, kind, "", "A", *vals)


def fake_fbx(path, up=1, unit=100.0, roots=(("rig", (-90.0, 0.0, 0.0), (1.0, 1.0, 1.0)),)):
    gs = N("GlobalSettings", children=[N("Properties70", children=[
        P("UpAxis", "int", up), P("UpAxisSign", "int", 1), P("FrontAxis", "int", 2), P("FrontAxisSign", "int", 1),
        P("CoordAxis", "int", 0), P("CoordAxisSign", "int", 1), P("UnitScaleFactor", "double", unit)])])
    models, conns = [], []
    for i, (name, rot, sc) in enumerate(roots):
        mid = 1000 + i
        models.append(N("Model", mid, name + "\x00\x01Model", "Null", children=[N("Properties70", children=[
            P("Lcl Rotation", "Lcl Rotation", *rot), P("Lcl Scaling", "Lcl Scaling", *sc)])]))
        conns.append(N("C", "OO", mid, 0))
        # a bone under it, also linked to a (missing) skin cluster: neither makes it a root
        models.append(N("Model", mid + 500, "bone\x00\x01Model", "LimbNode", children=[N("Properties70", children=[
            P("Lcl Rotation", "Lcl Rotation", 12.0, 0.0, 0.0)])]))
        conns += [N("C", "OO", mid + 500, mid), N("C", "OO", mid + 500, 99999)]
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400)
    for n in (gs, N("Objects", children=models), N("Connections", children=conns)):
        out += n(len(out))
    out += b"\0" * 13
    with open(path, "wb") as fh: fh.write(out)


# ---- a minimal GLB: one triangle; `broken` points its accessor past the end of the buffer

def tiny_glb(path, broken=False):
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "t"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 30 if broken else 3, "type": "VEC3",
                           "min": [0, 0, 0], "max": [1, 1, 0]}],
            "bufferViews": [{"buffer": 0, "byteLength": len(pos)}], "buffers": [{"byteLength": len(pos)}]}
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(pos), 0x004E4942) + pos
    with open(path, "wb") as fh: fh.write(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


class FbxInspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-fbx-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_axes_scale_and_roots(self):
        p = os.path.join(self.tmp, "a.fbx")
        fake_fbx(p)
        info = fbx_inspect.summary(p)
        self.assertEqual(info["up"], "+Y")
        self.assertEqual(info["front"], "+Z")
        self.assertEqual(info["unit_scale"], 100.0)
        self.assertEqual([r["name"] for r in info["roots"]], ["rig"])       # the bone is not a root
        self.assertEqual(fbx_inspect.unity_problems(info), ["root rig has rotation [-90.0, 0.0, 0.0]"])

    def test_unity_clean_file(self):
        p = os.path.join(self.tmp, "b.fbx")
        fake_fbx(p, roots=(("rig", (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),))
        self.assertEqual(fbx_inspect.unity_problems(fbx_inspect.summary(p)), [])

    def test_unity_scale_and_axis_problems(self):
        p = os.path.join(self.tmp, "c.fbx")
        fake_fbx(p, up=2, unit=1.0, roots=(("prop", (0.0, 0.0, 0.0), (100.0, 100.0, 100.0)),))
        probs = " | ".join(fbx_inspect.unity_problems(fbx_inspect.summary(p)))
        self.assertIn("+Z-up", probs)
        self.assertIn("UnitScaleFactor is 1", probs)
        self.assertIn("root prop has scale", probs)

    def test_ascii_fbx_is_refused(self):
        p = os.path.join(self.tmp, "d.fbx")
        with open(p, "w") as fh: fh.write("; FBX 7.4.0 project file\n")
        with self.assertRaises(ValueError):
            fbx_inspect.summary(p)


class UnityPassTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-unity-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_file_is_copied(self):
        src, dst = os.path.join(self.tmp, "a.fbx"), os.path.join(self.tmp, "out.fbx")
        fake_fbx(src, roots=(("rig", (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),))
        rec = export_passes.unity_fbx(src, dst, run_blender=lambda *a: self.fail("no rewrite needed"))
        self.assertEqual(rec["status"], "ok")
        self.assertTrue(os.path.isfile(dst))

    def test_without_blender_copies_and_says_skipped(self):
        src, dst = os.path.join(self.tmp, "a.fbx"), os.path.join(self.tmp, "out.fbx")
        fake_fbx(src)
        with mock.patch.dict(os.environ, {"AUTORIG_NO_BLENDER": "1"}):
            rec = export_passes.unity_fbx(src, dst)
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("Blender not found", rec["detail"])
        self.assertIn("-90", rec["detail"])
        self.assertTrue(os.path.isfile(dst))

    def test_rewrite_that_still_fails_stops_the_export(self):
        src, dst = os.path.join(self.tmp, "a.fbx"), os.path.join(self.tmp, "out.fbx")
        fake_fbx(src)

        def bad_blender(script, inp, out):
            shutil.copy2(inp, out)                          # "rewrites" without fixing anything
            return subprocess.CompletedProcess([], 0, "", "")
        with self.assertRaises(export_passes.GateError):
            export_passes.unity_fbx(src, dst, run_blender=bad_blender)
        self.assertFalse(os.path.exists(dst))

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_rewrite_keeps_pose_motion_and_facing(self):
        made, fixed = os.path.join(self.tmp, "critter.fbx"), os.path.join(self.tmp, "critter_unity.fbx")
        probe = os.path.join(HERE, "unity_fbx_probe.py")
        r = blender.run(probe, "make", made)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        before = fbx_inspect.summary(made)
        self.assertIn("root critter_rig has rotation [-90.0, 0.0, 0.0]", fbx_inspect.unity_problems(before))

        rec = export_passes.unity_fbx(made, fixed)
        self.assertEqual(rec["status"], "fixed", rec)
        after = fbx_inspect.summary(fixed)
        self.assertEqual(fbx_inspect.unity_problems(after), [])
        self.assertEqual(after["up"], "+Y")
        self.assertEqual(sorted(after["takes"]), ["hop", "nod"])
        for root in after["roots"]:
            self.assertTrue(all(abs(v) < 1e-3 for v in root["rotation"]), root)
            self.assertTrue(all(abs(v - 1) < 1e-4 for v in root["scaling"]), root)
        # facing: the nose (Blender -Y) is Unity's forward, +Z in the file
        pts = fbx_inspect.world_points(fixed)
        self.assertGreater(max(p[2] for p in pts), 0.75)

        # the same bones and deformed mesh at every sampled frame of every take
        samples = []
        for f in (made, fixed):
            out = f + ".json"
            r = blender.run(probe, "sample", f, out)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            samples.append(json.load(open(out)))
        a, b = samples
        self.assertEqual(sorted(a), sorted(b))
        worst = 0.0
        for take in a:
            for frame in a[take]:
                fa, fb = a[take][frame], b[take][frame]
                self.assertEqual(sorted(fa["bones"]), sorted(fb["bones"]))
                worst = max([worst] + [abs(x - y) for x, y in zip(fa["nose"], fb["nose"])])
                for bone in fa["bones"]:
                    for i in (0, 1):
                        worst = max([worst] + [abs(x - y) for x, y in zip(fa["bones"][bone][i], fb["bones"][bone][i])])
        self.assertLess(worst, 1e-4)


class GltfPassTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-gltf-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_without_tools_the_passes_are_skipped(self):
        p = os.path.join(self.tmp, "m.glb")
        tiny_glb(p)
        with mock.patch.dict(os.environ, {"AUTORIG_NO_TOOLS": "1"}):
            opt = export_passes.optimise_glb(p, "web")
            val = export_passes.validate_glb(p)
            passes = exporter.run_glb_passes(self.tmp, "web", "m.glb")
        self.assertEqual((opt["status"], val["status"]), ("skipped", "skipped"))
        self.assertIn("extras install gltfpack", opt["detail"])
        self.assertIn("extras install gltf-validator", val["detail"])
        self.assertEqual([p["status"] for p in passes], ["skipped", "skipped"])

    def test_turned_off(self):
        p = os.path.join(self.tmp, "m.glb")
        tiny_glb(p)
        self.assertEqual(export_passes.optimise_glb(p, "web", enabled=False)["detail"], "turned off")
        self.assertEqual(export_passes.validate_glb(p, enabled=False)["detail"], "turned off")

    def test_validator_errors_stop_the_package(self):
        tiny_glb(os.path.join(self.tmp, "m.glb"))
        report = {"issues": {"numErrors": 1, "numWarnings": 0, "messages": [
            {"code": "ACCESSOR_TOTAL_OFFSET_ALIGNMENT", "message": "bad", "severity": 0, "pointer": "/accessors/0"}]}}
        fake = lambda path, enabled=True: export_passes.validation_record({"pass": "gltf-validator", "file": "m.glb"},
                                                                          report)
        with mock.patch.object(export_passes, "validate_glb", fake):
            with self.assertRaises(export_passes.GateError) as cm:
                exporter.run_glb_passes(self.tmp, "web", "m.glb")
        self.assertIn("ACCESSOR_TOTAL_OFFSET_ALIGNMENT", str(cm.exception))

    @unittest.skipUnless(export_passes.find_tool("gltf-validator"), "glTF-Validator not installed")
    def test_real_validator(self):
        good, bad = os.path.join(self.tmp, "good.glb"), os.path.join(self.tmp, "bad.glb")
        tiny_glb(good)
        tiny_glb(bad, broken=True)
        self.assertEqual(export_passes.validate_glb(good)["status"], "passed")
        rec = export_passes.validate_glb(bad)
        self.assertEqual(rec["status"], "failed")
        self.assertGreater(rec["errors"], 0)

    @unittest.skipUnless(export_passes.find_tool("gltfpack") and export_passes.find_tool("gltf-validator"),
                         "gltfpack and the glTF-Validator not installed")
    def test_real_gltfpack_then_validator(self):
        for target in ("web", "godot"):
            d = os.path.join(self.tmp, target)
            os.makedirs(d)
            tiny_glb(os.path.join(d, "m.glb"))
            passes = exporter.run_glb_passes(d, target, "m.glb")
            self.assertEqual([(p["pass"], p["status"]) for p in passes],
                             [("gltf-validator", "passed"), ("gltfpack", "done"), ("gltf-validator", "passed")])
            data = open(os.path.join(d, "m.glb"), "rb").read()
            js = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            used = js.get("extensionsUsed", [])
            if target == "web":
                self.assertTrue(any("meshopt_compression" in e for e in used), used)
            else:                                           # Godot reads neither compression nor quantisation
                self.assertEqual(used, [])


class ToolInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-tools-")
        self.env = mock.patch.dict(os.environ, {"AUTORIG_TOOLS": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _opener(self, data):
        return lambda url: io.BytesIO(data)

    def test_zip_release(self):
        buf = io.BytesIO()
        exe = "gltfpack" + (".exe" if os.name == "nt" else "")
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("README.md", "x")
            z.writestr(exe, b"binary")
        with mock.patch.object(export_passes, "platform", lambda: "windows"):
            path = export_passes.install_tool("gltfpack", opener=self._opener(buf.getvalue()))
        self.assertEqual(open(path, "rb").read(), b"binary")
        self.assertEqual(export_passes.find_tool("gltfpack"), path)

    def test_tar_xz_release(self):
        buf = io.BytesIO()
        exe = "gltf_validator" + (".exe" if os.name == "nt" else "")
        with tarfile.open(fileobj=buf, mode="w:xz") as tf:
            info = tarfile.TarInfo("gltf_validator-2.0/" + exe)
            info.size = 6
            tf.addfile(info, io.BytesIO(b"binary"))
        with mock.patch.object(export_passes, "platform", lambda: "linux"):
            path = export_passes.install_tool("gltf-validator", opener=self._opener(buf.getvalue()))
        self.assertEqual(open(path, "rb").read(), b"binary")

    def test_every_tool_has_a_gpl_compatible_licence_and_pinned_release(self):
        for name, t in export_passes.TOOLS.items():
            self.assertIn(t["licence"], ("MIT", "Apache-2.0"), name)
            for plat in ("windows", "linux", "macos"):
                self.assertIn(t["version"], t["urls"][plat], name)
                self.assertTrue(t["urls"][plat].startswith("https://github.com/"), name)


class UnityPackageTest(unittest.TestCase):
    """The Unity package carries FBX that pass the Unity check, and says what it did in export_report.json."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-unitypkg-")
        cls.saved = {k: os.environ.get(k) for k in ("AUTORIG_MODELS", "AUTORIG_WORK")}
        models = os.path.join(cls.tmp, "models")
        shutil.copytree(os.path.join(REPO, "samples"), models)
        os.environ["AUTORIG_MODELS"] = models
        os.environ["AUTORIG_WORK"] = os.path.join(cls.tmp, "_autorig")
        layout.ROOT = os.path.abspath(models)
        layout.WORK = os.path.abspath(os.environ["AUTORIG_WORK"])
        spec_store.reload()
        cls.rig_error = rigged_sample.rig("biped", layout.WORK)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_unity_package_fbx_imports_unrotated(self):
        rigged_sample.need(self, self.rig_error)
        res = exporter.create_export_package("biped", target="unity", out_dir=os.path.join(self.tmp, "out"))
        unity = [p for p in res["passes"] if p["pass"] == "unity-fbx"]
        self.assertTrue(unity)
        self.assertTrue(all(p["status"] in ("ok", "fixed") for p in unity), unity)
        fbx = os.path.join(res["dir"], "biped.fbx")
        self.assertEqual(fbx_inspect.unity_problems(fbx_inspect.summary(fbx)), [])
        report = json.load(open(os.path.join(res["dir"], "export_report.json")))
        self.assertEqual(report["passes"], res["passes"])
        with zipfile.ZipFile(res["zip_path"]) as zf:
            self.assertIn("export_report.json", zf.namelist())

    def test_web_package_reports_its_glb_passes(self):
        rigged_sample.need(self, self.rig_error)
        res = exporter.create_export_package("biped", target="web", out_dir=os.path.join(self.tmp, "web"))
        kinds = {p["pass"] for p in res["passes"]}
        if any(f.endswith(".glb") for f in res["files"]):
            self.assertEqual(kinds, {"gltf-validator", "gltfpack"})
            self.assertFalse([p for p in res["passes"] if p["status"] == "failed"])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a headless run of the GUI server's API against one generated model.
#
#   python -m unittest discover -s tests -v
#
# Starts the server (no browser) on a temporary models folder, then talks to its API (/api/v1) as the page does: the
# token is required, files outside the served folders are refused and the old JSON routes are gone; a model is
# uploaded (an OBJ written here, with its rig.json, in a zip) and every step runs through the API; a running step is
# cancelled and its Blender process is gone; samples, the engine package, Mesh Doctor, batches and retarget uploads.
# The Blender parts are skipped when Blender cannot be found.
import io, json, math, os, re, secrets, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse
import urllib.request, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender  # noqa: E402
from autorig.gui.server import Job, Runner

TOKEN = "test-" + secrets.token_hex(4)
HAVE_BLENDER = blender.find(required=False) is not None


def column_obj(path, radius=0.2, height=2.0, seg=24, rings=30):
    """A capped cylinder standing on z=0 (Y up in the OBJ, as exporters write it): a stand-in for a real model."""
    v, f = [], []
    for r in range(rings + 1):
        y = height * r / rings
        for s in range(seg):
            a = 2 * math.pi * s / seg
            v.append((radius * math.cos(a), y, radius * math.sin(a)))
    for r in range(rings):
        for s in range(seg):
            a, b = r * seg + s, r * seg + (s + 1) % seg
            f.append((a, b, b + seg, a + seg))
    v += [(0, 0, 0), (0, height, 0)]
    lo, hi = len(v) - 2, len(v) - 1
    for s in range(seg):
        f.append((lo, (s + 1) % seg, s))
        f.append((hi, rings * seg + s, rings * seg + (s + 1) % seg))
    with open(path, "w") as fh:
        fh.write("o column\n")
        for p in v: fh.write("v %.5f %.5f %.5f\n" % p)
        for face in f: fh.write("f " + " ".join(str(i + 1) for i in face) + "\n")


SPEC = {
    "schema": "autorig-spec/1",
    "rig": {"kind": "build", "forward": [0, -1, 0],
            "chains": [{"name": "spine", "points": [[0.5, 0.5, 0.0], [0.5, 0.5, 0.5], [0.5, 0.5, 1.0]],
                        "names": ["body", "top"]}]},
    "budget": 600,
    "clips": {"archetype": "walker", "display": "Column", "category": "Props"},
    "card": {"metres": 2.0, "role": "prop"},
    "notes": {"rig": "a test column"},
}

BVH = b"""HIERARCHY
ROOT Hips
{
  OFFSET 0.00 0.00 0.00
  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
  End Site
  {
    OFFSET 0.00 1.00 0.00
  }
}
MOTION
Frames: 2
Frame Time: 0.0333333
0.0 0.0 0.0 0.0 0.0 0.0
0.0 0.0 1.0 0.0 0.0 0.0
"""


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-")
        cls.models = os.path.join(cls.tmp, "models")
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
        cls.proc.kill()
        cls.proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, token=TOKEN):
        """The API's JSON answer; an HTTP error raises."""
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json" if raw is None else "application/octet-stream"}
        if token: headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)

    def status(self, method, path, **kw):
        try:
            self.call(method, path, **kw)
            return 200
        except urllib.error.HTTPError as e:
            return e.code

    def get(self, path):
        return self.call("GET", path)

    def wait(self, job, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.get("/api/v1/jobs/%d?log=400" % job["id"])
            if j["state"] not in ("queued", "running"): return j
            time.sleep(0.5)
        self.fail("job %s did not finish" % job)

    def models_by_name(self):
        return {m["name"]: m for m in self.get("/api/v1/models")["models"]}

    # ---- no Blender needed

    def test_1_token_and_confinement(self):
        self.assertEqual(self.status("GET", "/api/v1/models", token=None), 401)
        self.assertEqual(self.status("GET", "/api/v1/models", token="wrong"), 401)
        self.assertEqual(self.get("/api/v1/models")["models"], [])
        self.assertIn("blender_version", self.get("/api/v1/server"))
        self.assertIn(self.status("GET", "/files/models/../../etc/passwd"), (400, 403, 404))
        self.assertEqual(self.status("POST", "/api/v1/models/x/steps/rm%20-rf"), 404)
        # the old page's JSON routes went in M7 (docs/OLD-UI.md maps them to /api/v1)
        for method, old in (("GET", "/api/state"), ("POST", "/api/run"), ("POST", "/api/upload"), ("GET", "/api/samples"),
                            ("GET", "/api/studio"), ("POST", "/api/ai/start"), ("GET", "/api/spec"), ("GET", "/samples/x.png"),
                            ("POST", "/api/workspace/set-root"), ("POST", "/api/git/commit"), ("POST", "/api/clean")):
            self.assertEqual(self.status(method, old, body={} if method == "POST" else None), 404, old)

        # the page needs the token and carries it for its API calls; its code is static
        self.assertEqual(self.status("GET", "/", token=None), 403)
        with urllib.request.urlopen(self.base + "/?t=" + TOKEN) as r:
            page = r.read().decode("utf-8")
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertIn(TOKEN, page)
        self.assertNotIn("__AUTORIG_TOKEN__", page)
        # the page's code is asked for at a versioned address, so no cache in between serves old code
        m = re.search(r'src="(/app\.js\?v=[0-9a-f]+)"', page)
        self.assertTrue(m, "app.js is not versioned in the page")
        with urllib.request.urlopen(self.base + m.group(1)) as r:
            self.assertIn("immutable", r.headers["Cache-Control"])
            self.assertRegex(r.read().decode("utf-8"), r"from '/viewer\.js\?v=[0-9a-f]+'")
        with urllib.request.urlopen(self.base + "/app.js") as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))
            self.assertIn(b"/api/v1", r.read())
        # the old pages went with M5 (docs/OLD-UI.md maps them to the API and CLI)
        for old in ("/help.html", "/viewer.html", "/spec_editor.html", "/studio.html", "/workbench.js",
                    "/spec_editor.js"):
            self.assertEqual(self.status("GET", old), 404, old)
        # the preview (gui/viewer.js) and its vendored three.js: served like /app.js, nothing outside vendor/
        for ok in ("/viewer.js", "/vendor/three/build/three.module.js", "/vendor/three/examples/jsm/loaders/FBXLoader.js"):
            with urllib.request.urlopen(self.base + ok) as r:
                self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"), ok)
        for bad in ("/vendor/three/LICENSE", "/vendor/../server.py", "/vendor/%2e%2e/api_v1.py"):
            self.assertIn(self.status("GET", bad), (400, 403, 404), bad)

    def test_1b_mesh2motion_static(self):
        """/m2m/ serves the built Mesh2Motion app without the token, and nothing outside its folder."""
        def get(path):
            try:
                with urllib.request.urlopen(self.base + path, timeout=30) as r:
                    return r.status, r.headers.get("Content-Type", ""), r.read()
            except urllib.error.HTTPError as e:
                return e.code, "", b""
        self.assertIn(get("/m2m/../server.py")[0], (400, 403, 404))
        self.assertIn(get("/m2m/%2e%2e/server.py")[0], (400, 403, 404))
        if not os.path.isdir(os.path.join(REPO, "autorig", "gui", "m2m")):
            self.assertEqual(get("/m2m/create.html")[0], 404)
            self.skipTest("Mesh2Motion not built (python scripts/build_m2m.py)")
        code, ctype, body = get("/m2m/create.html")
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"<script", body)
        code, ctype, body = get("/m2m/rigs/rig-human.glb")
        self.assertEqual((code, ctype), (200, "model/gltf-binary"))
        self.assertEqual(body[:4], b"glTF")

    # ---- the pipeline, through the API

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_2_upload_and_run_all(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        column_obj(os.path.join(src, "column.obj"))
        with open(os.path.join(src, "rig.json"), "w") as fh: json.dump(SPEC, fh)
        buf = io.BytesIO()                                 # a model with its rig.json comes as one zip
        with zipfile.ZipFile(buf, "w") as z:
            for f in ("column.obj", "rig.json"): z.write(os.path.join(src, f), f)
        added = self.call("POST", "/api/v1/models?filename=column.zip&name=column&group=Props", raw=buf.getvalue())
        self.assertEqual(added["model"]["name"], "column")

        m = self.models_by_name()["column"]
        self.assertEqual((m["group"], m["kind"], m["source"]), ("Props", "build", "column.obj"))
        self.assertIsNone(m["steps"]["all"])
        self.assertIn("run Rig", m["steps"]["audit"])            # greyed out, and says why

        survey = self.wait(self.call("POST", "/api/v1/models/column/steps/survey"))
        self.assertEqual(survey["state"], "done", "\n".join(survey["log"][-30:]))
        job = self.wait(self.call("POST", "/api/v1/models/column/steps/all"))
        self.assertEqual(job["state"], "done", "\n".join(job["log"][-40:]))

        d = self.get("/api/v1/models/column")
        self.assertEqual(d["survey"]["skeleton"], "none")
        self.assertEqual(len(d["facing"]), 4)
        self.assertIsNotNone(d["bend_test"])
        a = d["audit"]
        self.assertIsInstance(a["pass"], bool)
        self.assertIn(a["grade"], ("PASS", "CHECK", "FAIL"))
        self.assertEqual(a["pass"], a["grade"] == "PASS")            # pass stays the strict result
        self.assertTrue({c["check"] for c in a["checks"]} >= {"bleed_pct", "combined_tears", "max_influences"})
        self.assertTrue(d["clip_frames"].get("walk"), "no walk preview frames")
        self.assertEqual(d["clips"]["format"], "autorig-clips/1")
        card = d["card"]
        self.assertEqual((card["name"], card["group"], card["metres"], card["role"]), ("column", "Props", 2.0, "prop"))
        self.assertEqual((card["rig"]["audit"]["pass"], card["rig"]["audit"]["grade"]), (a["pass"], a["grade"]))
        rows = self.get("/api/v1/audits")["models"]
        self.assertEqual([(r["model"], r["grade"]) for r in rows], [("column", a["grade"])])
        full = self.get("/api/v1/models/column/audit")
        self.assertIn("verdict", full["audit"])
        self.assertIsInstance(full["bones"], list)
        req = urllib.request.Request(self.base + d["bend_test"] + "?t=" + TOKEN)
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_3_cancel_kills_only_its_process(self):
        job = self.call("POST", "/api/v1/models/column/steps/rig")
        pid = None
        for _ in range(200):
            j = self.get("/api/v1/jobs/%d" % job["id"])
            if j["pid"] or j["state"] not in ("queued", "running"): pid = j["pid"]; break
            time.sleep(0.02)
        self.call("DELETE", "/api/v1/jobs/%d" % job["id"])
        j = self.wait(job)
        if j["state"] == "done":
            self.skipTest("the step finished before it could be cancelled")
        self.assertEqual(j["state"], "cancelled")
        self.assertTrue(any("killing PID %d" % pid in l for l in j["log"]))
        time.sleep(0.5)
        self.assertFalse(alive(pid), "PID %d still running after Cancel" % pid)

    def test_4_samples_gallery_api(self):
        samples = self.get("/api/v1/samples")["samples"]
        self.assertEqual(sorted(s["name"] for s in samples), ["beetle", "biped", "canine", "pedestal", "wyvern"])
        for s in samples:
            self.assertTrue(s["title"])
            self.assertTrue(s["archetype"])
            self.assertIn(s["kind"], ("placed", "build"))
            self.assertGreater(s["budget"], 0)
            self.assertTrue(s["description"])
            self.assertIn("installed", s)

    def test_5_load_sample_model(self):
        self.assertEqual(self.status("POST", "/api/v1/samples/nonexistent"), 400)
        res = self.call("POST", "/api/v1/samples/pedestal")
        self.assertEqual((res["name"], res["loaded"], res["already_installed"]), ("pedestal", True, False))
        self.assertEqual(res["model"]["name"], "pedestal")
        self.assertTrue(os.path.isfile(os.path.join(self.models, "pedestal", "rig.json")))
        self.assertTrue(self.models_by_name()["pedestal"]["spec"])
        self.assertTrue(self.call("POST", "/api/v1/samples/pedestal")["already_installed"])
        self.assertTrue(next(s for s in self.get("/api/v1/samples")["samples"] if s["name"] == "pedestal")["installed"])

    def test_6_engine_package(self):
        # an engine package is Model Studio's package tool now (the old File > Export)
        job = self.call("POST", "/api/v1/models/pedestal/studio/package",
                        {"settings": {"target": "unreal", "include_lods": False}})
        self.assertEqual(job["step"], "studio-package")
        j = self.wait(job)
        self.assertEqual(j["state"], "done", "\n".join(j["log"][-30:]))
        arts = {a["path"]: a for a in self.get("/api/v1/models/pedestal/studio")["artifacts"]}
        self.assertIn("_studio/package/package.json", arts)
        z = next(a for p, a in arts.items() if p.endswith(".zip"))
        with urllib.request.urlopen(self.base + z["url"] + "?t=" + TOKEN) as r:
            self.assertEqual(len(r.read()), z["bytes"])

    def test_7_doctor_api(self):
        diag = self.get("/api/v1/models/pedestal/doctor")
        for k in ("health_score", "grade", "verts", "faces"): self.assertIn(k, diag)
        self.assertIn(diag["grade"], ("HEALTHY", "WARN", "CRITICAL"))

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8_rig_all_api(self):
        job = self.call("POST", "/api/v1/batch/rig-all", {})
        self.assertEqual(job["step"], "rig-all")
        self.assertRegex(job["model"], r"^\(\d+ rig-ready models\)$")
        for k in ("total", "passed", "failed", "last_result"): self.assertIn(k, job)
        self.call("DELETE", "/api/v1/jobs/%d" % job["id"])
        self.wait(job)

    def test_9_abrupt_client_disconnect(self):
        import socket, struct
        host, port = "127.0.0.1", int(self.base.split(":")[-1])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        req = f"GET /api/v1/models?t={TOKEN} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode("utf-8"))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()

        time.sleep(0.1)
        self.assertIn("models", self.get("/api/v1/models"))

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8b_source_all_api(self):
        job = self.call("POST", "/api/v1/batch/source-all", {"missing_only": False})
        self.assertEqual(job["step"], "source-all")
        self.assertRegex(job["model"], r"^\(\d+ source views\)$")
        self.call("DELETE", "/api/v1/jobs/%d" % job["id"])
        self.wait(job)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8c_clips_all_api(self):
        job = self.call("POST", "/api/v1/batch/clips-all", {"models": ["column"]})
        self.assertEqual((job["step"], job["model"]), ("clips-all", "(1 selected models)"))
        self.call("DELETE", "/api/v1/jobs/%d" % job["id"])
        self.wait(job)
        self.assertEqual(self.status("POST", "/api/v1/batch/clips-all", body={"models": ["nobody"]}), 400)
        self.assertEqual(self.status("POST", "/api/v1/batch/everything", body={}), 404)

    def test_8d_rebake_clips_endpoint_and_commands(self):
        import autorig.gui.server as server
        import layout
        import blender
        server.layout = layout
        server.blender = blender
        mock_spec = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "clips": {"archetype": "walker"}}}
        if HAVE_BLENDER:
            cmds = server.commands(".", "test_creature", "rebake-clips", mock_spec)
            self.assertEqual(len(cmds), 2)
            self.assertTrue(cmds[0][0].startswith("make clips"))
            self.assertEqual(cmds[1][0], "preview for the viewer")
        else:                                          # a clean reason, not a dropped connection
            with self.assertRaises(ValueError):
                server.commands(".", "test_creature", "rebake-clips", mock_spec)

        # pedestal with no clips section: rebake-clips is refused with the reason (409)
        pedestal_rig = os.path.join(self.models, "pedestal", "rig.json")
        if os.path.exists(pedestal_rig):
            with open(pedestal_rig) as f:
                p_spec = json.load(f)
            p_spec.pop("clips", None)
            with open(pedestal_rig, "w") as f:
                json.dump(p_spec, f)
        pedestal = self.models_by_name().get("pedestal")
        self.assertIsNotNone(pedestal)
        self.assertIsNotNone(pedestal["steps"].get("rebake-clips"))
        self.assertEqual(self.status("POST", "/api/v1/models/pedestal/steps/rebake-clips"), 409)

    def test_8e_retarget_endpoints(self):
        up = self.call("POST", "/api/v1/mocap?filename=test_motion.bvh", raw=BVH)
        self.assertEqual(up["file"], "test_motion.bvh")
        self.assertTrue(os.path.isfile(up["path"]))
        self.assertEqual((up["meta"]["format"], up["meta"]["frames"]), ("BVH", 2))
        self.assertIn("test_motion.bvh", [f["file"] for f in self.get("/api/v1/mocap")["files"]])

        # a mocap path outside the models root and the work folder is refused, as /files/ refuses it
        outside = os.path.join(tempfile.mkdtemp(prefix="autorig-outside-"), "elsewhere.bvh")
        with open(outside, "wb") as fh:
            fh.write(BVH)
        self.assertEqual(self.status("POST", "/api/v1/models/pedestal/retarget", body={"file": outside}), 404)
        self.assertEqual(self.status("GET", "/api/v1/models/pedestal/retarget?file=" + urllib.parse.quote(outside)), 404)
        # a model with no rig has nothing to retarget onto
        if not os.path.exists(os.path.join(self.models, "pedestal", "rigged", "pedestal.blend")):
            self.assertEqual(self.status("GET", "/api/v1/models/pedestal/retarget?file=test_motion.bvh"), 409)

        # an upload named to climb out of the mocap folder, or not a mocap file, or not BVH inside, is refused
        for bad, data in (("..", BVH), ("notes.txt", BVH), (".bvh", BVH), ("junk.bvh", b"not a bvh")):
            self.assertEqual(self.status("POST", "/api/v1/mocap?filename=" + urllib.parse.quote(bad), raw=data), 400, bad)
        self.assertNotIn("junk.bvh", [f["file"] for f in self.get("/api/v1/mocap")["files"]])


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class BulkJobTrackingTest(unittest.TestCase):
    def test_job_bulk_tracking_with_failure(self):
        cmds = [
            ("audit 1/2: m1", [sys.executable, "-c", "print('AUDIT_PASS m1')"], None, "m1"),
            ("audit 2/2: m2", [sys.executable, "-c", "import sys; print('AUDIT_FAIL m2'); sys.exit(1)"], None, "m2"),
        ]
        job = Job("(all)", "audit-all", "audit-all", cmds)
        runner = Runner(dict(os.environ))
        runner.submit(job)

        t0 = time.time()
        while time.time() - t0 < 10:
            if job.state not in ("queued", "running"):
                break
            time.sleep(0.05)

        info = job.info()
        self.assertEqual(info["state"], "failed")
        self.assertEqual(info["total"], 2)
        self.assertEqual(info["current"], 2)
        self.assertEqual(info["passed"], 1)
        self.assertEqual(info["failed"], 1)
        self.assertEqual(info["completed"], 2)
        self.assertIsNotNone(info["prev_result"])
        self.assertEqual(info["prev_result"]["model"], "m1")
        self.assertEqual(info["prev_result"]["status"], "PASSED")
        self.assertIsNotNone(info["last_result"])
        self.assertEqual(info["last_result"]["model"], "m2")
        self.assertEqual(info["last_result"]["status"], "FAILED")

        text = "\n".join(job.lines)
        self.assertIn(":: SUBJOB_PROGRESS 1 2 m1", text)
        self.assertIn(">> [Job 1 of 2] Starting: m1", text)
        self.assertIn(":: SUBJOB_RESULT m1 PASSED", text)
        self.assertIn(">> [Job 2 of 2] Starting: m2", text)
        self.assertIn("(Previous: m1 PASSED)", text)
        self.assertIn(":: SUBJOB_PREV m1 PASSED", text)
        self.assertIn(":: SUBJOB_RESULT m2 FAILED", text)
        self.assertIn(">> [Job 2 of 2] m2 FAILED", text)

    def test_job_bulk_tracking_all_pass(self):
        cmds = [
            ("audit 1/2: m1", [sys.executable, "-c", "print('AUDIT_PASS m1')"], None, "m1"),
            ("audit 2/2: m2", [sys.executable, "-c", "print('AUDIT_PASS m2')"], None, "m2"),
        ]
        job = Job("(all)", "audit-all", "audit-all", cmds)
        runner = Runner(dict(os.environ))
        runner.submit(job)

        t0 = time.time()
        while time.time() - t0 < 10:
            if job.state not in ("queued", "running"):
                break
            time.sleep(0.05)

        info = job.info()
        self.assertEqual(info["state"], "done")
        self.assertEqual(info["total"], 2)
        self.assertEqual(info["current"], 2)
        self.assertEqual(info["passed"], 2)
        self.assertEqual(info["failed"], 0)
        self.assertEqual(info["completed"], 2)
        self.assertIsNotNone(info["last_result"])
        self.assertEqual(info["last_result"]["model"], "m2")
        self.assertEqual(info["last_result"]["status"], "PASSED")

        text = "\n".join(job.lines)
        self.assertIn("BULK RUN FINISHED: 2 of 2 completed (2 passed, 0 failed)", text)


if __name__ == "__main__":
    unittest.main()

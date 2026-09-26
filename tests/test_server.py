# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a headless run of the GUI server's API against one generated model.
#
#   python -m unittest discover -s tests -v
#
# Starts the server (no browser) on a temporary models folder, then talks to it as the page does: the token is
# required and files outside the served folders are refused; a model is uploaded (an OBJ written here, with its
# rig.json) and every step runs through the API; a running step is cancelled and its Blender process is gone.
# The Blender parts are skipped when Blender cannot be found.
import json, math, os, secrets, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse
import urllib.request

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

    def call(self, path, body=None, raw=None, token=TOKEN):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if token: headers["X-Autorig-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)

    def status(self, path, **kw):
        try:
            self.call(path, **kw)
            return 200
        except urllib.error.HTTPError as e:
            return e.code

    def wait(self, job, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.call("/api/jobs/%d" % job["id"])
            if j["state"] not in ("queued", "running"): return j
            time.sleep(0.5)
        self.fail("job %s did not finish" % job)

    # ---- no Blender needed

    def test_1_token_and_confinement(self):
        self.assertEqual(self.status("/api/state", token=None), 403)
        self.assertEqual(self.status("/api/state", token="wrong"), 403)
        st = self.call("/api/state")
        self.assertEqual(st["models"], [])
        self.assertIn("blender_version", st)
        self.assertIn(self.status("/files/models/../../etc/passwd"), (400, 403, 404))
        self.assertIn(self.status("/samples/../../etc/passwd"), (400, 403, 404))
        self.assertEqual(self.status("/api/run", body={"model": "x", "step": "rm -rf"}), 400)
        self.assertEqual(self.status("/api/upload?batch=abcdefgh12&path=../evil.py", raw=b"x"), 400)

        # /help.html requires token and serves HTML guide
        self.assertEqual(self.status("/help.html", token=None), 403)
        req = urllib.request.Request(self.base + "/help.html?t=" + TOKEN)
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("Autorig Workbench", r.read().decode("utf-8"))

    # ---- the pipeline, through the API

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_2_upload_and_run_all(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        column_obj(os.path.join(src, "column.obj"))
        with open(os.path.join(src, "rig.json"), "w") as fh: json.dump(SPEC, fh)
        batch = secrets.token_hex(8)
        for f in ("column.obj", "rig.json"):
            with open(os.path.join(src, f), "rb") as fh: data = fh.read()
            r = self.call("/api/upload?batch=%s&path=column/%s" % (batch, f), raw=data)
            self.assertTrue(r["stored"])
        added = self.call("/api/upload/done", {"batch": batch, "group": "Props", "name": "column"})
        self.assertEqual(sorted(added["files"]), ["column.obj", "rig.json"])

        m = next(x for x in self.call("/api/state")["models"] if x["name"] == "column")
        self.assertEqual((m["group"], m["kind"], m["source"]), ("Props", "build", "column.obj"))
        self.assertIsNone(m["steps"]["all"])
        self.assertIn("run Rig", m["steps"]["audit"])            # greyed out, and says why

        survey = self.wait(self.call("/api/run", {"model": "column", "step": "survey"}))
        self.assertEqual(survey["state"], "done", "\n".join(survey["log"][-30:]))
        job = self.wait(self.call("/api/run", {"model": "column", "step": "all"}))
        self.assertEqual(job["state"], "done", "\n".join(job["log"][-40:]))

        d = self.call("/api/model?name=column")
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
        rows = self.call("/api/audits")["models"]
        self.assertEqual([(r["model"], r["grade"]) for r in rows], [("column", a["grade"])])
        req = urllib.request.Request(self.base + d["bend_test"] + "?t=" + TOKEN)
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_3_cancel_kills_only_its_process(self):
        job = self.call("/api/run", {"model": "column", "step": "rig"})
        pid = None
        for _ in range(200):
            j = self.call("/api/jobs/%d" % job["id"])
            if j["pid"] or j["state"] not in ("queued", "running"): pid = j["pid"]; break
            time.sleep(0.02)
        self.call("/api/cancel", {"job": job["id"]})
        j = self.wait(job)
        if j["state"] == "done":
            self.skipTest("the step finished before it could be cancelled")
        self.assertEqual(j["state"], "cancelled")
        self.assertTrue(any("killing PID %d" % pid in l for l in j["log"]))
        time.sleep(0.5)
        self.assertFalse(alive(pid), "PID %d still running after Cancel" % pid)

    def test_4_samples_gallery_api(self):
        data = self.call("/api/samples")
        self.assertIn("samples", data)
        samples = data["samples"]
        self.assertEqual(len(samples), 5)
        names = [s["name"] for s in samples]
        self.assertEqual(sorted(names), ["beetle", "biped", "canine", "pedestal", "wyvern"])
        for s in samples:
            self.assertTrue(s["title"])
            self.assertTrue(s["archetype"])
            self.assertIn(s["kind"], ("placed", "build"))
            self.assertGreater(s["budget"], 0)
            self.assertTrue(s["description"])
            self.assertIn("installed", s)
            self.assertIn("thumbnail", s)
            if s["thumbnail"]:
                req = urllib.request.Request(self.base + s["thumbnail"] + "?t=" + TOKEN)
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(r.headers["Content-Type"], "image/png")

    def test_5_load_sample_model(self):
        # invalid sample name fails with 400
        self.assertEqual(self.status("/api/samples/load", body={"name": "nonexistent"}), 400)

        # load pedestal
        res = self.call("/api/samples/load", body={"name": "pedestal"})
        self.assertTrue(res.get("loaded"))
        self.assertEqual(res.get("name"), "pedestal")
        self.assertFalse(res.get("already_installed"))

        # verify files exist in models folder
        pedestal_dir = os.path.join(self.models, "pedestal")
        self.assertTrue(os.path.isdir(pedestal_dir))
        self.assertTrue(os.path.isfile(os.path.join(pedestal_dir, "rig.json")))

        # verify model appears in state
        st = self.call("/api/state")
        names = [m["name"] for m in st["models"]]
        self.assertIn("pedestal", names)
        pm = next(m for m in st["models"] if m["name"] == "pedestal")
        self.assertTrue(pm["spec"])

        # loading again reports already_installed: True
        res2 = self.call("/api/samples/load", body={"name": "pedestal"})
        self.assertTrue(res2.get("loaded"))
        self.assertTrue(res2.get("already_installed"))

    def test_6_export_api(self):
        targets_res = self.call("/api/export/targets")
        self.assertIn("targets", targets_res)
        target_ids = {t["id"] for t in targets_res["targets"]}
        self.assertEqual(target_ids, {"unreal", "unity", "godot", "web"})

        # Export unreal package for pedestal
        res = self.call("/api/export", body={"model": "pedestal", "target": "unreal"})
        self.assertEqual(res.get("model"), "pedestal")
        self.assertEqual(res.get("target"), "unreal")
        self.assertIn("zip_rel", res)
        self.assertGreater(res.get("zip_size", 0), 0)

        # Download the zip archive through /files/work/...
        req = urllib.request.Request(self.base + "/files/work/" + res["zip_rel"] + "?t=" + TOKEN)
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get("Content-Type"), "application/zip")
            zip_bytes = r.read()
            self.assertEqual(len(zip_bytes), res["zip_size"])

    def test_7_doctor_api(self):
        diag = self.call("/api/doctor?model=pedestal")
        self.assertIn("health_score", diag)
        self.assertIn("grade", diag)
        self.assertIn("verts", diag)
        self.assertIn("faces", diag)
        self.assertIn(diag["grade"], ("HEALTHY", "WARN", "CRITICAL"))

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8_rig_all_api(self):
        job = self.call("/api/rig-all", {})
        self.assertEqual(job["step"], "rig-all")
        self.assertEqual(job["model"], "(all rig-ready models)")
        self.assertIn("total", job)
        self.assertIn("passed", job)
        self.assertIn("failed", job)
        self.assertIn("last_result", job)
        self.call("/api/cancel", {"job": job["id"]})
        self.wait(job)

    def test_9_abrupt_client_disconnect(self):
        import socket, struct
        host, port = "127.0.0.1", int(self.base.split(":")[-1])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        req = f"GET /api/state?t={TOKEN} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode("utf-8"))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()

        time.sleep(0.1)
        st = self.call("/api/state")
        self.assertIn("models", st)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8b_source_all_api(self):
        job = self.call("/api/source-all", {"missing_only": False})
        self.assertEqual(job["step"], "source-all")
        self.assertEqual(job["model"], "(all source views)")
        self.call("/api/cancel", {"job": job["id"]})
        self.wait(job)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_8c_clips_all_api(self):
        job = self.call("/api/clips-all", {})
        self.assertEqual(job["step"], "rebake-all-clips")
        self.assertEqual(job["model"], "(all rigged models)")
        self.call("/api/cancel", {"job": job["id"]})
        self.wait(job)

    def test_8d_rebake_clips_endpoint_and_commands(self):
        import autorig.gui.server as server
        import layout
        import blender
        server.layout = layout
        server.blender = blender
        mock_spec = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "clips": {"archetype": "walker"}}}
        if HAVE_BLENDER:
            cmds = server.commands(".", "test_creature", "rebake-clips", mock_spec)
            self.assertEqual(len(cmds), 3)
            self.assertTrue(cmds[0][0].startswith("make clips"))
            self.assertEqual(cmds[1][0], "clip audit")                 # every clip played and graded
            self.assertEqual(cmds[2][0], "preview for the viewer")
        else:                                          # a clean reason, not a dropped connection
            with self.assertRaises(ValueError):
                server.commands(".", "test_creature", "rebake-clips", mock_spec)

        # ensure pedestal has no clips section: /api/run with rebake-clips must fail with 409
        pedestal_rig = os.path.join(self.models, "pedestal", "rig.json")
        if os.path.exists(pedestal_rig):
            with open(pedestal_rig) as f:
                p_spec = json.load(f)
            p_spec.pop("clips", None)
            with open(pedestal_rig, "w") as f:
                json.dump(p_spec, f)

        st = self.call("/api/state")
        pedestal = next((m for m in st["models"] if m["name"] == "pedestal"), None)
        self.assertIsNotNone(pedestal)
        self.assertIsNotNone(pedestal["steps"].get("rebake-clips"))
        self.assertEqual(self.status("/api/run", body={"model": "pedestal", "step": "rebake-clips"}), 409)

    def test_8e_retarget_endpoints(self):
        # 1. upload a sample mocap BVH via /api/retarget/upload
        bvh_data = b"""HIERARCHY
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
        req = urllib.request.Request(self.base + "/api/retarget/upload?filename=test_motion.bvh&t=" + TOKEN,
                                     data=bvh_data, method="POST", headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req) as r:
            up_res = json.load(r)
        self.assertTrue(up_res.get("stored"))
        self.assertTrue(os.path.isfile(up_res["file"]))
        self.assertEqual(up_res["meta"]["format"], "BVH")
        self.assertEqual(up_res["meta"]["frames"], 2)

        # 2. inspect endpoint
        insp = self.call("/api/retarget/inspect", body={"file": up_res["file"]})
        self.assertEqual(insp.get("format"), "BVH")
        self.assertEqual(insp.get("frames"), 2)

        # 3. a mocap path outside the models root and the work folder is refused, as /files/ refuses it
        outside = os.path.join(tempfile.mkdtemp(prefix="autorig-outside-"), "elsewhere.bvh")
        with open(outside, "wb") as fh:
            fh.write(bvh_data)
        self.assertEqual(self.status("/api/retarget/inspect", body={"file": outside}), 400)
        self.assertEqual(self.status("/api/retarget", body={"model": "pedestal", "file": outside}), 400)
        self.assertEqual(self.status("/api/retarget/plan?model=pedestal&file=" + urllib.parse.quote(outside)), 400)

        # 4. an upload named to climb out of the mocap folder, or not a mocap file, is refused
        for bad in ("..", "notes.txt", ".bvh"):
            req = urllib.request.Request(self.base + "/api/retarget/upload?filename=%s&t=%s" % (urllib.parse.quote(bad), TOKEN),
                                         data=bvh_data, method="POST", headers={"Content-Type": "application/octet-stream"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)



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

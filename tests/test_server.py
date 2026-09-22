# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a headless run of the GUI server's API against one generated model.
#
#   python -m unittest discover -s tests -v
#
# Starts the server (no browser) on a temporary models folder, then talks to it as the page does: the token is
# required and files outside the served folders are refused; a model is uploaded (an OBJ written here, with its
# rig.json) and every step runs through the API; a running step is cancelled and its Blender process is gone.
# The Blender parts are skipped when Blender cannot be found.
import json, math, os, secrets, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender  # noqa: E402

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
        self.assertEqual(self.call("/api/state")["models"], [])
        self.assertIn(self.status("/files/models/../../etc/passwd"), (400, 403, 404))
        self.assertEqual(self.status("/api/run", body={"model": "x", "step": "rm -rf"}), 400)
        self.assertEqual(self.status("/api/upload?batch=abcdefgh12&path=../evil.py", raw=b"x"), 400)

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


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: AI generation (core/generators, gui/generate_api.py) against a stub of each provider's HTTP API.
#
#   python -m unittest tests.test_generate -v
#
# No test can spend: every provider's API root is pointed at a stub server on 127.0.0.1 (AUTORIG_TRIPO_URL,
# AUTORIG_MESHY_URL, AUTORIG_RODIN_URL), and the class refuses to start unless all three are. The stub records every
# request, so the tests check that an estimate creates no task, that a refused confirm creates none, that one
# confirm creates exactly one, and that keys go only to the API and never to the download links.
import base64, json, os, shutil, struct, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import tripo_glb  # noqa: E402
import generators  # noqa: E402
from generators import common, meshy, rodin, tripo  # noqa: E402

TOKEN = "gen-test-token"
KEYS = {"tripo": "tsk_TRIPO_SECRET_123", "meshy": "msy_MESHY_SECRET_456", "rodin": "rdn_RODIN_SECRET_789"}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()


def triangle_glb():
    """The smallest model the workbench takes: one triangle."""
    binary = bytearray(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    j = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "tri"}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
         "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                        "min": [0, 0, 0], "max": [1, 1, 0]}],
         "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}], "buffers": [{"byteLength": 36}]}
    fd, p = tempfile.mkstemp(suffix=".glb"); os.close(fd)
    tripo_glb.write_glb(j, binary, p)
    with open(p, "rb") as fh: data = fh.read()
    os.remove(p)
    return data


class Stub:
    """Tripo v3, Meshy and Rodin Gen-2.5, as far as the adapters use them. Tasks finish on their second poll."""

    def __init__(self):
        self.calls = []                     # (method, path, authorization header, body bytes)
        self.polls = {}
        self.glb = triangle_glb()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _reply(self, code, obj=None, raw=None, ctype="application/json"):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                stub.calls.append((method, self.path, self.headers.get("Authorization"), body))
                stub.route(self, method, self.path, body)

            def do_GET(self): self._do("GET")
            def do_POST(self): self._do("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def creates(self):
        """The requests that would spend money on the real services."""
        spend = ("/tripo/v3/generation/", "/meshy/openapi/v2/text-to-3d", "/meshy/openapi/v1/image-to-3d",
                 "/meshy/openapi/v1/multi-image-to-3d", "/rodin/api/v2/rodin")
        return [c for c in self.calls if c[0] == "POST" and c[1].startswith(spend)]

    def _done(self, tid):
        self.polls[tid] = self.polls.get(tid, 0) + 1
        return self.polls[tid] >= 2

    def route(self, h, method, path, body):
        auth = h.headers.get("Authorization") or ""
        if path.startswith("/cdn/"):
            return h._reply(200, raw=self.glb, ctype="model/gltf-binary")
        provider = path.split("/")[1]
        if auth != "Bearer " + KEYS.get(provider, "?"):
            return h._reply(401, {"code": 1002, "message": "bad key"})
        p = path[len(provider) + 1:]
        if provider == "tripo":
            if p == "/v3/account/balance": return h._reply(200, {"code": 0, "data": {"balance": 500.0, "frozen": 0}})
            if p == "/v3/files": return h._reply(200, {"code": 0, "data": {"file_token": "file_abc"}})
            if p.startswith("/v3/generation/"): return h._reply(200, {"code": 0, "data": {"task_id": "task_t%d" % len(self.calls)}})
            if p.startswith("/v3/tasks/"):
                tid = p.rsplit("/", 1)[1]
                if tid == "task_missing": return h._reply(404, {"code": 2001, "message": "task not found"})
                if tid.startswith("task_fail"):
                    return h._reply(200, {"code": 0, "data": {"task_id": tid, "status": "failed", "progress": 10}})
                if tid == "task_old" or self._done(tid):
                    return h._reply(200, {"code": 0, "data": {"task_id": tid, "status": "success", "progress": 100,
                                                              "credits_consumed": 20.0,
                                                              "output": {"model_url": self.url + "/cdn/%s.glb" % tid}}})
                return h._reply(200, {"code": 0, "data": {"task_id": tid, "status": "running", "progress": 40}})
        if provider == "meshy":
            if p == "/openapi/v1/balance": return h._reply(200, {"balance": 1000})
            if method == "POST": return h._reply(202, {"result": "m%d" % len(self.calls)})
            tid = p.rsplit("/", 1)[1]
            if p.startswith("/openapi/v2/text-to-3d/") and tid.startswith("img"):
                return h._reply(404, {"message": "Task not found"})
            if tid == "img_old" or self._done(tid):
                return h._reply(200, {"id": tid, "status": "SUCCEEDED", "progress": 100, "consumed_credits": 10,
                                      "model_urls": {"glb": self.url + "/cdn/%s.glb" % tid}})
            return h._reply(200, {"id": tid, "status": "IN_PROGRESS", "progress": 30})
        if provider == "rodin":
            if p == "/api/v2/rodin":
                return h._reply(201, {"uuid": "r-uuid-1", "jobs": {"uuids": ["j1"], "subscription_key": "sub-1"}, "consumed": 0.5})
            if p == "/api/v2/status":
                st = "Done" if self._done("sub") else "Generating"
                return h._reply(201, {"jobs": [{"uuid": "j1", "status": st}]})
            if p == "/api/v2/download":
                return h._reply(201, {"list": [{"name": "preview.webp", "url": self.url + "/cdn/p.webp"},
                                               {"name": "base_basic_pbr.glb", "url": self.url + "/cdn/r.glb"}]})
        return h._reply(404, {"message": "stub: no route " + path})


class EstimateTest(unittest.TestCase):
    """The prices, offline (no network at all)."""

    def test_tripo(self):
        self.assertEqual(tripo.estimate("a cow", [], {})["credits"], 20)
        self.assertEqual(tripo.estimate("a cow", [], {"texture": False, "pbr": False})["credits"], 10)
        self.assertEqual(tripo.estimate("a cow", [], {"texture_quality": "detailed", "geometry_quality": "detailed",
                                                      "quad": True})["credits"], 10 + 20 + 20 + 5)
        two = common.images(["data:image/png;base64," + PNG, "https://example.com/side.png"])
        e = tripo.estimate(None, two, {})
        self.assertEqual((e["kind"], e["credits"]), ("multiview", 30))
        self.assertEqual([im.view for im in two], ["front", "left"])
        with self.assertRaises(ValueError): tripo.estimate("a cow", [], {"model": "P2-20260801"})
        with self.assertRaises(ValueError): tripo.estimate("a cow", [], {"surprise": 1})
        with self.assertRaises(ValueError): tripo.estimate("", [], {})
        with self.assertRaises(ValueError): tripo.estimate(None, common.images([{"left": "https://e.com/l.png"}, {"back": "https://e.com/b.png"}]), {})

    def test_meshy(self):
        self.assertEqual(meshy.estimate("a cow", [], {})["credits"], 30)
        self.assertEqual(meshy.estimate("a cow", [], {"texture": False, "ai_model": "meshy-6-lite"})["credits"], 5)
        self.assertEqual(meshy.estimate("a cow", [], {"texture_resolution": "8k", "geometry_resolution": "4k"})["credits"], 20 + 15 + 5)
        one = common.images(["https://e.com/a.png"])
        self.assertEqual(meshy.estimate(None, one, {})["credits"], 30)
        self.assertEqual(meshy.estimate(None, one, {"texture": False})["credits"], 20)
        with self.assertRaises(ValueError): meshy.estimate(None, one, {"ai_model": "meshy-6-lite", "texture_resolution": "4k"})

    def test_rodin(self):
        self.assertEqual(rodin.estimate("a cow", [], {})["credits"], 0.5)
        self.assertEqual(rodin.estimate("a cow", [], {"tier": "Gen-2.5-Extreme-High", "texture_mode": "extreme-high"})["credits"], 3.0)
        with self.assertRaises(ValueError): rodin.estimate("a cow", [], {"tier": "Gen-9"})

    def test_images(self):
        self.assertEqual(common.images(None), [])
        with self.assertRaises(ValueError): common.images(["http://insecure.example/a.png"])
        with self.assertRaises(ValueError): common.images(["file:///etc/passwd"])
        with self.assertRaises(ValueError): common.images(["data:text/html;base64,AAAA"])
        with self.assertRaises(ValueError): common.images([{"top": "https://e.com/a.png"}])

    def test_download_needs_https(self):
        with self.assertRaises(ValueError):
            common.download("http://cdn.example.com/a.glb", os.path.join(tempfile.gettempdir(), "x.glb"), "https://api.example.com")

    def test_caps(self):
        d = tempfile.mkdtemp()
        try:
            led = generators.Ledger(os.path.join(d, "u.jsonl"))
            s = dict(generators.DEFAULTS, tripo_daily=40, tripo_monthly=200)
            self.assertTrue(generators.check_cap(s, led, "tripo", 40)[0])
            led.add("tripo", 30, "held")
            ok, why = generators.check_cap(s, led, "tripo", 20)
            self.assertFalse(ok); self.assertIn("daily cap", why)
            led.add("tripo", -30, "released")
            self.assertTrue(generators.check_cap(s, led, "tripo", 20)[0])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class GenerateApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = Stub()
        cls.tmp = tempfile.mkdtemp(prefix="autorig-gen-")
        cls.models = os.path.join(cls.tmp, "models")
        cls.settings = os.path.join(cls.tmp, "generate.json")
        cls.usage = os.path.join(cls.tmp, "usage.jsonl")
        cls.set(enabled=True)
        url_file = os.path.join(cls.tmp, "url.txt")
        env = {k: v for k, v in os.environ.items() if k not in (
            "TRIPO_API_KEY", "MESHY_API_KEY", "HYPER3D_API_KEY", "AUTORIG_ALLOW_ANY_FOLDER")}
        env.update(AUTORIG_URL_FILE=url_file, AUTORIG_GENERATE_FILE=cls.settings, AUTORIG_GENERATE_USAGE_FILE=cls.usage,
                   AUTORIG_TRIPO_URL=cls.stub.url + "/tripo", AUTORIG_MESHY_URL=cls.stub.url + "/meshy",
                   AUTORIG_RODIN_URL=cls.stub.url + "/rodin",
                   AUTORIG_AI_FILE=os.path.join(cls.tmp, "ai.json"), AUTORIG_ROOTS_FILE=os.path.join(cls.tmp, "roots.json"),
                   AUTORIG_CONFIG=os.path.join(cls.tmp, "server.json"))
        for k in ("AUTORIG_TRIPO_URL", "AUTORIG_MESHY_URL", "AUTORIG_RODIN_URL"):
            assert env[k].startswith("http://127.0.0.1:"), "every provider must point at the stub"
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w")
        cls.proc = subprocess.Popen([sys.executable, "-m", "autorig", "--models", cls.models, "--port", "0",
                                     "--no-browser", "--token", TOKEN], cwd=REPO, env=env, stdout=cls.log, stderr=cls.log)
        url = ""
        for _ in range(150):
            if os.path.exists(url_file):
                with open(url_file) as fh: url = fh.read()
                if url: break
            time.sleep(0.1)
        cls.base = url.split("/?")[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill(); cls.proc.wait(); cls.log.close()
        cls.stub.server.shutdown()
        with open(os.path.join(cls.tmp, "server.log"), encoding="utf-8", errors="replace") as fh: log = fh.read()
        with open(cls.usage, encoding="utf-8") as fh: usage = fh.read() if os.path.exists(cls.usage) else ""
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k in KEYS.values():
            assert k not in log and k not in usage, "a key reached the log"

    @classmethod
    def set(cls, **changes):
        s = dict(enabled=False, poll_seconds=0.1, timeout_minutes=1, tripo_key=KEYS["tripo"], meshy_key=KEYS["meshy"],
                 rodin_key=KEYS["rodin"], tripo_daily=100, tripo_monthly=500, meshy_daily=100, meshy_monthly=500,
                 rodin_daily=5, rodin_monthly=20)
        s.update(changes)
        with open(cls.settings, "w") as fh: json.dump(s, fh)

    def setUp(self):
        self.set(enabled=True)

    def req(self, method, path, body=None):
        h = {"Authorization": "Bearer " + TOKEN}
        data = None
        if body is not None:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                out = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            out = e.code, json.loads(e.read())
        for k in KEYS.values():
            self.assertNotIn(k, json.dumps(out[1]))
        return out

    def wait(self, jid, until=("done", "failed", "timed_out")):
        for _ in range(200):
            code, j = self.req("GET", "/api/v1/generate/" + jid)
            self.assertEqual(code, 200)
            if j["state"] in until and (j["state"] != "done" or not j["pipeline"] or j["rig_job"] or j["error"]):
                return j
            time.sleep(0.1)
        self.fail("generation %s never finished: %s" % (jid, j))

    def test_estimate_spends_nothing(self):
        before = len(self.stub.creates())
        code, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a brown cow standing"})
        self.assertEqual(code, 200)
        self.assertEqual((e["credits"], e["unit"], e["can_spend"], e["balance"]), (20, "credits", True, 500.0))
        self.assertTrue(e["estimate"].startswith("est_"))
        self.assertEqual(len(self.stub.creates()), before)

    def test_confirm_generates_once(self):
        code, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a red barn", "name": "Barn"})
        before = len(self.stub.creates())
        code, out = self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})
        self.assertEqual(code, 202, out)
        self.assertEqual(len(self.stub.creates()), before + 1)
        j = self.wait(out["job"]["id"])
        self.assertEqual(j["state"], "done", j)
        self.assertEqual((j["model"], j["credits_charged"]), ("barn", 20.0))
        code, m = self.req("GET", "/api/v1/models/barn")
        self.assertEqual(code, 200)
        self.assertTrue(any(f["path"].endswith(".glb") for f in m["files"]), m["files"])
        # works once
        code, again = self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})
        self.assertEqual(code, 404)
        self.assertEqual(len(self.stub.creates()), before + 1)
        # the download link got no key; the API got it on every call
        cdn = [c for c in self.stub.calls if c[1].startswith("/cdn/")]
        self.assertTrue(cdn)
        self.assertTrue(all(c[2] is None for c in cdn))

    def test_refusals_spend_nothing(self):
        before = len(self.stub.creates())
        self.assertEqual(self.req("POST", "/api/v1/generate", {"confirm": "est_made_up"})[0], 404)
        _, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a goat"})
        self.assertEqual(self.req("POST", "/api/v1/generate", {"confirm": e["estimate"], "prompt": "a dragon"})[0], 409)
        self.set(enabled=False)
        _, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a goat"})
        self.assertFalse(e["can_spend"]); self.assertIn("off", e["reason"])
        self.assertEqual(self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})[0], 403)
        self.set(enabled=True, tripo_daily=5)
        _, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a goat"})
        self.assertFalse(e["can_spend"]); self.assertIn("daily cap", e["reason"])
        self.assertEqual(self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})[0], 402)
        self.set(enabled=True, tripo_key="")
        _, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a goat"})
        self.assertEqual(self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})[0], 412)
        self.assertEqual(self.req("POST", "/api/v1/generate", {"provider": "nobody", "prompt": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "x", "options": {"bogus": 1}})[0], 400)
        self.assertEqual(len(self.stub.creates()), before)

    def test_meshy_text_then_texture(self):
        _, e = self.req("POST", "/api/v1/generate", {"provider": "meshy", "prompt": "a grey wolf", "name": "Wolf"})
        self.assertEqual(e["credits"], 30)
        before = len(self.stub.creates())
        code, out = self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})
        j = self.wait(out["job"]["id"])
        self.assertEqual(j["state"], "done", j)
        made = self.stub.creates()[before:]
        modes = [json.loads(c[3]).get("mode") for c in made]
        self.assertEqual(modes, ["preview", "refine"])
        self.assertEqual(j["credits_charged"], 20.0)        # the stub charges 10 a task

    def test_rodin_from_picture(self):
        _, e = self.req("POST", "/api/v1/generate", {"provider": "rodin", "images": ["data:image/png;base64," + PNG],
                                                     "name": "Statue"})
        self.assertEqual(e["credits"], 0.5)
        code, out = self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})
        self.assertEqual(code, 202, out)
        j = self.wait(out["job"]["id"])
        self.assertEqual((j["state"], j["model"]), ("done", "statue"), j)
        sent = [c for c in self.stub.creates() if c[1] == "/rodin/api/v2/rodin"][-1][3]
        self.assertIn(b'name="images"', sent)
        self.assertIn(b"geometry_file_format", sent)

    def test_failed_task_is_released(self):
        # a failed task is refunded by the provider, so its credits leave the ledger too
        led = generators.Ledger(self.usage)
        before = led.spent("tripo", 0)
        _, e = self.req("POST", "/api/v1/generate", {"provider": "tripo", "prompt": "a cat"})
        orig = self.stub.route

        def failing(h, method, path, body):
            if path.startswith("/tripo/v3/generation/"):            # the stub fails tasks named task_fail*
                self.stub.calls.append(("POST", path, h.headers.get("Authorization"), body))
                return h._reply(200, {"code": 0, "data": {"task_id": "task_fail1"}})
            return orig(h, method, path, body)
        self.stub.route = failing
        try:
            code, out = self.req("POST", "/api/v1/generate", {"confirm": e["estimate"]})
            j = self.wait(out["job"]["id"])
        finally:
            self.stub.route = orig
        self.assertEqual(j["state"], "failed")
        self.assertIn("failed", j["error"])
        self.assertAlmostEqual(led.spent("tripo", 0), before)

    def test_import_by_task_id_is_free(self):
        before = len(self.stub.creates())
        code, out = self.req("POST", "/api/v1/models", {"provider": "tripo", "task_id": "task_old", "name": "OldCow"})
        self.assertEqual(code, 201, out)
        self.assertEqual(out["model"]["name"], "oldcow")
        code, out = self.req("POST", "/api/v1/models", {"provider": "meshy", "task_id": "img_old", "name": "OldPig"})
        self.assertEqual(code, 201, out)
        code, out = self.req("POST", "/api/v1/models", {"provider": "rodin", "task_id": "r-uuid-1", "name": "OldStatue"})
        self.assertEqual(code, 201, out)
        self.assertEqual(self.req("POST", "/api/v1/models", {"provider": "tripo", "task_id": "task_missing"})[0], 404)
        self.assertEqual(len(self.stub.creates()), before)

    def test_pipeline(self):
        before = len(self.stub.creates())
        code, e = self.req("POST", "/api/v1/pipeline", {"provider": "tripo", "prompt": "a horse", "name": "Pony",
                                                        "template": "auto", "clips": ["Idle", "Walk"]})
        self.assertEqual(code, 200, e)
        self.assertIn("/api/v1/pipeline", e["to_spend"])
        self.assertEqual(len(self.stub.creates()), before)
        self.assertEqual(self.req("POST", "/api/v1/pipeline", {"provider": "tripo", "prompt": "x", "template": "nope"})[0], 400)
        code, out = self.req("POST", "/api/v1/pipeline", {"confirm": e["estimate"]})
        self.assertEqual(code, 202, out)
        j = self.wait(out["job"]["id"])
        self.assertEqual((j["state"], j["model"]), ("done", "pony"), j)
        if os.environ.get("AUTORIG_NO_BLENDER") == "1":       # no Blender: the model lands, the rig says why not
            self.assertIn("Blender not found", j["error"] or "", j)
        else:
            self.assertTrue(j["rig_job"], j)                    # the rig is queued (it needs Blender to finish)
            code, rj = self.req("GET", "/api/v1/jobs/%s" % j["rig_job"])
            self.assertEqual((code, rj["step"], rj["model"]), (200, "m2m-rig", "pony"), rj)
        self.assertEqual(len(self.stub.creates()), before + 1)

    def test_overview(self):
        code, o = self.req("GET", "/api/v1/generate")
        self.assertEqual(code, 200)
        self.assertTrue(o["enabled"])
        self.assertEqual([p["name"] for p in o["providers"]], ["tripo", "meshy", "rodin"])
        self.assertEqual(o["providers"][0]["key"], "saved")
        code, idx = self.req("GET", "/api/v1")
        self.assertIn("/api/v1/generate", [r["path"] for r in idx["routes"]])


if __name__ == "__main__":
    unittest.main()

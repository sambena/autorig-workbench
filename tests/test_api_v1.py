# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the HTTP API v1 (gui/api_v1.py), headless, as a script or an agent would use it.
#
#   python -m unittest tests.test_api_v1 -v
#
# Starts the server with no browser on a temporary models folder, then: the token (Bearer, header, refused without),
# the OpenAPI description, the steps and tools list, Mesh2Motion's templates and clips (when the submodule is there),
# adding a model as a raw body, its files, the rig/animate/skeleton routes' refusals, tool path confinement, and jobs.
# Nothing here needs Blender: a route that would start it answers with a reason when it is missing.
import json, os, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steps_registry  # noqa: E402
from test_server import column_obj  # noqa: E402

TOKEN = "api-test-token"
HAVE_M2M = steps_registry.m2m_app() is not None


class ApiV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-api-")
        cls.models = os.path.join(cls.tmp, "models")
        url_file = os.path.join(cls.tmp, "url.txt")
        # what the server may reach: a second collection and a browsable inbox, both in the temp folder; its AI
        # settings, spend log and recent roots there too, so nothing in the repo is read or written
        cls.other = os.path.join(cls.tmp, "other")
        cls.inbox = os.path.join(cls.tmp, "inbox")
        os.makedirs(os.path.join(cls.other, "Rock"))
        os.makedirs(cls.inbox)
        column_obj(os.path.join(cls.other, "Rock", "Rock.obj"))
        column_obj(os.path.join(cls.inbox, "Post.obj"))
        with open(os.path.join(cls.inbox, "notes.txt"), "w") as fh: fh.write("not a model")
        config = os.path.join(cls.tmp, "server.json")
        with open(config, "w") as fh:
            json.dump({"collections": [{"name": "Other", "path": cls.other}], "browse": [cls.inbox]}, fh)
        env = {k: v for k, v in os.environ.items() if k not in (
            "AUTORIG_ALLOW_ANY_FOLDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")}
        env.update(AUTORIG_URL_FILE=url_file, AUTORIG_CONFIG=config,
                   AUTORIG_AI_FILE=os.path.join(cls.tmp, "ai.json"),
                   AUTORIG_AI_USAGE_FILE=os.path.join(cls.tmp, "ai_usage.jsonl"),
                   AUTORIG_ROOTS_FILE=os.path.join(cls.tmp, "roots.json"))
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

    def req(self, method, path, body=None, raw=None, headers=None, auth="bearer"):
        h = dict(headers or {})
        if auth == "bearer": h["Authorization"] = "Bearer " + TOKEN
        elif auth == "header": h["X-Autorig-Token"] = TOKEN
        data = raw
        if body is not None:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw_out = resp.read()
                ct = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw_out) if ct.startswith("application/json") else raw_out)
        except urllib.error.HTTPError as e:
            raw_out = e.read()
            try: return e.code, json.loads(raw_out)
            except ValueError: return e.code, raw_out

    def test_1_token(self):
        self.assertEqual(self.req("GET", "/api/v1", auth=None)[0], 401)
        self.assertEqual(self.req("GET", "/api/v1", headers={"Authorization": "Bearer wrong"}, auth=None)[0], 401)
        code, idx = self.req("GET", "/api/v1")
        self.assertEqual(code, 200)
        self.assertEqual(idx["version"], "1")
        self.assertEqual(self.req("GET", "/api/v1", auth="header")[0], 200)
        self.assertEqual(self.req("GET", "/api/v1?t=" + TOKEN, auth=None)[0], 200)
        bad_host = urllib.request.Request(self.base + "/api/v1", headers={"Host": "evil.example",
                                                                          "Authorization": "Bearer " + TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(bad_host, timeout=10)
        self.assertEqual(cm.exception.code, 403)

    def test_2_openapi_and_docs(self):
        code, doc = self.req("GET", "/api/v1/openapi.json", auth=None)
        self.assertEqual(code, 200)
        self.assertEqual(doc["openapi"], "3.1.0")
        for p in ("/api/v1/models", "/api/v1/models/{model}/rig", "/api/v1/models/{model}/animate",
                  "/api/v1/pipeline", "/api/v1/jobs/{id}/events", "/api/v1/tools/{tool}"):
            self.assertIn(p, doc["paths"])
        code, page = self.req("GET", "/api/docs", auth=None)
        self.assertEqual(code, 200)
        self.assertIn(b"Autorig Workbench API", page)

    def test_3_steps(self):
        code, st = self.req("GET", "/api/v1/steps")
        self.assertEqual(code, 200)
        names = [s["name"] for s in st["model_steps"]]
        for n in ("survey", "rig", "trim", "audit", "clips", "publish", "preview", "all"):
            self.assertIn(n, names)
        tools = {t["name"]: t for t in st["tools"]}
        for n in ("m2m-rig", "game-prop", "fbx-maps", "mixamo-import"):
            self.assertIn(n, tools)
        self.assertTrue(tools["game-prop"]["available"])
        self.assertIn("template", [o["name"] for o in tools["m2m-rig"]["options"]])

    @unittest.skipUnless(HAVE_M2M, "Mesh2Motion submodule not checked out")
    def test_4_templates_and_clips(self):
        code, t = self.req("GET", "/api/v1/templates")
        self.assertEqual(code, 200)
        by = {x["name"]: x for x in t["templates"]}
        self.assertEqual(set(by), set(steps_registry.M2M_TEMPLATES))
        self.assertEqual(by["human"]["clips"], 178)
        code, c = self.req("GET", "/api/v1/templates/human/clips")
        self.assertEqual(code, 200)
        rm = [x["name"] for x in c["clips"] if x["root_motion"]]
        self.assertIn("Roll_RM", rm)
        self.assertIn("Crawl RM", rm)
        self.assertNotIn("Cheer_One_arm", rm)            # Mesh2Motion's endsWith('rm') misfire
        self.assertTrue(all(x["duration"] > 0 for x in c["clips"] if x["name"] != "Rest Pose"))
        self.assertEqual(self.req("GET", "/api/v1/templates/unicorn/clips")[0], 400)

    def test_5_add_model_and_files(self):
        obj = os.path.join(self.tmp, "column.obj")
        column_obj(obj)
        with open(obj, "rb") as fh: data = fh.read()
        self.assertEqual(self.req("POST", "/api/v1/models?filename=column.exe", raw=data)[0], 400)
        code, m = self.req("POST", "/api/v1/models?filename=column.obj&name=Column&group=Props", raw=data,
                           headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(code, 201, m)
        self.assertEqual((m["model"]["name"], m["model"]["group"]), ("column", "Props"))
        code, again = self.req("POST", "/api/v1/models?filename=column.obj&name=Column", raw=data)
        self.assertEqual(code, 400)                     # names are unique
        code, ms = self.req("GET", "/api/v1/models")
        listed = {x["name"]: x for x in ms["models"]}
        self.assertIsNone(listed["column"]["thumbnail"])            # the page's library cards
        self.assertIsNone(listed["column"]["m2m_template"])
        with open(os.path.join(self.models, "Props", "column", "thumbnail.png"), "wb") as fh: fh.write(b"PNG")
        code, ms = self.req("GET", "/api/v1/models")
        thumb = {x["name"]: x for x in ms["models"]}["column"]["thumbnail"]
        self.assertEqual(thumb, "/api/v1/models/column/files/thumbnail.png")
        self.assertEqual(self.req("GET", thumb)[0], 200)
        code, d = self.req("GET", "/api/v1/models/column")
        self.assertEqual(code, 200)
        self.assertIn("column.obj", [f["path"] for f in d["files"]])
        code, raw = self.req("GET", "/api/v1/models/column/files/column.obj")
        self.assertEqual(code, 200)
        self.assertTrue(raw.startswith(b"o column"))
        self.assertIn(self.req("GET", "/api/v1/models/column/files/../../url.txt")[0], (400, 403, 404))
        self.assertEqual(self.req("GET", "/api/v1/models/nothing")[0], 404)
        # JSON adds: https only; provider task ids come with generation (M8)
        self.assertEqual(self.req("POST", "/api/v1/models", body={"url": "http://example.com/a.glb"})[0], 400)
        self.assertEqual(self.req("POST", "/api/v1/models", body={"provider": "tripo", "task_id": "x"})[0], 501)

    def test_6_rig_animate_skeleton(self):
        if self.req("GET", "/api/v1/models/column")[0] == 404:
            self.test_5_add_model_and_files()
        self.assertEqual(self.req("GET", "/api/v1/models/column/skeleton")[0], 404)
        self.assertEqual(self.req("POST", "/api/v1/models/column/animate", body={"clips": ["Idle"]})[0], 409)
        self.assertEqual(self.req("POST", "/api/v1/models/column/rig", body={"template": "unicorn"})[0], 400)
        self.assertEqual(self.req("POST", "/api/v1/models/column/rig", body={"template": "fox", "height": -1})[0], 400)
        self.assertEqual(self.req("PUT", "/api/v1/models/column/skeleton", body={"joints": {}})[0], 400)
        code, out = self.req("POST", "/api/v1/models/column/rig", body={"template": "fox", "clips": ["Idle", "Walk"]})
        # 202 with a job when steps/m2m_rig.py and Blender are here; 501 before M2 lands; 400 without Blender
        self.assertIn(code, (202, 400, 501), out)
        if code == 202:
            self.assertEqual(out["step"], "m2m-rig")
            self.req("DELETE", "/api/v1/jobs/%d" % out["id"])
        code, d = self.req("GET", "/api/v1/models/COLUMN")             # any case finds it
        self.assertEqual((code, d.get("name")), (200, "column"))
        code, out = self.req("POST", "/api/v1/pipeline", body={"model": "Column", "template": "fox", "clips": ["Idle"]})
        self.assertIn(code, (202, 400, 501), out)
        if code == 202:
            self.assertEqual((out["step"], out["model"]), ("m2m-rig", "column"))
            self.req("DELETE", "/api/v1/jobs/%d" % out["id"])
        else:
            self.assertIn("error", out)
        self.assertEqual(self.req("POST", "/api/v1/pipeline", body={"model": "nothing"})[0], 404)
        self.assertEqual(self.req("POST", "/api/v1/models/column/steps/explode", body={})[0], 404)
        code, out = self.req("POST", "/api/v1/models/column/steps/rig", body={})
        self.assertEqual(code, 409)                     # no rig.json yet: the reason, as the page shows it

    def test_7_tools(self):
        self.assertEqual(self.req("POST", "/api/v1/tools/rm-rf", body={})[0], 404)
        code, out = self.req("POST", "/api/v1/tools/game-prop", body={"options": {"name": "Barn"}})
        self.assertEqual(code, 400)                     # --in and --out are required
        code, out = self.req("POST", "/api/v1/tools/game-prop", body={"options": {"bogus": 1}})
        self.assertEqual(code, 400)
        outside = os.path.abspath(os.path.join(self.tmp, "..", "elsewhere.glb"))
        code, out = self.req("POST", "/api/v1/tools/game-prop",
                             body={"options": {"in": outside, "name": "Barn", "out": "Props/out"}})
        self.assertEqual(code, 403, out)
        code, out = self.req("POST", "/api/v1/tools/game-prop",
                             body={"options": {"in": "../../elsewhere.glb", "name": "Barn", "out": "Props/out"}})
        self.assertEqual(code, 403, out)
        code, out = self.req("POST", "/api/v1/tools/game-prop",
                             body={"options": {"in": "Props/nothing.glb", "name": "Barn", "out": "Props/out"}})
        self.assertEqual(code, 404, out)

    def test_9_collections_and_server_files(self):
        if self.req("GET", "/api/v1/models/column")[0] == 404:
            self.test_5_add_model_and_files()
        code, c = self.req("GET", "/api/v1/collections")
        self.assertEqual(code, 200)
        paths = {os.path.realpath(x["path"]): x for x in c["collections"]}
        self.assertIn(os.path.realpath(self.models), paths)
        self.assertIn(os.path.realpath(self.other), paths)
        self.assertIn(os.path.realpath(os.path.join(self.models, "Props")), paths)    # a group, as a view
        self.assertFalse(c["allow_any_folder"])
        # only listed collections open; adding folders is off
        self.assertEqual(self.req("POST", "/api/v1/collections/select", body={"path": self.inbox})[0], 403)
        self.assertEqual(self.req("POST", "/api/v1/collections", body={"path": self.tmp})[0], 403)
        code, c = self.req("POST", "/api/v1/collections/select", body={"path": self.other})
        self.assertEqual(code, 200, c)
        self.assertEqual(os.path.realpath(c["current"]), os.path.realpath(self.other))
        code, ms = self.req("GET", "/api/v1/models")
        self.assertEqual([x["name"] for x in ms["models"]], ["Rock"])
        code, c = self.req("POST", "/api/v1/collections/select", body={"path": self.models})
        self.assertEqual(code, 200, c)
        # the server's files: the roots, a folder, nothing outside them
        code, top = self.req("GET", "/api/v1/files")
        self.assertEqual(code, 200)
        roots = [os.path.realpath(e["path"]) for e in top["entries"]]
        self.assertIn(os.path.realpath(self.inbox), roots)
        code, d = self.req("GET", "/api/v1/files?path=" + urllib.parse.quote(self.inbox))
        self.assertEqual(code, 200)
        self.assertEqual([e["name"] for e in d["entries"]], ["Post.obj"])        # models only, no notes.txt
        self.assertEqual(self.req("GET", "/api/v1/files?path=" + urllib.parse.quote(self.tmp))[0], 403)
        self.assertEqual(self.req("GET", "/api/v1/files?path=" + urllib.parse.quote(
            os.path.join(self.inbox, "..")))[0], 403)
        # a file on the server becomes a model (a copy); one inside a model opens that model
        code, out = self.req("POST", "/api/v1/models", body={"path": os.path.join(self.inbox, "Post.obj")})
        self.assertEqual(code, 201, out)
        self.assertEqual((out["model"]["name"], out["existing"]), ("post", False))
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "Post.obj")))         # copied, not moved
        code, out = self.req("POST", "/api/v1/models", body={"path": os.path.join(self.models, "Props", "column", "column.obj")})
        self.assertEqual((code, out["model"]["name"], out["existing"]), (200, "column", True))
        self.assertEqual(self.req("POST", "/api/v1/models", body={"path": os.path.join(self.tmp, "url.txt")})[0], 403)
        self.assertEqual(self.req("POST", "/api/v1/models", body={"path": os.path.join(self.inbox, "notes.txt")})[0], 400)

    def test_9b_ai_routes(self):
        code, st = self.req("GET", "/api/v1/ai/status")
        self.assertEqual(code, 200)
        self.assertFalse(st["enabled"])                     # no provider, no key: off, and it says why
        self.assertTrue(st.get("problem"))
        code, s = self.req("GET", "/api/v1/ai/settings")
        self.assertEqual(code, 200)
        self.assertIn("providers", s["catalog"])
        self.assertEqual(self.req("GET", "/api/v1/ai/status", auth=None)[0], 401)
        code, u = self.req("GET", "/api/v1/ai/usage")
        self.assertEqual((code, u["recent"]), (200, []))

    def test_8_jobs_and_methods(self):
        code, j = self.req("GET", "/api/v1/jobs")
        self.assertEqual(code, 200)
        self.assertIsInstance(j["jobs"], list)
        self.assertEqual(self.req("GET", "/api/v1/jobs/999999")[0], 404)
        self.assertEqual(self.req("DELETE", "/api/v1/jobs/999999")[0], 404)
        self.assertEqual(self.req("PUT", "/api/state", body={})[0], 405)
        self.assertEqual(self.req("GET", "/api/v1/nothing/here")[0], 404)

    def test_9c_advanced_routes(self):
        """The Advanced panel's routes (gui/api_manage.py) that need no Blender: in the openapi, and their refusals."""
        paths = self.req("GET", "/api/v1/openapi.json", auth=None)[1]["paths"]
        for p in ("/api/v1/models/{model}/rename", "/api/v1/batch/{action}", "/api/v1/mocap", "/api/v1/samples",
                  "/api/v1/clean", "/api/v1/models/{model}/studio/{tool}", "/api/v1/models/{model}/spec"):
            self.assertIn(p, paths)
        code, s = self.req("GET", "/api/v1/server")
        self.assertEqual(code, 200)
        self.assertIn("step_timeouts", s)
        code, c = self.req("GET", "/api/v1/clean")
        self.assertEqual(code, 200)
        self.assertIn("source", [k["key"] for k in c["kinds"]])
        self.assertEqual(self.req("POST", "/api/v1/clean", body={"kinds": ["everything"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/v1/clean", body={})[0], 400)
        self.assertIsInstance(self.req("GET", "/api/v1/groups")[1]["groups"], list)
        self.assertEqual(self.req("GET", "/api/v1/audits")[0], 200)
        for method, path in (("POST", "/api/v1/models/nobody/rename"), ("DELETE", "/api/v1/models/nobody?confirm=nobody"),
                             ("GET", "/api/v1/models/nobody/studio"), ("GET", "/api/v1/models/nobody/spec")):
            self.assertEqual(self.req(method, path, body={} if method == "POST" else None)[0], 404, path)

    def test_9d_refused_body_closes_the_connection(self):
        """A request refused before its body is read must not leave that body on a kept-alive connection, where it
        would be read as the next request (the page's fetch() reuses connections)."""
        import http.client
        port = int(self.base.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        h = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
        conn.request("POST", "/api/v1/models/nobody/move", body=json.dumps({"group": "x" * 50}), headers=h)
        r = conn.getresponse(); r.read()
        self.assertEqual(r.status, 404)
        conn.request("GET", "/api/v1/groups", headers=h)                     # reconnects when it was closed
        r = conn.getresponse()
        self.assertEqual(r.status, 200, r.read())
        conn.close()


class RegistryTest(unittest.TestCase):
    def test_tool_args(self):
        args = steps_registry.tool_args("game-prop", {"in": "a.glb", "name": "Barn", "out": "o", "length": 18,
                                                      "carve-wheels": True, "front": "+x"})
        self.assertEqual(args, ["--in", "a.glb", "--name", "Barn", "--out", "o", "--length", "18", "--carve-wheels",
                                "--front", "+x"])
        for bad in ({"in": "a", "name": "n", "out": "o", "length": "x"},
                    {"in": "a", "name": "n", "out": "o", "front": "up"},
                    {"in": "a", "name": "n", "out": "o", "carve-wheels": "yes"},
                    {"in": "a", "name": "n", "out": "o", "nope": 1},
                    {"name": "n", "out": "o"}):
            with self.assertRaises(ValueError):
                steps_registry.tool_args("game-prop", bad)

    def test_model_commands(self):
        spec = {"rig": {"kind": "humanoid"}, "clips": {"archetype": "walker"}}
        cmds = steps_registry.model_commands("all", "wolf", "", spec, "/w", "/m/wolf")
        self.assertEqual([c.script for c in cmds], ["rerig_humanoid.py", "decimate.py", "audit.py", "publish.py",
                                                     "make_clips.py", "publish.py", "preview_glb.py"])
        self.assertEqual(cmds[3].runner, "python")
        cmds = steps_registry.model_commands("all", "rock", "", {"rig": {"kind": "placed"}}, "/w", "/m/rock")
        self.assertNotIn("make_clips.py", [c.script for c in cmds])
        custom = steps_registry.rig_script({"kind": "custom", "builder": "b.py"}, "/m/x")
        self.assertEqual(custom, os.path.join("/m/x", "b.py"))
        with self.assertRaises(ValueError):
            steps_registry.model_commands("explode", "x", "", {}, "/w", "/m/x")

    def test_tool_script(self):
        runner, script = steps_registry.tool_script("game-prop")
        self.assertEqual((runner, script), ("blender", "game_prop.py"))
        runner, script = steps_registry.tool_script("mixamo-import")
        self.assertEqual(runner, "python")
        self.assertTrue(os.path.isabs(script))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the HTTP API, version 1, for scripts, AI agents, Unity tooling and the page.
#
#   /api/v1/...     JSON in, JSON out. Token: "Authorization: Bearer <token>", "X-Autorig-Token: <token>" or ?t=.
#   /api/v1/openapi.json and /api/docs describe every route (no token needed for those two).
#
# The flow it is built for, in three calls:
#   POST /api/v1/models            a file (raw body) or {"url": ...}             -> the model, by name
#   POST /api/v1/models/{m}/rig    {"template": "auto"}                          -> a job: fit, skin, audit
#   POST /api/v1/models/{m}/animate {"clips": ["Idle", "Walk"], "format": "fbx"} -> a job: clips, export
# or the same in one: POST /api/v1/pipeline. Jobs run one at a time on the server's queue (the same Runner the page
# uses); follow one with GET /api/v1/jobs/{id} or its Server-Sent Events at /api/v1/jobs/{id}/events.
#
# Every step and tool the CLI has is reachable too (GET /api/v1/steps lists them with their options): a model step
# with POST /api/v1/models/{m}/steps/{step}, a tool with POST /api/v1/tools/{tool}. Tool paths are kept inside the
# models root and the work folder. docs/API.md has examples.
import json, os, re, secrets, shutil, sys, tempfile, time, urllib.parse, urllib.request

VERSION = "1"
MAX_DOWNLOAD = 1 << 30                                 # 1 GB: a generated model with its textures fits easily
MODEL_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".zip")
RESULT = re.compile(r"^([A-Z][A-Z0-9_]+) (\{.*\})\s*$")

# (method, path pattern, summary, body example or None): the route table, which also writes the OpenAPI document
ROUTES = [
    ("GET", "/api/v1", "this index", None),
    ("GET", "/api/v1/openapi.json", "the OpenAPI 3.1 description of this API (no token)", None),
    ("GET", "/api/v1/steps", "every model step and tool, with its options (the page's Advanced menu)", None),
    ("GET", "/api/v1/templates", "Mesh2Motion's skeleton templates and how many clips each has", None),
    ("GET", "/api/v1/templates/{template}/clips", "one template's clips: name, duration, root motion", None),
    ("GET", "/api/v1/models", "every model in the collection, with its state, Mesh2Motion template and thumbnail", None),
    ("POST", "/api/v1/models", "add a model: a raw file body (?filename=&name=&group=), or JSON {url | path, name, group}",
     {"url": "https://example.com/Cow.glb", "name": "Cow", "group": "Farm"}),
    ("GET", "/api/v1/models/{model}", "one model: state, audit, clips, files", None),
    ("GET", "/api/v1/models/{model}/files/{path}", "a file in the model's folder (results: GLB, FBX, model.json...)", None),
    ("GET", "/api/v1/models/{model}/skeleton", "the fitted Mesh2Motion skeleton: template and joint positions", None),
    ("PUT", "/api/v1/models/{model}/skeleton", "edited joints: re-fit and re-skin from them (a job)",
     {"template": "fox", "joints": {"Hips": [0, 0.1, 0.6]}}),
    ("POST", "/api/v1/models/{model}/rig", "rig on a Mesh2Motion skeleton (template auto or named; classic: the old rig)",
     {"template": "auto", "height": 0}),
    ("POST", "/api/v1/models/{model}/animate", "attach Mesh2Motion clips to the rigged model and export",
     {"clips": ["Idle", "Walk", "Run"], "format": "glb,fbx", "naming": "default", "target": "unity"}),
    ("POST", "/api/v1/models/{model}/steps/{step}", "run one model step (GET /api/v1/steps lists them)", {}),
    ("POST", "/api/v1/tools/{tool}", "run one tool with its options (GET /api/v1/steps lists them)",
     {"options": {"in": "Farm/Barn/Barn.glb", "name": "Barn", "out": "Farm/Barn/game"}}),
    ("POST", "/api/v1/pipeline", "add (optional), rig and animate in one job",
     {"url": "https://example.com/Cow.glb", "name": "Cow", "template": "auto", "clips": ["Idle", "Walk"]}),
    ("GET", "/api/v1/collections", "the collections (models roots) this server offers, and which is open", None),
    ("POST", "/api/v1/collections/select", "open another collection: {path}", {"path": "/home/me/asset-packs"}),
    ("POST", "/api/v1/collections", "add a collection (only when the server allows any folder): {path, name}",
     {"path": "/home/me/my-models", "name": "Mine"}),
    ("GET", "/api/v1/files", "list a folder on the server (?path=; none: the folders you may browse)", None),
    ("GET", "/api/v1/files/models", "every model file in the server's inbox folders, one per model (the best file "
                                    "picked: GLB, glTF, FBX, OBJ, ZIP), newest first", None),
    ("GET", "/api/v1/ai/{route}", "AI Assist: settings, status, estimate, todo, job, usage (docs/AI-ASSIST.md)", None),
    ("POST", "/api/v1/ai/{route}", "AI Assist: settings, start, apply, undo, run-steps. A start spends money: "
                                   "estimate first", {"task": "fix-rig", "model": "wolf", "message": "fix the tail"}),
    ("GET", "/api/v1/jobs", "recent jobs", None),
    ("GET", "/api/v1/jobs/{id}", "one job: state, progress, results, the last lines of its log (?log=N)", None),
    ("GET", "/api/v1/jobs/{id}/events", "the job's log as Server-Sent Events, ending with event: end", None),
    ("DELETE", "/api/v1/jobs/{id}", "cancel a job", None),
]


try:
    from . import generate_api                    # AI generation (gui/generate_api.py, docs/GENERATE.md)
except ImportError:
    import generate_api
ROUTES += generate_api.ROUTES


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def handle(h, app, srv, method, path, q):
    """Handles /api/v1/* and /api/docs. True when the path was ours (a reply has been sent)."""
    if path == "/api/docs" or path.startswith("/api/docs/"):
        h._send(200, docs_page(), "text/html; charset=utf-8",
                {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
        return True
    if not (path == "/api/v1" or path.startswith("/api/v1/")):
        return False
    if not h._host_ok():
        h._send(403, {"error": "bad host"}); return True
    if path == "/api/v1/openapi.json":
        h._send(200, openapi()); return True
    if not h._token_ok(q):
        h._send(401, {"error": "missing or wrong token: send Authorization: Bearer <token>"},
                extra={"WWW-Authenticate": "Bearer"})
        return True
    try:
        out = route(h, app, srv, method, path, q)
        if out is not None:
            code, body = out if isinstance(out, tuple) else (200, out)
            h._send(code, body)
    except ApiError as e:
        h._send(e.code, {"error": str(e)})
    except srv.Busy as e:
        h._send(409, {"error": str(e)})
    except ValueError as e:
        h._send(400, {"error": str(e)})
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    except Exception as e:                                   # noqa: a reason, not a dropped connection
        h._send(500, {"error": repr(e)})
    return True


def route(h, app, srv, method, path, q):
    parts = [urllib.parse.unquote(p) for p in path.split("/")[3:] if p]       # after /api/v1
    reg = srv.steps_registry
    if method == "GET" and not parts:
        return {"version": VERSION, "docs": "/api/docs", "openapi": "/api/v1/openapi.json",
                "routes": [{"method": m, "path": p, "summary": s} for m, p, s, _ in ROUTES]}
    head = parts[0] if parts else ""

    if head == "steps" and method == "GET" and len(parts) == 1:
        return reg.step_info()

    if head == "templates" and method == "GET":
        import m2m_catalog
        app_dir = reg.m2m_app()
        if not app_dir: raise ApiError(503, "Mesh2Motion is not checked out: git submodule update --init third_party/mesh2motion-app")
        if len(parts) == 1: return {"templates": m2m_catalog.templates(app_dir)}
        if len(parts) == 3 and parts[2] == "clips":
            return {"template": parts[1], "clips": m2m_catalog.clips(app_dir, parts[1])}

    if head == "jobs":
        return jobs(h, app, method, parts, q)

    if head == "generate":
        return generate_api.route(h, app, srv, sys.modules[__name__], method, parts)

    if head == "collections":
        if method == "GET" and len(parts) == 1:
            return collections(srv, app)
        if method == "POST" and parts[1:] == ["select"]:
            return select_collection(srv, app, h._json_body())
        if method == "POST" and len(parts) == 1:
            return add_collection(srv, app, h._json_body())

    if head == "files" and method == "GET" and len(parts) == 1:
        return list_files(srv, app, (q.get("path") or [""])[0])
    if head == "files" and method == "GET" and parts[1:] == ["models"]:
        return inbox_models(srv, app)

    if head == "ai" and len(parts) >= 2:                   # AI Assist (gui/ai_api.py), under the v1 token rules
        legacy = "/api/ai/" + "/".join(parts[1:])
        if method == "GET" and srv.ai_api.get(h, app, srv, legacy, q): return None
        if method == "POST" and srv.ai_api.post(h, app, srv, legacy, h._json_body()): return None

    if head == "models":
        if len(parts) == 1:
            if method == "GET":
                srv.spec_store.reload()
                return {"models": [listed(srv, g, n) for g, n in srv.layout.all_models()]}
            if method == "POST":
                m = add_model(h, srv, q, app)
                return (200 if m.get("existing") else 201), {"model": srv.status(m["group"], m["name"]),
                                                              "existing": bool(m.get("existing"))}
        name, g = find_model(srv, parts[1] if len(parts) > 1 else "")
        if g is None: raise ApiError(404, "no model %r" % name)
        rest = parts[2:]
        if not rest and method == "GET":
            srv.spec_store.reload()
            return dict(srv.details(g, name), name=name, group=g, files=model_files(srv, g, name))
        if rest and rest[0] == "files" and method == "GET" and len(rest) > 1:
            h.file("models", "/".join(([g] if g else []) + [name] + rest[1:]))
            return None
        if rest == ["skeleton"] and method == "GET":
            sk = skeleton(srv, name)
            if not sk: raise ApiError(404, "%s has no Mesh2Motion skeleton yet: POST .../rig first" % name)
            return sk
        if rest == ["skeleton"] and method == "PUT":
            body = h._json_body()
            joints = body.get("joints")
            if not isinstance(joints, dict) or not joints: raise ValueError("joints is {bone: [x, y, z]}")
            sk = skeleton(srv, name) or {}
            tmpl = body.get("template") or sk.get("template")
            if not tmpl: raise ValueError("template is needed: this model has no fitted skeleton yet")
            return submit(app, srv, g, name, "m2m-rig", m2m_args(srv, name, template=tmpl, joints=joints))
        if rest == ["rig"] and method == "POST":
            body = h._json_body()
            if body.get("template") == "classic":
                return run_model_step(app, srv, g, name, "all")
            return submit(app, srv, g, name, "m2m-rig", m2m_args(
                srv, name, template=body.get("template") or "auto", height=body.get("height"),
                clips=body.get("clips"), naming=body.get("naming"), target=body.get("target"),
                fmt=body.get("format")))
        if rest == ["animate"] and method == "POST":
            body = h._json_body()
            sk = skeleton(srv, name)
            if not sk: raise ApiError(409, "%s is not rigged on a Mesh2Motion skeleton yet: POST .../rig first" % name)
            clips = body.get("clips")
            if not clips: raise ValueError("clips: the clip names to attach (GET /api/v1/templates/%s/clips)" % sk["template"])
            return submit(app, srv, g, name, "m2m-rig", m2m_args(
                srv, name, template=sk["template"], joints=sk.get("joints"), clips=clips,
                naming=body.get("naming"), target=body.get("target"), fmt=body.get("format")))
        if len(rest) == 2 and rest[0] == "steps" and method == "POST":
            return run_model_step(app, srv, g, name, rest[1])

    if head == "tools" and method == "POST" and len(parts) == 2:
        body = h._json_body()
        tool = parts[1]
        if tool not in reg.TOOLS: raise ApiError(404, "no tool %r (GET /api/v1/steps)" % tool)
        values = confine_paths(srv, tool, body.get("options") or {})
        args = reg.tool_args(tool, values)
        return submit(app, srv, "", "(%s)" % tool, tool, args)

    if head == "pipeline" and method == "POST" and len(parts) == 1:
        body = h._json_body()
        if body.get("provider") or body.get("confirm"):
            return generate_api.pipeline(h, app, srv, sys.modules[__name__], body)
        if body.get("url"):
            m = add_from_url(srv, body["url"], body.get("name"), body.get("group") or "")
            name, g = m["name"], m["group"]
        else:
            name, g = find_model(srv, body.get("model") or "")
            if g is None: raise ApiError(404, "no model %r: give model, or url to add one" % name)
        code, job = submit(app, srv, g, name, "m2m-rig", m2m_args(
            srv, name, template=body.get("template") or "auto", height=body.get("height"),
            clips=body.get("clips") or "all", naming=body.get("naming"), target=body.get("target"),
            fmt=body.get("format")))
        return code, dict(job, model=name)

    raise ApiError(404, "no route %s %s (GET /api/v1 lists them)" % (method, path))


def find_model(srv, name):
    """(name as the collection spells it, group), or (name, None). Names are matched without regard to case: the
    tool lowercases the names it gives (clean_name), and a caller may well send the name it asked for."""
    g = srv.find_group(name)
    if g is not None: return name, g
    for grp, n in srv.layout.all_models():
        if n.lower() == name.lower(): return n, grp
    return name, None


# ---------------------------------------------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------------------------------------------

def job_view(job, log_lines=0):
    out = dict(job.info())
    results = {}
    for line in job.lines:
        m = RESULT.match(line)
        if m:
            try: results[m.group(1)] = json.loads(m.group(2))
            except ValueError: pass
    out["results"] = results
    if log_lines: out["log"] = job.lines[-log_lines:]
    out["links"] = {"self": "/api/v1/jobs/%d" % job.id, "events": "/api/v1/jobs/%d/events" % job.id}
    return out


def jobs(h, app, method, parts, q):
    if len(parts) == 1 and method == "GET":
        return {"jobs": [job_view(j) for j in sorted(app.runner.jobs.values(), key=lambda j: -j.id)[:50]]}
    try:
        jid = int(parts[1])
    except (IndexError, ValueError):
        raise ApiError(404, "no such job")
    job = app.runner.jobs.get(jid)
    if not job: raise ApiError(404, "no such job")
    if len(parts) == 2 and method == "GET":
        n = int((q.get("log") or ["40"])[0] or 0)
        return job_view(job, max(0, min(n, 5000)))
    if len(parts) == 2 and method == "DELETE":
        app.runner.cancel(job)
        return job_view(job)
    if len(parts) == 3 and parts[2] == "events" and method == "GET":
        h.events(jid, int((q.get("from") or ["0"])[0] or 0))
        return None
    raise ApiError(404, "no such route")


def submit(app, srv, group, model, step, args):
    reg = srv.steps_registry
    try:
        runner, script = reg.tool_script(step)
    except ValueError as e:
        raise ApiError(501, str(e))
    c = reg.Cmd("%s %s" % (step, model), runner, script, args, None)
    job = app.runner.submit(srv.Job(model, group, step, [srv.cmd_argv(c, model if not model.startswith("(") else None)]))
    return 202, job_view(job)


def run_model_step(app, srv, g, name, step):
    reg = srv.steps_registry
    if step not in reg.MODEL_STEP_NAMES: raise ApiError(404, "no step %r (GET /api/v1/steps)" % step)
    srv.spec_store.reload()
    why = srv.status(g, name)["steps"].get(step)
    if why: raise ApiError(409, why)
    job = app.runner.submit(srv.Job(name, g, step, srv.commands(g, name, step, srv.spec_store.model(name))))
    return 202, job_view(job)


# ---------------------------------------------------------------------------------------------------------------
# Mesh2Motion rig and clips (steps/m2m_rig.py)
# ---------------------------------------------------------------------------------------------------------------

def skeleton(srv, name):
    """{template, joints} from the model's rig.json (written by m2m-rig), or None."""
    try:
        spec = srv.spec_store.model(name) or {}
    except Exception:
        return None
    m = spec.get("m2m") or (spec.get("rig") or {}).get("m2m")
    if not m or not m.get("template"): return None
    return {"model": name, "template": m["template"], "joints": m.get("joints") or {}}


def m2m_args(srv, name, template, joints=None, height=None, clips=None, naming=None, target=None, fmt=None):
    reg = srv.steps_registry
    if template not in ("auto",) + reg.M2M_TEMPLATES:
        raise ValueError("template is auto, classic or one of %s" % ", ".join(reg.M2M_TEMPLATES))
    args = ["--model", name, "--template", template]
    app_dir = reg.m2m_app()
    if app_dir: args += ["--m2m", app_dir]
    if height not in (None, "", 0):
        if isinstance(height, bool) or not isinstance(height, (int, float)) or height <= 0:
            raise ValueError("height is metres, more than 0")
        args += ["--height", str(height)]
    if joints:
        d = os.path.join(srv.layout.WORK, "m2m_joints")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%s_%d.json" % (name, int(time.time() * 1000)))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"template": template, "joints": joints}, fh)
        args += ["--joints", p]
    if clips:
        if isinstance(clips, list):
            if not all(isinstance(c, str) and c.strip() for c in clips): raise ValueError("clips are names")
            clips = ",".join(c.strip() for c in clips)
        if not isinstance(clips, str): raise ValueError("clips is a list of names, or all")
        args += ["--clips", clips]
    for flag, v, ok in (("--naming", naming, ("default", "mixamo")), ("--target", target, ("unity", "generic"))):
        if v:
            if v not in ok: raise ValueError("%s is one of %s" % (flag[2:], ", ".join(ok)))
            args += [flag, v]
    if fmt:
        fs = [f.strip() for f in (fmt if isinstance(fmt, list) else str(fmt).split(",")) if f.strip()]
        if not fs or any(f not in ("glb", "fbx") for f in fs): raise ValueError("format is glb, fbx or both")
        args += ["--format", ",".join(fs)]
    return args


# ---------------------------------------------------------------------------------------------------------------
# Adding models
# ---------------------------------------------------------------------------------------------------------------

def add_model(h, srv, q, app=None):
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        body = h._json_body()
        if body.get("provider") or body.get("task_id"):
            return generate_api.import_model(srv, sys.modules[__name__], body)
        if body.get("path"):
            return add_from_server(srv, app, body["path"], body.get("name"), body.get("group") or "")
        if not body.get("url"): raise ValueError("JSON body needs url or path (or send the file itself as the body)")
        return add_from_url(srv, body["url"], body.get("name"), body.get("group") or "")
    filename = os.path.basename((q.get("filename") or [""])[0])
    if not filename.lower().endswith(MODEL_EXTS):
        raise ValueError("?filename= names the file: .glb, .gltf, .fbx, .obj or .zip")
    n = int(h.headers.get("Content-Length") or 0)
    if n <= 0: raise ValueError("empty body")
    if n > MAX_DOWNLOAD: raise ApiError(413, "over 1 GB")
    stage = tempfile.mkdtemp(prefix="api_", dir=incoming(srv))
    dest = os.path.join(stage, safe_filename(filename))
    left = n
    with open(dest, "wb") as fh:
        while left > 0:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk: break
            fh.write(chunk); left -= len(chunk)
    if left: shutil.rmtree(stage, ignore_errors=True); raise ValueError("the body ended early")
    return place(srv, stage, dest, (q.get("name") or [""])[0], (q.get("group") or [""])[0])


def add_from_url(srv, url, name=None, group=""):
    u = urllib.parse.urlparse(url or "")
    if u.scheme != "https" or not u.netloc: raise ValueError("url must be https://")
    filename = safe_filename(os.path.basename(urllib.parse.unquote(u.path)) or "model.glb")
    if not filename.lower().endswith(MODEL_EXTS):
        filename += ".glb"                          # signed links (Tripo, Meshy) often hide the type; most are GLB
    stage = tempfile.mkdtemp(prefix="api_", dir=incoming(srv))
    dest = os.path.join(stage, filename)
    req = urllib.request.Request(url, headers={"User-Agent": "AutorigWorkbench/1"})
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk: break
                got += len(chunk)
                if got > MAX_DOWNLOAD: raise ApiError(413, "the download is over 1 GB")
                fh.write(chunk)
    except ApiError:
        shutil.rmtree(stage, ignore_errors=True); raise
    except Exception as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise ApiError(502, "download failed: %s" % e)
    return place(srv, stage, dest, name, group)


def place(srv, stage, path, name, group):
    """Into the models root as one model. A GLB is mended first (core/tripo_glb.py: NaNs, flat skeletons)."""
    try:
        if path.lower().endswith(".glb"):
            with open(path, "rb") as fh:
                if fh.read(4) != b"glTF": raise ValueError("the file is not a GLB")
            import tripo_glb
            fixed = tripo_glb.mend(path, report=lambda *_: None)
            if fixed != path:
                shutil.move(fixed, path)
        name = srv.clean_name(name or os.path.splitext(os.path.basename(path))[0])
        if not name: raise ValueError("no usable model name: give name")
        if group and not srv.NAME.match(group): raise ValueError("a group name is letters, digits, _ and -")
        return srv.place(stage, group, name, move=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------
# Collections and server files: only the folders .autorig_server.json allows (core/server_config.py)
# ---------------------------------------------------------------------------------------------------------------

def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def collection_list(srv, app):
    """[(summary)] of the collections the page may open, and the config."""
    import server_config
    cfg = server_config.load()
    out = []

    def add(path, name, kind):
        if not os.path.isdir(path) or any(_same(path, c["path"]) for c in out): return
        s = srv.layout.root_summary(path, kind)
        if name: s["name"] = name
        out.append(s)

    start = getattr(app, "start_root", None) or srv.layout.ROOT
    add(start, None, "collection")
    for d in sorted(os.listdir(start)) if os.path.isdir(start) else ():
        p = os.path.join(start, d)
        if d.startswith((".", "_")) or not os.path.isdir(p) or srv.layout.is_model_dir(p): continue
        if srv.layout.count_models(p): add(p, "%s / %s" % (os.path.basename(os.path.normpath(start)), d), "group")
    for c in cfg["collections"]:
        add(c["path"], c["name"], "collection")
    add(srv.layout.ROOT, None, "current")
    if cfg["allow_any_folder"]:
        for r in srv.layout.discover_roots():
            add(r["path"], None, r.get("kind") or "found")
    return out, cfg


def collections(srv, app):
    out, cfg = collection_list(srv, app)
    for c in out: c["current"] = _same(c["path"], srv.layout.ROOT)
    return {"current": srv.layout.ROOT, "work": srv.layout.WORK, "collections": out,
            "allow_any_folder": cfg["allow_any_folder"]}


def select_collection(srv, app, body):
    want = str(body.get("path") or "")
    if not want: raise ValueError("path: the collection to open")
    out, cfg = collection_list(srv, app)
    if not any(_same(c["path"], want) for c in out) and not cfg["allow_any_folder"]:
        raise ApiError(403, "that folder is not one of this server's collections (.autorig_server.json lists them)")
    if not os.path.isdir(want): raise ApiError(404, "no folder %s" % want)
    srv._busy(app)
    srv.layout.set_root(want)
    srv.spec_store.reload()
    return collections(srv, app)


def add_collection(srv, app, body):
    import server_config
    if not server_config.load()["allow_any_folder"]:
        raise ApiError(403, "this server does not let the page add folders; list them in .autorig_server.json")
    p = os.path.realpath(os.path.expanduser(str(body.get("path") or "")))
    if not os.path.isdir(p): raise ApiError(404, "no folder %s" % p)
    server_config.add_collection(p, body.get("name"))
    return collections(srv, app)


def browse_roots(srv, app):
    """The folders whose files the page may list: the collections (not their group views) and config browse."""
    out, cfg = collection_list(srv, app)
    roots = []
    for r in [c["path"] for c in out if c["kind"] != "group"] + cfg["browse"]:
        if os.path.isdir(r) and not any(_same(r, u) for u in roots):
            roots.append(os.path.realpath(r))
    return roots, cfg


def allowed_path(srv, app, p):
    import server_config
    roots, cfg = browse_roots(srv, app)
    full = os.path.realpath(os.path.expanduser(p))
    if not cfg["allow_any_folder"] and not server_config.inside(full, roots):
        raise ApiError(403, "outside the folders this server lets you browse")
    return full


def list_files(srv, app, p):
    import server_config
    roots, cfg = browse_roots(srv, app)
    if not p:
        return {"path": None, "parent": None, "entries": [
            {"name": r, "path": r, "type": "dir", "model": srv.layout.is_model_dir(r)} for r in roots]}
    full = allowed_path(srv, app, p)
    if not os.path.isdir(full): raise ApiError(404, "no folder %s" % full)
    parent = os.path.dirname(full)
    if parent == full or (not cfg["allow_any_folder"] and not server_config.inside(parent, roots)):
        parent = ""                                       # the top: back to the list of roots
    try:
        names = sorted(os.listdir(full), key=str.lower)
    except OSError as e:
        raise ApiError(403, str(e))
    entries = []
    for n in names:
        if n.startswith("."): continue
        q = os.path.join(full, n)
        if os.path.isdir(q):
            entries.append({"name": n, "path": q, "type": "dir", "model": srv.layout.is_model_dir(q)})
        elif n.lower().endswith(MODEL_EXTS):
            entries.append({"name": n, "path": q, "type": "file", "bytes": os.path.getsize(q)})
        if len(entries) >= 2000: break
    return {"path": full, "parent": parent, "entries": entries}


PREFER = (".glb", ".gltf", ".fbx", ".obj", ".zip")


def inbox_models(srv, app):
    """The model files in the browse folders that are not collections (the inbox: ~/incoming on the box), as one
    entry per model: files with the same name in one folder are one model, and the best of them is the one shown
    (GLB first). Texture folders (.fbm, textures/), hidden and _ folders are skipped; three levels deep at most."""
    import server_config
    roots, cfg = browse_roots(srv, app)
    cols, _ = collection_list(srv, app)
    inbox = [r for r in roots if not any(_same(r, c["path"]) for c in cols)]
    best = {}
    for root in inbox:
        base = root.count(os.sep)
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "_")) and not x.lower().endswith(".fbm")
                       and x.lower() not in ("textures", "texture", "maps")]
            if d.count(os.sep) - base >= 3: dirs[:] = []
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext.lower() not in PREFER: continue
                key = (d, stem.lower())
                rank = PREFER.index(ext.lower())
                if key not in best or rank < best[key][0]:
                    best[key] = (rank, os.path.join(d, f))
    out = []
    for (d, _), (_, p) in best.items():
        st = os.stat(p)
        out.append({"name": os.path.splitext(os.path.basename(p))[0], "path": p, "ext": os.path.splitext(p)[1].lower(),
                    "bytes": st.st_size, "modified": st.st_mtime, "folder": os.path.relpath(d, next(r for r in inbox if server_config.inside(d, [r])))})
        if len(out) >= 500: break
    out.sort(key=lambda x: -x["modified"])
    return {"inbox": inbox, "models": out}


def add_from_server(srv, app, p, name=None, group=""):
    """A file or folder already on the server, as a model: the model it already belongs to in the open collection,
    else a copy brought in (GLBs mended, as an upload's are)."""
    import server_config
    full = allowed_path(srv, app, p)
    if not os.path.exists(full): raise ApiError(404, "no such file %s" % full)
    for g, n in srv.layout.all_models():
        if server_config.inside(full, [srv.model_dir(g, n)]):
            return {"group": g, "name": n, "existing": True}
    if os.path.isfile(full) and not full.lower().endswith(MODEL_EXTS):
        raise ValueError("pick a .glb, .gltf, .fbx, .obj or .zip, or a folder")
    name = srv.clean_name(name or os.path.splitext(os.path.basename(os.path.normpath(full)))[0])
    if not name: raise ValueError("no usable model name: give name")
    if group and not srv.NAME.match(group): raise ValueError("a group name is letters, digits, _ and -")
    m = srv.place(full, group, name, move=False)
    import tripo_glb
    for f in os.listdir(m["dir"]):
        if f.lower().endswith(".glb"):
            g = os.path.join(m["dir"], f)
            fixed = tripo_glb.mend(g, report=lambda *_: None)
            if fixed != g: shutil.move(fixed, g)
    return m


def incoming(srv):
    d = os.path.join(srv.layout.WORK, "_incoming")
    os.makedirs(d, exist_ok=True)
    return d


def safe_filename(s):
    s = re.sub(r"[^A-Za-z0-9_\-.]+", "_", s).lstrip(".")
    return s[:120] or "model"


THUMBNAILS = ("thumbnail.png", "thumbnail.jpg", "preview.png", "rigged/{name}_qa.png")


def listed(srv, g, name):
    """A model as GET /api/v1/models lists it: its state, the Mesh2Motion template it is rigged on (or None), and
    a picture where one exists (the page's library cards)."""
    out = srv.status(g, name)
    sk = skeleton(srv, name)
    out["m2m_template"] = sk["template"] if sk else None
    out["thumbnail"] = None
    d = srv.model_dir(g, name)
    for rel in THUMBNAILS:
        rel = rel.format(name=name)
        if os.path.isfile(os.path.join(d, *rel.split("/"))):
            out["thumbnail"] = "/api/v1/models/%s/files/%s" % (urllib.parse.quote(name), urllib.parse.quote(rel))
            break
    return out


def model_files(srv, g, name):
    d = srv.model_dir(g, name)
    out = []
    for r, dirs, fs in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith((".", "_"))]
        for f in fs:
            rel = os.path.relpath(os.path.join(r, f), d).replace("\\", "/")
            out.append({"path": rel, "bytes": os.path.getsize(os.path.join(r, f)),
                        "url": "/api/v1/models/%s/files/%s" % (urllib.parse.quote(name), urllib.parse.quote(rel))})
    return sorted(out, key=lambda x: x["path"])


def confine_paths(srv, tool, values):
    """Tool options that are files or folders, made absolute and kept inside the models root or the work folder.
    A relative path is taken from the models root."""
    if not isinstance(values, dict): raise ValueError("options is an object")
    known = {o["name"]: o for o in srv.steps_registry.TOOLS[tool]["options"]}
    roots = [os.path.realpath(srv.layout.ROOT), os.path.realpath(srv.layout.WORK)]
    out = dict(values)
    for k, v in values.items():
        o = known.get(k)
        if not o or not o.get("path") or v in (None, ""): continue
        items = v if isinstance(v, list) else [v]
        fixed = []
        for item in items:
            if not isinstance(item, str): raise ValueError("--%s is a path" % k)
            pre, sep, p = item.partition("=") if k == "card" else ("", "", item)
            p, colon, view = p.partition(":") if k == "card" and not re.match(r"^[A-Za-z]:[\\/]", p) else (p, "", "")
            full = os.path.realpath(p if os.path.isabs(p) else os.path.join(roots[0], p))
            if not any(os.path.normcase(full) == os.path.normcase(r) or
                       os.path.normcase(full).startswith(os.path.normcase(r) + os.sep) for r in roots):
                raise ApiError(403, "--%s must be inside the models root or the work folder" % k)
            if o["path"] == "in" and not os.path.exists(full): raise ApiError(404, "--%s: no such file %s" % (k, p))
            fixed.append(pre + sep + full + colon + view)
        out[k] = fixed if isinstance(v, list) else fixed[0]
    return out


# ---------------------------------------------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------------------------------------------

def openapi():
    paths = {}
    for method, path, summary, example in ROUTES:
        op = {"summary": summary, "responses": {"200": {"description": "JSON"}}}
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                  for n in re.findall(r"\{(\w+)\}", path)]
        if params: op["parameters"] = params
        if example is not None:
            op["requestBody"] = {"content": {"application/json": {"example": example}}}
        if method in ("POST", "PUT", "DELETE") and "jobs" not in path and path not in ("/api/v1/models",):
            op["responses"]["202"] = {"description": "a job: follow links.self or links.events"}
        if path != "/api/v1/openapi.json":
            op["security"] = [{"bearer": []}]
        paths.setdefault(path, {})[method.lower()] = op
    return {"openapi": "3.1.0",
            "info": {"title": "Autorig Workbench API", "version": VERSION,
                     "description": "Rig any mesh on a Mesh2Motion skeleton and give it Mesh2Motion's clips. "
                                    "See docs/API.md."},
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
            "paths": paths}


def docs_page():
    rows = "".join("<tr><td><code>%s</code></td><td><code>%s</code></td><td>%s</td></tr>"
                   % (m, p.replace("<", "&lt;"), s.replace("<", "&lt;")) for m, p, s, _ in ROUTES)
    return ("<!doctype html><meta charset=utf-8><title>Autorig Workbench API</title>"
            "<style>body{font:15px system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
            "td{padding:.3rem .6rem;border-bottom:1px solid #8884;vertical-align:top}</style>"
            "<h1>Autorig Workbench API v%s</h1><p>Send <code>Authorization: Bearer &lt;token&gt;</code>. "
            "Machine-readable: <a href=/api/v1/openapi.json>/api/v1/openapi.json</a>. Examples: docs/API.md in the "
            "repository.</p><table>%s</table>" % (VERSION, rows))

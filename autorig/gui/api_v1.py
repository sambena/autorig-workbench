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
import json, os, re, secrets, shutil, tempfile, time, urllib.parse, urllib.request

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
    ("GET", "/api/v1/models", "every model in the collection, with its state", None),
    ("POST", "/api/v1/models", "add a model: a raw file body (?filename=&name=&group=), or JSON {url, name, group}",
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
    ("GET", "/api/v1/jobs", "recent jobs", None),
    ("GET", "/api/v1/jobs/{id}", "one job: state, progress, results, the last lines of its log (?log=N)", None),
    ("GET", "/api/v1/jobs/{id}/events", "the job's log as Server-Sent Events, ending with event: end", None),
    ("DELETE", "/api/v1/jobs/{id}", "cancel a job", None),
]


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

    if head == "models":
        if len(parts) == 1:
            if method == "GET":
                srv.spec_store.reload()
                return {"models": [srv.status(g, n) for g, n in srv.layout.all_models()]}
            if method == "POST":
                m = add_model(h, srv, q)
                return 201, {"model": srv.status(m["group"], m["name"])}
        name = parts[1] if len(parts) > 1 else ""
        g = srv.find_group(name)
        if g is None: raise ApiError(404, "no model %r" % name)
        rest = parts[2:]
        if not rest and method == "GET":
            srv.spec_store.reload()
            return dict(srv.details(g, name), files=model_files(srv, g, name))
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
        if body.get("url"):
            m = add_from_url(srv, body["url"], body.get("name"), body.get("group") or "")
            name, g = m["name"], m["group"]
        else:
            name = body.get("model") or ""
            g = srv.find_group(name)
            if g is None: raise ApiError(404, "no model %r: give model, or url to add one" % name)
        job = submit(app, srv, g, name, "m2m-rig", m2m_args(
            srv, name, template=body.get("template") or "auto", height=body.get("height"),
            clips=body.get("clips") or "all", naming=body.get("naming"), target=body.get("target"),
            fmt=body.get("format")))
        return 202, dict(job, model=name)

    raise ApiError(404, "no route %s %s (GET /api/v1 lists them)" % (method, path))


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

def add_model(h, srv, q):
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        body = h._json_body()
        if body.get("provider") or body.get("task_id"):
            raise ApiError(501, "import by provider task id comes with generation (docs/PLAN.md, M8); "
                                "for now give the download url")
        if not body.get("url"): raise ValueError("JSON body needs url (or send the file itself as the body)")
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


def incoming(srv):
    d = os.path.join(srv.layout.WORK, "_incoming")
    os.makedirs(d, exist_ok=True)
    return d


def safe_filename(s):
    s = re.sub(r"[^A-Za-z0-9_\-.]+", "_", s).lstrip(".")
    return s[:120] or "model"


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

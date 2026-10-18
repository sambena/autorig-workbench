# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the HTTP API's routes for the page's Advanced panel (phase M7, docs/PLAN.md section M): the
# features the old UI had beyond Load -> Rig -> Animate, moved onto /api/v1 from its JSON routes.
#
#   model manager    POST /api/v1/models/{m}/rename|move|duplicate, DELETE /api/v1/models/{m}?confirm={m}
#   batch            POST /api/v1/batch/{audit-all|rig-all|clips-all|audit-failed|source-all}, GET /api/v1/audits
#   retarget         POST /api/v1/mocap (a BVH or FBX as the body), GET/POST /api/v1/models/{m}/retarget
#   Model Studio     GET /api/v1/models/{m}/studio, POST .../studio/{tool}, POST .../studio/import
#   samples          GET /api/v1/samples, POST /api/v1/samples/{name}
#   clean up         GET /api/v1/clean, POST /api/v1/clean
#   rig.json         GET/PUT /api/v1/models/{m}/spec
#   and              GET /api/v1/models/{m}/audit, GET /api/v1/models/{m}/doctor, GET /api/v1/groups, GET /api/v1/server
#
# gui/api_v1.py calls route() first and falls through to its own routes when this answers NOT_OURS; its handle()
# turns ApiError, Busy and ValueError into the error codes docs/API.md lists. Studio's paid generation is not here:
# phase M8 owns generation.
import json, os, re, sys, urllib.parse, uuid

NOT_OURS = object()

ROUTES = [
    ("POST", "/api/v1/models/{model}/rename", "rename a model (its work files follow): {name}", {"name": "wolf_old"}),
    ("POST", "/api/v1/models/{model}/move", "move a model to a group (\"\" is the top): {group}", {"group": "Animals"}),
    ("POST", "/api/v1/models/{model}/duplicate", "copy a model under a new name: {name}", {"name": "wolf_copy"}),
    ("DELETE", "/api/v1/models/{model}", "delete a model and its work files; ?confirm= must repeat its name", None),
    ("GET", "/api/v1/groups", "the collection's groups", None),
    ("GET", "/api/v1/audits", "the audit table for the collection, worst first", None),
    ("GET", "/api/v1/models/{model}/audit", "the model's whole audit, with a digest of the bones it blames", None),
    ("GET", "/api/v1/models/{model}/doctor", "Mesh Doctor's geometry report on the source (the file is not changed)", None),
    ("POST", "/api/v1/batch/{action}", "one job over the collection: audit-all, rig-all, clips-all, audit-failed, "
                                       "source-all; {models?, keep_going?, missing_only?}",
     {"keep_going": True}),
    ("GET", "/api/v1/mocap", "the mocap clips uploaded for retargeting", None),
    ("POST", "/api/v1/mocap", "upload a mocap clip: a .bvh or .fbx as the body, ?filename=", None),
    ("GET", "/api/v1/models/{model}/retarget", "plan a retarget without running it: ?file=&clip_name=&source_action=", None),
    ("POST", "/api/v1/models/{model}/retarget", "retarget an uploaded clip onto the rigged model (a job)",
     {"file": "walk.bvh", "clip_name": "walk", "root_motion": True}),
    ("GET", "/api/v1/models/{model}/studio", "Model Studio: the model's tool settings and outputs", None),
    ("POST", "/api/v1/models/{model}/studio/{tool}", "run a Studio tool (lods, vehicle, reference, materials, foliage, "
                                                     "validate, package, engine) as a job, or {save: true} to keep the settings",
     {"settings": {"ratios": [1, 0.4, 0.15]}}),
    ("POST", "/api/v1/models/{model}/studio/import", "copy a Studio output into a new model: {artifact, name, group}",
     {"artifact": "_studio/lods/wolf_LOD1.glb", "name": "wolf_lod1"}),
    ("GET", "/api/v1/models/{model}/spec", "the classic rig spec (rig.json): its text, base hash and problems", None),
    ("PUT", "/api/v1/models/{model}/spec", "save rig.json: {spec, base} (409 when it changed since; force to overwrite)",
     {"spec": {"schema": "autorig-spec/1", "rig": {"kind": "placed"}}, "base": "..."}),
    ("GET", "/api/v1/samples", "the bundled CC0 sample models", None),
    ("POST", "/api/v1/samples/{name}", "load a sample model into the open collection", None),
    ("GET", "/api/v1/clean", "generated files that could be deleted, by kind, with their sizes", None),
    ("POST", "/api/v1/clean", "delete generated files of these kinds: {kinds}", {"kinds": ["source", "previews"]}),
    ("GET", "/api/v1/server", "Blender, the folders, the running job and the step time limits", None),
]

STUDIO_PAID = ("generate",)                 # spends money: generation is phase M8's


def route(h, app, srv, method, parts, q):
    v1 = srv.api_v1
    head = parts[0] if parts else ""
    if head == "groups" and method == "GET" and len(parts) == 1:
        return {"groups": srv.layout.groups()}
    if head == "audits" and method == "GET" and len(parts) == 1:
        srv.spec_store.reload()
        return {"models": srv.audit_table()}
    if head == "batch" and method == "POST" and len(parts) == 2:
        return batch(app, srv, parts[1], h._json_body())
    if head == "mocap" and len(parts) == 1:
        if method == "GET": return {"files": mocap_files(srv)}
        if method == "POST": return 201, upload_mocap(h, srv, q)
    if head == "samples":
        if method == "GET" and len(parts) == 1: return {"samples": srv.sample_models_list()}
        if method == "POST" and len(parts) == 2:
            res = srv.load_sample_model(parts[1])
            return (200 if res["already_installed"] else 201), dict(res, model=srv.status(res["group"], res["name"]))
    if head == "clean" and len(parts) == 1:
        import clean_generated
        if method == "GET":
            return {"work": srv.layout.WORK, "kinds": clean_generated.scan(srv.layout.ROOT, srv.layout.WORK)}
        if method == "POST":
            kinds = h._json_body().get("kinds")
            if not isinstance(kinds, list) or not kinds: raise ValueError("kinds: the kinds of file to delete (GET /api/v1/clean)")
            srv._busy(app)
            return clean_generated.clean(srv.layout.ROOT, srv.layout.WORK, [str(k) for k in kinds])
    if head == "server" and method == "GET" and len(parts) == 1:
        return server_info(app, srv)
    if head != "models" or len(parts) < 2:
        return NOT_OURS

    name, g = v1.find_model(srv, parts[1])
    rest = parts[2:]
    if g is None:
        if rest or method == "DELETE": raise v1.ApiError(404, "no model %r" % name)
        return NOT_OURS
    if not rest and method == "DELETE":
        if (q.get("confirm") or [""])[0] != name:
            raise ValueError("deleting removes %s and its work files for good: repeat its name in ?confirm=" % name)
        srv._busy(app, name)
        out = srv.layout.delete_model(name)
        srv._forget_grades(name)
        srv.spec_store.reload()
        return out
    if len(rest) == 1 and rest[0] in ("rename", "move", "duplicate") and method == "POST":
        return manage(app, srv, g, name, rest[0], h._json_body())
    if rest == ["audit"] and method == "GET":
        return audit(srv, g, name)
    if rest == ["doctor"] and method == "GET":
        src = srv._quiet(srv.layout.source_model, name)
        if not src: raise v1.ApiError(404, "%s has no source mesh" % name)
        return srv.doctor_report(src, name)
    if rest == ["retarget"]:
        if method == "GET":
            one = lambda k: (q.get(k) or [None])[0]
            return retarget_plan(srv, name, mocap_path(srv, one("file") or ""), one("clip_name"), one("source_action"))
        if method == "POST":
            body = h._json_body()
            return retarget(app, srv, g, name, body)
    if rest and rest[0] == "studio":
        if method == "GET" and len(rest) == 1: return studio_view(srv, name)
        if method == "POST" and len(rest) == 2: return studio_post(app, srv, g, name, rest[1], h._json_body())
    if rest == ["spec"]:
        if method == "GET": return spec_view(srv, name)
        if method == "PUT": return spec_save(srv, name, h._json_body())
    return NOT_OURS


# ---------------------------------------------------------------------------------------------------------------
# Model manager (core/layout.py does the work; a model a job is running on is left alone)
# ---------------------------------------------------------------------------------------------------------------

def manage(app, srv, g, name, what, body):
    if what == "move":
        group = str(body.get("group") or "").strip()
        if group and not srv.NAME.match(group): raise ValueError("a group name is letters, digits, _ and -")
        srv._busy(app, name)
        out = srv.layout.move_model(name, group)
        new = name
    else:
        new = str(body.get("name") or "").strip()
        if not new: raise ValueError("name: the new model name")
        if what == "rename":
            srv._busy(app, name)
            out = srv.layout.rename_model(name, new)
            srv._forget_grades(name)
        else:
            out = srv.layout.duplicate_model(name, new)
        new = out.get("new_name") or out.get("name") or new
    srv.spec_store.reload()
    gg = srv.find_group(new)
    return dict(out, model=srv.status(gg, new) if gg is not None else None)


def audit(srv, g, name):
    """The model's audit file as steps/audit.py wrote it: in the work folder (the classic rig) or beside the rig
    (m2m-rig), the newer of the two; with viewer_api's digest of it (the bones it blames, worst first)."""
    v1 = srv.api_v1
    rd = srv._quiet(srv.layout.rigged_dir, name)
    found = [p for p in (srv.audit_file(name), rd and os.path.join(rd, "audit", name + ".json")) if p and os.path.isfile(p)]
    if not found: raise v1.ApiError(404, "%s has not been audited yet" % name)
    p = max(found, key=os.path.getmtime)
    with open(p, encoding="utf-8") as fh: full = json.load(fh)
    return {"model": name, "file": p, "audit": full, "bones": srv.viewer_api.worst_bones(full)}


# ---------------------------------------------------------------------------------------------------------------
# Batch actions over the open collection: one job, a step per model, going on past a failure unless told not to
# ---------------------------------------------------------------------------------------------------------------

def batch(app, srv, action, body):
    if action not in srv.BATCHES: raise srv.api_v1.ApiError(404, "no batch %r: %s" % (action, ", ".join(srv.BATCHES)))
    models = body.get("models")
    if models is not None and (not isinstance(models, list) or not all(isinstance(x, str) for x in models)):
        raise ValueError("models is a list of model names")
    srv.spec_store.reload()
    job = srv.batch_job(action, models or None, keep_going=body.get("keep_going", True) is not False,
                        missing_only=bool(body.get("missing_only")))
    app.runner.submit(job)
    return 202, srv.api_v1.job_view(job)


# ---------------------------------------------------------------------------------------------------------------
# Retarget a mocap clip (core/retargeter.py, steps/retarget.py)
# ---------------------------------------------------------------------------------------------------------------

def mocap_dir(srv):
    d = os.path.join(srv.layout.WORK, "mocap")
    os.makedirs(d, exist_ok=True)
    return d


def mocap_files(srv):
    d = mocap_dir(srv)
    return [{"file": f, "bytes": os.path.getsize(os.path.join(d, f))}
            for f in sorted(os.listdir(d), key=str.lower) if f.lower().endswith((".bvh", ".fbx"))]


def mocap_path(srv, f):
    """An uploaded clip by its name, or a path under the models root or the work folder."""
    if not f: raise ValueError("file: an uploaded clip (GET /api/v1/mocap) or a path in the collection")
    p = f if os.path.isabs(f) else os.path.join(mocap_dir(srv), os.path.basename(f))
    if not srv.mocap_ok(p): raise srv.api_v1.ApiError(404, "no mocap clip %s in the uploads, the models or the work folder" % f)
    return p


def upload_mocap(h, srv, q):
    filename = os.path.basename((q.get("filename") or [""])[0])
    clean = re.sub(r"[^A-Za-z0-9_\-.]+", "_", filename).lstrip(".")
    stem, ext = os.path.splitext(clean)
    if ext.lower() not in (".bvh", ".fbx") or not stem: raise ValueError("?filename= names a .bvh or .fbx file")
    n = int(h.headers.get("Content-Length") or 0)
    if n <= 0: raise ValueError("empty body")
    if n > srv.api_v1.MAX_DOWNLOAD: raise srv.api_v1.ApiError(413, "over 1 GB")
    dest = os.path.join(mocap_dir(srv), clean)
    left = n
    with open(dest + ".part", "wb") as fh:
        while left > 0:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk: break
            fh.write(chunk); left -= len(chunk)
    if left:
        os.remove(dest + ".part"); raise ValueError("the body ended early")
    os.replace(dest + ".part", dest)
    try:
        meta = retargeter().inspect_mocap_file(dest)
    except (ValueError, RuntimeError) as e:
        os.remove(dest)
        raise ValueError("not a mocap clip this tool can read: %s" % e)
    return {"file": clean, "path": dest, "meta": meta}


def retargeter():
    try:
        import retargeter as r
    except ImportError:
        from autorig.core import retargeter as r
    return r


def retarget_plan(srv, name, path, clip_name=None, source_action=None):
    try:
        plan = retargeter().plan_retarget(name, path, clip_name=clip_name, source_action=source_action)
    except FileNotFoundError as e:
        raise srv.api_v1.ApiError(409, "%s: rig it (a .blend in its rig folder) first" % e)
    plan.pop("tgt_dirs", None)                         # every rest bone's direction: large, and only for the solver
    return plan


def retarget(app, srv, g, name, body):
    path = mocap_path(srv, str(body.get("file") or ""))
    if not srv.blender.find(required=False):
        raise ValueError("Blender not found: install it or set AUTORIG_BLENDER")
    try:
        retargeter().find_blend_file(name)
    except FileNotFoundError:
        raise srv.api_v1.ApiError(409, "%s is not rigged yet (no .blend in its rig folder)" % name)
    argv = [sys.executable, "-u", os.path.join(srv.STEPS, "retarget.py"), name, os.path.realpath(path), "--preview"]
    if body.get("clip_name"): argv += ["--clip-name", srv.clean_name(str(body["clip_name"])) or "mocap_clip"]
    if body.get("source_action"): argv += ["--action", str(body["source_action"])]
    if body.get("root_motion", True) is False: argv.append("--no-root-motion")
    job = app.runner.submit(srv.Job(name, g, "retarget", [("retarget %s" % os.path.basename(path), argv, None, name)]))
    return 202, srv.api_v1.job_view(job)


# ---------------------------------------------------------------------------------------------------------------
# Model Studio (core/studio.py, steps/studio_job.py): LODs, vehicle, reference sheet, materials, foliage, clip
# check, engine package and engine FBX. Generation spends money and is phase M8's.
# ---------------------------------------------------------------------------------------------------------------

def studio():
    import studio as s                     # core/ is on the path (gui/server.py), as studio_assets imports it
    return s


def studio_folder(srv, name):
    s = studio()
    folder = srv.layout.pack_dir(name)
    if not s.inside(folder, srv.layout.ROOT): raise srv.api_v1.ApiError(403, "the model is outside the collection")
    return folder


def studio_tools():
    return [t for t in studio().TOOLS if t not in STUDIO_PAID]


def studio_view(srv, name):
    s = studio()
    folder = studio_folder(srv, name)
    try:
        settings = s.load(folder)
    except ValueError as e:
        raise ValueError("studio.json cannot be used: %s" % e)
    items = s.artifacts(folder)
    for a in items:
        a["url"] = "/api/v1/models/%s/files/%s" % (urllib.parse.quote(name), urllib.parse.quote(a["path"]))
    return {"model": name, "tools": studio_tools(), "settings": {t: settings[t] for t in studio_tools()},
            "defaults": {t: s.DEFAULTS[t] for t in studio_tools()}, "artifacts": items}


def studio_post(app, srv, g, name, tool, body):
    s = studio()
    v1 = srv.api_v1
    folder = studio_folder(srv, name)
    if tool == "import":
        if any(j.model == name and j.state in ("queued", "running") for j in app.runner.jobs.values()):
            raise srv.Busy("wait for this model's jobs to finish before importing an output")
        from studio_assets import promote_output
        out = promote_output(name, str(body.get("artifact") or ""), srv.clean_name(str(body.get("name") or "")),
                             str(body.get("group") or ""))
        srv.spec_store.reload()
        return 201, dict(out, model=srv.status(out["group"], out["name"]))
    if tool in STUDIO_PAID:
        raise v1.ApiError(501, "Studio generation spends money and comes with phase M8 (docs/PLAN.md)")
    if tool not in s.TOOLS: raise v1.ApiError(404, "no Studio tool %r: %s" % (tool, ", ".join(studio_tools())))
    settings = s.validate(tool, body.get("settings") or {})
    if tool == "reference" and settings["folder"]:
        s.input_folder(settings["folder"], folder, [srv.layout.ROOT, srv.layout.WORK])
    if body.get("save"):
        data = s.load(folder)
        data[tool] = settings
        s.atomic_json(os.path.join(folder, "studio.json"), data)
        return {"saved": True, "tool": tool, "settings": settings}
    snap = os.path.join(srv.layout.work_dir("studio_jobs"), uuid.uuid4().hex + ".json")
    s.atomic_json(snap, settings)
    argv = [sys.executable, "-u", os.path.join(srv.STEPS, "studio_job.py"), tool, name, "--settings", snap]
    job = app.runner.submit(srv.Job(name, g, "studio-" + tool, [("%s: %s" % (tool, name), argv, None, name)]))
    return 202, v1.job_view(job)


# ---------------------------------------------------------------------------------------------------------------
# rig.json, the classic rig's spec (gui/spec_api.py checks and writes it, keeping rig.json.bak)
# ---------------------------------------------------------------------------------------------------------------

def spec_view(srv, name):
    sa = srv.spec_api
    path, text = sa.read_spec_text(srv, name)
    spec, errors, warnings = None, [], []
    if text is not None:
        try:
            spec = json.loads(text)
        except ValueError as e:
            errors = [{"path": "", "message": "not JSON: %s" % e}]
        else:
            errors, warnings = sa.check(spec, sa._load(os.path.join(srv.layout.WORK, "source", name + ".json")))
    return {"model": name, "text": text, "spec": spec, "base": sa.text_hash(text), "errors": errors,
            "warnings": warnings, "backup": os.path.exists(path + ".bak")}


def spec_save(srv, name, body):
    sa = srv.spec_api
    spec = body.get("spec")
    if not isinstance(spec, dict): raise ValueError("spec: the rig.json object")
    try:
        text, errs, warns = sa.write_spec(srv, name, spec, body.get("base"), force=bool(body.get("force")))
    except sa.Conflict as e:
        raise srv.api_v1.ApiError(409, str(e))
    if errs:
        raise ValueError("rig.json has problems: " + "; ".join("%s: %s" % (e["path"], e["message"]) for e in errs[:5]))
    return {"model": name, "saved": True, "text": text, "base": sa.text_hash(text), "warnings": warns}


# ---------------------------------------------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------------------------------------------

def server_info(app, srv):
    try:
        import autorig_watchdog as wd
    except ImportError:
        from autorig.core import autorig_watchdog as wd
    b = srv.blender.find(required=False)
    cur = app.runner.current
    return {"blender": b, "blender_version": srv.blender.version_string(b) if b else None,
            "blender_warning": srv.blender.version_warning(b) if b else None,
            "models": srv.layout.ROOT, "work": srv.layout.WORK,
            "running": srv.api_v1.job_view(cur) if cur else None,
            "step_timeouts": wd.DEFAULT_TIMEOUTS, "max_memory_mb": wd.get_max_memory_mb()}

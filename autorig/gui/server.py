# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the local GUI server. Standard library only.
#
#   python -m autorig [--models DIR] [--work DIR] [--port N] [--no-browser]
#
# Serves one page (gui/index.html) on 127.0.0.1 and runs the pipeline's steps as headless Blender subprocesses, one
# job at a time, streaming each step's output to the page (Server-Sent Events). Blender is never shown.
#
# Safety: bound to localhost only, with a random session token on every API call and file URL (the browser is
# opened on a URL carrying it), a Host check against DNS rebinding, steps from a fixed list, and files served only
# from under the models root and the work folder. Cancel kills the running step's own process by its PID, with the
# processes it started (found from that PID), never anything by image name.
import argparse, contextlib, io, json, mimetypes, os, queue, re, secrets, shutil, subprocess, sys, threading, time
import webbrowser, zipfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
try:
    from . import viewer_api                      # audit digests (gui/viewer_api.py)
    from . import spec_api                        # rig.json checks and writes (gui/spec_api.py)
    from . import ai_api                          # AI Assist (gui/ai_api.py), under /api/v1/ai/
    from . import api_v1                          # the HTTP API (gui/api_v1.py), the page's only back end
    from . import api_manage                      # its Advanced panel's routes (gui/api_manage.py)
except ImportError:
    import viewer_api
    import spec_api
    import ai_api
    import api_v1
    import api_manage

HERE = os.path.dirname(os.path.abspath(__file__))
M2M_DIR = os.path.join(HERE, "m2m")     # the built Mesh2Motion app (scripts/build_m2m.py)
M2M_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
             ".svg": "image/svg+xml", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
             ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".webm": "video/webm", ".mp4": "video/mp4",
             ".json": "application/json", ".fbx": "application/octet-stream", ".wasm": "application/wasm"}
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
CORE, STEPS, CLI = (os.path.join(PKG, d) for d in ("core", "steps", "cli"))
if CORE not in sys.path: sys.path.insert(0, CORE)
import steps_registry                             # every step and tool (core/steps_registry.py): no folders needed

# What may be brought into the models root: sources and their textures, archives, the model's own data.
UPLOAD_EXTS = {".fbx", ".glb", ".gltf", ".bin", ".obj", ".mtl", ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif",
               ".tiff", ".webp", ".zip", ".json", ".py", ".bvh"}
SKIP_FILES = {"model.json", "pack.json"}          # outputs: publish writes them again
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
STEP_NAMES = steps_registry.MODEL_STEP_NAMES
TAG = re.compile(r"^[A-Z][A-Z0-9_]+ ")

layout = spec_store = blender = grades = exporter = None    # imported in main(), once the environment names the folders


def _quiet(fn, *a):
    """Calls a layout helper without its LAYOUT notes on the server's console."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def code_version():
    """A short hash of the page's own code (app.js, viewer.js): it changes with every deploy that changes them."""
    import hashlib
    h = hashlib.sha1()
    for f in ("app.js", "viewer.js"):
        try:
            st = os.stat(os.path.join(HERE, f))
            h.update(("%s:%d:%d;" % (f, st.st_size, st.st_mtime_ns)).encode())
        except OSError:
            pass
    return h.hexdigest()[:10]


def safe_rel(rel):
    """A relative path with no way out of the folder it is joined to, or None."""
    rel = rel.replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return "/".join(parts)


def wanted(rel):
    """True when an uploaded or imported file belongs in a model folder."""
    parts = rel.split("/")
    if any(p.startswith(("_", ".")) or p.startswith("rigged") or p == "clips" for p in parts[:-1]):
        return False
    name = parts[-1]
    return name not in SKIP_FILES and not name.startswith(".") and os.path.splitext(name)[1].lower() in UPLOAD_EXTS


def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s.strip()).strip("_").lower()
    return s[:64]


# ---------------------------------------------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------------------------------------------

class Job:
    _next = 1

    def __init__(self, model, group, step, cmds):
        self.id = Job._next; Job._next += 1
        self.model, self.group, self.step, self.cmds = model, group, step, cmds
        self.state = "queued"
        self.lines = []
        self.proc = None
        self.pid = None
        self.cancelled = False
        self.keep_going = False
        self.started = self.ended = None
        self.total = len(cmds) if cmds else 0
        self.current_idx = 0
        self.current_model = None
        self.current_label = None
        self.prev_result = None
        self.last_result = None
        self.completed_count = 0
        self.passed_count = 0
        self.failed_count = 0
        self.cond = threading.Condition()

    def add(self, line):
        with self.cond:
            self.lines.append(line.rstrip("\r\n"))
            self.cond.notify_all()

    def finish(self, state):
        with self.cond:
            self.state = state
            self.ended = time.time()
            self.cond.notify_all()

    def info(self):
        return {"id": self.id, "model": self.model, "group": self.group, "step": self.step, "state": self.state,
                "pid": self.pid, "lines": len(self.lines), "started": self.started, "ended": self.ended,
                "total": self.total, "current": self.current_idx, "current_model": self.current_model,
                "prev_result": self.prev_result, "last_result": self.last_result,
                "completed": self.completed_count,
                "passed": self.passed_count, "failed": self.failed_count}


class Runner:
    """One job at a time, in order. Each command is a subprocess whose handle is kept, so Cancel can kill exactly
    that process."""

    def __init__(self, env):
        self.env = env
        self.q = queue.Queue()
        self.jobs = {}
        self.current = None
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, job):
        self.jobs[job.id] = job
        self.q.put(job)
        return job

    def cancel(self, job):
        job.cancelled = True
        p = job.proc
        if p is not None and p.poll() is None:
            job.add("== cancelled: killing PID %d and the processes it started" % p.pid)
            try:
                import autorig_watchdog as _wd
            except ImportError:
                from autorig.core import autorig_watchdog as _wd
            # the step's own process and its children (auto-tune and retarget are Python wrappers that start
            # Blender themselves), found from that PID: never anything by name
            _wd.kill_process_tree(p)
        elif job.state == "queued":
            job.finish("cancelled")

    def _loop(self):
        while True:
            job = self.q.get()
            if job.cancelled:
                if job.state == "queued": job.finish("cancelled")
                continue
            self.current = job
            job.state, job.started = "running", time.time()
            ok = True
            total_cmds = len(job.cmds)
            job.total = total_cmds
            for idx, item in enumerate(job.cmds, 1):
                if job.cancelled: break
                label = item[0]
                argv = item[1]
                prep = item[2]
                cmd_model = item[3] if len(item) > 3 else (job.model if job.model and not job.model.startswith("(") else None)
                if not cmd_model:
                    m_lbl = re.search(r":\s*([A-Za-z0-9_\-]+)", label)
                    if m_lbl:
                        cmd_model = m_lbl.group(1)

                job.current_idx = idx
                job.current_model = cmd_model
                job.current_label = label
                job.prev_result = job.last_result

                if prep: prep()

                if total_cmds > 1:
                    job.add("--------------------------------------------------------------------------------")
                    job.add(">> [Job %d of %d] Starting: %s" % (idx, total_cmds, cmd_model or label))
                    if job.prev_result:
                        prev_m = job.prev_result.get("model", "")
                        prev_st = job.prev_result.get("status", "")
                        job.add("   (Previous: %s %s)" % (prev_m, prev_st))
                    job.add("--------------------------------------------------------------------------------")

                if cmd_model:
                    job.add(":: MODEL_ACTIVE %s" % cmd_model)
                job.add(":: SUBJOB_PROGRESS %d %d %s" % (idx, total_cmds, cmd_model or ""))
                if job.prev_result:
                    job.add(":: SUBJOB_PREV %s %s" % (job.prev_result.get("model", ""), job.prev_result.get("status", "")))
                job.add("== %s" % label)
                job.add("   " + " ".join('"%s"' % a if " " in a else a for a in argv))
                t_step = time.time()
                try:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env=self.env, cwd=REPO, text=True, encoding="utf-8", errors="replace",
                                         bufsize=1, creationflags=flags)
                except OSError as e:
                    job.add("!! could not start: %r" % e); ok = False; break
                job.proc, job.pid = p, p.pid
                failed = False
                audit_verdict = None
                try:
                    import autorig_watchdog as _wd
                except ImportError:
                    from autorig.core import autorig_watchdog as _wd
                guard = _wd.JobWatchdog(job, p, label, argv=argv)
                guard.start()
                for line in p.stdout:
                    job.add(line)
                    if line.startswith("AUDIT_PASS"):
                        audit_verdict = "PASSED"
                    elif line.startswith("AUDIT_FAIL"):
                        audit_verdict = "FAILED"
                        failed = True
                    elif line.startswith("AUDIT_CHECK"):
                        audit_verdict = "CHECK"
                    elif line.startswith("Traceback") or (TAG.match(line) and '"error"' in line):
                        failed = True
                rc = p.wait()
                secs = round(time.time() - t_step, 1)          # each step's time, for comparing runs
                guard.stop()
                job.proc = None
                if job.cancelled: break

                is_err = (rc != 0 or failed or audit_verdict == "FAILED")
                status_str = "FAILED" if is_err else (audit_verdict or "PASSED")
                job.completed_count += 1
                if is_err:
                    job.failed_count += 1
                else:
                    job.passed_count += 1

                job.last_result = {
                    "model": cmd_model or label,
                    "status": status_str,
                    "code": rc,
                    "label": label,
                    "index": idx,
                    "total": total_cmds,
                    "seconds": secs,
                }
                job.add(":: SUBJOB_RESULT %s %s" % (cmd_model or label, status_str))

                if is_err:
                    job.add("!! %s failed (exit code %s, %.1f s)" % (label, rc, secs))
                    if total_cmds > 1:
                        job.add(">> [Job %d of %d] %s FAILED" % (idx, total_cmds, cmd_model or label))
                    ok = False
                    if job.keep_going: continue
                    break
                else:
                    job.add("== %s done (%.1f s)" % (label, secs))
                    if total_cmds > 1:
                        job.add(">> [Job %d of %d] %s %s" % (idx, total_cmds, cmd_model or label, status_str))

            if not job.cancelled and total_cmds > 1:
                job.add("================================================================================")
                job.add("BULK RUN FINISHED: %d of %d completed (%d passed, %d failed)" %
                        (job.completed_count, total_cmds, job.passed_count, job.failed_count))
                job.add("================================================================================")

            job.finish("cancelled" if job.cancelled else "done" if ok else "failed")
            self.current = None


# ---------------------------------------------------------------------------------------------------------------
# Models: what each has, and what can run on it
# ---------------------------------------------------------------------------------------------------------------

def model_dir(group, name):
    return os.path.join(layout.ROOT, group, name) if group else os.path.join(layout.ROOT, name)


def find_group(name):
    if layout is None: return None
    for g, m in layout.all_models():
        if m == name: return g
    return None


def doctor_report(src, name):
    """Geometry report for one source file. The file itself is not modified.

    An OBJ is read in this process. FBX and glTF need Blender, which writes
    work/doctor/<name>.json and does not overwrite the source.
    """
    ext = os.path.splitext(src)[1].lower()
    from autorig.core import mesh_doctor as _doc
    if ext == ".obj" or _doc.HAVE_BLENDER:
        return _doc.inspect_source_model(src)
    if blender.find(required=False) is None:
        return {
            "file": os.path.basename(src),
            "size_bytes": os.path.getsize(src),
            "grade": "UNCHECKED",
            "health_score": None,
            "note": "Blender is not available, so this %s file was not checked." % ext,
            "pass": None,
        }
    r = blender.run("mesh_doctor.py", name)
    report = os.path.join(layout.work_dir("doctor"), name + ".json")
    if r.returncode != 0 or not os.path.isfile(report):
        text = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
        picked = ""
        for line in reversed(text.splitlines()):
            if "DOCTOR_ERROR" in line or line.startswith("Error"):
                picked = line.split("DOCTOR_ERROR:", 1)[-1].strip()
                break
        if not picked:
            picked = text[-500:] if text else "mesh doctor failed"
        if getattr(r, "timed_out", False):
            picked = "Mesh Doctor timed out. " + picked
        raise ValueError(picked)
    with open(report, encoding="utf-8") as fh:
        diag = json.load(fh)
    if isinstance(diag, dict) and diag.get("error") and not diag.get("grade"):
        raise ValueError(str(diag["error"]))
    return diag


def export_manifest(name):
    """The winged archetype's export manifest beside the rig, if any (make_clips.py)."""
    import glob
    rd = _quiet(layout.rigged_dir, name)
    for m in glob.glob(os.path.join(glob.escape(rd), "*", "*.json")):
        try:
            man = json.load(open(m, encoding="utf-8"))
        except Exception:
            continue
        if str(man.get("format", "")).startswith("autorig-export/"):
            return m, man
    return None, None


def audit_file(name):
    p = os.path.join(layout.WORK, "audit", name + ".json")
    return p if os.path.exists(p) else None


_GRADES = {}


def audit_grade(path, overrides):
    """The model's grade from its audit JSON, re-read only when the file (or the spec's audit thresholds) changed:
    the model table asks for every model's grade on each refresh, and an audit with its tear sites runs large."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size, json.dumps(overrides, sort_keys=True, default=str))
    except (OSError, TypeError):
        key = None
    if key is not None and _GRADES.get(path, (None,))[0] == key:
        return _GRADES[path][1]
    try:
        with open(path, encoding="utf-8") as fh:
            g = grades.grade_of(json.load(fh)["verdict"], overrides) or "?"
    except Exception:
        g = "?"
    if key is not None: _GRADES[path] = (key, g)
    return g


def status(group, name):
    d = model_dir(group, name)
    out = {"name": name, "group": group}
    try:
        spec = spec_store.model(name)
        out["spec_error"] = None
    except Exception as e:
        spec, out["spec_error"] = {}, str(e)
    rig = spec.get("rig") or {}
    src = _quiet(layout.source_model, name)
    rd = _quiet(layout.rigged_dir, name)
    blend = os.path.join(rd, name + ".blend")
    fbx = os.path.join(rd, name + ".fbx")
    c_arch = (spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}).get("archetype")
    if not c_arch and spec_store:
        c_arch = spec_store.infer_clip_archetype(spec)
    out.update({
        "source": os.path.relpath(src, d).replace("\\", "/") if src else None,
        "source_ext": os.path.splitext(src)[1].lower() if src else None,
        "has_textures": bool(src and layout.has_own_textures(src)),
        "prepared": bool(src and layout.is_prepared(src)),
        "spec": bool(spec), "kind": rig.get("kind"), "rig_folder": os.path.basename(rd),
        "rigged": os.path.exists(blend), "rigged_fbx": os.path.exists(fbx),
        "clips_spec": c_arch,
        "clips": os.path.exists(os.path.join(d, "clips", name + "_clips.json")) or export_manifest(name)[0] is not None,
        "card": os.path.exists(os.path.join(d, "model.json")),
        "budget": _quiet(layout.budget, name) if spec or True else None,
        "audit": None,
    })
    a = audit_file(name)
    if a:
        out["audit"] = audit_grade(a, rig.get("audit"))
    out["preview"] = viewer_api.has_preview(layout, name)          # results viewer
    out["steps"] = availability(out, spec, d)
    return out


def availability(st, spec, d):
    """Each step: None when it can run, else why not (shown on the greyed-out button)."""
    rig = spec.get("rig") or {}
    no_src = "no source export (FBX, GLB, glTF or OBJ) in the model folder"
    why = {}
    why["prepare"] = None if _quiet(layout.raw_source, st["name"]) else no_src
    why["survey"] = None if st["source"] else no_src
    if st["spec_error"]:
        why["rig"] = "rig.json cannot be read: " + st["spec_error"]
    elif not rig:
        why["rig"] = "no rig spec yet: press Edit spec (or run Survey, read the facing views, and write rig.json: docs/SPEC.md)"
    elif rig.get("kind") == "custom" and not os.path.exists(os.path.join(d, rig.get("builder", ""))):
        why["rig"] = "the custom builder %s is not in the model folder" % rig.get("builder")
    elif not st["source"]:
        why["rig"] = no_src
    else:
        why["rig"] = None
    why["trim"] = None if (st["rigged"] or st["source"]) else "nothing to trim yet: run Rig"
    why["audit"] = None if st["rigged_fbx"] else "no rigged FBX yet: run Rig (and Trim)"
    if not st["clips_spec"]:
        why["clips"] = "no clip archetype in rig.json (\"clips\": {\"archetype\": ...})"
    elif not st["rigged"]:
        why["clips"] = "no rig yet: run Rig"
    else:
        why["clips"] = None
    why["rebake-clips"] = why["rebake_clips"] = why["clips"]
    why["publish"] = None if st["rigged_fbx"] else "no rigged FBX yet: run Rig"
    why["preview"] = None if (st["rigged"] or st["rigged_fbx"]) else "no rig yet: run Rig"     # results viewer
    why["all"] = why["rig"]
    m2m = (spec.get("m2m") or rig.get("m2m") or {}) if isinstance(spec, dict) else {}
    why["fix-skin"] = None if m2m.get("template") else "rig it on a Mesh2Motion skeleton first (Auto rig)"
    return why


def blender_cmd(script, *args):
    """The step's command line, with Python's output line-buffered so the log streams as it happens. Raises
    ValueError (a 400 with the reason) when Blender is not installed, instead of exiting the request's thread."""
    if not blender.find(required=False):
        raise ValueError("Blender not found: install Blender (tested on 5.2) or set AUTORIG_BLENDER")
    argv = blender.command(script, *args)
    return argv[:2] + ["--python-expr", "import sys; sys.stdout.reconfigure(line_buffering=True)"] + argv[2:]


def commands(group, name, step, spec):
    """The subprocesses one button runs, as (label, argv, prepare, model): the only commands the server ever starts.
    Which scripts a step runs comes from core/steps_registry.py, as for the CLI, batches and the API."""
    return [cmd_argv(c, name) for c in steps_registry.model_commands(
        step, name, group, spec, layout.WORK, model_dir(group, name), spec_store=spec_store, rmtree=shutil.rmtree)]


def cmd_argv(c, model=None):
    """A registry Cmd as the Runner's (label, argv, prepare, model)."""
    if c.runner == "blender":
        argv = blender_cmd(c.script, *c.args)
    else:
        argv = [sys.executable, "-u", c.script if os.path.isabs(c.script) else os.path.join(STEPS, c.script)] + list(c.args)
    return (c.label, argv, c.prepare, model)


def mocap_ok(p):
    """A mocap file the GUI may read: an existing .bvh/.fbx under the models root or the work folder (uploads land
    in <work>/mocap). Anything else on disk is refused, as /files/ refuses it."""
    try:
        import retargeter as _ret
    except ImportError:
        from autorig.core import retargeter as _ret
    return (bool(p) and os.path.isfile(p) and os.path.splitext(p)[1].lower() in (".bvh", ".fbx")
            and _ret.inside(p, [layout.ROOT, layout.WORK]))


def default_thresholds():
    """The audit's pass limits before any allowance (core/grades.py, which audit.py grades with too)."""
    return dict(grades.THRESHOLDS)



def rigged_models():
    out = []
    for g, n in layout.all_models():
        rd = _quiet(layout.rigged_dir, n)
        if not rd: continue
        if os.path.exists(os.path.join(rd, n + ".fbx")) or os.path.exists(os.path.join(rd, n + ".blend")):
            out.append((g, n))
    return out


def riggable_models():
    """All models ready to rig: has a rig spec, source exists, and no spec error."""
    out = []
    for g, n in layout.all_models():
        st = status(g, n)
        if st["steps"].get("rig") is None:
            out.append((g, n))
    return out


def _busy(app, *names):
    """ValueError when a job is running on one of these models (or on any, when no name is given)."""
    cur = app.runner.current
    if cur and (not names or set(names) & {cur.model, cur.current_model}):
        raise Busy("a step is running%s; cancel it or wait for it first" % (" on " + cur.current_model if names else ""))


class Busy(ValueError):
    pass


def _forget_grades(*names):
    for key in list(_GRADES):
        if os.path.splitext(os.path.basename(key))[0] in names:
            _GRADES.pop(key, None)


def failed_models():
    """All models whose latest audit failed (grade is FAIL or verdict pass is False)."""
    out = []
    for row in audit_table():
        if row.get("grade") == "FAIL" or row.get("pass") is False:
            out.append((row.get("group", ""), row["model"]))
    return out


def clippable_models():
    """All models ready for clips: is rigged, and has a clip archetype in rig.json."""
    if layout is None:
        return []
    out = []
    for g, n in layout.all_models():
        st = status(g, n)
        if st["steps"].get("clips") is None:
            out.append((g, n))
    return out


def sourced_models(missing_only=False):
    """Every model with a source mesh; with missing_only, those whose source view is not drawn yet."""
    work = os.path.join(layout.WORK, "source")
    out = []
    for g, n in layout.all_models():
        if not _quiet(layout.source_model, n): continue
        if missing_only and all(os.path.exists(os.path.join(work, n + e)) for e in (".glb", ".json")): continue
        out.append((g, n))
    return out


# ---- Batches over the collection (POST /api/v1/batch/{action}): one job, a step per model, going on past a
# failure, so a run over many models says at the end which ones failed

def _forgetting(work, n):                       # an audit that fails leaves no stale grade in the table
    p = os.path.join(work, n + ".json")
    return lambda: os.path.exists(p) and os.remove(p)


def _audit_cmds(ms):
    work = os.path.join(layout.WORK, "audit")
    os.makedirs(work, exist_ok=True)
    return [("audit %d/%d: %s" % (k, len(ms), n), blender_cmd("audit.py", "-model", n, "-out", work), _forgetting(work, n), n)
            for k, (g, n) in enumerate(ms, 1)]


def _rig_cmds(ms):
    return [("%d/%d: %s %s" % (k, len(ms), n, c[0]), c[1], c[2], n)
            for k, (g, n) in enumerate(ms, 1) for c in commands(g, n, "rig", spec_store.model(n))]


def _clips_cmds(ms):
    cmds = []
    for k, (g, n) in enumerate(ms, 1):
        pv = os.path.join(layout.WORK, "clips", n)
        cmds.append(("make clips %d/%d: %s" % (k, len(ms), n), blender_cmd("make_clips.py", n, "--preview", pv),
                     lambda pv=pv: shutil.rmtree(pv, ignore_errors=True), n))
        cmds.append(("preview %d/%d: %s" % (k, len(ms), n), blender_cmd("preview_glb.py", "-only", n), None, n))
    return cmds


def _source_cmds(ms):
    work = os.path.join(layout.WORK, "source")
    os.makedirs(work, exist_ok=True)
    return [("source view %d/%d: %s" % (k, len(ms), n), blender_cmd("source_preview.py", "-only", n, "-out", work), None, n)
            for k, (g, n) in enumerate(ms, 1)]


# action: (which models, as the job names them; picks them; their commands; why there are none)
BATCHES = {
    "audit-all": ("rigged models", lambda mo: rigged_models(), _audit_cmds, "no rigged models yet: rig one first"),
    "rig-all": ("rig-ready models", lambda mo: riggable_models(), _rig_cmds, "no models with a valid rig spec to rig"),
    "clips-all": ("models with clips", lambda mo: clippable_models(), _clips_cmds,
                  "no rigged models with a clip archetype in rig.json"),
    "audit-failed": ("failed models", lambda mo: failed_models(), _audit_cmds,
                     "no failed models to audit: all audited models pass or check"),
    "source-all": ("source views", sourced_models, _source_cmds, "no models with a source 3D file found"),
}


def batch_job(action, names=None, keep_going=True, missing_only=False):
    what, pick, make, empty = BATCHES[action]
    ms = pick(missing_only)
    if names is not None:
        want = {n.lower() for n in names}
        ms = [(g, n) for g, n in ms if n.lower() in want]
        if not ms: raise ValueError("none of those models is ready for %s (%s)" % (action, empty))
    elif not ms:
        raise ValueError(empty)
    job = Job("(%d %s)" % (len(ms), what if names is None else "selected models"), "", action, make(ms))
    job.keep_going = keep_going
    return job


def audit_table():
    """One row per model (cli/audit_all.py's columns), worst first; a model never audited has no grade."""
    rows = []
    for g, n in layout.all_models():
        rd = _quiet(layout.rigged_dir, n)
        fbx = os.path.join(rd, n + ".fbx") if rd else None
        blend = os.path.join(rd, n + ".blend") if rd else None
        has_fbx = bool(fbx and os.path.exists(fbx))
        has_blend = bool(blend and os.path.exists(blend))
        a = audit_file(n)
        try:
            full = json.load(open(a)) if a else None
        except Exception:
            full = None
        try:
            allow = (spec_store.model(n).get("rig") or {}).get("audit")
        except Exception:
            allow = None

        if not has_fbx and not has_blend:
            row_status = "unrigged"
        elif not a or full is None:
            row_status = "not audited"
        else:
            row_status = "audited"

        row = dict(grades.summary(n, full, allow), group=g, status=row_status)
        rows.append(row)
    return sorted(rows, key=grades.severity)


def details(group, name):
    import glob
    d = model_dir(group, name)
    st = status(group, name)
    try:
        spec = spec_store.model(name)
    except Exception:
        spec = {}
    work = layout.WORK

    def url(path):
        path = os.path.abspath(path)
        for base, pre in ((layout.ROOT, "models"), (work, "work")):
            if os.path.normcase(path).startswith(os.path.normcase(base + os.sep)):
                return "/files/%s/%s" % (pre, os.path.relpath(path, base).replace("\\", "/"))
        return None

    out = {"status": st, "dir": d, "spec": spec or None, "notes": spec.get("notes", {}) if spec else {}}
    prep_path = os.path.join(d, "prepared", "prepare.json")
    try:
        out["prepare"] = json.load(open(prep_path, encoding="utf-8")) if os.path.isfile(prep_path) else None
    except Exception:
        out["prepare"] = None
    sp = os.path.join(work, "survey", name + ".json")
    out["survey"] = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else None
    out["facing"] = [url(p) for p in sorted(glob.glob(os.path.join(glob.escape(work), "facing", glob.escape(name) + "__*.png")))]

    rd = _quiet(layout.rigged_dir, name)
    qa = [p for p in (os.path.join(work, "qa", name + ".png"), os.path.join(rd, name + "_qa.png")) if os.path.exists(p)]
    out["bend_test"] = url(qa[0]) if qa else None
    a = audit_file(name)
    out["audit"] = None
    if a:
        full = json.load(open(a))
        v = full.get("verdict", {})
        spec_allow = ((spec.get("rig") or {}).get("audit") or {}) if spec else {}
        reason = out["notes"].get("rig.audit")
        rows, base = [], default_thresholds()
        for k, c in v.get("checks", {}).items():
            rows.append({"check": k, "value": c.get("value"), "limit": c.get("limit"), "ok": c.get("ok"),
                         "grade": c.get("grade") or ("PASS" if c.get("ok") else "FAIL"), "check_limit": c.get("check_limit"),
                         "gap_pct": c.get("gap_pct"),
                         "threshold": base.get(k, c.get("limit")),
                         "allowance": spec_allow.get(k), "reason": reason if k in spec_allow else None})
        t = full.get("tears") or {}
        out["audit"] = {"pass": v.get("pass"), "grade": grades.grade_of(v, spec_allow), "checks": rows,
                        "warnings": v.get("warnings", []),
                        "tears": {k: t.get(k) for k in ("worst_gap_pct", "worst_bone", "bones_tearing", "by_bone")} if t else None,
                        "tear_sites": len(full.get("tear_sites") or []),
                        "skin": url(os.path.join(work, "audit", name + "_skin.png")) if os.path.exists(os.path.join(work, "audit", name + "_skin.png")) else None,
                        "bend": url(os.path.join(work, "audit", name + "_bend.png")) if os.path.exists(os.path.join(work, "audit", name + "_bend.png")) else None,
                        "fbx": full.get("fbx"),
                        "fbx_rel": os.path.relpath(full["fbx"], d).replace("\\", "/") if full.get("fbx") else None}
    frames = {}
    for p in sorted(glob.glob(os.path.join(glob.escape(work), "clips", glob.escape(name), "*.png"))):
        clip, _, f = os.path.splitext(os.path.basename(p))[0].rpartition("_")
        frames.setdefault(clip, []).append(url(p))
    out["clip_frames"] = frames
    cj = os.path.join(d, "clips", name + "_clips.json")
    man_path, man = export_manifest(name)
    if os.path.exists(cj):
        c = json.load(open(cj, encoding="utf-8"))
        out["clips"] = {"file": url(cj), "format": c.get("format"), "clips": [{"name": x["name"], "seconds": x.get("seconds"), "loops": x.get("loops")} for x in c.get("clips", [])]}
    elif man:
        out["clips"] = {"file": url(man_path), "format": man.get("format"), "clips": [{"name": x["name"], "seconds": x.get("length"), "loops": x.get("loop")} for x in man.get("clips", [])]}
    else:
        out["clips"] = None
    cp = os.path.join(d, "model.json")
    out["card"] = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else None
    return out


# ---------------------------------------------------------------------------------------------------------------
# Bringing models in
# ---------------------------------------------------------------------------------------------------------------

def collapse(dest):
    """A drop that is one folder holding everything (a zip's own top folder): lift its contents up a level."""
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])) and not entries[0].lower().endswith(".fbm"):
        inner = os.path.join(dest, entries[0])
        for e in os.listdir(inner): shutil.move(os.path.join(inner, e), os.path.join(dest, e))
        os.rmdir(inner)


def unzip_all(dest):
    for z in [os.path.join(r, f) for r, _, fs in os.walk(dest) for f in fs if f.lower().endswith(".zip")]:
        base = os.path.dirname(z)
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                rel = safe_rel(info.filename)
                if info.is_dir() or not rel or not wanted(rel) or rel.lower().endswith(".zip"): continue
                out = os.path.join(base, *rel.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with zf.open(info) as i, open(out, "wb") as o: shutil.copyfileobj(i, o)
        # the archive is kept beside what it held, as a download would be


def place(src, group, name, move):
    """Brings a staged folder (or a folder or files chosen on disk) into the models root as one model."""
    if group and not NAME.match(group): raise ValueError("a group name is letters, digits, _ and -")
    if not NAME.match(name): raise ValueError("a model name is letters, digits, _ and - (up to 64)")
    dest = model_dir(group, name)
    if os.path.exists(dest): raise ValueError("%s already exists in the models root" % os.path.relpath(dest, layout.ROOT))
    if find_group(name) is not None: raise ValueError("a model named %s already exists (names are unique)" % name)
    os.makedirs(dest)
    try:
        items = src if isinstance(src, list) else [src]
        for s in items:
            if os.path.isdir(s):
                for r, dirs, fs in os.walk(s):
                    for f in fs:
                        p = os.path.join(r, f)
                        rel = os.path.relpath(p, s).replace("\\", "/")
                        if not wanted(rel): continue
                        out = os.path.join(dest, *rel.split("/"))
                        os.makedirs(os.path.dirname(out), exist_ok=True)
                        (shutil.move if move else shutil.copy2)(p, out)
            elif os.path.isfile(s):
                if wanted(os.path.basename(s)):
                    shutil.copy2(s, os.path.join(dest, os.path.basename(s)))
                    fbm = os.path.splitext(s)[0] + ".fbm"          # an FBX's texture folder comes with it
                    if s.lower().endswith(".fbx") and os.path.isdir(fbm):
                        shutil.copytree(fbm, os.path.join(dest, os.path.basename(fbm)))
            else:
                raise ValueError("not found: %s" % s)
        collapse(dest)
        unzip_all(dest)
        spec_store.reload()
        if not _quiet(layout.source_model, name):
            raise ValueError("no FBX, GLB, glTF or OBJ among the files")
        return {"group": group, "name": name, "dir": dest,
                "files": sorted(os.path.relpath(os.path.join(r, f), dest).replace("\\", "/") for r, _, fs in os.walk(dest) for f in fs)}
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


SAMPLE_MODEL_NAMES = ("beetle", "biped", "canine", "wyvern", "pedestal")


def sample_models_list():
    """Returns summaries of the 5 built-in CC0 sample models."""
    out = []
    samples_dir = os.path.join(REPO, "samples")
    for slug in SAMPLE_MODEL_NAMES:
        d = os.path.join(samples_dir, slug)
        rj = os.path.join(d, "rig.json")
        spec = {}
        if os.path.exists(rj):
            try:
                with open(rj, encoding="utf-8") as fh:
                    spec = json.load(fh)
            except Exception:
                pass
        rig = spec.get("rig", {})
        clips = spec.get("clips", {})
        notes = spec.get("notes", {})
        card = spec.get("card", {})

        is_installed = find_group(slug) is not None
        out.append({
            "name": slug,
            "title": clips.get("display") or slug.capitalize(),
            "archetype": clips.get("archetype") or rig.get("skeleton") or card.get("role") or "creature",
            "kind": rig.get("kind", "placed"),
            "budget": spec.get("budget", 1500),
            "description": notes.get("rig") or f"CC0 {slug} sample model",
            "category": clips.get("category") or "Creatures",
            "installed": is_installed,
        })
    return out


def load_sample_model(slug):
    """Loads a built-in sample model into the models root."""
    if slug not in SAMPLE_MODEL_NAMES:
        raise ValueError(f"unknown sample model {slug} (valid: {', '.join(SAMPLE_MODEL_NAMES)})")
    if find_group(slug) is not None:
        return {"name": slug, "loaded": True, "already_installed": True, "group": find_group(slug)}
    src = os.path.join(REPO, "samples", slug)
    if not os.path.isdir(src):
        raise ValueError(f"sample directory missing for {slug}")
    res = place(src, "", slug, move=False)
    spec_store.reload()
    return {"name": slug, "loaded": True, "already_installed": False, "group": res.get("group", "")}


# ---------------------------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------------------------

class App:
    def __init__(self, port):
        self.token = secrets.token_urlsafe(18)
        self.port = port
        env = dict(os.environ, AUTORIG_MODELS=layout.ROOT, AUTORIG_WORK=layout.WORK, PYTHONUNBUFFERED="1")
        self.runner = Runner(env)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutorigWorkbench/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if os.environ.get("AUTORIG_HTTP_LOG"): sys.stderr.write("%s\n" % (fmt % args))

        # ---- plumbing
        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _token_ok(self, q):
            auth = self.headers.get("Authorization") or ""
            bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            t = self.headers.get("X-Autorig-Token") or bearer or (q.get("t") or [""])[0]
            return secrets.compare_digest(t.encode("utf-8"), app.token.encode("utf-8"))

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            try:
                if isinstance(body, (dict, list)): body = json.dumps(body)
                if isinstance(body, str): body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if "Cache-Control" not in (extra or {}): self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items(): self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD": self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _guard(self, q):
            if not self._host_ok():
                self._send(403, {"error": "bad host"}); return False
            if not self._token_ok(q):
                self._send(403, {"error": "missing or wrong session token: open the link the server printed"}); return False
            return True

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query); path = u.path
            if path == "/":
                if not self._host_ok() or not self._token_ok(q):
                    return self._send(403, "<p>Open the link Autorig Workbench printed in its console (it carries a "
                                           "session token).</p>", "text/html; charset=utf-8")
                with open(os.path.join(HERE, "index.html"), encoding="utf-8") as fh:
                    page = fh.read().replace("__AUTORIG_TOKEN__", app.token)
                page = page.replace('src="/app.js"', 'src="/app.js?v=%s"' % code_version())
                return self._send(200, page, "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; connect-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            # ---- Mesh2Motion (third_party/mesh2motion-app, built by scripts/build_m2m.py): open-source pages
            # and CC0 rigs and clips, so like /vendor/ they need only the Host check
            if path == "/m2m" or path.startswith("/m2m/"):
                if not self._host_ok(): return self._send(403, {"error": "bad host"})
                return self.m2m_file(path)
            if api_v1.handle(self, app, sys.modules[__name__], "GET", path, q): return
            if path in ("/app.js", "/viewer.js"):                  # the page's code: static, like /m2m/
                if not self._host_ok(): return self._send(403, {"error": "bad host"})
                with open(os.path.join(HERE, path[1:]), "rb") as fh:
                    code = fh.read()
                # the page asks for /app.js?v=<version> and app.js for /viewer.js?v=<version>: a new version is a new
                # address, so nothing in between (Cloudflare keeps .js four hours whatever we say) serves old code
                code = code.replace(b"from '/viewer.js'", b"from '/viewer.js?v=" + code_version().encode() + b"'")
                versioned = bool(q.get("v"))
                return self._send(200, code, "text/javascript; charset=utf-8",
                                  {"Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache"})
            if path.startswith("/vendor/"):                       # three.js for the preview (vendor/three/VERSION.txt)
                if not self._host_ok(): return self._send(403, {"error": "bad host"})
                rel = safe_rel(unquote(path[len("/vendor/"):]))
                p = os.path.realpath(os.path.join(HERE, "vendor", *rel.split("/"))) if rel else ""
                root = os.path.realpath(os.path.join(HERE, "vendor"))
                if not rel or not p.startswith(root + os.sep) or not p.endswith(".js") or not os.path.isfile(p):
                    return self._send(404, {"error": "not found"})
                with open(p, "rb") as fh:
                    return self._send(200, fh.read(), "text/javascript; charset=utf-8", {"Cache-Control": "public, max-age=86400"})
            if not self._guard(q): return
            # the model's and the work folder's files, which GET /api/v1/models/{m} links to (renders, audits)
            m = re.match(r"^/files/(models|work)/(.+)$", path)
            if m: return self.file(m.group(1), unquote(m.group(2)), versioned=bool(q.get("v")))
            self._send(404, {"error": "not found: the API is /api/v1 (GET /api/v1 lists its routes)"})

        def events(self, jid, start):
            job = app.runner.jobs.get(jid)
            if not job: return self._send(404, {"error": "no such job"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            i = max(0, start)
            try:                 # a browser reconnecting after a dropped stream says the last line it had
                i = max(i, int(self.headers.get("Last-Event-ID", "")) + 1)
            except ValueError:
                pass
            try:
                while True:
                    with job.cond:
                        while i >= len(job.lines) and job.state in ("queued", "running"):
                            if not job.cond.wait(timeout=15): break
                        new = job.lines[i:]; state = job.state
                    for line in new:
                        self.wfile.write(("id: %d\ndata: %s\n\n" % (i, json.dumps(line))).encode("utf-8")); i += 1
                    if not new:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    if state not in ("queued", "running") and i >= len(job.lines):
                        self.wfile.write(("event: end\ndata: %s\n\n" % json.dumps(job.info())).encode("utf-8"))
                        self.wfile.flush()
                        return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def m2m_file(self, path):
            if path in ("/m2m", "/m2m/"):
                return self._send(302, b"", "text/plain", {"Location": "/m2m/create.html"})
            if not os.path.isdir(M2M_DIR):
                return self._send(404, {"error": "Mesh2Motion is not built: run python scripts/build_m2m.py"})
            rel = safe_rel(unquote(path[len("/m2m/"):]))
            if not rel: return self._send(400, {"error": "bad path"})
            p = os.path.realpath(os.path.join(M2M_DIR, *rel.split("/")))
            root = os.path.realpath(M2M_DIR)
            if os.path.commonpath([os.path.normcase(p), os.path.normcase(root)]) != os.path.normcase(root):
                return self._send(403, {"error": "outside the Mesh2Motion folder"})
            if os.path.isdir(p): p = os.path.join(p, "index.html")
            if not os.path.isfile(p): return self._send(404, {"error": "no such file"})
            ctype = M2M_TYPES.get(os.path.splitext(p)[1].lower()) or mimetypes.guess_type(p)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"): ctype += "; charset=utf-8"
            # pages are checked every time (a rebuild renames the code they load); assets/ names carry a content
            # hash, so they never change; rigs, clips and icons may change with a rebuild
            if p.lower().endswith(".html"): cache = "no-cache"
            elif "/assets/" in rel and "/" not in rel.split("assets/", 1)[1]: cache = "public, max-age=31536000, immutable"
            else: cache = "public, max-age=3600"
            with open(p, "rb") as fh: data = fh.read()
            self._send(200, data, ctype, {"Cache-Control": cache})

        def file(self, base, rel, versioned=False):
            root = layout.ROOT if base == "models" else layout.WORK
            rel = safe_rel(rel)
            if not rel: return self._send(400, {"error": "bad path"})
            p = os.path.realpath(os.path.join(root, *rel.split("/")))
            if os.path.commonpath([os.path.normcase(p), os.path.normcase(os.path.realpath(root))]) != os.path.normcase(os.path.realpath(root)):
                return self._send(403, {"error": "outside the served folders"})
            if not os.path.isfile(p): return self._send(404, {"error": "no such file"})
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            if ctype not in ("image/png", "image/jpeg", "application/json", "text/plain", "image/webp", "application/zip", "model/gltf-binary"):
                ctype = "application/octet-stream"
            extra = {}
            if versioned:        # a ?v=<mtime> URL names one version of the file: the browser may keep it
                extra["Cache-Control"] = "private, max-age=31536000, immutable"
            if p.lower().endswith(".zip"):
                ctype = "application/zip"
                extra["Content-Disposition"] = f'attachment; filename="{os.path.basename(p)}"'
            with open(p, "rb") as fh: data = fh.read()
            self._send(200, data, ctype, extra=extra)

        # ---- PUT and DELETE: the API only (gui/api_v1.py)
        def do_PUT(self):
            u = urlparse(self.path)
            if not api_v1.handle(self, app, sys.modules[__name__], "PUT", u.path, parse_qs(u.query)):
                self._send(405, {"error": "method not allowed"})

        def do_DELETE(self):
            u = urlparse(self.path)
            if not api_v1.handle(self, app, sys.modules[__name__], "DELETE", u.path, parse_qs(u.query)):
                self._send(405, {"error": "method not allowed"})

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query); path = u.path
            if api_v1.handle(self, app, sys.modules[__name__], "POST", path, q): return
            if self._guard(q):
                self._send(404, {"error": "not found: the API is /api/v1 (GET /api/v1 lists its routes)"})

    return Handler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        cls, val, tb = sys.exc_info()
        if cls in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        super().handle_error(request, client_address)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m autorig", description="Autorig Workbench: the local GUI.")
    ap.add_argument("--models", help="models root (default: AUTORIG_MODELS, else the repo's samples/)")
    ap.add_argument("--work", help="work folder (default: AUTORIG_WORK, else <models>/_autorig)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AUTORIG_PORT") or 0), help="port (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--token", default=os.environ.get("AUTORIG_TOKEN"), help="session token (default: a random one)")
    ap.add_argument("--allow-any-folder", action="store_true",
                    help="let the page open and browse any folder (a copy only you use; core/server_config.py)")
    a = ap.parse_args(argv)
    if a.allow_any_folder: os.environ["AUTORIG_ALLOW_ANY_FOLDER"] = "1"

    global layout, spec_store, blender, grades, exporter
    sys.path.insert(0, CORE)
    import layout as _l, spec_store as _s, blender as _b, grades as _g, exporter as _e
    layout, spec_store, blender, grades, exporter = _l, _s, _b, _g, _e

    # --models, else AUTORIG_MODELS, else the root the GUI had last, else samples/ (layout's default). Only a root
    # picked in the GUI becomes the one it opens next time.
    if a.models:
        layout.set_root(a.models, a.work, create=True, remember=False)
    elif a.work:
        layout.set_root(layout.ROOT, a.work, create=True, remember=False)
    elif "AUTORIG_MODELS" not in os.environ and layout.active_root():
        layout.set_root(*layout.active_root())
    spec_store.reload()

    os.makedirs(layout.ROOT, exist_ok=True)
    os.makedirs(layout.WORK, exist_ok=True)

    httpd = QuietThreadingHTTPServer(("127.0.0.1", a.port), None)
    httpd.daemon_threads = True
    app = App(httpd.server_address[1])
    if a.token: app.token = a.token
    app.start_root = layout.ROOT                      # always one of the collections (gui/api_v1.py)
    httpd.RequestHandlerClass = make_handler(app)
    url = "http://127.0.0.1:%d/?t=%s" % (app.port, app.token)
    b_exe = blender.find(required=False)
    b_ver = blender.version_string(b_exe) if b_exe else ""
    print("Autorig Workbench")
    print("  models  %s" % layout.ROOT)
    print("  work    %s" % layout.WORK)
    print("  blender %s%s" % (b_exe or "NOT FOUND (set AUTORIG_BLENDER)", f" ({b_ver})" if b_ver else ""))
    b_warn = blender.version_warning(b_exe) if b_exe else None
    if b_warn:
        print("  WARNING: %s" % b_warn)
    print("  open    %s" % url, flush=True)
    url_file = os.environ.get("AUTORIG_URL_FILE")
    if url_file:
        with open(url_file, "w") as fh: fh.write(url)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cur = app.runner.current
        if cur and cur.proc and cur.proc.poll() is None:
            cur.proc.kill()
        httpd.server_close()


if __name__ == "__main__":
    main()

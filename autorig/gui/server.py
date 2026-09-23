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
# from under the models root and the work folder. Cancel kills the running step's own process by its PID, never
# anything by image name.
import argparse, contextlib, io, json, mimetypes, os, queue, re, secrets, shutil, subprocess, sys, threading, time
import webbrowser, zipfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
try:
    from . import viewer_api                      # the results viewer's routes (gui/viewer_api.py)
    from . import spec_api                        # the spec editor's routes (gui/spec_api.py)
except ImportError:
    import viewer_api
    import spec_api

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
CORE, STEPS, CLI = (os.path.join(PKG, d) for d in ("core", "steps", "cli"))

# What may be brought into the models root: sources and their textures, archives, the model's own data.
UPLOAD_EXTS = {".fbx", ".glb", ".gltf", ".bin", ".obj", ".mtl", ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif",
               ".tiff", ".webp", ".zip", ".json", ".py"}
SKIP_FILES = {"model.json", "pack.json"}          # outputs: publish writes them again
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
STEP_NAMES = ("survey", "rig", "trim", "audit", "clips", "publish", "preview", "all")
TAG = re.compile(r"^[A-Z][A-Z0-9_]+ ")

layout = spec_store = blender = grades = exporter = None    # imported in main(), once the environment names the folders


def _quiet(fn, *a):
    """Calls a layout helper without its LAYOUT notes on the server's console."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


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
                "pid": self.pid, "lines": len(self.lines), "started": self.started, "ended": self.ended}


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
            job.add("== cancelled: killing PID %d" % p.pid)
            p.kill()                     # this process only: TerminateProcess / SIGKILL on its own PID
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
            for label, argv, prep in job.cmds:
                if job.cancelled: break
                if prep: prep()
                job.add("== %s" % label)
                job.add("   " + " ".join('"%s"' % a if " " in a else a for a in argv))
                try:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env=self.env, cwd=REPO, text=True, encoding="utf-8", errors="replace",
                                         bufsize=1, creationflags=flags)
                except OSError as e:
                    job.add("!! could not start: %r" % e); ok = False; break
                job.proc, job.pid = p, p.pid
                failed = False
                for line in p.stdout:
                    job.add(line)
                    if line.startswith("Traceback") or (TAG.match(line) and '"error"' in line):
                        failed = True
                rc = p.wait()
                job.proc = None
                if job.cancelled: break
                if rc != 0 or failed:
                    job.add("!! %s failed (exit code %s)" % (label, rc)); ok = False
                    if job.keep_going: continue
                    break
                job.add("== %s done" % label)
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
    out.update({
        "source": os.path.relpath(src, d).replace("\\", "/") if src else None,
        "spec": bool(spec), "kind": rig.get("kind"), "rig_folder": os.path.basename(rd),
        "rigged": os.path.exists(blend), "rigged_fbx": os.path.exists(fbx),
        "clips_spec": (spec.get("clips") or {}).get("archetype"),
        "clips": os.path.exists(os.path.join(d, "clips", name + "_clips.json")) or export_manifest(name)[0] is not None,
        "card": os.path.exists(os.path.join(d, "model.json")),
        "budget": _quiet(layout.budget, name) if spec or True else None,
        "audit": None,
    })
    a = audit_file(name)
    if a:
        try:
            out["audit"] = grades.grade_of(json.load(open(a))["verdict"], rig.get("audit")) or "?"
        except Exception:
            out["audit"] = "?"
    out["preview"] = viewer_api.has_preview(layout, name)          # results viewer
    out["steps"] = availability(out, spec, d)
    return out


def availability(st, spec, d):
    """Each step: None when it can run, else why not (shown on the greyed-out button)."""
    rig = spec.get("rig") or {}
    no_src = "no source export (FBX, GLB, glTF or OBJ) in the model folder"
    why = {}
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
    why["publish"] = None if st["rigged_fbx"] else "no rigged FBX yet: run Rig"
    why["preview"] = None if (st["rigged"] or st["rigged_fbx"]) else "no rig yet: run Rig"     # results viewer
    why["all"] = why["rig"]
    return why


def blender_cmd(script, *args):
    """The step's command line, with Python's output line-buffered so the log streams as it happens."""
    argv = blender.command(script, *args)
    return argv[:2] + ["--python-expr", "import sys; sys.stdout.reconfigure(line_buffering=True)"] + argv[2:]


def commands(group, name, step, spec):
    """The subprocesses one button runs, as (label, argv, prepare) - the only commands the server ever starts."""
    work = layout.WORK
    py = [sys.executable, "-u"]
    rig = spec.get("rig") or {}

    def survey():
        return [("survey", blender_cmd("survey.py", "-only", name), None),
                ("facing views", blender_cmd("facing.py", "-only", name, "-out", os.path.join(work, "facing")), None)]

    def rig_step():
        if rig.get("kind") == "humanoid": script = "rerig_humanoid.py"
        elif rig.get("kind") == "custom": script = os.path.join(model_dir(group, name), rig["builder"])
        else: script = "rerig.py"
        return [("rig (%s)" % (rig.get("kind") or "?"), blender_cmd(script, "-only", name, "-qa", os.path.join(work, "qa")), None)]

    def trim():
        return [("trim to budget", blender_cmd("decimate.py", "-only", name), None)]

    def audit():
        return [("audit", blender_cmd("audit.py", "-model", name, "-out", os.path.join(work, "audit")), None)]

    def clips():
        pv = os.path.join(work, "clips", name)
        return [("make clips (%s)" % (spec.get("clips") or {}).get("archetype"),
                 blender_cmd("make_clips.py", name, "--preview", pv), lambda: shutil.rmtree(pv, ignore_errors=True))]

    def publish():
        return [("publish card", py + [os.path.join(STEPS, "publish.py"), group or ".", "-only", name], None)]

    def preview():                                                # results viewer: rigged/preview.glb
        return [("preview for the viewer", blender_cmd("preview_glb.py", "-only", name), None)]

    if step == "survey": return survey()
    if step == "rig": return rig_step()
    if step == "trim": return trim()
    if step == "audit": return audit()
    if step == "clips": return clips()
    if step == "publish": return publish()
    if step == "preview": return preview()
    if step == "all":
        # the card goes before the clips (they read its size) and again after (it lists them)
        out = rig_step() + trim() + audit() + publish()
        if (spec.get("clips") or {}).get("archetype"): out += clips() + publish()
        return out + preview()                                    # the viewer's copy, last: it carries the clips
    raise ValueError(step)


def default_thresholds():
    """The audit's pass limits before any allowance (core/grades.py, which audit.py grades with too)."""
    return dict(grades.THRESHOLDS)


# ---- Audit all: every rigged model through audit.py, one Blender each, then a table worst first

def rigged_models():
    return [(g, n) for g, n in layout.all_models() if os.path.exists(os.path.join(_quiet(layout.rigged_dir, n), n + ".fbx"))]


def audit_all_job():
    work = os.path.join(layout.WORK, "audit")
    ms = rigged_models()
    if not ms: raise ValueError("no rigged models yet: rig one first")
    def forget(n):                                    # an audit that fails leaves no stale grade in the table
        return lambda: os.path.exists(os.path.join(work, n + ".json")) and os.remove(os.path.join(work, n + ".json"))
    cmds = [("audit %d/%d: %s" % (k, len(ms), n), blender_cmd("audit.py", "-model", n, "-out", work), forget(n))
            for k, (g, n) in enumerate(ms, 1)]
    job = Job("(all rigged models)", "", "audit-all", cmds)
    job.keep_going = True
    return job


def audit_table():
    """One row per rigged model (cli/audit_all.py's columns), worst first; a model never audited has no grade."""
    rows = []
    for g, n in rigged_models():
        a = audit_file(n)
        try:
            full = json.load(open(a)) if a else None
        except Exception:
            full = None
        try:
            allow = (spec_store.model(n).get("rig") or {}).get("audit")
        except Exception:
            allow = None
        rows.append(dict(grades.summary(n, full, allow), group=g))
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

class Uploads:
    """Files dropped on the page arrive one request each, grouped by a batch id, into a staging folder under the
    work folder; the batch is then moved into <root>/<group>/<name>/ in one step, zips unpacked."""

    def __init__(self):
        self.lock = threading.Lock()

    def stage_dir(self, batch):
        return os.path.join(layout.WORK, "_incoming", batch)

    def put(self, batch, rel, stream, length):
        if not re.match(r"^[A-Za-z0-9]{8,40}$", batch): raise ValueError("bad batch id")
        rel = safe_rel(rel)
        if not rel: raise ValueError("bad path")
        if not wanted(rel): return False
        dest = os.path.join(self.stage_dir(batch), *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        left = length
        with open(dest, "wb") as fh:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk: break
                fh.write(chunk); left -= len(chunk)
        return True

    def finish(self, batch, group, name):
        src = self.stage_dir(batch)
        if not os.path.isdir(src): raise ValueError("nothing was uploaded")
        try:
            return place(src, group, name, move=True)
        finally:
            shutil.rmtree(src, ignore_errors=True)


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
        thumb_rel = f"_autorig/qa/{slug}.png"
        thumb_path = os.path.join(samples_dir, "_autorig", "qa", f"{slug}.png")
        out.append({
            "name": slug,
            "title": clips.get("display") or slug.capitalize(),
            "archetype": clips.get("archetype") or rig.get("skeleton") or card.get("role") or "creature",
            "kind": rig.get("kind", "placed"),
            "budget": spec.get("budget", 1500),
            "description": notes.get("rig") or f"CC0 {slug} sample model",
            "category": clips.get("category") or "Creatures",
            "installed": is_installed,
            "thumbnail": f"/samples/{thumb_rel}" if os.path.exists(thumb_path) else None,
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


def open_folder(path):
    if sys.platform.startswith("win"): os.startfile(path)                                   # noqa
    elif sys.platform == "darwin": subprocess.Popen(["open", path])
    else: subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------------------------

class App:
    def __init__(self, port):
        self.token = secrets.token_urlsafe(18)
        self.port = port
        env = dict(os.environ, AUTORIG_MODELS=layout.ROOT, AUTORIG_WORK=layout.WORK, PYTHONUNBUFFERED="1")
        self.runner = Runner(env)
        self.uploads = Uploads()
        self.page = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()


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
            t = self.headers.get("X-Autorig-Token") or (q.get("t") or [""])[0]
            return secrets.compare_digest(t, app.token)

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)): body = json.dumps(body)
            if isinstance(body, str): body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items(): self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(body)

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
                page = app.page.replace("__AUTORIG_TOKEN__", app.token)
                return self._send(200, page, "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            # ---- results viewer (gui/viewer_api.py): its code and vendored three.js need only the Host check
            if path in viewer_api.CODE or path.startswith("/vendor/"):
                if not self._host_ok(): return self._send(403, {"error": "bad host"})
                f = viewer_api.static_file(path)
                return self._send(200, f[0], f[1]) if f else self._send(404, {"error": "not found"})
            if path == "/spec_editor.js":                         # the spec editor (gui/spec_api.py)
                if not self._host_ok(): return self._send(403, {"error": "bad host"})
                f = spec_api.static_file(path)
                return self._send(200, f[0], f[1])
            if path == "/spec_editor.html":
                if not self._guard(q): return
                return self._send(200, spec_api.page(app.token), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": spec_api.CSP})
            if path == "/viewer.html":
                if not self._guard(q): return
                return self._send(200, viewer_api.page(app.token), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": viewer_api.CSP})
            if path in ("/help.html", "/help"):
                if not self._guard(q): return
                help_path = os.path.join(HERE, "help.html")
                if os.path.exists(help_path):
                    with open(help_path, "r", encoding="utf-8") as fh:
                        page = fh.read().replace("__AUTORIG_TOKEN__", app.token)
                    return self._send(200, page, "text/html; charset=utf-8",
                                      {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
                return self._send(404, {"error": "help page not found"})
            if not self._guard(q): return
            try:
                if path == "/api/previews":
                    spec_store.reload()
                    return self._send(200, {"models": viewer_api.previews(layout, _quiet)})
                if path == "/api/state": return self._send(200, self.state())
                if path == "/api/samples":
                    return self._send(200, {"samples": sample_models_list()})
                if path == "/api/export/targets":
                    try:
                        import exporter as _exp
                    except ImportError:
                        from autorig.core import exporter as _exp
                    return self._send(200, {"targets": _exp.list_presets()})
                if path == "/api/doctor":
                    name = (q.get("model") or q.get("name") or [""])[0]
                    g = find_group(name)
                    if g is None: return self._send(404, {"error": "no model " + name})
                    src = layout.source_model(name)
                    if not src: return self._send(404, {"error": "no source 3D model found"})
                    try:
                        import mesh_doctor as _doc
                    except ImportError:
                        from autorig.core import mesh_doctor as _doc
                    return self._send(200, _doc.inspect_source_model(src))
                if path == "/api/audits":                           # the collection table (Audit all)
                    spec_store.reload()
                    return self._send(200, {"models": audit_table()})
                if path == "/api/audit":                            # one model's whole audit: tear sites for a viewer
                    name = (q.get("name") or [""])[0]
                    a = audit_file(name) if find_group(name) is not None else None
                    if not a: return self._send(404, {"error": "no audit for " + name})
                    with open(a) as fh: return self._send(200, json.load(fh))
                if spec_api.get(self, app, sys.modules[__name__], path, q): return
                if path == "/api/model":
                    name = (q.get("name") or [""])[0]
                    g = find_group(name)
                    if g is None: return self._send(404, {"error": "no model " + name})
                    spec_store.reload()
                    return self._send(200, details(g, name))
                m = re.match(r"^/api/jobs/(\d+)/events$", path)
                if m:
                    lei = self.headers.get("Last-Event-ID")          # a reconnect resumes after the last line seen
                    start = int(lei) + 1 if lei and lei.isdigit() else int((q.get("from") or ["0"])[0])
                    return self.events(int(m.group(1)), start)
                m = re.match(r"^/api/jobs/(\d+)$", path)
                if m:
                    j = app.runner.jobs.get(int(m.group(1)))
                    if not j: return self._send(404, {"error": "no such job"})
                    return self._send(200, dict(j.info(), log=j.lines))
                m = re.match(r"^/files/(models|work)/(.+)$", path)
                if m: return self.file(m.group(1), unquote(m.group(2)))
                m_sample = re.match(r"^/samples/(.+)$", path)
                if m_sample:
                    samples_root = os.path.join(REPO, "samples")
                    rel = safe_rel(unquote(m_sample.group(1)))
                    if not rel: return self._send(400, {"error": "bad path"})
                    p = os.path.realpath(os.path.join(samples_root, *rel.split("/")))
                    if os.path.commonpath([os.path.normcase(p), os.path.normcase(os.path.realpath(samples_root))]) != os.path.normcase(os.path.realpath(samples_root)):
                        return self._send(403, {"error": "outside samples folder"})
                    if not os.path.isfile(p): return self._send(404, {"error": "no such file"})
                    ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                    with open(p, "rb") as fh:
                        return self._send(200, fh.read(), ctype)
                self._send(404, {"error": "not found"})
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def state(self):
            spec_store.reload()
            models = [status(g, n) for g, n in layout.all_models()]
            cur = app.runner.current
            b_path = blender.find(required=False)
            return {"root": layout.ROOT, "work": layout.WORK, "blender": b_path,
                    "blender_version": blender.version_string(b_path) if b_path else None,
                    "blender_warning": blender.version_warning(b_path) if b_path else None,
                    "models": models, "groups": layout.groups(),
                    "jobs": [j.info() for j in sorted(app.runner.jobs.values(), key=lambda j: -j.id)[:20]],
                    "running": cur.info() if cur else None}

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

        def file(self, base, rel):
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
            if p.lower().endswith(".zip"):
                ctype = "application/zip"
                extra["Content-Disposition"] = f'attachment; filename="{os.path.basename(p)}"'
            with open(p, "rb") as fh: data = fh.read()
            self._send(200, data, ctype, extra=extra)

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query); path = u.path
            if not self._guard(q): return
            try:
                if path == "/api/upload":
                    n = int(self.headers.get("Content-Length") or 0)
                    ok = app.uploads.put((q.get("batch") or [""])[0], (q.get("path") or [""])[0], self.rfile, n)
                    return self._send(200, {"stored": ok})
                body = self._json_body()
                if path == "/api/upload/done":
                    r = app.uploads.finish(body.get("batch", ""), body.get("group", ""), clean_name(body.get("name", "")))
                    return self._send(200, r)
                if path == "/api/import":
                    paths = [p.strip().strip('"') for p in body.get("paths", []) if p.strip()]
                    if not paths: return self._send(400, {"error": "no paths"})
                    name = clean_name(body.get("name") or os.path.splitext(os.path.basename(paths[0].rstrip("/\\")))[0])
                    return self._send(200, place([os.path.abspath(p) for p in paths], body.get("group", ""), name, move=False))
                if spec_api.post(self, app, sys.modules[__name__], path, body): return
                if path == "/api/run":
                    name, step = body.get("model", ""), body.get("step", "")
                    if step not in STEP_NAMES: return self._send(400, {"error": "unknown step"})
                    g = find_group(name)
                    if g is None: return self._send(404, {"error": "no model " + name})
                    spec_store.reload()
                    st = status(g, name)
                    if st["steps"].get(step): return self._send(409, {"error": st["steps"][step]})
                    job = app.runner.submit(Job(name, g, step, commands(g, name, step, spec_store.model(name))))
                    return self._send(200, job.info())
                if path == "/api/audit-all":
                    if app.runner.current: return self._send(409, {"error": "a step is running"})
                    return self._send(200, app.runner.submit(audit_all_job()).info())
                if path == "/api/cancel":
                    j = app.runner.jobs.get(int(body.get("job", 0)))
                    if not j: return self._send(404, {"error": "no such job"})
                    app.runner.cancel(j)
                    return self._send(200, j.info())
                if path == "/api/open":
                    g = find_group(body.get("model", ""))
                    if g is None: return self._send(404, {"error": "no such model"})
                    open_folder(model_dir(g, body["model"]))
                    return self._send(200, {"opened": model_dir(g, body["model"])})
                if path == "/api/samples/load":
                    slug = clean_name(body.get("name", ""))
                    return self._send(200, load_sample_model(slug))
                if path == "/api/export":
                    name = clean_name(body.get("model", ""))
                    g = find_group(name)
                    if g is None: return self._send(404, {"error": "no such model"})
                    target = body.get("target", "all")
                    try:
                        import exporter as _exp
                    except ImportError:
                        from autorig.core import exporter as _exp
                    res = _exp.create_export_package(name, target=target)
                    return self._send(200, res)
                self._send(404, {"error": "not found"})
            except ValueError as e:
                self._send(400, {"error": str(e)})
            except Exception as e:
                self._send(500, {"error": repr(e)})

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m autorig", description="Autorig Workbench: the local GUI.")
    ap.add_argument("--models", help="models root (default: AUTORIG_MODELS, else the repo's samples/)")
    ap.add_argument("--work", help="work folder (default: AUTORIG_WORK, else <models>/_autorig)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AUTORIG_PORT") or 0), help="port (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--token", default=os.environ.get("AUTORIG_TOKEN"), help="session token (default: a random one)")
    a = ap.parse_args(argv)
    if a.models: os.environ["AUTORIG_MODELS"] = os.path.abspath(a.models)
    if a.work: os.environ["AUTORIG_WORK"] = os.path.abspath(a.work)

    global layout, spec_store, blender, grades, exporter
    sys.path.insert(0, CORE)
    import layout as _l, spec_store as _s, blender as _b, grades as _g, exporter as _e
    layout, spec_store, blender, grades, exporter = _l, _s, _b, _g, _e
    os.makedirs(layout.ROOT, exist_ok=True)
    os.makedirs(layout.WORK, exist_ok=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), None)
    httpd.daemon_threads = True
    app = App(httpd.server_address[1])
    if a.token: app.token = a.token
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

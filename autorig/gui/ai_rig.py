# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: AI Assist > Fix this rig. The AI reads one model's audit, its rig.json, the survey and the QA
# pictures; tries candidate specs on a scratch copy of the model (rig, trim and audit there, on this computer, free);
# and proposes the rig.json it found best. Nothing changes until the person applies the plan, which saves rig.json
# (the old one kept for Undo) and re-rigs through the usual job queue.

import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import time

try:
    from . import spec_api
except ImportError:
    import spec_api

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
MAX_IMAGE_BYTES = 4_500_000          # the providers take about 5 MB a picture
MAX_SIDE = 1568                      # larger pictures are scaled down first (when Pillow is there) to save tokens
TRIAL_TIMEOUT = 1800

SYSTEM = """You are the rigging assistant inside Autorig Workbench, a tool that rigs 3D models for games from a spec \
file (rig.json) and grades each rig with an audit. You are working on one model (named below). Your job: make its \
rig better, by changing its rig.json.

How to work:
- Look first. get_audit says what failed and where it tears (bones, bend or twist, how many torn edges, the widest gap \
as a % of the model). get_spec is the current rig.json. view_picture shows the bend test, the skin sheet and the QA \
render; list_pictures says what there is. spec_reference quotes the rig.json documentation (coordinates are 0..1 of \
the model's box, facing -Y, Z up).
- Then try. trial_rig rigs a candidate rig.json on a scratch copy of the model and returns its audit, without \
touching the real one. It runs on the person's computer and takes seconds to minutes. Change one or two things at a \
time and compare against the current audit; keep what helps.
- Then propose. Call propose_spec once with the best rig.json you found (the whole file) and a one-line summary. It \
does not change anything: it adds the spec to a plan the person reads and applies. Never say a change has been made.
- If nothing you tried beat the current rig, say so plainly and don't propose.

Grades: PASS is within every limit, CHECK is past a limit but inside its warn band, FAIL is clearly broken. Tears \
(edges that stretch open when a bone bends or twists) matter most; then weight bleed and the head's share.
Keep replies short and plain: what was wrong, what you changed, and the audit before and after."""


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _picture(path):
    """{"media_type", "data"} for one PNG or JPEG, scaled down when it is large."""
    ext = os.path.splitext(path)[1].lower()
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext)
    if not media:
        raise ValueError("only PNG and JPEG pictures can be shown")
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data))
        if max(im.size) > MAX_SIDE or len(data) > MAX_IMAGE_BYTES:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=88)
            data, media = buf.getvalue(), "image/jpeg"
    except ImportError:
        pass
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("that picture is too large to send (%.1f MB); install Pillow to have it scaled" % (len(data) / 1e6))
    return {"media_type": media, "data": base64.b64encode(data).decode("ascii")}


def _obj(props, required):
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


NO_ARGS = _obj({}, [])
SPEC_ARG = {"type": "object", "description": "A whole rig.json: {\"schema\": \"autorig-spec/1\", \"rig\": {...}, ...}."}


class FixRig:
    name = "fix-rig"
    label = "Fix this rig"
    defs = [
        {"name": "get_status", "kind": "read", "schema": NO_ARGS,
         "description": "The model's state: its source file, whether it is rigged, the audit grade, the rig kind."},
        {"name": "get_spec", "kind": "read", "schema": NO_ARGS, "description": "The model's current rig.json."},
        {"name": "get_audit", "kind": "read", "schema": NO_ARGS,
         "description": "The current audit: each check with its value and limit, the worst bends (bone, bend or twist, "
                        "torn edges, where in the 0..1 box, which bones own the torn edges) and each bone's share."},
        {"name": "get_survey", "kind": "read", "schema": NO_ARGS,
         "description": "What the source file holds: size, the skeleton it came with, how much of it is skinned."},
        {"name": "get_rig_log", "kind": "read", "schema": NO_ARGS,
         "description": "The rig step's own report: the bones it built, their roles, and its warnings."},
        {"name": "list_pictures", "kind": "read", "schema": NO_ARGS,
         "description": "The pictures there are for this model (bend test, skin sheet, QA render, facing views, trial "
                        "renders), by name."},
        {"name": "view_picture", "kind": "read",
         "schema": _obj({"name": {"type": "string", "description": "A name from list_pictures."}}, ["name"]),
         "description": "Look at one picture."},
        {"name": "spec_reference", "kind": "read",
         "schema": _obj({"topic": {"type": "string", "description": "A word from a heading (e.g. rig, placed, clips, "
                                                                     "humanoid, grades, tears), or empty for the list."}}, ["topic"]),
         "description": "Quote the documentation for rig.json (docs/SPEC.md) or the audit's grades (docs/PIPELINE.md)."},
        {"name": "trial_rig", "kind": "trial",
         "schema": _obj({"spec": SPEC_ARG, "note": {"type": "string", "description": "What this candidate changes."}},
                        ["spec", "note"]),
         "description": "Rig, trim and audit a candidate rig.json on a scratch copy of the model; returns its audit and "
                        "adds its QA render to the pictures. The real model is not touched."},
        {"name": "propose_spec", "kind": "change",
         "schema": _obj({"spec": SPEC_ARG, "summary": {"type": "string", "description": "One line: what it changes."}},
                        ["spec", "summary"]),
         "description": "Propose this rig.json for the model. It is added to a plan the person applies or not."},
    ]
    LABELS = {"get_status": "Read the model's state", "get_spec": "Read rig.json", "get_audit": "Read the audit",
              "get_survey": "Read the survey", "get_rig_log": "Read the rig log", "list_pictures": "Listed the pictures"}
    system = SYSTEM

    def __init__(self, srv, app=None):
        self.srv, self.app = srv, app
        self.trials = {}                        # model -> {trial number: work folder}

    # ---- helpers ----------------------------------------------------------------------------------------------

    def _model(self, ctx):
        name = (ctx or {}).get("model", "")
        if self.srv.find_group(name) is None:
            raise ValueError("no model %s" % name)
        return name

    def context_note(self, ctx):
        return "Model: %s" % (ctx or {}).get("model", "")

    def step_label(self, name, args):
        if name == "view_picture":
            return "Looked at %s" % args.get("name", "a picture")
        if name == "spec_reference":
            return "Read the docs on %s" % (args.get("topic") or "rig.json")
        if name == "trial_rig":
            return "Tried: %s" % (args.get("note") or "a candidate spec")
        return self.LABELS.get(name, name.replace("_", " "))

    def _pictures(self, name):
        L = self.srv.layout
        W = L.WORK
        found = {}

        def add(key, path, what):
            if os.path.isfile(path):
                found[key] = (path, what)
        add("bend_test", os.path.join(W, "audit", name + "_bend.png"), "the audit's bend test: each bone bent and twisted, torn edges in red")
        add("skin_sheet", os.path.join(W, "audit", name + "_skin.png"), "the audit's skin sheet: which bone owns each part of the mesh")
        add("qa_render", os.path.join(W, "qa", name + ".png"), "the rig step's QA render")
        for p in sorted(glob.glob(os.path.join(glob.escape(W), "facing", glob.escape(name) + "__*.png"))):
            add("facing_" + os.path.splitext(os.path.basename(p))[0].split("__", 1)[1], p, "a survey view, for reading which way it faces")
        for n, folder in sorted((self.trials.get(name) or {}).items()):
            add("trial%d_qa_render" % n, os.path.join(folder, "work", "qa", name + ".png"), "trial %d's QA render" % n)
        return found

    def _audit_view(self, a):
        if not a:
            return None
        spots, share = spec_api.hot_spots(a)
        v = a.get("verdict") or {}
        return {"grade": v.get("grade"), "pass": v.get("pass"), "summary": spec_api.audit_summary(a),
                "worst_bends": spots[:15], "bone_share_pct": share, "warnings": v.get("warnings", []),
                "unweighted_verts": a.get("unweighted_verts"), "islands": a.get("islands")}

    def _check(self, spec, name):
        if not isinstance(spec, dict):
            raise ValueError("spec must be a whole rig.json object")
        src = _load(os.path.join(self.srv.layout.WORK, "source", name + ".json"))
        errs, warns = spec_api.check(spec, src)
        if errs:
            raise ValueError("the spec has problems: " + "; ".join("%s: %s" % (e["path"], e["message"]) for e in errs[:8]))
        return warns

    # ---- the tools --------------------------------------------------------------------------------------------

    def run(self, tool, args, ctx):
        name = self._model(ctx)
        L, srv = self.srv.layout, self.srv
        if tool == "get_status":
            st = srv.status(srv.find_group(name), name)
            keep = ("name", "group", "source", "source_ext", "kind", "rigged", "audit", "spec", "spec_error", "clips_spec", "budget")
            return {"content": json.dumps({k: st.get(k) for k in keep})}
        if tool == "get_spec":
            _, text = spec_api.read_spec_text(srv, name)
            return {"content": text or "(this model has no rig.json yet)"}
        if tool == "get_audit":
            a = _load(os.path.join(L.WORK, "audit", name + ".json"))
            return {"content": json.dumps(self._audit_view(a)) if a else "(no audit yet: the model has not been rigged and audited)"}
        if tool == "get_survey":
            s = _load(os.path.join(L.WORK, "survey", name + ".json"))
            return {"content": json.dumps(s)[:12000] if s else "(no survey yet)"}
        if tool == "get_rig_log":
            q = _load(os.path.join(L.WORK, "qa", name + ".json"))
            return {"content": json.dumps(q)[:12000] if q else "(no rig log yet)"}
        if tool == "list_pictures":
            pics = self._pictures(name)
            return {"content": json.dumps({k: what for k, (_, what) in pics.items()}) if pics else "(no pictures yet)"}
        if tool == "view_picture":
            pics = self._pictures(name)
            key = str(args.get("name") or "")
            if key not in pics:
                raise ValueError("no picture called %r; list_pictures has: %s" % (key, ", ".join(pics) or "none"))
            return {"content": "%s: %s" % (key, pics[key][1]), "images": [_picture(pics[key][0])]}
        if tool == "spec_reference":
            return {"content": spec_reference(args.get("topic") or "")}
        if tool == "trial_rig":
            return self._trial(name, args.get("spec"), str(args.get("note") or ""))
        if tool == "propose_spec":
            spec = args.get("spec")
            warns = self._check(spec, name)
            _, old = spec_api.read_spec_text(srv, name)
            new = spec_api.dumps(spec)
            try:
                same = old is not None and json.loads(old) == spec
            except ValueError:
                same = False
            if same:
                raise ValueError("that is the rig.json the model already has")
            summary = str(args.get("summary") or "").strip()[:200] or changed_fields(old, spec)
            change = {"type": "spec", "summary": summary, "spec": spec, "base": spec_api.text_hash(old),
                      "detail": spec_api.diff(old, new)[:6000]}
            note = (" Warnings: " + "; ".join(w["message"] for w in warns[:5])) if warns else ""
            return {"change": change, "replaces": True,
                    "content": "Added to the plan: %s. The person applies it from the page.%s" % (summary, note)}
        raise ValueError("no tool called %s" % tool)

    def _trial(self, name, spec, note):
        """Rig, trim and audit a candidate on a scratch copy: <work>/ai_trials/<model>/<n>/{models,work}."""
        self._check(spec, name)
        L = self.srv.layout
        trials = self.trials.setdefault(name, {})
        n = max(trials, default=0) + 1
        top = os.path.join(L.WORK, "ai_trials", name, str(n))
        if os.path.isdir(top):
            shutil.rmtree(top, ignore_errors=True)
        group = L.group_of(name)
        models = os.path.join(top, "models")
        dest = os.path.join(models, group, name) if group else os.path.join(models, name)
        src = L.pack_dir(name)

        def outputs(folder, entries):         # the rig's own outputs are made again; the source and its textures are kept
            return [e for e in entries if folder == src and (e.startswith("rigged") or e in ("clips", "_studio"))]

        def link_or_copy(a, b):
            try:
                os.link(a, b)                 # a hard link costs nothing for a big FBX and its textures
            except OSError:
                shutil.copy2(a, b)
        shutil.copytree(src, dest, ignore=outputs, copy_function=link_or_copy)
        os.remove(os.path.join(dest, "rig.json")) if os.path.exists(os.path.join(dest, "rig.json")) else None
        with open(os.path.join(dest, "rig.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(spec_api.dumps(spec))
        col = os.path.join(L.ROOT, "autorig.json")      # the collection's budget and card rules
        if os.path.isfile(col):
            shutil.copy2(col, os.path.join(models, "autorig.json"))
        work = os.path.join(top, "work")
        os.makedirs(work, exist_ok=True)
        env = dict(os.environ, AUTORIG_MODELS=models, AUTORIG_WORK=work, PYTHONUNBUFFERED="1")
        t0 = time.time()
        r = subprocess.run([sys.executable, os.path.join(REPO, "autorig", "cli", "pipeline.py"), name, "-noSheets"],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=TRIAL_TIMEOUT)
        trials[n] = top
        a = _load(os.path.join(work, "audit", name + ".json"))
        if not a:
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-12:]
            raise ValueError("trial %d did not finish an audit (exit %s):\n%s" % (n, r.returncode, "\n".join(tail)))
        if a.get("error"):
            raise ValueError("trial %d's audit failed: %s" % (n, a["error"]))
        view = self._audit_view(a)
        view.update(trial=n, note=note, seconds=round(time.time() - t0, 1))
        pics = [k for k in self._pictures(name) if k.startswith("trial%d_" % n)]
        return {"content": json.dumps(view) + ("\nPictures: " + ", ".join(pics) if pics else ""),
                "label": "Tried (%d): %s -> %s" % (n, note or "a candidate", view.get("grade") or "?")}

    # ---- applying ---------------------------------------------------------------------------------------------

    def _journal_dir(self):
        d = os.path.join(self.srv.layout.WORK, "ai", "journal")
        os.makedirs(d, exist_ok=True)
        return d

    def _rerig(self, name, label):
        srv = self.srv
        g = srv.find_group(name)
        spec = srv.spec_store.model(name)
        st = srv.status(g, name)
        if st["steps"].get("rig"):
            raise ValueError(st["steps"]["rig"])
        c_arch = srv.spec_store.infer_clip_archetype(spec)
        steps = ["rig", "trim", "audit"] + (["clips"] if c_arch else []) + ["preview"]
        cmds = [c for s in steps for c in srv.commands(g, name, s, spec)]
        return spec_api._job(self.app, srv, name, label, cmds)

    def apply(self, changes, ctx):
        name = self._model(ctx)
        change = changes[-1]                         # one rig.json at a time: the last proposal
        _, old = spec_api.read_spec_text(self.srv, name)
        # the spec editor's own save: checks it, keeps rig.json.bak, refuses when rig.json changed since the proposal
        text, errs, _ = spec_api.write_spec(self.srv, name, change["spec"], change["base"])
        if errs:
            raise ValueError("the spec has problems: " + "; ".join("%s: %s" % (e["path"], e["message"]) for e in errs[:5]))
        jid = "%s-%d" % (name, int(time.time() * 1000))
        with open(os.path.join(self._journal_dir(), jid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"model": name, "old": old, "new": text, "at": time.time(), "summary": change["summary"]}, fh)
        out = {"results": [{"n": change["n"], "ok": True, "summary": change["summary"]}], "journal": jid}
        try:
            out["job"] = self._rerig(name, "AI fix: re-rig")
        except ValueError as e:
            out["error"] = "rig.json is saved, but the re-rig didn't start: %s" % e
        return out

    def undo(self, journal):
        jid = os.path.basename(str(journal or ""))
        path = os.path.join(self._journal_dir(), jid + ".json")
        rec = _load(path)
        if not rec:
            raise ValueError("there is nothing to undo under that name")
        name = rec["model"]
        if self.srv.find_group(name) is None:
            raise ValueError("the model %s is not there any more" % name)
        spec_path, now = spec_api.read_spec_text(self.srv, name)
        if (now or "") != (rec["new"] or ""):
            raise ValueError("rig.json has changed since the fix was applied, so undo would lose that change")
        if rec["old"] is None:
            os.remove(spec_path)
        else:
            with open(spec_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(rec["old"])
        self.srv.spec_store.reload()
        os.replace(path, path + ".undone")
        out = {"results": [{"ok": True, "summary": "rig.json put back"}]}
        if rec["old"] is not None:
            try:
                out["job"] = self._rerig(name, "AI fix undone: re-rig")
            except ValueError as e:
                out["error"] = "rig.json is back, but the re-rig didn't start: %s" % e
        return out


def changed_fields(old_text, spec):
    """A plan line when the AI gave none: which rig.json fields the proposal changes."""
    try:
        old = json.loads(old_text) if old_text else {}
    except ValueError:
        old = {}
    names = ["rig." + k for k in sorted(set((old.get("rig") or {})) | set((spec.get("rig") or {})))
             if (old.get("rig") or {}).get(k) != (spec.get("rig") or {}).get(k)]
    names += [k for k in sorted(set(old) | set(spec)) if k != "rig" and old.get(k) != spec.get(k)]
    return "Change " + ", ".join(names[:6]) + (" and more" if len(names) > 6 else "") if names else "A new rig.json"


def spec_reference(topic):
    """Sections of docs/SPEC.md and docs/PIPELINE.md whose heading holds the topic; the headings when none does."""
    sections = []
    for doc in ("SPEC.md", "PIPELINE.md"):
        try:
            with open(os.path.join(REPO, "docs", doc), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        cur = None
        for line in lines:
            if line.startswith("#"):
                cur = {"doc": doc, "head": line.lstrip("#").strip(), "level": len(line) - len(line.lstrip("#")), "lines": [line]}
                sections.append(cur)
            elif cur:
                cur["lines"].append(line)
    t = (topic or "").strip().lower().strip("`")
    hits = [s for s in sections if t and t in s["head"].lower().replace("`", "")]
    if not hits:
        return "Headings (ask for one by a word in it):\n" + "\n".join("%s: %s" % (s["doc"], s["head"]) for s in sections)
    return "\n\n".join("\n".join(s["lines"]) for s in hits)[:14000]

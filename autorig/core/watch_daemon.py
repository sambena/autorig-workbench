# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Watch Folder Ingestion Daemon.
#
# Monitors an incoming directory for 3D assets (FBX, OBJ, GLB, glTF, ZIP, or a folder holding one):
#   1. File stability check (verifies the file, or every file in a dropped folder, is fully written).
#   2. Ingestion into the models root (<root>/<group>/<model>/), with the files a model needs beside it: an OBJ's
#      .mtl and textures, a glTF's .bin and images, an FBX's .fbm folder.
#   3. Pre-flight Mesh Doctor geometry diagnosis.
#   4. A suggested rig.json when none came with the model: the survey and suggest steps under headless Blender.
#   5. Automated pipeline execution (rig -> trim -> audit -> clips -> publish -> preview).
#   6. Optional multi-target engine export (Unreal, Unity, Godot, Web).
#   7. Archiving processed items into <incoming>/_processed/.
#
# One item that fails is reported and skipped; it never stops the loop, and an item that cannot be archived is
# remembered so it is not ingested again on the next pass. A dropped model whose rig.json names its own Python
# builder (kind "custom") is placed but not rigged: code from a drop folder is never run.

import json
import os
import re
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import blender
    import layout
    import mesh_doctor
    import spec_store
    from batch_runner import run_model_pipeline
except ImportError:
    from autorig.core import blender
    from autorig.core import layout
    from autorig.core import mesh_doctor
    from autorig.core import spec_store
    from autorig.core.batch_runner import run_model_pipeline

VALID_EXTS = {".fbx", ".glb", ".gltf", ".obj", ".zip"}
PIPELINE = ["rig", "trim", "audit", "clips", "publish", "preview"]


def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s.strip()).strip("_").lower()
    return s[:64] or "model"


def clean_group(g):
    """A group folder name: one plain path segment, never a way out of the models root."""
    return clean_name(g) if g and g.strip() else ""


def _files_under(path):
    if os.path.isfile(path):
        return [path]
    out = []
    for d, _, files in os.walk(path):
        out += [os.path.join(d, f) for f in files]
    return out


def _signature(path):
    """(file count, total bytes, newest mtime) of a file or of everything in a folder."""
    files = _files_under(path)
    size = mtime = 0
    for f in files:
        st = os.stat(f)
        size += st.st_size
        mtime = max(mtime, st.st_mtime)
    return len(files), size, mtime


def is_file_stable(path, min_age=1.0, sample=0.25):
    """Checks whether an incoming file (or every file in an incoming folder) has finished copying: something is
    there, nothing changed in the last min_age seconds, and the size holds still over a short sample. A copy that
    keeps the source's old mtime (Explorer, copy2) is still caught by the size sample."""
    if not os.path.exists(path):
        return False
    try:
        n, s1, mtime = _signature(path)
        if n == 0 or s1 == 0:
            return False
        if (time.time() - mtime) < min_age:
            return False
        time.sleep(sample)
        return _signature(path)[:2] == (n, s1)
    except OSError:
        return False


def sidecars(src):
    """The files a model file needs beside it, found from the file itself: an OBJ's material libraries and their
    texture maps, a glTF's buffers and images, an FBX's <stem>.fbm texture folder. Only existing files in the same
    folder (or below it) are returned; a path that climbs out of the folder is ignored."""
    folder = os.path.dirname(os.path.abspath(src))
    ext = os.path.splitext(src)[1].lower()
    refs = []

    def local(rel):
        rel = rel.strip().strip('"').replace("\\", "/")
        if not rel or rel.startswith("data:") or "://" in rel:
            return None
        p = os.path.normpath(os.path.join(folder, rel))
        try:
            if os.path.commonpath([p, folder]) != folder or not os.path.exists(p):
                return None
        except ValueError:                        # another drive (a Windows absolute path): not beside the model
            return None
        return p

    try:
        if ext == ".obj":
            with open(src, encoding="utf-8", errors="replace") as fh:
                mtls = [local(line.split(None, 1)[1]) for line in fh
                        if line.startswith("mtllib ") and len(line.split(None, 1)) > 1]
            for m in filter(None, mtls):
                refs.append(m)
                with open(m, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        parts = line.split()
                        if parts and (parts[0].startswith("map_") or parts[0] in ("bump", "disp", "decal", "refl")):
                            refs.append(local(parts[-1]))      # the file is the last token, after any options
        elif ext == ".gltf":
            with open(src, encoding="utf-8") as fh:
                g = json.load(fh)
            for item in (g.get("buffers") or []) + (g.get("images") or []):
                if item.get("uri"):
                    refs.append(local(item["uri"]))
        elif ext == ".fbx":
            refs.append(local(os.path.splitext(os.path.basename(src))[0] + ".fbm"))
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(r for r in refs if r))


def _copy(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def place_incoming(src_path, target_group=None, model_name=None):
    """Copies an incoming file (with its sidecars), folder or archive into the models root as a new model folder.
    Returns (name, dest, extras): extras are the sidecar paths that were copied with it."""
    group = clean_group(target_group)
    stem = os.path.splitext(os.path.basename(src_path.rstrip("/\\")))[0]
    name = clean_name(model_name or stem)
    base = os.path.join(layout.ROOT, group) if group else layout.ROOT

    # model names are unique across the whole collection (the tool finds a model by name), not just in its group
    taken = {m for _, m in layout.all_models()}
    dest = os.path.join(base, name)
    if name in taken or os.path.exists(dest):
        for i in range(1, 1000):
            cand = f"{name}_{i}"
            if cand not in taken and not os.path.exists(os.path.join(base, cand)):
                name, dest = cand, os.path.join(base, cand)
                break

    os.makedirs(dest, exist_ok=True)
    extras = []
    if os.path.isdir(src_path):
        shutil.copytree(src_path, dest, dirs_exist_ok=True)
    elif src_path.lower().endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(src_path) as zf:
            zf.extractall(dest)                  # zipfile drops ".." and absolute members
    else:
        shutil.copy2(src_path, os.path.join(dest, os.path.basename(src_path)))
        folder = os.path.dirname(os.path.abspath(src_path))
        for extra in sidecars(src_path):
            _copy(extra, os.path.join(dest, os.path.relpath(extra, folder)))
            extras.append(extra)

    spec_store.reload()
    return name, dest, extras


def suggest_spec(name, log=print, spec_file=None):
    """Surveys the model and writes the suggested rig.json (steps/survey.py, steps/suggest.py under headless
    Blender) to spec_file (default: the model's own). Returns the archetype suggested, or None when no spec could
    be made."""
    if not blender.find(required=False):
        log(f"WATCH_SUGGEST {name}: Blender not found, no rig.json suggested")
        return None
    r = blender.run("survey.py", "-only", name)
    if r.returncode != 0:
        log(f"WATCH_SUGGEST {name}: survey failed (exit {r.returncode})")
    out_dir = layout.work_dir("suggest")
    out = os.path.join(out_dir, name + "_suggest.json")
    if os.path.exists(out):
        os.remove(out)
    r = blender.run("suggest.py", name, "-out", out_dir)
    if r.returncode != 0 or not os.path.isfile(out):
        log(f"WATCH_SUGGEST {name}: suggest failed (exit {r.returncode})")
        return None
    with open(out, encoding="utf-8") as fh:
        prop = json.load(fh)
    spec = prop.get("spec")
    if not spec or prop.get("error"):
        log(f"WATCH_SUGGEST {name}: no spec ({prop.get('error') or 'nothing proposed'})")
        return None
    with open(spec_file or spec_store.spec_path(name), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(spec, fh, indent=2)
    spec_store.reload()
    log(f"WATCH_SUGGEST {name}: wrote rig.json ({prop.get('archetype')}, {prop.get('confidence')} confidence)")
    return prop.get("archetype")


def archive(path, extras=()):
    """Moves a processed item (and the sidecars that went with it) into <incoming>/_processed/, beside any earlier
    item of the same name. Returns True when the item itself was moved."""
    proc_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "_processed")
    os.makedirs(proc_dir, exist_ok=True)
    moved = False
    for p in [path] + list(extras):
        target = os.path.join(proc_dir, os.path.basename(p.rstrip("/\\")))
        if os.path.exists(target):
            stem, ext = os.path.splitext(target)
            target = "%s_%d%s" % (stem, int(time.time()), ext)
        try:
            shutil.move(p, target)
            moved = moved or p == path
        except (OSError, shutil.Error):
            pass
    return moved


def process_incoming_file(filepath, target_group=None, auto_rig=True, auto_tune=False, export_target=None,
                          log=print):
    """Processes a single incoming 3D asset file or folder. Returns None when it is not ready yet."""
    if not is_file_stable(filepath):
        return None

    name, dest, extras = place_incoming(filepath, target_group=target_group)

    # 1. A rig.json that came with the model is kept; otherwise one is suggested (when rigging: import-only leaves
    # the model as it came)
    suggested = None
    spec_error = None
    spec_file = os.path.join(dest, "rig.json")          # the placed model's own folder, never a namesake's
    if auto_rig and not os.path.exists(spec_file):
        try:
            suggested = suggest_spec(name, log=log, spec_file=spec_file)
        except Exception as e:
            spec_error = str(e)
            log(f"WATCH_SUGGEST {name}: {e}")

    # 2. Pre-flight mesh doctor inspection
    src_file = layout.source_model(name)
    doctor_report = None
    if src_file:
        try:
            doctor_report = mesh_doctor.inspect_source_model(src_file)
        except Exception:
            pass

    # 3. Automated pipeline run
    pipeline_result = None
    if auto_rig:
        try:
            rig = (spec_store.model(name).get("rig") or {})
        except Exception as e:
            rig, spec_error = {}, str(e)
        if rig.get("kind") == "custom":
            pipeline_result = {"status": "SKIPPED", "steps": [], "grade": "-", "duration": 0,
                               "error": "rig.json names a custom Python builder: rig it from the workbench "
                                        "after checking the builder"}
        else:
            pipeline_result = run_model_pipeline(name, steps=PIPELINE, auto_tune=auto_tune,
                                                 export_target=export_target)

    # 4. Move processed file to _processed archive directory
    archived = archive(filepath, extras)

    return {
        "file": os.path.basename(filepath.rstrip("/\\")),
        "model": name,
        "group": clean_group(target_group) or "(root)",
        "suggested": suggested,
        "spec_error": spec_error,
        "doctor": doctor_report,
        "pipeline": pipeline_result,
        "archived": archived,
    }


def scan_incoming(incoming_dir, target_group=None, auto_rig=True, auto_tune=False, export_target=None,
                  seen=None, log=print):
    """Scans the watch folder once for all stable unprocessed files. `seen` (a set kept by the caller across
    passes) remembers items that were handled but could not be archived, or that failed, so they are not taken
    again until they change."""
    if not os.path.isdir(incoming_dir):
        return []
    seen = seen if seen is not None else set()

    candidates = []
    for f in os.listdir(incoming_dir):
        if f.startswith(("_", ".")):
            continue
        p = os.path.join(incoming_dir, f)
        if os.path.isfile(p):
            if os.path.splitext(f)[1].lower() in VALID_EXTS:
                candidates.append(p)
        elif os.path.isdir(p):
            candidates.append(p)

    def mtime(p):
        try:
            return _signature(p)[2]
        except OSError:
            return 0
    candidates.sort(key=mtime)
    # an OBJ's .mtl and textures, a glTF's .bin: taken with their model, never as models of their own
    claimed = set()
    for p in candidates:
        if os.path.isfile(p):
            claimed.update(os.path.normcase(s) for s in sidecars(p))
    candidates = [p for p in candidates if os.path.normcase(p) not in claimed]

    processed = []
    for p in candidates:
        try:
            key = (os.path.normcase(p),) + _signature(p)
        except OSError:
            continue
        if key in seen:
            continue
        try:
            res = process_incoming_file(p, target_group=target_group, auto_rig=auto_rig,
                                        auto_tune=auto_tune, export_target=export_target, log=log)
        except Exception as e:
            log(f"WATCH_ERROR {os.path.basename(p)}: {e}")
            log(traceback.format_exc().rstrip())
            seen.add(key)
            continue
        if res is None:
            continue
        if not res.get("archived") and os.path.exists(p):
            seen.add(key)
        processed.append(res)

    return processed


def run_watch_loop(incoming_dir, interval=2.0, once=False, target_group=None,
                   auto_rig=True, auto_tune=False, export_target=None, callback=None):
    """Runs watch folder polling loop. If once=True, runs single pass and returns."""
    os.makedirs(incoming_dir, exist_ok=True)
    seen = set()
    if once:
        results = scan_incoming(incoming_dir, target_group=target_group, auto_rig=auto_rig,
                                auto_tune=auto_tune, export_target=export_target, seen=seen)
        if callback:
            for r in results:
                callback(r)
        return results

    try:
        while True:
            try:
                results = scan_incoming(incoming_dir, target_group=target_group, auto_rig=auto_rig,
                                        auto_tune=auto_tune, export_target=export_target, seen=seen)
                if callback:
                    for r in results:
                        callback(r)
            except Exception as e:                # the loop outlives anything one pass throws
                print(f"WATCH_ERROR pass failed: {e}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return []

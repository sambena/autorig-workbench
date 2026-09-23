# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Watch Folder Ingestion Daemon.
#
# Monitors an incoming directory for 3D assets (FBX, OBJ, GLB, glTF, ZIP):
#   1. File stability check (verifies file is fully written before reading).
#   2. Automatic ingestion into models root (<root>/<group>/<model>/).
#   3. Pre-flight Mesh Doctor geometry diagnosis & auto-healing.
#   4. Skeleton heuristic suggestion if no rig.json spec exists.
#   5. Automated pipeline execution (rig -> trim -> audit -> clips -> preview).
#   6. Optional multi-target engine export (Unreal, Unity, Godot, Web).
#   7. Archiving processed items into <incoming>/_processed/.

import glob
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import layout
    import mesh_doctor
    import spec_store
    import suggest
    from batch_runner import run_model_pipeline
except ImportError:
    from autorig.core import layout
    from autorig.core import mesh_doctor
    from autorig.core import spec_store
    from autorig.core import suggest
    from autorig.core.batch_runner import run_model_pipeline

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
VALID_EXTS = {".fbx", ".glb", ".gltf", ".obj", ".zip"}


def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s.strip()).strip("_").lower()
    return s[:64] or "model"


def is_file_stable(path, min_age=1.0):
    """Checks whether an incoming file has finished copying/downloading."""
    if not os.path.exists(path):
        return False
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return False
        # Must be at least min_age seconds old
        if (time.time() - st.st_mtime) < min_age:
            return False
        # Sample size over a tiny sleep to detect active writes
        s1 = st.st_size
        time.sleep(0.05)
        s2 = os.path.getsize(path)
        return s1 == s2
    except OSError:
        return False


def place_incoming(src_path, target_group=None, model_name=None):
    """Copies incoming file or archive into the models root as a new model folder."""
    group = target_group or ""
    stem = os.path.splitext(os.path.basename(src_path))[0]
    name = clean_name(model_name or stem)

    dest = os.path.join(layout.ROOT, group, name) if group else os.path.join(layout.ROOT, name)
    if os.path.exists(dest):
        # Generate unique suffix if already exists
        for i in range(1, 1000):
            cand = f"{name}_{i}"
            cand_dest = os.path.join(layout.ROOT, group, cand) if group else os.path.join(layout.ROOT, cand)
            if not os.path.exists(cand_dest):
                name = cand
                dest = cand_dest
                break

    os.makedirs(dest, exist_ok=True)
    if src_path.lower().endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(src_path) as zf:
            zf.extractall(dest)
    elif os.path.isdir(src_path):
        for f in os.listdir(src_path):
            shutil.copy2(os.path.join(src_path, f), os.path.join(dest, f))
    else:
        shutil.copy2(src_path, os.path.join(dest, os.path.basename(src_path)))

    spec_store.reload()
    return name, dest


def process_incoming_file(filepath, target_group=None, auto_rig=True, auto_tune=False, export_target=None):
    """Processes a single incoming 3D asset file."""
    if not is_file_stable(filepath):
        return None

    name, dest = place_incoming(filepath, target_group=target_group)

    # 1. Check if rig.json exists; if not, suggest skeleton heuristics
    spec_path = os.path.join(dest, "rig.json")
    if not os.path.exists(spec_path):
        try:
            prop = suggest.suggest_skeleton(name)
            if prop and prop.get("proposal"):
                with open(spec_path, "w", encoding="utf-8") as fh:
                    json.dump(prop["proposal"], fh, indent=2)
                spec_store.reload()
        except Exception:
            pass

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
        steps = ["survey", "rig", "trim", "audit", "clips", "publish", "preview"]
        pipeline_result = run_model_pipeline(name, steps=steps, auto_tune=auto_tune, export_target=export_target)

    # 4. Move processed file to _processed archive directory
    inc_dir = os.path.dirname(os.path.abspath(filepath))
    proc_dir = os.path.join(inc_dir, "_processed")
    os.makedirs(proc_dir, exist_ok=True)
    try:
        shutil.move(filepath, os.path.join(proc_dir, os.path.basename(filepath)))
    except Exception:
        pass

    return {
        "file": os.path.basename(filepath),
        "model": name,
        "group": target_group or "(root)",
        "doctor": doctor_report,
        "pipeline": pipeline_result,
    }


def scan_incoming(incoming_dir, target_group=None, auto_rig=True, auto_tune=False, export_target=None):
    """Scans the watch folder once for all stable unprocessed files."""
    if not os.path.isdir(incoming_dir):
        return []

    candidates = []
    for f in os.listdir(incoming_dir):
        if f.startswith(("_", ".")):
            continue
        p = os.path.join(incoming_dir, f)
        if os.path.isfile(p):
            ext = os.path.splitext(f)[1].lower()
            if ext in VALID_EXTS:
                candidates.append(p)
        elif os.path.isdir(p):
            candidates.append(p)

    candidates.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    processed = []
    for p in candidates:
        res = process_incoming_file(p, target_group=target_group, auto_rig=auto_rig,
                                    auto_tune=auto_tune, export_target=export_target)
        if res is not None:
            processed.append(res)

    return processed


def run_watch_loop(incoming_dir, interval=2.0, once=False, target_group=None,
                   auto_rig=True, auto_tune=False, export_target=None, callback=None):
    """Runs watch folder polling loop. If once=True, runs single pass and returns."""
    os.makedirs(incoming_dir, exist_ok=True)
    if once:
        results = scan_incoming(incoming_dir, target_group=target_group, auto_rig=auto_rig,
                                auto_tune=auto_tune, export_target=export_target)
        if callback:
            for r in results:
                callback(r)
        return results

    try:
        while True:
            results = scan_incoming(incoming_dir, target_group=target_group, auto_rig=auto_rig,
                                    auto_tune=auto_tune, export_target=export_target)
            if callback:
                for r in results:
                    callback(r)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return []

# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio's `engine` tool: one engine-ready FBX from the separate LOD GLBs of the `lods` tool.

The scene work is steps/engine_package.py (inside Blender). This side finds the inputs, writes the
job and hands the result to the exporter's Unity and Unreal packages.
"""
import json
import os
import shutil
import uuid
from pathlib import Path

import layout
import studio


def lod_files(model_dir):
    """The LOD GLBs recorded by the last `lods` run, in level order."""
    folder = Path(model_dir) / "_studio" / "lods"
    report = folder / "lods.json"
    if not report.is_file():
        raise ValueError("run the Studio lods tool first (no _studio/lods/lods.json)")
    levels = json.loads(report.read_text(encoding="utf-8"))["levels"]
    files = [folder / level["file"] for level in levels]
    if any(f.suffix.lower() != ".glb" for f in files):
        raise ValueError("rerun the Studio lods tool with format glb; the engine package reads GLB levels")
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        raise ValueError("LOD files missing: " + ", ".join(missing))
    return [str(f) for f in files]


def resolve(path, model_dir, roots):
    """A settings path, relative to the model folder, confined to the collection or work folder."""
    full = os.path.realpath(os.path.join(model_dir, path))
    if not any(studio.inside(full, root) for root in roots):
        raise ValueError(path + " must be under the models or work folder")
    if not os.path.isfile(full):
        raise ValueError("file does not exist: " + full)
    return full


def job_for(model, settings, out):
    model_dir = layout.pack_dir(model)
    roots = [layout.ROOT, layout.WORK]
    if settings["animation"]:
        animation = resolve(settings["animation"], model_dir, roots)
    else:
        # The viewer's preview GLB carries the rig and every clip made for it.
        preview = os.path.join(model_dir, layout.rig_folder(model), "preview.glb")
        animation = preview if os.path.isfile(preview) else None
    pivots = {}
    parts = (resolve(settings["parts"], model_dir, roots) if settings["parts"] else
             os.path.join(model_dir, "_studio", "vehicle", "parts.json"))
    if os.path.isfile(parts):
        pivots = json.loads(Path(parts).read_text(encoding="utf-8")).get("pivots", {})
    return {"lods": lod_files(model_dir), "name": settings["name"] or layout.leaf(model), "out": str(out),
            "animation": animation, "pivots": pivots, "settings": settings}


def build(model, settings, out):
    """Runs the Blender packager; returns its report (also saved as engine.json)."""
    import blender
    out = Path(out)
    for old in list(out.glob("*.fbx")) + list(out.glob("*.fbm")) + [out / "engine.json"]:
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
    job = job_for(model, settings, out)
    path = Path(layout.work_dir("studio_jobs")) / (uuid.uuid4().hex + "_engine.json")
    studio.atomic_json(path, job)
    result = blender.run("engine_package.py", "--job", str(path))
    print(result.stdout or "", end="")
    if result.returncode or not (out / "engine.json").is_file():
        raise RuntimeError("engine package failed: " + (result.stderr or result.stdout or "")[-2000:])
    return json.loads((out / "engine.json").read_text(encoding="utf-8"))


def package_files(model, dest_dir):
    """Copies the engine FBX, its .fbm maps and engine.json into dest_dir/LODs; returns the files."""
    source = Path(layout.pack_dir(model)) / "_studio" / "engine"
    report = source / "engine.json"
    if not report.is_file():
        raise ValueError("run the Studio engine tool first (no _studio/engine/engine.json)")
    data = json.loads(report.read_text(encoding="utf-8"))
    dest = Path(dest_dir) / "LODs"
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    for name in (data["file"], "engine.json"):
        shutil.copy2(source / name, dest / name)
        files.append("LODs/" + name)
    maps = source / (data["name"] + ".fbm")
    if maps.is_dir():
        shutil.copytree(maps, dest / maps.name, dirs_exist_ok=True)
        files += ["LODs/%s/%s" % (maps.name, p.name) for p in sorted(maps.iterdir()) if p.is_file()]
    return files

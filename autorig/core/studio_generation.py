# SPDX-License-Identifier: GPL-3.0-or-later
"""Resumable Hyper3D jobs adapted from Storm Chaser's rodin_people.py."""
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
import studio


def photo_set(folder):
    found = []
    for view in ("Front", "Side", "Back", "Face"):
        hits = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.stem.lower().endswith(view.lower()))
        if hits: found.append(hits[0])
        elif view != "Face": raise ValueError("missing reference view: " + view)
    return found


def command(args):
    exe = shutil.which("hyper3d")
    if not exe: raise ValueError("Hyper3D CLI not installed: configure it before starting generation")
    prefix = [exe]
    if os.name == 'nt' and Path(exe).suffix.lower() in ('.cmd', '.bat', '.ps1'):
        # Invoke the installed npm entry point directly rather than putting user paths through cmd.exe.
        entry = Path(exe).parent / 'node_modules' / '@hyper3d' / 'cli' / 'dist' / 'index.js'
        node = shutil.which('node')
        if not entry.is_file() or not node: raise ValueError('Hyper3D npm entry point or Node runtime unavailable')
        prefix = [node, str(entry)]
    # Explicit argv; no shell interpolation of photo paths or provider output.
    result = subprocess.run(prefix + ["--output", "json"] + args, capture_output=True, text=True,
                            timeout=1900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode: raise RuntimeError("Hyper3D: " + (result.stderr or result.stdout)[-1200:])
    return json.loads(result.stdout)


OUTPUT_EXTS = (".glb", ".gltf", ".bin", ".obj", ".mtl", ".fbx", ".png", ".jpg", ".jpeg", ".webp")


def check_output(name, url, allow_http=False):
    """A file a generator returned: a plain file name of a known type, fetched over HTTPS (core/generators too)."""
    if not isinstance(name, str) or not name or Path(name).name != name or "\\" in name or ":" in name or name.startswith("."):
        raise ValueError("unsafe generation filename")
    if Path(name).suffix.lower() not in OUTPUT_EXTS:
        raise ValueError("unsupported generation output: " + name)
    if not isinstance(url, str) or not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
        raise ValueError("generation download must use HTTPS")


def generate(settings, model_dir, out):
    images = photo_set(studio.input_folder(settings["folder"], model_dir))
    fingerprint = hashlib.sha256(json.dumps({"tier": settings["tier"], "quality": settings["quality"],
        "photos": [hashlib.sha256(p.read_bytes()).hexdigest() for p in images]}, sort_keys=True).encode()).hexdigest()
    for n in range(1, settings["candidates"] + 1):
        folder = out / ("candidate_%d" % n); folder.mkdir(exist_ok=True)
        state_path = folder / "generation.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state and state.get("fingerprint") != fingerprint:
            raise ValueError("candidate settings or photos changed; archive the candidate folder before generating a replacement")
        if state.get("status") == "complete" and all((folder / f).is_file() for f in state.get("files", [])) and state.get("files"):
            print("STUDIO candidate %d already downloaded" % n); continue
        gid = state.get("generation_id")
        if state.get("status") == "submitting" and not gid:
            raise ValueError("submission was interrupted; recover the generation ID from Hyper3D into generation.json before resuming (no automatic paid retry)")
        if not gid:
            state = {"status": "submitting", "fingerprint": fingerprint}
            studio.atomic_json(state_path, state)
            args = ["generate", "--tier", settings["tier"], "--mesh-mode", "Quad", "--quality", settings["quality"], "--format", "glb"]
            for p in images: args += ["--image", str(p)]
            result = command(args); gid = result["generation_id"]
            state.update(generation_id=gid, status="submitted"); studio.atomic_json(state_path, state)
        print("STUDIO candidate %d resuming %s" % (n, gid), flush=True)
        command(["poll", "--timeout", "1800", gid])
        result = command(["result", gid]); names = []
        for f in result.get("files", []):
            name = f["name"]
            check_output(name, f["url"])
            dest = folder / name; temp = folder / (name + ".tmp")
            if not dest.exists():
                with urllib.request.urlopen(f["url"], timeout=600) as response, temp.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
                os.replace(temp, dest)
            names.append(name)
        if not any(Path(p).suffix.lower() in (".glb", ".gltf", ".fbx", ".obj") for p in names):
            raise ValueError("provider returned no model; generation ID retained for retry")
        state.update(status="complete", files=names); studio.atomic_json(state_path, state)

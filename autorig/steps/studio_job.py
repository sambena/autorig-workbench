# SPDX-License-Identifier: GPL-3.0-or-later
"""Model Studio dispatcher. Python handles images/network; Blender handles scenes."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import layout
import studio


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tool", choices=studio.TOOLS)
    ap.add_argument("model")
    ap.add_argument("--settings", help="JSON settings file; otherwise reads the model's studio.json")
    a = ap.parse_args(argv)
    if a.model not in {n for _, n in layout.all_models()}: raise ValueError("unknown model: " + a.model)
    model_dir = layout.pack_dir(a.model)
    settings = studio.validate(a.tool, json.loads(Path(a.settings).read_text(encoding="utf-8")) if a.settings else studio.load(model_dir)[a.tool])
    out = Path(model_dir) / "_studio" / a.tool
    out.mkdir(parents=True, exist_ok=True)
    # Every run has an immutable settings snapshot, including CLI runs.
    import uuid
    snap = Path(layout.work_dir("studio_jobs")) / (uuid.uuid4().hex + ".json")
    studio.atomic_json(snap, settings)
    if a.tool == "validate":
        import blender
        args = [a.model, "--every", str(settings["every"]), "--seam-mm", str(settings["seam_mm"]), "--report", str(out / "quality.json")]
        for name in settings["loops"]: args += ["--loop", name]
        result = blender.run("clip_check.py", *args)
        print(result.stdout or "", end="")
        if result.stderr: print(result.stderr, file=sys.stderr)
        if result.returncode: return result.returncode
    if a.tool in ("lods", "vehicle", "reference") or (a.tool == "foliage" and settings["apply_wind"]):
        import blender
        result = blender.run("studio_blender.py", a.tool, a.model, "--settings", str(snap))
        print(result.stdout or "", end="")
        if result.stderr: print(result.stderr, file=sys.stderr)
        if result.returncode: return result.returncode
    if a.tool == "reference":
        from studio_images import reference_sheet
        reference_sheet(settings, model_dir, out)
    elif a.tool == "foliage":
        from studio_images import foliage
        foliage(settings, out)
    elif a.tool == "materials":
        from studio_images import material
        material(settings, out)
    elif a.tool == "generate":
        from studio_generation import generate
        generate(settings, model_dir, out)
    studio.atomic_json(out / "last_run.json", {"tool": a.tool, "model": a.model, "settings": settings, "status": "complete"})
    print("STUDIO " + json.dumps({"tool": a.tool, "output": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one command for everything, for launchers that point the tool at a collection.
#
#   python autorig/cli/run.py                          the GUI (same as python -m autorig)
#   python autorig/cli/run.py gui [--port N] ...       the GUI, with its options
#   python autorig/cli/run.py wolf,moth [options]      rig -> trim -> audit (cli/pipeline.py and its options)
#   python autorig/cli/run.py rig wolf,moth            one step: rig, survey, facing, trim, audit, clips, measure, probe
#   python autorig/cli/run.py auto-tune wolf,moth      closed-loop auto-tuning optimizer (self-healing rigs)
#   python autorig/cli/run.py preview wolf,moth        the 3D viewer's rigged/preview.glb (steps/preview_glb.py)
#   python autorig/cli/run.py audit-all [-render 0] [-strict]   audit every rigged model, then a table worst first
#   python autorig/cli/run.py batch [models|all] [--group <g>] [--steps ...] [--export ...]
#   python autorig/cli/run.py watch <dir> [--group <g>] [--once] [--export ...]
#   python autorig/cli/run.py retarget <model> <clip>  retarget external mocap / BVH / FBX clip onto character
#   python autorig/cli/run.py clips wolf [--preview dir] [--split-clips dir]
#   python autorig/cli/run.py publish Creatures [-only wolf]
#
# Folders come from AUTORIG_MODELS and AUTORIG_WORK, as everywhere else.
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "core"))


def blender_step(script, *args):
    import blender
    r = blender.run(script, *args)
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr and r.stderr.strip() != r.stdout.strip():
        print(r.stderr, end="", file=sys.stderr)
    return r.returncode


def main(argv):
    import blender
    blender.warn_if_untested()
    if not argv or argv[0] == "gui" or argv[0].startswith("--"):
        sys.path.insert(0, PKG)
        from gui import server
        server.main(argv[1:] if argv and argv[0] == "gui" else argv)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "publish":
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "publish.py")] + rest)
    if cmd in ("survey", "trim", "facing") and rest:
        script = {"survey": "survey.py", "trim": "decimate.py", "facing": "facing.py"}[cmd]
        # a group name, or a comma list of models
        args = rest if rest[0].startswith("-") or "," not in rest[0] and not model_exists(rest[0]) else ["-only"] + rest
        return blender_step(script, *args)
    if cmd == "audit-all":
        return subprocess.call([sys.executable, os.path.join(HERE, "audit_all.py"), "all"] + rest)
    if cmd == "audit" and rest:
        return subprocess.call([sys.executable, os.path.join(HERE, "audit_all.py")] + rest)
    if cmd in ("clips", "rebake-clips", "rebake"):
        targets = rest[0].split(",") if rest and not rest[0].startswith("-") else ["all"]
        extra = rest[1:] if rest and not rest[0].startswith("-") else rest
        if targets == ["all"] or "all" in targets:
            import spec_store
            targets = sorted(list(spec_store.CLIPS))
            if not targets:
                print("CLIPS: no models with a \"clips\" section found in rig.json")
                return 0
        rc = max(blender_step("make_clips.py", m, *extra) for m in targets)
        if cmd in ("rebake-clips", "rebake") and rc == 0:
            for m in targets:
                blender_step("preview_glb.py", "-only", m)
        return rc
    if cmd == "measure" and rest:
        return max(blender_step("measure.py", m, *rest[1:]) for m in rest[0].split(","))
    if cmd == "preview" and rest:                             # results viewer
        return blender_step("preview_glb.py", *(rest if rest[0].startswith("-") else ["-only"] + rest))
    if cmd == "probe" and rest:
        return blender_step("probe_tips.py", *rest)
    if cmd == "auto-tune" and rest:
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "auto_tune.py")] + rest)
    if cmd in ("export", "package") and rest:
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "export.py")] + rest)
    if cmd == "doctor" and rest:
        if "--heal" in rest:                                  # healing needs Blender's mesh tools
            return blender_step("mesh_doctor.py", *rest)
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "mesh_doctor.py")] + rest)
    if cmd == "batch":
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "batch.py")] + rest)
    if cmd == "watch" and rest:
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "watch.py")] + rest)
    if cmd == "retarget" and rest:
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "retarget.py")] + rest)
    if cmd == "rig" and rest:
        import pipeline
        code = 0
        for m in rest[0].split(","):
            script = pipeline.rig_script(m)
            if script is None:
                print("RUN %s has no rig spec (rig.json \"rig\")" % m); code = 1; continue
            import layout
            code |= blender_step(script, "-only", m, "-qa", layout.work_dir("qa"), *rest[1:])
        return code
    if cmd in ("help", "-h", "--help"):
        with open(__file__, encoding="utf-8") as fh:
            print("".join(l[2:] for l in fh if l.startswith("#") and "SPDX" not in l))
        return 0
    # anything else is a model list for the pipeline
    return subprocess.call([sys.executable, os.path.join(HERE, "pipeline.py")] + argv)


def model_exists(name):
    import layout
    return any(m == name for _, m in layout.all_models())


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main(sys.argv[1:]))

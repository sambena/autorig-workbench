# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one command for everything, for launchers that point the tool at a collection.
#
#   python autorig/cli/run.py                          the GUI (same as python -m autorig)
#   python autorig/cli/run.py gui [--port N] ...       the GUI, with its options
#   python autorig/cli/run.py wolf,moth [options]      rig -> trim -> audit (cli/pipeline.py and its options)
#   python autorig/cli/run.py rig wolf,moth            one step: rig, prepare, survey, facing, trim, audit, clips, measure, probe
#   python autorig/cli/run.py auto-tune wolf,moth      closed-loop auto-tuning optimizer (self-healing rigs)
#   python autorig/cli/run.py preview wolf,moth        the 3D viewer's rigged/preview.glb (steps/preview_glb.py)
#   python autorig/cli/run.py audit-all [-render 0] [-strict]   audit every rigged model, then a table worst first
#   python autorig/cli/run.py score [models|all] [--group G] [--render 0] [--audit read|run|skip] [--rescore]
#                                                   the 0-100 quality score per rigged model, ranked (docs/QUALITY.md)
#   python autorig/cli/run.py batch [models|all] [--group <g>] [--steps ...] [--export ...]
#   python autorig/cli/run.py watch <dir> [--group <g>] [--once] [--export ...]
#   python autorig/cli/run.py retarget <model> <clip>  retarget external mocap / BVH / FBX clip onto character
#   python autorig/cli/run.py smpl-to-bvh <motion.pkl> <out.bvh> [--arms-out 15 ...]   EDGE / SMPL dance -> BVH
#   python autorig/cli/run.py clip-check <model|file.glb>   does it move, do seams hold, where are the hands
#   python autorig/cli/run.py studio <tool> <model> [--settings file.json]   Model Studio tools
#   python autorig/cli/run.py gpu                     which GPU Blender draws (Workbench) and bakes (Cycles) with
#   python autorig/cli/run.py extras [install <name ...|all>]   optional pip packages, export-pass programs
#   python autorig/cli/run.py suggest <model> [--force]   survey it and write a suggested rig.json (keeps one there)
#   python autorig/cli/run.py clips wolf [--preview dir] [--split-clips dir] [--variants N [--seed S]]
#   python autorig/cli/run.py publish Creatures [-only wolf]
#   python autorig/cli/run.py game-prop --in barn.glb --name Barn --length 18 --out <folder> [--front +x] [--carve-wheels]
#                                                   a generated prop as a game prop: metres, facing, LODs, maps, wheels
#   python autorig/cli/run.py tripo-clips --in Dog_rigged.glb --name Dog --height 0.62 --out <folder>
#                                                   idle/walk/trot/gallop on a Tripo-rigged animal's own skeleton
#   python autorig/cli/run.py game-parts --in Pumpjack.fbx --spec parts.json --out <folder> [--renders <folder>] [--survey]
#                                                   a game prop's moving parts cut out of LOD0, each its own mesh at its pivot
#   python autorig/cli/run.py crop-cards --out <folder> --card <name>=<plant.glb>[:<view>[:<wide>]] ...
#                                                   flat picture cards of plants, for a foliage atlas
#   python autorig/cli/run.py smoke-flipbook --out dust_puff.png [--grid 4] [--size 256] [--res 64]
#                                                   a puff of smoke simulated and rendered as a flipbook sheet
#   python autorig/cli/run.py fbx-maps --fbx rigged/Ice.fbx --source Ice.glb --name IceTeen --out <folder>
#                                                   a rigged FBX given its GLB source's colour and normal maps
#   python autorig/cli/run.py mixamo-upload --in Ice.glb --out <folder>/IceAdult.fbx --height 1.8 [--triangles 30000]
#                                                   a generated character cleaned and packed for Mixamo's auto-rigger
#   python autorig/cli/run.py mixamo-import --rig <download>.fbx --source Ice.glb --name IceAdult --out <folder>
#                                                   the Mixamo rig with full maps and Unity's axes, as a game file
#   python autorig/cli/run.py m2m-rig --model Cow_Holstein_Tripo [--template auto] [--clips all] [--joints j.json]
#                                                   fit a Mesh2Motion skeleton, skin, audit, attach its clips
#   python autorig/cli/run.py m2m-clips <model> --clips "Idle,Walk,Run" [--root-motion] [--out <folder>] [--replace]
#                                                   Mesh2Motion's human clips on a Mixamo-rigged person, as takes
#   python autorig/cli/run.py fix-skin <model>       re-skin a Mesh2Motion rig several ways, keep the best (Advanced -> Fix skin)
#   python autorig/cli/run.py m2m-quadruped --in Cow.glb --name Cow --rig horse-animations.glb --out <folder>
#                                                   the first Mesh2Motion fit, four-legged only
#
# Every tool and step is listed in autorig/core/steps_registry.py (the GUI, batches and the API read it too).
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
        sys.path.insert(0, os.path.dirname(PKG))              # the gui imports autorig.core.* by package name
        from gui import server
        server.main(argv[1:] if argv and argv[0] == "gui" else argv)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in NEEDS_ARGS and not rest:
        # without its arguments a step's name would fall through to the pipeline as a model name
        lines = [l[2:].rstrip() for l in open(__file__, encoding="utf-8")
                 if l.startswith("#   python autorig/cli/run.py %s " % cmd)]
        print("usage:\n" + "\n".join(lines or ["  python autorig/cli/run.py %s <model>[,<model>...]" % cmd]))
        return 2
    if cmd == "gpu":                                  # which GPU Blender uses (core/gpu_setup.py)
        return blender_step("gpu_report.py")
    if cmd == "extras":                               # optional pip packages for Blender's Python (core/extras.py)
        import extras
        return extras.main(rest)
    if cmd == "suggest" and rest:
        import spec_store, watch_daemon
        name = rest[0]
        if os.path.exists(spec_store.spec_path(name)) and "--force" not in rest:
            print("SUGGEST %s already has a rig.json; --force replaces it" % name)
            return 0
        arch = watch_daemon.suggest_spec(name)
        print("SUGGEST %s: %s" % (name, ("rig.json written (%s)" % arch) if arch else "no spec could be suggested"))
        return 0 if arch else 1
    import steps_registry
    if cmd in steps_registry.TOOLS or cmd in steps_registry.CLI_ONLY:   # files in, files out (core/steps_registry.py)
        try:
            runner, script = steps_registry.tool_script(cmd)
        except ValueError as e:
            print("RUN %s" % e); return 2
        if cmd.startswith("m2m-") and not os.environ.get("AUTORIG_M2M") and steps_registry.m2m_app():
            os.environ["AUTORIG_M2M"] = steps_registry.m2m_app()     # the Mesh2Motion submodule's rigs and clips
        if runner == "blender":
            return blender_step(script, *rest)
        return subprocess.call([sys.executable, script] + rest)
    if cmd == "studio":
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "studio_job.py")] + rest)
    if cmd == "publish":
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "publish.py")] + rest)
    if cmd in ("survey", "trim", "facing") and rest:
        script = {"survey": "survey.py", "trim": "decimate.py", "facing": "facing.py"}[cmd]
        # a group name, or a comma list of models
        args = rest if rest[0].startswith("-") or "," not in rest[0] and not model_exists(rest[0]) else ["-only"] + rest
        return blender_step(script, *args)
    if cmd == "score":                                # the quality score (cli/score_all.py, docs/QUALITY.md)
        return subprocess.call([sys.executable, os.path.join(HERE, "score_all.py")] + rest)
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
    if cmd == "prepare" and rest:
        # prepare wolf[,moth] [--unwrap] [--loops] [--format glb,fbx,obj]
        args = rest if rest[0].startswith("-") else ["-only", rest[0]] + rest[1:]
        return blender_step("prepare.py", *args)
    if cmd == "doctor" and rest:
        if {"--heal", "--repair", "--watertight"} & set(rest):  # these need Blender's mesh tools
            return blender_step("mesh_doctor.py", *rest)
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "mesh_doctor.py")] + rest)
    if cmd == "fix-skin" and rest:                            # re-skin a Mesh2Motion rig, keep the best (steps/m2m_fix.py)
        return subprocess.call([sys.executable, os.path.join(PKG, "steps", "m2m_fix.py")] + rest)
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


# Subcommands that do nothing useful without arguments (batch, clips, audit-all and studio handle their own)
NEEDS_ARGS = ("suggest", "fix-skin", "m2m-rig", "m2m-quadruped", "smoke-flipbook", "mixamo-upload", "mixamo-import", "game-prop", "game-parts", "tripo-clips", "crop-cards", "fbx-maps", "rig", "prepare", "survey", "trim", "facing", "audit", "measure", "preview", "probe", "auto-tune",
              "export", "package", "doctor", "watch", "smpl-to-bvh", "clip-check", "retarget")


def model_exists(name):
    import layout
    return any(m == name for _, m in layout.all_models())


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main(sys.argv[1:]))

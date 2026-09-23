# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one model (or several) through the whole rig pipeline: rig -> decimate -> audit, one Blender
# per step.
#
#   python autorig/cli/pipeline.py wolf,moth              re-rig, cut to budget, audit
#   python autorig/cli/pipeline.py wolf -skipRig          decimate and audit only
#   python autorig/cli/pipeline.py knight -rig rerig_humanoid.py   force a rig script
#   python autorig/cli/pipeline.py wolf -preview          also write rigged/preview.glb for the GUI's 3D viewer
#
# The rig script is picked from the model's spec (rig.json "rig"): kind="humanoid" -> rerig_humanoid.py,
# kind="custom" -> its `builder` (a script beside the model, run through run_builder.py), anything else ->
# rerig.py. The rig step writes <rig folder>/<model>.blend and .fbx; decimate rewrites the .fbx at the engine budget
# with at most four influences; audit.py judges that .fbx, the one an engine imports. Run publish.py afterwards for
# the cards. Exit code 1 if anything failed or any audit graded FAIL (CHECK is reported, not failed).
#
# Folders come from AUTORIG_MODELS / AUTORIG_WORK (see autorig/core/layout.py and spec_store.py).
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender, layout, spec_store

TAG = re.compile(r"^[A-Z][A-Z0-9_]+ ")


def step(script, *args):
    r = blender.run(script, *args)
    for line in r.stdout.splitlines():
        if TAG.match(line) or "Error" in line:
            print(line[:600])
    if r.returncode != 0 or "Traceback" in r.stdout + r.stderr:
        print((r.stdout + r.stderr)[-3000:])
        return False
    return True


def rig_script(model):
    """The script that rigs this model, or None when its spec cannot be rigged (no spec)."""
    spec = spec_store.SPECS.get(model)
    if spec is None: return None
    if spec.get("kind") == "humanoid": return "rerig_humanoid.py"
    if spec.get("kind") == "custom": return os.path.join(layout.pack_dir(model), spec["builder"])
    return "rerig.py"


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"): sys.exit("usage: python pipeline.py model[,model...] [-skipRig] [-rig script] [-qa dir] [-noSheets] [-preview] [--auto-tune]")
    if "--auto-tune" in a or "-autoTune" in a:
        max_iter = ["-max-iter", a[a.index("-max-iter") + 1]] if "-max-iter" in a else []
        auto_tune_script = os.path.join(os.path.dirname(HERE), "steps", "auto_tune.py")
        sys.exit(subprocess.call([sys.executable, auto_tune_script, a[0]] + max_iter))
    models = a[0].split(",")
    qa = a[a.index("-qa") + 1] if "-qa" in a else layout.work_dir("qa")
    ok = True
    for m in models:
        if "-skipRig" not in a:
            script = a[a.index("-rig") + 1] if "-rig" in a else rig_script(m)
            if script is None:
                print("PIPELINE %s has no rig spec (rig.json \"rig\")" % m); ok = False; continue
            ok &= step(script, "-only", m, "-qa", qa)
        ok &= step("decimate.py", "-only", m)
        if "-preview" in a:                                   # the results viewer's copy (steps/preview_glb.py)
            ok &= step("preview_glb.py", "-only", m)
    sys.stdout.flush()
    r = subprocess.run([sys.executable, os.path.join(HERE, "audit_all.py"), ",".join(models)] +
                       (["-render", "0"] if "-noSheets" in a else []))
    sys.exit(0 if ok and r.returncode == 0 else 1)


if __name__ == "__main__":
    main()

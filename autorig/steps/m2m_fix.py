# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one press to fix a Mesh2Motion rig's skin. Plain Python; runs steps/m2m_rig.py in Blender.
#
#   python autorig/steps/m2m_fix.py <model>
#   python autorig/cli/run.py fix-skin <model>          (also Advanced -> Fix skin on the page)
#
# The model keeps the skeleton it was fitted to (rig.json m2m: template and joints). Only the skin is tried again,
# several ways, each into a scratch folder and graded by steps/audit.py:
#   - autorig: our bone heat on a voxel copy, faded (core/skin_fade.py; the default);
#   - fine:    the same on a finer voxel copy, which keeps thin parts (udders, dewlaps, ears) apart from the body;
#   - coarse:  a coarser copy, which closes gaps between loose shells so they move with the body;
#   - ring:    ours as before M9: six passes of ring smoothing instead of the fade (still better on a few
#              models: docs/SKIN-BAKEOFF.md, "M9");
#   - m2m:     Mesh2Motion's own closest-bone skinning (core/m2m_skin.py).
# The best by audit (grade, then tears, then bleed) wins only if it beats the rig the model has now. The winner is
# then rigged into the model's own folder exactly as Auto rig would, with the same clips. Prints M2M_FIX {json}.
import json, os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender  # noqa: E402
import layout  # noqa: E402
import steps_registry  # noqa: E402

RANK = {"PASS": 0, "CHECK": 1, "FAIL": 2}
RESULT = re.compile(r"^M2M_RIG (\{.*\})\s*$", re.M)


def score(audit):
    """Smaller is better: the grade, then the tears, then the bleed. No audit is the worst."""
    if not audit or not audit.get("grade"):
        return (9, 1e9, 1e9)
    c = audit.get("checks") or {}
    return (RANK.get(audit["grade"], 3), c.get("combined_tears") or 0, c.get("bleed_pct") or 0)


def describe(audit):
    if not audit: return "no audit"
    c = audit.get("checks") or {}
    return "%s, %s tears, %s%% bleed" % (audit.get("grade"), c.get("combined_tears"), c.get("bleed_pct"))


def m2m_rig(args):
    """One m2m_rig run: (its M2M_RIG log, or None, and its output)."""
    r = blender.run("m2m_rig.py", *args)
    out = (r.stdout or "") + (r.stderr or "")
    m = None
    for m in RESULT.finditer(out): pass
    try:
        return (json.loads(m.group(1)) if m else None), out
    except ValueError:
        return None, out


def main(name):
    folder = layout.pack_dir(name)
    spec_path = os.path.join(folder, "rig.json")
    try:
        spec = json.load(open(spec_path, encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit("M2M_FIX %s has no rig.json: rig it first (Auto rig)" % name)
    m2m = spec.get("m2m") or (spec.get("rig") or {}).get("m2m") or {}
    if not m2m.get("template") or not m2m.get("joints"):
        sys.exit("M2M_FIX %s is not rigged on a Mesh2Motion skeleton yet: run Auto rig first" % name)
    rig_dir = layout.rigged_dir(name)
    logs = sorted((f for f in os.listdir(rig_dir) if f.endswith("_m2m.json")), key=lambda f: os.path.getmtime(os.path.join(rig_dir, f)))
    last = json.load(open(os.path.join(rig_dir, logs[-1]), encoding="utf-8")) if logs else {}
    current = last.get("audit")
    height = float(last.get("height") or 0) or 1.0
    clips = ",".join(m2m.get("clips") or [])
    app = steps_registry.m2m_app()

    work = tempfile.mkdtemp(prefix="m2m_fix_")
    joints = os.path.join(work, "joints.json")
    with open(joints, "w", encoding="utf-8") as fh:
        json.dump({"template": m2m["template"], "joints": m2m["joints"]}, fh)
    common = ["--template", m2m["template"], "--joints", joints, "--no-preview", "--format", "glb"]
    swing = ["--swing", "%g" % m2m["swing"]] if m2m.get("swing") not in (None, 1, 1.0) else []
    common += swing
    if app: common += ["--m2m", app]
    if last.get("source"): common += ["--in", last["source"]]
    if last.get("height"): common += ["--height", str(last["height"])]

    tries = [("autorig", ["--skin", "autorig"]),
             ("fine", ["--skin", "autorig", "--voxel", "%.4f" % (height / 120)]),
             ("coarse", ["--skin", "autorig", "--voxel", "%.4f" % (height / 45)]),
             ("ring", ["--skin", "autorig", "--fade", "0"]),
             ("m2m", ["--skin", "m2m"])]
    print("M2M_FIX now: %s" % describe(current), flush=True)
    results = []
    try:
        for label, extra in tries:
            out = os.path.join(work, label)
            log, text = m2m_rig(common + ["--name", "trial", "--out", out] + extra)
            audit = (log or {}).get("audit")
            if log:
                print("M2M_FIX try %-8s %s" % (label, describe(audit)), flush=True)
            else:
                tail = text.strip().splitlines()[-1] if text.strip() else "no output"
                print("M2M_FIX try %-8s failed: %s" % (label, tail), flush=True)
            if log: results.append((score(audit), label, extra, audit))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if not results:
        sys.exit("M2M_FIX every try failed: the rig is unchanged")
    results.sort(key=lambda r: r[0])
    best_score, label, extra, audit = results[0]
    if current and score(current) <= best_score:
        print("M2M_FIX " + json.dumps({"model": name, "kept": "current", "audit": current,
                                        "tries": {r[1]: r[3] for r in results}}))
        print("M2M_FIX no try beats the current skin (%s); the rig is unchanged" % describe(current))
        return 0

    # the winner, into the model's own folder as Auto rig writes it (rig.json, card, preview, clips)
    final = ["--model", name, "--template", m2m["template"]] + extra + swing
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"template": m2m["template"], "joints": m2m["joints"]}, fh)
        final += ["--joints", fh.name]
    if clips: final += ["--clips", clips]
    if app: final += ["--m2m", app]
    log, text = m2m_rig(final)
    os.unlink(fh.name)
    if not log:
        sys.exit("M2M_FIX the final rig with %s failed: %s" % (label, text.strip()[-400:]))
    print("M2M_FIX " + json.dumps({"model": name, "kept": label, "audit": log.get("audit"), "before": current,
                                    "tries": {r[1]: r[3] for r in results}}))
    print("M2M_FIX fixed with %s: %s (was %s)" % (label, describe(log.get("audit")), describe(current)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit("usage: python autorig/steps/m2m_fix.py <model>")
    sys.exit(main(sys.argv[1]))

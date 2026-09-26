#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: headless Blender CI audit thresholds verification script.
#
# Runs headless Blender pipeline steps against test models, executes audit.py,
# and verifies results against the audit thresholds via autorig/cli/audit_all.py.
#
# Exit 0 if all models PASS audit thresholds; non-zero on failure.
import json, math, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender, grades


def make_cylinder_obj(path, radius=0.25, height=2.0, seg=24, rings=30):
    """Generates an OBJ cylinder mesh."""
    v, f = [], []
    for r in range(rings + 1):
        y = height * r / rings
        for s in range(seg):
            a = 2 * math.pi * s / seg
            v.append((radius * math.cos(a), y, radius * math.sin(a)))
    for r in range(rings):
        for s in range(seg):
            a, b = r * seg + s, r * seg + (s + 1) % seg
            f.append((a, b, b + seg, a + seg))
    v += [(0, 0, 0), (0, height, 0)]
    lo, hi = len(v) - 2, len(v) - 1
    for s in range(seg):
        f.append((lo, (s + 1) % seg, s))
        f.append((hi, rings * seg + s, rings * seg + (s + 1) % seg))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("o sample_column\n")
        for p in v:
            fh.write("v %.5f %.5f %.5f\n" % p)
        for face in f:
            fh.write("f " + " ".join(str(i + 1) for i in face) + "\n")


def run_ci():
    print("=== Autorig CI: Headless Blender Audit Thresholds Verification ===", flush=True)

    # 1. Blender Version Check
    b_exe = blender.find(required=True)
    b_ver = blender.version_string(b_exe)
    print(f"Blender executable: {b_exe} ({b_ver})", flush=True)
    warn = blender.version_warning(b_exe)
    if warn:
        print(f"WARNING: {warn}", file=sys.stderr, flush=True)
    if not blender.is_tested_version(b_exe):
        print(f"Notice: Running outside primary tested version ({blender.TESTED_RANGE_DESC})", flush=True)

    tmp_dir = tempfile.mkdtemp(prefix="autorig-ci-audit-")
    try:
        models_dir = os.path.join(tmp_dir, "models")
        work_dir = os.path.join(tmp_dir, "work")
        os.makedirs(models_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

        env = dict(os.environ, AUTORIG_MODELS=models_dir, AUTORIG_WORK=work_dir)

        # 2. Setup Sample Model
        model_name = "sample_column"
        model_folder = os.path.join(models_dir, model_name)
        os.makedirs(model_folder, exist_ok=True)

        obj_path = os.path.join(model_folder, f"{model_name}.obj")
        make_cylinder_obj(obj_path)

        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "build",
                "forward": [0, -1, 0],
                "chains": [
                    {
                        "name": "spine",
                        "points": [[0.5, 0.5, 0.0], [0.5, 0.5, 0.5], [0.5, 0.5, 1.0]],
                        "names": ["body", "top"]
                    }
                ]
            },
            "budget": 500,
            "clips": {"archetype": "walker", "display": "Sample Column", "category": "CI"},
            "card": {"metres": 2.0, "role": "prop"},
            "notes": {"rig": "CI audit test model"}
        }
        with open(os.path.join(model_folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)

        # 3. Run Pipeline Steps: Survey, Rig, Trim
        steps = [
            ("survey.py", ["-only", model_name]),
            ("rerig.py", ["-only", model_name, "-qa", os.path.join(work_dir, "qa")]),
            ("decimate.py", ["-only", model_name])
        ]
        for step, args in steps:
            print(f"--> Running pipeline step: {step}...", flush=True)
            cmd = blender.command(step, *args)
            res = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace", timeout=300)
            if res.returncode != 0 or '"error":' in res.stdout:
                print(f"ERROR running {step}:\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}", file=sys.stderr)
                return 1
            print(f"    {step} completed successfully.", flush=True)

        # 4. Run Audit Step
        print("--> Running audit step: audit.py...", flush=True)
        audit_out = os.path.join(work_dir, "audit")
        audit_cmd = blender.command("audit.py", "-model", model_name, "-out", audit_out, "-render", "0")
        res = subprocess.run(audit_cmd, env=env, capture_output=True, text=True, errors="replace", timeout=300)
        if res.returncode != 0 or "AUDIT_DONE" not in res.stdout:
            print(f"ERROR running audit.py:\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}", file=sys.stderr)
            return 1

        # 5. Verify Individual Audit File & Thresholds
        audit_file = os.path.join(audit_out, f"{model_name}.json")
        if not os.path.exists(audit_file):
            print(f"ERROR: Missing audit file {audit_file}", file=sys.stderr)
            return 1
        with open(audit_file, "r", encoding="utf-8") as fh:
            audit_data = json.load(fh)

        print("--> Verifying audit results against strict thresholds:", flush=True)
        # the verdict audit.py graded and stored (its checks carry each value, limit and grade); an audit with no
        # checks is a failure, not a pass
        verdict = audit_data.get("verdict") or {}
        checks = verdict.get("checks") or {}
        grade = grades.grade_of(verdict, spec["rig"].get("audit"))
        print(f"    Grade: {grade} (Pass: {verdict.get('pass')})", flush=True)
        for chk, details in checks.items():
            val = details.get("value")
            lim = details.get("limit")
            grd = details.get("grade")
            print(f"    - {chk}: {val} (limit: {lim}) -> {grd}", flush=True)

        if not checks:
            print("FAILED: the audit has no checks to grade", file=sys.stderr)
            return 1
        if grade != grades.PASS or not verdict.get("pass"):
            print(f"FAILED: Expected PASS, got {grade}", file=sys.stderr)
            return 1

        # 6. Run audit_all CLI with -strict
        print("--> Running audit_all CLI across collection with -strict...", flush=True)
        audit_all_py = os.path.join(REPO, "autorig", "cli", "audit_all.py")
        res = subprocess.run([sys.executable, audit_all_py, "all", "-render", "0", "-strict"],
                             env=env, capture_output=True, text=True, errors="replace", timeout=120)
        print(res.stdout, flush=True)
        if res.returncode != 0:
            print(f"FAILED: audit_all returned code {res.returncode}\n{res.stderr}", file=sys.stderr)
            return 1

        print("=== CI Audit Thresholds Check PASSED ===", flush=True)
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run_ci())

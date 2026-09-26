# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Batch Rigger execution engine.
#
# Runs multiple models through selected pipeline steps, auto-tuning, and engine packaging
# with resilient error isolation, timing, and formatted status reporting.

import fnmatch
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import blender
    import layout
    import spec_store
    import grades
    import exporter
except ImportError:
    from autorig.core import blender
    from autorig.core import layout
    from autorig.core import spec_store
    from autorig.core import grades
    from autorig.core import exporter


DEFAULT_STEPS = ["rig", "trim", "audit"]
ALL_STEPS = ["survey", "rig", "trim", "audit", "clips", "publish", "preview"]


def resolve_models(names=None, group=None, pattern=None, riggable_only=False):
    """Resolves a list of model names from targets, group filter, and wildcard patterns."""
    all_pairs = layout.all_models()  # [(group, name), ...]
    if group is not None:
        all_pairs = [p for p in all_pairs if p[0] == (group or "")]

    is_riggable_query = (names and ("riggable" in names or names == ["riggable"])) or riggable_only

    if not names or names == ["all"] or "all" in names or is_riggable_query:
        resolved = [p[1] for p in all_pairs]
    else:
        # Check explicit names
        explicit = set()
        for item in names:
            for sub in item.split(","):
                s = sub.strip()
                if s:
                    explicit.add(s)
        resolved = [p[1] for p in all_pairs if p[1] in explicit]

    if pattern:
        resolved = [m for m in resolved if fnmatch.fnmatch(m, pattern) or fnmatch.fnmatch(m.lower(), pattern.lower())]

    if is_riggable_query:
        resolved = [m for m in resolved if (spec_store.model(m) or {}).get("rig")]

    return sorted(list(dict.fromkeys(resolved)))


def run_model_pipeline(model, steps=None, auto_tune=False, export_target=None):
    """Runs a single model through the requested pipeline steps.
    Returns: dict with timing, executed steps, audit grade, and status."""
    t0 = time.time()
    steps_to_run = list(steps or DEFAULT_STEPS)
    executed = []
    audit_grade = None
    export_result = None
    status = "OK"
    error_msg = None

    group = layout.find_group(model) if hasattr(layout, "find_group") else ""
    if not group:
        for g, m in layout.all_models():
            if m == model:
                group = g
                break

    pack_d = layout.pack_dir(model)
    spec = spec_store.model(model)

    for st in steps_to_run:
        st = st.strip().lower()
        if not st:
            continue
        try:
            if st == "survey":
                r = blender.run("survey.py", "-only", model)
                if r.returncode != 0:
                    raise RuntimeError("survey step failed")
                executed.append("survey")

            elif st == "rig":
                # Determine rig script from spec
                if not spec or not spec.get("rig"):
                    raise ValueError(f"no rig spec (rig.json 'rig') for '{model}'")
                kind = spec.get("rig", {}).get("kind", spec.get("kind", "placed"))
                script = "rerig_humanoid.py" if kind == "humanoid" else (
                    os.path.join(pack_d, spec.get("rig", {}).get("builder") or spec.get("builder") or "") if kind == "custom" else "rerig.py"
                )
                r = blender.run(script, "-only", model, "-qa", layout.work_dir("qa"))
                if r.returncode != 0:
                    raise RuntimeError(f"rig step failed ({os.path.basename(script)})")
                executed.append("rig")

            elif st == "trim":
                r = blender.run("decimate.py", "-only", model)
                if r.returncode != 0:
                    raise RuntimeError("trim (decimate) step failed")
                executed.append("trim")

            elif st == "audit":
                audit_script = os.path.join(PKG, "cli", "audit_all.py")
                r = subprocess.run([sys.executable, audit_script, model], capture_output=True, text=True, cwd=PKG)
                audit_file = os.path.join(layout.work_dir("audit"), f"{model}.json")
                if os.path.isfile(audit_file):
                    try:
                        v = json.load(open(audit_file, encoding="utf-8")).get("verdict", {})
                        audit_grade = grades.grade_of(v) if v else None
                    except Exception:
                        pass
                executed.append("audit")

                # If auto-tune enabled and audit failed/checked, run self-healing optimizer
                if auto_tune and audit_grade in ("FAIL", "CHECK"):
                    tune_script = os.path.join(PKG, "steps", "auto_tune.py")
                    subprocess.run([sys.executable, tune_script, model, "-max-iter", "3"],
                                   capture_output=True, text=True, cwd=PKG)
                    # Re-read audit
                    if os.path.isfile(audit_file):
                        try:
                            v = json.load(open(audit_file, encoding="utf-8")).get("verdict", {})
                            audit_grade = grades.grade_of(v) if v else audit_grade
                        except Exception:
                            pass
                    executed.append("auto-tune")

            elif st == "clips":
                c_arch = (spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}).get("archetype")
                if c_arch:
                    r = blender.run("make_clips.py", model)
                    if r.returncode != 0:
                        raise RuntimeError(f"clips step failed for '{model}'")
                    executed.append("clips")

            elif st == "publish":
                pub_script = os.path.join(PKG, "steps", "publish.py")
                subprocess.run([sys.executable, pub_script, group or ".", "-only", model],
                               capture_output=True, text=True, cwd=PKG)
                executed.append("publish")

            elif st == "preview":
                r = blender.run("preview_glb.py", "-only", model)
                if r.returncode == 0:
                    executed.append("preview")

        except Exception as e:
            status = "ERROR"
            error_msg = str(e)
            break

    # If export requested and pipeline succeeded
    if export_target and status != "ERROR":
        try:
            export_result = exporter.create_export_package(model, target=export_target)
            executed.append(f"export:{export_target}")
        except Exception as e:
            export_result = {"error": str(e)}

    elapsed = round(time.time() - t0, 2)
    if status != "ERROR" and audit_grade == "FAIL":
        status = "FAIL"
    elif status != "ERROR" and audit_grade == "CHECK":
        status = "CHECK"

    return {
        "model": model,
        "group": group or "(root)",
        "steps": executed,
        "grade": audit_grade or "-",
        "status": status,
        "duration": elapsed,
        "export": export_result.get("zip_rel") if export_result and "zip_rel" in export_result else None,
        "error": error_msg,
    }


def run_batch(models=None, group=None, pattern=None, steps=None, auto_tune=False, export_target=None, continue_on_error=True, riggable_only=False, workers=1):
    """Executes the pipeline in batch across resolved models.
    Returns: dict with summary metrics and per-model results list."""
    target_models = resolve_models(names=models, group=group, pattern=pattern, riggable_only=riggable_only)
    if not target_models:
        return {"models_count": 0, "results": [], "passed": 0, "failed": 0, "elapsed": 0.0}

    t_start = time.time()
    results = []
    passed = 0
    failed = 0

    if workers and workers > 1:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_model = {
                executor.submit(run_model_pipeline, m, steps=steps, auto_tune=auto_tune, export_target=export_target): m
                for m in target_models
            }
            for future in concurrent.futures.as_completed(future_to_model):
                res = future.result()
                results.append(res)
                if res["status"] in ("OK", "PASS", "CHECK"):
                    passed += 1
                else:
                    failed += 1
                print(f"[{len(results)}/{len(target_models)}] {res['model']} ({res['group']}): {res['status']} {res['grade']} ({res['duration']}s)", flush=True)
                if not continue_on_error and res["status"] not in ("OK", "PASS", "CHECK"):
                    break
        model_order = {m: i for i, m in enumerate(target_models)}
        results.sort(key=lambda r: model_order.get(r["model"], 999999))
    else:
        for m in target_models:
            res = run_model_pipeline(m, steps=steps, auto_tune=auto_tune, export_target=export_target)
            results.append(res)
            if res["status"] in ("OK", "PASS", "CHECK"):
                passed += 1
            else:
                failed += 1
            print(f"[{len(results)}/{len(target_models)}] {res['model']} ({res['group']}): {res['status']} {res['grade']} ({res['duration']}s)", flush=True)
            if not continue_on_error and res["status"] not in ("OK", "PASS", "CHECK"):
                break

    total_time = round(time.time() - t_start, 2)
    return {
        "models_count": len(target_models),
        "results": results,
        "passed": passed,
        "failed": failed,
        "elapsed": total_time,
    }


def format_batch_table(batch_summary):
    """Formats an ASCII summary table of the batch run."""
    results = batch_summary.get("results", [])
    if not results:
        return "No models were matched for batch execution."

    header = f"{'Model':<16} {'Group':<12} {'Steps Run':<26} {'Grade':<8} {'Time':>7}  {'Status'}"
    sep = "-" * len(header)
    lines = [header, sep]

    for r in results:
        steps_str = ",".join(r["steps"])
        if len(steps_str) > 25:
            steps_str = steps_str[:22] + "..."
        line = f"{r['model']:<16} {r['group']:<12} {steps_str:<26} {r['grade']:<8} {r['duration']:>6.1f}s  {r['status']}"
        if r.get("error"):
            line += f" ({r['error']})"
        lines.append(line)

    lines.append(sep)
    lines.append(f"Total: {batch_summary['models_count']} models | {batch_summary['passed']} Passed | {batch_summary['failed']} Failed | Elapsed: {batch_summary['elapsed']}s")
    return "\n".join(lines)

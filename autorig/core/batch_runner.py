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


def _tail(r, n=6):
    """The last lines a failed step printed, for the batch table."""
    text = ((getattr(r, "stderr", "") or "") + "\n" + (getattr(r, "stdout", "") or "")).strip()
    return " | ".join(text.splitlines()[-n:])


def _audit_grade(audit_file, spec):
    v = json.load(open(audit_file, encoding="utf-8")).get("verdict", {})
    return grades.grade_of(v, (spec.get("rig") or {}).get("audit")) if v else None


def publish(model, group):
    """publish.py for one model (plain Python: it writes the card and the group's pack.json)."""
    return subprocess.run([sys.executable, os.path.join(PKG, "steps", "publish.py"), group or ".", "-only", model],
                          capture_output=True, text=True, cwd=PKG)


def run_model_pipeline(model, steps=None, auto_tune=False, export_target=None, deferred_publish=None):
    """Runs a single model through the requested pipeline steps. With a deferred_publish list, a publish step is
    appended to it as (model, group) instead of run (run_batch runs those one at a time after a parallel batch).
    Returns: dict with timing, executed steps, audit grade, and status."""
    t0 = time.time()
    steps_to_run = list(steps or DEFAULT_STEPS)
    executed = []
    audit_grade = None
    clip_grade = None
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
    try:
        spec = spec_store.model(model)      # a rig.json that cannot be read fails this model, not the batch
    except Exception as e:
        return {"model": model, "group": group or "(root)", "steps": [], "grade": "-", "status": "ERROR",
                "duration": round(time.time() - t0, 2), "export": None, "error": "rig.json: %s" % e}

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
                rig = spec["rig"]
                kind = rig.get("kind", "placed")
                if kind == "custom":
                    if not rig.get("builder"):
                        raise ValueError(f"'{model}' is kind custom but rig.json names no rig.builder")
                    script = os.path.join(pack_d, rig["builder"])      # FORMATS.md: the builder is rig.builder
                    if not os.path.isfile(script):
                        raise ValueError(f"custom builder {rig['builder']} is not in the model folder")
                else:
                    script = "rerig_humanoid.py" if kind == "humanoid" else "rerig.py"
                r = blender.run(script, "-only", model, "-qa", layout.work_dir("qa"))
                if r.returncode != 0 or blender.step_error(r.stdout):
                    raise RuntimeError(f"rig step failed ({os.path.basename(script)}): {blender.step_error(r.stdout) or _tail(r)}")
                executed.append("rig")

            elif st == "trim":
                r = blender.run("decimate.py", "-only", model)
                if r.returncode != 0 or blender.step_error(r.stdout):
                    raise RuntimeError(f"trim (decimate) step failed: {_tail(r)}")
                executed.append("trim")

            elif st == "audit":
                # the same step the GUI runs; an old audit is removed first, so a failed audit never reports
                # the previous run's grade as this one's
                audit_dir = layout.work_dir("audit")
                audit_file = os.path.join(audit_dir, f"{model}.json")
                if os.path.exists(audit_file):
                    os.remove(audit_file)
                r = blender.run("audit.py", "-model", model, "-out", audit_dir)
                if r.returncode != 0 or not os.path.isfile(audit_file):
                    raise RuntimeError(f"audit step failed: {_tail(r)}")
                audit_grade = _audit_grade(audit_file, spec)
                executed.append("audit")

                # If auto-tune enabled and audit failed/checked, run self-healing optimizer
                if auto_tune and audit_grade in ("FAIL", "CHECK"):
                    tune_script = os.path.join(PKG, "steps", "auto_tune.py")
                    rt = subprocess.run([sys.executable, tune_script, model, "-max-iter", "3"],
                                        capture_output=True, text=True, cwd=PKG)
                    if rt.returncode != 0:
                        raise RuntimeError(f"auto-tune failed: {_tail(rt)}")
                    spec_store.reload()
                    spec = spec_store.model(model)
                    if os.path.isfile(audit_file):
                        audit_grade = _audit_grade(audit_file, spec) or audit_grade
                    executed.append("auto-tune")

            elif st == "clips":
                # the archetype the GUI would use: rig.json's, else the one inferred from the skeleton
                c_arch = spec_store.infer_clip_archetype(spec)
                if c_arch:
                    r = blender.run("make_clips.py", model, "--archetype", c_arch)
                    if r.returncode != 0 or blender.step_error(r.stdout):
                        raise RuntimeError(f"clips step failed for '{model}': {_tail(r)}")
                    executed.append("clips")
                    # the clips played and graded; a clip audit that cannot run is reported, not fatal
                    ca_dir = layout.work_dir("clip_audit")
                    ca = blender.run("clip_audit.py", "-model", model, "-out", ca_dir)
                    ca_file = os.path.join(ca_dir, f"{model}.json")
                    if ca.returncode == 0 and os.path.isfile(ca_file):
                        try:
                            with open(ca_file, encoding="utf-8") as fh:
                                clip_grade = json.load(fh).get("grade")
                        except (OSError, ValueError):
                            pass

            elif st == "publish":
                if deferred_publish is not None:
                    # a parallel batch: publish rewrites the group's shared pack.json, so it runs afterwards, alone
                    deferred_publish.append((model, group))
                    executed.append("publish")
                    continue
                rp = publish(model, group)
                if rp.returncode != 0:
                    raise RuntimeError(f"publish step failed: {_tail(rp)}")
                executed.append("publish")

            elif st == "preview":
                r = blender.run("preview_glb.py", "-only", model)
                if r.returncode != 0 or blender.step_error(r.stdout):
                    raise RuntimeError(f"preview step failed: {_tail(r)}")
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
        "clip_grade": clip_grade,
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
        deferred = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_model = {
                executor.submit(run_model_pipeline, m, steps=steps, auto_tune=auto_tune, export_target=export_target,
                                deferred_publish=deferred): m
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
                    for f in future_to_model:      # stop: models not started yet are dropped, running ones finish
                        f.cancel()
                    break
        by_model = {r["model"]: r for r in results}
        for m, g in deferred:                      # the publishes, one at a time, now nothing else writes the pack
            if by_model.get(m, {}).get("status") == "ERROR":
                continue
            rp = publish(m, g)
            if rp.returncode != 0 and m in by_model:
                if by_model[m]["status"] in ("OK", "PASS", "CHECK"):
                    passed -= 1; failed += 1
                by_model[m].update(status="ERROR", error="publish step failed: " + _tail(rp))
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

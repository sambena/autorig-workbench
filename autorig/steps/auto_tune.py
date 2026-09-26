# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: closed-loop auto-tune CLI step.
#
# Runs an iterative optimization loop on a model:
#   1. Reads model spec and executes QA audit to identify tear sites, worst bones, and bleed.
#   2. Proposes targeted micro-adjustments to joint blend, capsules, smoothing, and bone stations.
#   3. Re-rigs, decimates, and re-audits over up to N iterations (default 3).
#   4. Evaluates whether the objective improved (reduced tears/gaps and improved audit verdict).
#   5. Converges on the best spec and updates rig.json.
#
# Usage:
#   python autorig/steps/auto_tune.py <model> [-max-iter 3] [-dry-run] [-out <work_dir>]
import argparse, copy, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import auto_tune, blender, layout, spec_store


def rig_script_for(model):
    """Returns the rigging script appropriate for the model."""
    spec = spec_store.SPECS.get(model) or spec_store.load_spec(model)
    if not spec:
        return "rerig.py"
    rig = spec.get("rig", spec)
    kind = rig.get("kind")
    if kind == "humanoid":
        return "rerig_humanoid.py"
    if kind == "custom" and rig.get("builder"):
        return os.path.join(layout.pack_dir(model), rig["builder"])
    return "rerig.py"


def run_pipeline_eval(model, work_dir=None, qa_dir=None, log_fn=None):
    """Executes rig -> decimate -> audit (fast, -render 0) and returns the audit result dict."""
    w = work_dir or layout.work_dir("audit")
    qa = qa_dir or layout.work_dir("qa")
    script = rig_script_for(model)

    if log_fn:
        log_fn(f"  [Eval] Running {script}...")
    r_rig = blender.run(script, "-only", model, "-qa", qa)
    if r_rig.returncode != 0 and "Traceback" in r_rig.stdout + r_rig.stderr:
        if log_fn:
            log_fn(f"  [Eval] Rig failed:\n{(r_rig.stdout + r_rig.stderr)[-500:]}")
        return None

    if log_fn:
        log_fn("  [Eval] Running decimate.py...")
    r_dec = blender.run("decimate.py", "-only", model)
    if r_dec.returncode != 0:
        if log_fn:
            log_fn(f"  [Eval] Decimate failed:\n{(r_dec.stdout + r_dec.stderr)[-500:]}")
        return None

    if log_fn:
        log_fn("  [Eval] Running audit.py (-render 0)...")
    r_audit = blender.run("audit.py", "-model", model, "-out", w, "-render", "0")
    if r_audit.returncode != 0:
        if log_fn:
            log_fn(f"  [Eval] Audit failed:\n{(r_audit.stdout + r_audit.stderr)[-500:]}")
        return None

    audit_path = os.path.join(w, f"{model}.json")
    if not os.path.exists(audit_path):
        return None

    try:
        with open(audit_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        if log_fn:
            log_fn(f"  [Eval] Failed reading audit JSON: {e}")
        return None


def get_spec_path(model):
    """Returns the absolute path to rig.json for a model."""
    pack = layout.pack_dir(model)
    return os.path.join(pack, "rig.json")


def save_spec_to_disk(model, spec):
    """Writes a spec dictionary to rig.json for a model."""
    path = get_spec_path(model)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)


def auto_tune_model(model, max_iterations=3, dry_run=False, work_dir=None, log_fn=print):
    """Main closed-loop optimization driver for a model.

    Returns (success: bool, best_score: float, history: list, best_spec: dict).
    """
    spec_path = get_spec_path(model)
    if not os.path.exists(spec_path):
        log_fn(f"ERROR: No rig.json found for {model} at {spec_path}")
        return False, float("inf"), [], None

    with open(spec_path, "r", encoding="utf-8") as fh:
        orig_spec = json.load(fh)

    w = work_dir or layout.work_dir("audit")
    audit_path = os.path.join(w, f"{model}.json")

    # 1. Baseline evaluation
    log_fn(f"=== Auto-Tuning Closed Loop: {model} (max {max_iterations} iterations) ===")
    initial_audit = None
    if os.path.exists(audit_path):
        try:
            with open(audit_path, "r", encoding="utf-8") as fh:
                initial_audit = json.load(fh)
        except Exception:
            pass

    if initial_audit is None:
        log_fn("Running initial baseline evaluation...")
        initial_audit = run_pipeline_eval(model, work_dir=w, log_fn=log_fn)

    if initial_audit is None:
        log_fn("ERROR: Could not establish baseline audit.")
        return False, float("inf"), [], orig_spec

    initial_score = auto_tune.audit_score(initial_audit)
    initial_diag = auto_tune.diagnose_audit(initial_audit)

    history = [
        {
            "iteration": 0,
            "action": "Baseline",
            "score": initial_score,
            "grade": initial_diag["grade"],
            "pass": initial_diag["pass"],
            "combined_tears": initial_diag["combined_tears"],
            "bend_tears": initial_diag["bend_tears"],
            "worst_gap_pct": initial_diag["worst_gap_pct"],
            "bleed_pct": initial_diag["bleed_pct"],
            "worst_bone": initial_diag["worst_bone"],
            "accepted": None,
        }
    ]

    log_fn(
        f"Baseline: Grade {initial_diag['grade']}, Comb Tears {initial_diag['combined_tears']}, "
        f"Bend Tears {initial_diag['bend_tears']}, Bleed {initial_diag['bleed_pct']:.1f}%, Score {initial_score:.1f}"
    )

    if initial_score == 0.0 and initial_diag["pass"]:
        log_fn("Rig is already optimal (PASS with 0 tears). No tuning required.")
        return True, 0.0, history, orig_spec

    best_spec = copy.deepcopy(orig_spec)
    best_score = initial_score
    best_audit = initial_audit

    # Backup original rig.json
    bak_path = spec_path + ".bak"
    if not os.path.exists(bak_path):
        shutil.copy2(spec_path, bak_path)

    # 2. Iterative optimization loop. Every candidate is rigged in place (rigged/<model>.blend, .fbx, the audit),
    # so after a rejected one the files on disk are the candidate's, not the best spec's: on_disk tracks that,
    # and the end of the loop rigs the spec it keeps once more when they differ.
    on_disk = "best"
    try:
        for iteration in range(1, max_iterations + 1):
            cand = auto_tune.propose_tuning_candidate(best_spec, best_audit, iteration=iteration, history=history)
            if not cand:
                log_fn(f"Iteration {iteration}: No further candidate proposals available.")
                break

            log_fn(f"Iteration {iteration}/{max_iterations}: Proposed -> {cand['description']}")

            # Apply candidate to disk
            save_spec_to_disk(model, cand["spec"])

            # Re-evaluate
            on_disk = "candidate"
            new_audit = run_pipeline_eval(model, work_dir=w, log_fn=log_fn)
            if new_audit is None:
                log_fn(f"  Iteration {iteration}: Evaluation failed. Rejecting candidate.")
                save_spec_to_disk(model, best_spec)
                history.append({
                    "iteration": iteration,
                    "action": cand["description"],
                    "param": cand.get("param"),
                    "score": float("inf"),
                    "grade": "FAIL",
                    "pass": False,
                    "combined_tears": 0,
                    "bend_tears": 0,
                    "worst_gap_pct": 0.0,
                    "bleed_pct": 0.0,
                    "accepted": False,
                })
                continue

            new_score = auto_tune.audit_score(new_audit)
            new_diag = auto_tune.diagnose_audit(new_audit)

            accepted = new_score < best_score
            step_record = {
                "iteration": iteration,
                "action": cand["description"],
                "param": cand.get("param"),
                "score": new_score,
                "grade": new_diag["grade"],
                "pass": new_diag["pass"],
                "combined_tears": new_diag["combined_tears"],
                "bend_tears": new_diag["bend_tears"],
                "worst_gap_pct": new_diag["worst_gap_pct"],
                "bleed_pct": new_diag["bleed_pct"],
                "worst_bone": new_diag["worst_bone"],
                "accepted": accepted,
            }
            history.append(step_record)

            if accepted:
                delta = best_score - new_score
                log_fn(f"  ACCEPTED (Score improved {best_score:.1f} -> {new_score:.1f}, -{delta:.1f})")
                best_score = new_score
                best_spec = copy.deepcopy(cand["spec"])
                best_audit = new_audit
                on_disk = "best"
                if best_score == 0.0 and new_diag["pass"]:
                    log_fn("  Target achieved: PASS with 0 tears! Converged early.")
                    break
            else:
                log_fn(f"  REJECTED (Score {new_score:.1f} did not improve over {best_score:.1f}). Reverting.")
                save_spec_to_disk(model, best_spec)

    finally:
        if dry_run:
            log_fn("[Dry Run] Reverting to original spec.")
            save_spec_to_disk(model, orig_spec)
            keep = orig_spec
        else:
            save_spec_to_disk(model, best_spec)
            keep = best_spec
        if on_disk != "best" or (dry_run and keep is not best_spec):
            log_fn("Rigging the spec kept once more, so the rig and audit on disk are its own.")
            run_pipeline_eval(model, work_dir=w, log_fn=log_fn)

    # 3. Final summary
    report = auto_tune.format_tuning_report(history)
    log_fn("\n" + report + "\n")

    is_success = best_score < initial_score or best_score == 0.0
    return is_success, best_score, history, best_spec


def main(argv=None):
    ap = argparse.ArgumentParser(description="Autorig Workbench: closed-loop auto-tune optimizer.")
    ap.add_argument("models", help="Comma-separated model name(s)")
    ap.add_argument("-max-iter", "--max-iterations", type=int, default=3, help="Max tuning iterations (default: 3)")
    ap.add_argument("-dry-run", "--dry-run", action="store_true", help="Evaluate candidates without saving to disk")
    ap.add_argument("-out", help="Work output folder (default: <AUTORIG_WORK>/audit)")
    a = ap.parse_args(argv)

    models = a.models.split(",")
    overall_ok = True
    for m in models:
        m = m.strip()
        if not m:
            continue
        ok, score, hist, spec = auto_tune_model(
            m,
            max_iterations=a.max_iterations,
            dry_run=a.dry_run,
            work_dir=a.out,
            log_fn=print,
        )
        overall_ok &= ok

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()

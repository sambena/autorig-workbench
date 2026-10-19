# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the quality score. Measures rigged models (steps/score.py, one Blender each), adds the audit's
# numbers, scores each 0-100 (core/quality_score.py; PASS >= 90) and prints a ranked table, worst first.
#
#   python autorig/cli/run.py score [models|all] [--group G] [--out dir] [--render 0] [--audit read|run|skip]
#                                   [--rescore] [--tiles 6]
#
#   models     a comma list, or all (every model with a rigged preview.glb); --group limits either to one group
#   --out      default <AUTORIG_WORK>/score; each model gets <out>/<model>/ with measure.json, score.json and a
#              contact sheet per clip (side over front), and the table goes in <out>/_ranked.json and _ranked.md
#   --audit    read (default): the model's committed rigged/audit/<model>.json, else the work audit, else none;
#              run: audit it now (numbers only) into <out>/<model>/; skip: score without it
#   --rescore  no Blender: score the measure.json files already in <out> again (after changing the limits)
#
# docs/QUALITY.md explains the checks and the calibration. Exit code 0 when no model FAILs.
import contextlib, io, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender, layout, quality_score as QS


def rig_glb(model):
    rd = layout.rigged_dir(model)
    for name in ("preview.glb", layout.leaf(model) + ".glb"):
        p = os.path.join(rd, name)
        if os.path.exists(p): return p
    return None


def scored_models(group=None):
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for g, m in layout.all_models():
            if group and g != group: continue
            if rig_glb(m): out.append(m)
    return out


def read_json(p):
    try:
        with open(p, encoding="utf-8") as fh: return json.load(fh)
    except (OSError, ValueError):
        return None


def audit_for(model, mode, out):
    if mode == "skip": return None, None
    leaf = layout.leaf(model)
    if mode == "run":
        r = blender.run("audit.py", "-model", model, "-out", out, "-render", "0")
        p = os.path.join(out, leaf + ".json")
        return (read_json(p), p) if "AUDIT_DONE" in r.stdout else (None, None)
    for p in (os.path.join(layout.rigged_dir(model), "audit", leaf + ".json"),
              os.path.join(layout.work_dir("audit"), leaf + ".json")):
        d = read_json(p)
        if d: return d, p
    return None, None


def source_for(model):
    with contextlib.redirect_stdout(io.StringIO()):
        try: return layout.source_model(model)
        except Exception: return None


def score_one(model, out, opts):
    d = os.path.join(out, model)
    os.makedirs(d, exist_ok=True)
    glb = rig_glb(model)
    if not opts.get("rescore"):
        if not glb: return {"model": model, "error": "no rigged preview.glb"}
        args = ["--glb", glb, "--out", d, "--name", model, "--render", opts.get("render", "1"),
                "--tiles", str(opts.get("tiles", 6))]
        src = source_for(model)
        if src: args += ["--source", src]
        t = time.time()
        r = blender.run("score.py", *args)
        for line in r.stdout.splitlines():
            if line.startswith("SCORE ") and " clip " not in line: print("  " + line[:240], flush=True)
        if "SCORE DONE" not in r.stdout:
            return {"model": model, "error": "measure did not finish: " + (r.stdout + r.stderr)[-800:]}
        print("  measured in %.0f s" % (time.time() - t), flush=True)
    measured = read_json(os.path.join(d, "measure.json"))
    if not measured: return {"model": model, "error": "no measure.json"}
    audit, audit_path = audit_for(model, opts.get("audit", "read"), d)
    s = QS.score(measured, audit)
    s.update({"model": model, "group": layout.group_of(model), "format": "autorig-score/1",
              "audit": audit_path, "clips": [c["name"] for c in measured.get("clips", [])],
              "sheets": sorted(f for f in os.listdir(d) if f.startswith("sheet_") and f.endswith(".png"))})
    with open(os.path.join(d, "score.json"), "w", encoding="utf-8") as fh: json.dump(s, fh, indent=1)
    return s


def fmt(v):
    return "-" if v is None else ("%.2f" % v if isinstance(v, float) else str(v))


COLS = ("rest_mean_pct", "rest_p99_pct", "stretch3_permille", "squash_permille", "arm_in_torso_pct", "leg_cross_pct",
        "foot_skate", "hover_pct", "sink_pct", "audit_bleed_pct")
SHORT = ("rest", "rest99", "str3", "squash", "arm-in", "legX", "skate", "hover", "sink", "bleed")


def table(rows):
    rows = sorted(rows, key=lambda r: (r.get("score") is not None, r.get("score") or 0, r["model"]))
    lines = ["| model | score | grade | " + " | ".join(SHORT) + " | worst |",
             "|---|---|---|" + "---|" * len(SHORT) + "---|"]
    for r in rows:
        if r.get("error"):
            lines.append("| %s | - | ERROR | %s | %s |" % (r["model"], " | ".join("-" * 1 for _ in SHORT), r["error"][:60].replace("|", "/").replace("\n", " ")))
            continue
        v = r["values"]
        lines.append("| %s | %.1f | %s | %s | %s |" % (r["model"], r["score"], r["grade"],
                     " | ".join(fmt(v.get(k)) for k in COLS), ", ".join(r["worst"])))
    return rows, "\n".join(lines)


def main(argv):
    a = list(argv)
    def opt(name, default=None):
        if name in a:
            i = a.index(name); v = a[i + 1]; del a[i:i + 2]; return v
        return default
    group = opt("--group"); out = opt("--out") or layout.work_dir("score")
    opts = {"render": opt("--render", "1"), "audit": opt("--audit", "read"), "tiles": int(opt("--tiles", "6"))}
    if "--rescore" in a: a.remove("--rescore"); opts["rescore"] = True
    target = a[0] if a else "all"
    if target == "all":
        models = scored_models(group) if not opts.get("rescore") else sorted(
            m for m in os.listdir(out) if os.path.exists(os.path.join(out, m, "measure.json")))
    else:
        models = [m for m in target.split(",") if m and (not group or layout.group_of(m) == group)]
    if not models: sys.exit("no rigged models to score")
    os.makedirs(out, exist_ok=True)
    rows = []
    for k, m in enumerate(models, 1):
        print("SCORE_ALL %d/%d %s" % (k, len(models), m), flush=True)
        r = score_one(m, out, opts)
        rows.append(r)
        print("  -> %s %s %s" % (fmt(r.get("score")), r.get("grade") or "ERROR", ", ".join(r.get("worst") or []) or r.get("error", "")[:200]), flush=True)
    # a partial run keeps the other models' rows in the table
    old = read_json(os.path.join(out, "_ranked.json")) or {}
    have = {r["model"] for r in rows}
    rows += [r for r in old.get("models", []) if r.get("model") not in have and os.path.exists(os.path.join(out, r["model"], "score.json"))]
    rows, md = table(rows)
    with open(os.path.join(out, "_ranked.json"), "w", encoding="utf-8") as fh: json.dump({"models": rows}, fh, indent=1)
    with open(os.path.join(out, "_ranked.md"), "w", encoding="utf-8") as fh: fh.write(md + "\n")
    print(md)
    return 1 if any(r.get("grade") in ("FAIL", None) for r in rows if r["model"] in have) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

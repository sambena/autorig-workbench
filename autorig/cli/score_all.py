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


def rig_source(model):
    """(rig file, clips blend) for a model with no preview.glb: the rig's .blend (else its FBX) in the rig folder,
    named after the model, its card's game name, or the only one there; and clips/<name>_clips.blend if any. A classic
    rig (rerig.py + make_clips.py) keeps its clips there and has no GLB until the preview step runs."""
    import glob
    rd, pd, leaf = layout.rigged_dir(model), layout.pack_dir(model), layout.leaf(model)
    names = [leaf]
    try:
        with open(os.path.join(pd, "model.json"), encoding="utf-8") as fh: game = json.load(fh).get("game_name")
        if game: names.append(game)
    except (OSError, ValueError):
        pass
    rig = None
    for ext in (".blend", ".fbx"):
        hits = [os.path.join(rd, n + ext) for n in names if os.path.exists(os.path.join(rd, n + ext))]
        if not hits:
            found = [p for p in glob.glob(os.path.join(glob.escape(rd), "*" + ext)) if "_mocap_" not in p
                     and not os.path.basename(p).endswith("_v1" + ext)]
            hits = found if len(found) == 1 else []
        if hits: rig = hits[0]; break
    clips = None
    for n in names:
        p = os.path.join(pd, "clips", n + "_clips.blend")
        if os.path.exists(p): clips = p; break
    if clips is None:
        found = glob.glob(os.path.join(glob.escape(os.path.join(pd, "clips")), "*_clips.blend"))
        clips = found[0] if len(found) == 1 else None
    return rig, clips


def build_preview(model, d):
    """A preview.glb in the score folder (never the model's own) from the rig and its clips: (path, None) or
    (None, why not)."""
    rig, clips = rig_source(model)
    if not rig: return None, "rigged folder has no .blend or .fbx it can tell is the rig"
    out = os.path.join(d, "preview.glb")
    args = ["-file", rig, "-out", out] + (["-clips", clips] if clips else [])
    r = blender.run("preview_glb.py", *args)
    if not os.path.exists(out):
        line = next((l for l in r.stdout.splitlines() if l.startswith("PREVIEW ")), "")
        return None, "preview did not build: " + (line[8:] or (r.stdout + r.stderr)[-300:]).replace("\n", " ")[:300]
    return out, None


def scored_models(group=None, skipped=None):
    """Every model with a rig to score: a preview.glb, or a rigged .blend/.fbx to build one from. A model with no
    rigged folder is not rigged (a prop that needs none) and goes in `skipped` with that reason."""
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for g, m in layout.all_models():
            if group and g != group: continue
            if rig_glb(m) or rig_source(m)[0]: out.append(m)
            elif skipped is not None:
                skipped[m] = "not rigged" if not os.path.isdir(layout.rigged_dir(m)) else "rigged folder has no rig file"
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


def rest_by_design(model):
    """True when the model's rig rests in its template's pose on purpose (rig.json m2m.rest "template")."""
    try:
        import spec_store
        return (spec_store.model(model).get("m2m") or {}).get("rest") == "template"
    except Exception:
        return False


def score_one(model, out, opts):
    d = os.path.join(out, model)
    os.makedirs(d, exist_ok=True)
    glb = rig_glb(model)
    if not opts.get("rescore"):
        if not glb:
            with contextlib.redirect_stdout(io.StringIO()):
                glb, why = build_preview(model, d)
            if not glb: return {"model": model, "error": why}
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
        if os.path.dirname(os.path.abspath(glb)) == os.path.abspath(d):
            for f in ("preview.glb", "preview.json"):       # built only to measure: up to 20 MB a person
                try: os.remove(os.path.join(d, f))
                except OSError: pass
    measured = read_json(os.path.join(d, "measure.json"))
    if not measured: return {"model": model, "error": "no measure.json"}
    audit, audit_path = audit_for(model, opts.get("audit", "read"), d)
    by_design = rest_by_design(model)
    s = QS.score(measured, audit, by_design)
    if by_design: s["rest_skipped"] = "rig.json m2m.rest is \"template\": it rests in the template's pose on purpose"
    s.update({"model": model, "group": layout.group_of(model), "format": "autorig-score/1",
              "preview": measured.get("glb"),
              "audit": audit_path, "clips": [c["name"] for c in measured.get("clips", [])],
              "sheets": sorted(f for f in os.listdir(d) if f.startswith("sheet_") and f.endswith(".png"))})
    with open(os.path.join(d, "score.json"), "w", encoding="utf-8") as fh: json.dump(s, fh, indent=1)
    return s


def fmt(v):
    return "-" if v is None else ("%.2f" % v if isinstance(v, float) else str(v))


COLS = ("rest_mean_pct", "rest_p99_pct", "stretch3_permille", "squash_permille", "arm_in_torso_pct", "leg_cross_pct",
        "foot_skate", "hover_pct", "sink_pct", "audit_bleed_pct")
SHORT = ("rest", "rest99", "str3", "squash", "arm-in", "legX", "skate", "hover", "sink", "bleed")


def table(rows, skipped=None):
    """(rows worst first, markdown): a summary per group, the scored models worst first, then every model that was
    not scored and why (a rig that would not build or measure), then the models with no rig at all."""
    rows = sorted(rows, key=lambda r: (r.get("score") is not None, r.get("score") or 0, r["model"]))
    scored = [r for r in rows if not r.get("error")]
    failed = [r for r in rows if r.get("error")]
    lines = ["| group | scored | PASS | CHECK | FAIL | mean |", "|---|---|---|---|---|---|"]
    for g in sorted({r.get("group") or "" for r in scored}):
        sub = [r for r in scored if (r.get("group") or "") == g]
        lines.append("| %s | %d | %d | %d | %d | %.1f |" % (g or "(root)", len(sub), *(sum(r["grade"] == k for r in sub)
                     for k in ("PASS", "CHECK", "FAIL")), sum(r["score"] for r in sub) / len(sub)))
    lines += ["", "| model | score | grade | " + " | ".join(SHORT) + " | worst | group |",
              "|---|---|---|" + "---|" * len(SHORT) + "---|---|"]
    for r in scored:
        v = r["values"]
        lines.append("| %s | %.1f | %s | %s | %s | %s |" % (r["model"], r["score"], r["grade"],
                     " | ".join(fmt(v.get(k)) for k in COLS), ", ".join(r["worst"]), r.get("group") or ""))
    if failed:
        lines += ["", "Not scored (%d): the rig is there but would not build or measure" % len(failed), "",
                  "| model | why |", "|---|---|"]
        lines += ["| %s | %s |" % (r["model"], r["error"][:200].replace("|", "/").replace("\n", " "))
                  for r in failed]
    if skipped:
        by = {}
        for m, why in skipped.items(): by.setdefault(why, []).append(m)
        for why, ms in sorted(by.items()):
            lines += ["", "%s (%d): %s" % (why.capitalize(), len(ms), ", ".join(sorted(ms)))]
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
    skipped = {}
    if target == "all":
        models = scored_models(group, skipped) if not opts.get("rescore") else sorted(
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
    if not skipped and target != "all": skipped = {}
    elif not skipped: skipped = old.get("skipped") or {}
    rows, md = table(rows, skipped)
    with open(os.path.join(out, "_ranked.json"), "w", encoding="utf-8") as fh: json.dump({"models": rows, "skipped": skipped}, fh, indent=1)
    with open(os.path.join(out, "_ranked.md"), "w", encoding="utf-8") as fh: fh.write(md + "\n")
    print(md)
    return 1 if any(r.get("grade") in ("FAIL", None) for r in rows if r["model"] in have) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

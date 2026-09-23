# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: runs audit.py over several models, one Blender each (an FBX import leaves state behind), and
# prints a table, worst first.
#
#   python autorig/cli/audit_all.py wolf,moth,beetle [-render 0] [-out dir] [-strict]
#   python autorig/cli/audit_all.py all [...]            every model with a rigged FBX (run.py audit-all)
#
# Each model is graded PASS, CHECK or FAIL (autorig/core/grades.py, docs/PIPELINE.md "Grades"). Exit code 0 when
# none FAILs (CHECK is "look at it", not broken); with -strict, 0 only when every model PASSes. The numbers are in
# <out>/<model>.json (default <AUTORIG_WORK>/audit), and the table in <out>/_collection.json.
import contextlib, io, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender, grades, layout


def rigged_models():
    """Every model with a rigged FBX or blend, in the order the GUI lists them."""
    out = []
    with contextlib.redirect_stdout(io.StringIO()):          # layout's notes about ambiguous folders
        for _, m in layout.all_models():
            rd = layout.rigged_dir(m)
            if os.path.exists(os.path.join(rd, m + ".fbx")) or os.path.exists(os.path.join(rd, m + ".blend")):
                out.append(m)
    return out


def allowances(model):
    try:
        import spec_store
        return (spec_store.model(model).get("rig") or {}).get("audit") or {}
    except Exception:
        return {}


def read(out, model):
    p = os.path.join(out, model + ".json")
    try:
        with open(p) as fh: return json.load(fh)
    except (OSError, ValueError):
        return None


def table(models, out, errors=()):
    """Rows for a collection table, worst first (grades.summary). A model whose audit did not finish is a row with
    no grade (its old JSON, if any, is not trusted)."""
    return sorted((grades.summary(m, None if m in errors else read(out, m), allowances(m)) for m in models),
                  key=grades.severity)


def fmt(v, spec="%s"):
    return "-" if v is None else spec % v


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"):
        sys.exit("usage: python audit_all.py model[,model...]|all|failed [-render 0] [-out dir] [-strict]")
    out = a[a.index("-out") + 1] if "-out" in a else layout.work_dir("audit")
    if a[0] == "all":
        models = rigged_models()
    elif a[0] == "failed":
        models = [m for m in rigged_models() if (read(out, m) or {}).get("verdict", {}).get("grade") == grades.FAIL
                  or (read(out, m) or {}).get("verdict", {}).get("pass") is False]
        if not models: sys.exit("no failed models found in %s" % out)
    else:
        models = a[0].split(",")
    render = a[a.index("-render") + 1] if "-render" in a else "1"
    if not models: sys.exit("no rigged models under %s" % layout.ROOT)
    errors = set()
    for k, m in enumerate(models, 1):
        print("AUDIT_ALL %d/%d %s" % (k, len(models), m), flush=True)
        r = blender.run("audit.py", "-model", m, "-out", out, "-render", render)
        for line in r.stdout.splitlines():
            if line.startswith("AUDIT_") or "Error" in line: print("  " + line[:300], flush=True)
        if "AUDIT_DONE" not in r.stdout:
            errors.add(m); print("  audit did not finish: " + (r.stdout + r.stderr)[-1500:], flush=True)
    rows = table(models, out, errors)
    with open(os.path.join(out, "_collection.json"), "w") as fh: json.dump({"models": rows}, fh, indent=1)
    print("%-20s %-5s %9s %6s %7s %7s %6s %4s  %s" % ("model", "grade", "combined", "bend", "gap%", "bleed%", "head%", "inf", "worst bone"))
    for r in rows:
        print("%-20s %-5s %9s %6s %7s %7s %6s %4s  %s" % (r["model"], r["grade"] or "ERROR", fmt(r.get("combined_tears")),
              fmt(r.get("bend_tears")), fmt(r.get("worst_gap_pct")), fmt(r.get("bleed_pct")), fmt(r.get("head_pct")),
              fmt(r.get("max_influences")), r.get("worst_bone") or ""))
    bad = {grades.FAIL, None} | ({grades.CHECK} if "-strict" in a else set())
    sys.exit(1 if any(r["grade"] in bad for r in rows) else 0)


if __name__ == "__main__":
    main()

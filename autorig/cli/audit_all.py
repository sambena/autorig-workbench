# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: runs audit.py over several models, one Blender each (an FBX import leaves state behind), and prints a table.
#
#   python autorig/cli/audit_all.py wolf,moth,beetle [-render 0] [-out dir]
#
# Exit code 0 when every model passes, 1 otherwise. The numbers are in <out>/<model>.json (default
# <AUTORIG_WORK>/audit).
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender, layout


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"): sys.exit("usage: python audit_all.py model[,model...] [-render 0] [-out dir]")
    models = a[0].split(",")
    out = a[a.index("-out") + 1] if "-out" in a else layout.work_dir("audit")
    render = a[a.index("-render") + 1] if "-render" in a else "1"
    rows, ok = [], True
    for m in models:
        blender.run("audit.py", "-model", m, "-out", out, "-render", render)
        p = os.path.join(out, m + ".json")
        if not os.path.exists(p):
            rows.append((m, "ERROR", {}, [])); ok = False; continue
        v = json.load(open(p))["verdict"]
        ok &= v["pass"]
        c = {k: c["value"] for k, c in v["checks"].items()}
        c["legacy"] = json.load(open(p)).get("bleed_legacy_pct")
        rows.append((m, "PASS" if v["pass"] else "FAIL", c, v["warnings"]))
    print("%-20s %-5s %7s %7s %9s %6s %6s %4s  %s" % ("model", "", "bleed%", "legacy", "combined", "bend", "head%", "inf", "warnings"))
    for m, verdict, c, w in rows:
        print("%-20s %-5s %7s %7s %9s %6s %6s %4s  %s" % (m, verdict, c.get("bleed_pct"), c.get("legacy"), c.get("combined_tears"),
              c.get("bend_tears"), c.get("head_pct"), c.get("max_influences"), "; ".join(w[:4])))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: how often Auto picks the right route, against the routes the collections' committed rigs took.
#
#   python autorig/cli/route_eval.py [--features dir] [--truth file] [--show all|wrong] [--json out.json]
#
# Replays Auto's choice in plain Python (no Blender): core/suggest.py's archetype from each model's saved tips and
# body plan, then core/m2m_templates.choose(), from the features steps/route_features.py saved (default
# tests/data/route_features/). The truth is tests/data/route_truth.json: {model: {"route": ..., "why": ...}}, where a
# route is "people" (the humanoid rig), "classic" (the placed or classic rig of a prop, a floater, a machine: no
# Mesh2Motion template), or a template: "m2m:horse", "m2m:fox", "m2m:bird", ...
#
# Two scores: the route (people / creature / classic) and the template (exact; "m2m:quad" in the truth takes horse or
# fox, where the committed rigs do not say which). A truth may list other routes it also accepts ("also").
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import m2m_templates as T, suggest

DATA = os.path.join(REPO, "tests", "data")


def predict(f):
    """(route, archetype, why) for one model's saved features, as m2m_rig --template auto picks it."""
    res = suggest.suggest_skeleton(name=f["model"], source_data=f["source_data"], tips=f["tips"],
                                   proportions=f["proportions"], forward=[0, -1, 0],
                                   body_plan=f.get("plan") if isinstance(f.get("plan"), dict) and "error" not in f["plan"] else None)
    arch = res.get("archetype")
    meas = dict(f.get("measures") or {})
    meas.update(T.plan_measures(f.get("plan")))
    t, why = T.choose(arch, meas)
    if T.is_person(t, arch, meas): return "people", arch, why
    return ("m2m:" + t) if t else "classic", arch, why


def kind(route):
    return "people" if route == "people" else "classic" if route == "classic" else "creature"


FOUR = {"m2m:horse", "m2m:fox", "m2m:quad"}


def same_template(got, want):
    """want "m2m:quad" takes horse or fox; otherwise horse and fox are told apart (the template, exactly)."""
    return got == want or (want == "m2m:quad" and got in FOUR)


def accepts(t, got):
    return any(same_template(got, w) for w in [t["route"]] + list(t.get("also") or []))


def route_accepts(t, got):
    return any(kind(got) == kind(w) for w in [t["route"]] + list(t.get("also") or []))


def evaluate(features_dir=None, truth_path=None):
    features_dir = features_dir or os.path.join(DATA, "route_features")
    truth = json.load(open(truth_path or os.path.join(DATA, "route_truth.json"), encoding="utf-8"))
    rows = []
    for m, t in sorted(truth.items()):
        if m.startswith("_"): continue
        p = os.path.join(features_dir, m + ".json")
        if not os.path.exists(p): continue
        f = json.load(open(p, encoding="utf-8"))
        if f.get("error"): continue
        route, arch, why = predict(f)
        rows.append({"model": m, "group": f.get("group"), "truth": t["route"], "also": t.get("also") or [], "got": route,
                     "archetype": arch, "why": why, "route_ok": route_accepts(t, route), "template_ok": accepts(t, route)})
    n = len(rows)
    out = {"models": n, "route_ok": sum(r["route_ok"] for r in rows), "template_ok": sum(r["template_ok"] for r in rows),
           "by_truth": {}, "rows": rows}
    for k in ("people", "creature", "classic"):
        sub = [r for r in rows if kind(r["truth"]) == k]
        out["by_truth"][k] = {"models": len(sub), "route_ok": sum(r["route_ok"] for r in sub),
                              "template_ok": sum(r["template_ok"] for r in sub)}
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--features"); ap.add_argument("--truth"); ap.add_argument("--show", default="wrong")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    r = evaluate(a.features, a.truth)
    for row in r["rows"]:
        if a.show == "all" or not row["template_ok"]:
            print("%-4s %-28s truth %-10s got %-10s (%s) %s" % ("ok" if row["template_ok"] else "BAD", row["model"][:28],
                  row["truth"], row["got"], row["archetype"], row["why"][:90]))
    print("route    %d/%d  (%.0f%%)" % (r["route_ok"], r["models"], 100.0 * r["route_ok"] / max(r["models"], 1)))
    print("template %d/%d  (%.0f%%)" % (r["template_ok"], r["models"], 100.0 * r["template_ok"] / max(r["models"], 1)))
    for k, v in r["by_truth"].items():
        print("  %-8s route %d/%d, template %d/%d" % (k, v["route_ok"], v["models"], v["template_ok"], v["models"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh: json.dump(r, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

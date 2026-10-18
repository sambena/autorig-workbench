# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what an audit (steps/audit.py's JSON) blames, bone by bone, for GET /api/v1/models/{m}/audit
# (gui/api_manage.py), and whether a model has its preview.glb (the model state's "preview").
import os, re


def worst_bones(audit, limit=12):
    """The bones an audit (steps/audit.py's JSON) blames, worst first: [{bone, level, score, reasons}].

    level is "bad" when the bone is behind a check that failed (bend tears, bleed, the head's share), else "warn".
    Blamed: a bone whose 40-degree bend tears edges; the bone that owns another bone's surface (each bleed pair);
    a head that owns too little; twist tears, a bend that drags surface outside the bone's own limb, a limb bone
    reaching far, a hard joint. Too many influences is per mesh in the audit, not per bone, so it is in "meshes".
    """
    checks = (audit.get("verdict") or {}).get("checks", {})
    failed = lambda k: k in checks and checks[k].get("ok") is False
    rows = {}

    def add(bone, level, score, why):
        if not bone: return
        r = rows.setdefault(bone, {"bone": bone, "level": "warn", "score": 0.0, "reasons": []})
        if level == "bad": r["level"] = "bad"
        r["score"] += score
        r["reasons"].append(why)

    for b in audit.get("bends", []):
        t = b.get("tear_edges") or 0
        if t and b.get("mode") == "bend":
            add(b["bone"], "bad" if failed("bend_tears") else "warn", 100 + t,
                "tears %d edge%s bent 40° (stretch %sx)" % (t, "" if t == 1 else "s", b.get("max_stretch")))
        elif t:
            add(b["bone"], "warn", 10 + 0.2 * t, "tears %d edge%s twisted 60°" % (t, "" if t == 1 else "s"))
        c = b.get("collateral_pct") or 0
        if c >= 2 and b.get("mode") == "bend":
            add(b["bone"], "warn", 5 + c, "bending it drags %.1f%% of the surface outside its limb" % c)
    for pair in audit.get("bleed_pairs", []):
        owner, nearest, pct = pair[0], pair[1], pair[2]
        if pct < 0.05: continue
        add(owner, "bad" if failed("bleed_pct") and pct >= 0.25 else "warn", 50 + 40 * pct,
            "bleed: owns %.2f%% of the surface, nearer %s" % (pct, nearest))
    if failed("head_pct"):
        head = next((x for x in audit.get("hierarchy", []) if x["bone"].split(":")[-1].lower() == "head"), None)
        if head:
            add(head["bone"], "bad", 80, "owns only %s%% of the surface (at least %s%%)" %
                (checks["head_pct"].get("value"), checks["head_pct"].get("limit")))
    for w in audit.get("verdict", {}).get("warnings", []):
        m = re.match(r"^(\S+) reaches ([\d.]+)$", w)
        if m: add(m.group(1), "warn", 8, "reaches %s of the model from its bone (90th percentile)" % m.group(2))
    for j in audit.get("joints", []):
        if (j.get("hard_edges") or 0) >= 3:
            add(j["joint"], "warn", 3 + j["hard_edges"], "hard joint: %d edges jump from %s to it" % (j["hard_edges"], j["parent"]))
    out = sorted(rows.values(), key=lambda r: (r["level"] != "bad", -r["score"], r["bone"]))
    for r in out: r["score"] = round(r["score"], 2)
    return out[:limit]


def has_preview(layout, name):
    return os.path.exists(os.path.join(layout.rigged_dir(name), "preview.glb"))

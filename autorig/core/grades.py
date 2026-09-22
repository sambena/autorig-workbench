# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the audit's grades. Plain Python (no bpy), so audit.py (inside Blender), the GUI server and the
# tests all grade the same way.
#
#   PASS   every check within its limit (the model's own allowances included): the strict result, as before.
#   CHECK  something is past its limit but inside a warn band: a few tears that open a small gap, bleed just over,
#          a head share slightly low. Look at it in the viewer before trusting it.
#   FAIL   clearly broken: past the warn band, or a tear that opens a big gap however few there are.
#
# The raw numbers are never softened: audit.py still reports every tear, and `pass` in the verdict stays the strict
# result (grade == PASS), so anything that read it before reads the same answer. docs/PIPELINE.md explains the bands.

PASS, CHECK, FAIL = "PASS", "CHECK", "FAIL"
RANK = {PASS: 0, CHECK: 1, FAIL: 2}

# A tear is an edge stretched past 2x that also opens a gap of more than GAP x the model's size (audit.py).
GAP = 0.004

# The pass limits. A model's spec can move them with audit={...} (same keys), and says why in notes["rig.audit"].
THRESHOLDS = {"bleed_pct": 2.0, "combined_tears": 0, "bend_tears": 0, "head_pct": 2.5, "max_influences": 4}

# How far past its pass limit a value may go and still be CHECK. An allowance moves the band with it: a model allowed
# 8 combined tears is CHECK up to 8 + 12. Influences have no band: an engine takes four, and a fifth is dropped.
CHECK_BAND = {"bleed_pct": 1.0, "combined_tears": 12, "bend_tears": 12, "head_pct": 1.0, "max_influences": 0}

# Tears past the limit are CHECK only while the widest gap they open (how much the worst edge grows) stays under
# this, in % of the model's size (its longest side). Past it they FAIL, however few. The combined pose bends every
# joint at once, so it gets more room than one joint's 40-degree bend. Calibrated on a real collection: rigs with a
# handful of tears opened 1-3.5% on a single bend and 5.5-7% in the combined pose; rigs with dozens, 9-15%.
GAP_CHECK_PCT = {"combined_tears": 8.0, "bend_tears": 5.0}

HIGHER_IS_BETTER = {"head_pct"}
TEAR_CHECKS = ("combined_tears", "bend_tears")


def limits(allowances=None):
    """The pass limits for one model: THRESHOLDS with its spec's audit allowances over them.

    An allowance block is either flat ({"bend_tears": 4}) or nested ({"thresholds": {...}}), as audit.py has always
    read it. Keys that are not checks are ignored."""
    th = dict(THRESHOLDS)
    a = allowances or {}
    a = a.get("thresholds", a) if isinstance(a, dict) else {}
    for k, v in a.items():
        if k in th and isinstance(v, (int, float)) and not isinstance(v, bool): th[k] = v
    return th


def check_limit(key, limit):
    """The edge of the warn band for a check whose pass limit is `limit`."""
    band = CHECK_BAND.get(key, 0)
    return round(limit - band, 4) if key in HIGHER_IS_BETTER else round(limit + band, 4)


def grade_check(key, value, limit, gap_pct=None):
    """(grade, check_limit) for one check. value None means the check does not apply (no head): PASS.

    gap_pct is the widest tear's opening in % of the model's size, for the tear checks; None when it was not
    measured (an audit from before the grades), and then the count alone decides."""
    cl = check_limit(key, limit)
    if value is None: return PASS, cl
    if key in HIGHER_IS_BETTER:
        if value >= limit: return PASS, cl
        return (CHECK if value >= cl else FAIL), cl
    if value <= limit: return PASS, cl
    if value > cl: return FAIL, cl
    if key in GAP_CHECK_PCT and gap_pct is not None and gap_pct > GAP_CHECK_PCT[key]: return FAIL, cl
    return CHECK, cl


def worst(grades):
    return max(grades, key=lambda g: RANK[g]) if grades else PASS


def grade_audit(values, allowances=None, gaps=None):
    """The verdict for one audit.

    values      {check: measured value} (THRESHOLDS' keys; head_pct None when the model has no head)
    allowances  the spec's audit block, or None
    gaps        {"combined_tears": widest gap %, "bend_tears": widest gap % over the single bends}

    Returns {"grade", "pass", "checks": {check: {"value", "limit", "check_limit", "ok", "grade"}}}; `ok` and `pass`
    are the strict results."""
    th = limits(allowances)
    gaps = gaps or {}
    checks = {}
    for k, v in values.items():
        if k not in th: continue
        g, cl = grade_check(k, v, th[k], gaps.get(k))
        checks[k] = {"value": v, "limit": th[k], "check_limit": cl, "ok": g == PASS, "grade": g}
        if k in GAP_CHECK_PCT: checks[k]["gap_pct"] = gaps.get(k)
    grade = worst([c["grade"] for c in checks.values()])
    return {"grade": grade, "pass": grade == PASS, "checks": checks}


def grade_of(verdict, allowances=None):
    """The grade of a stored verdict: its own when it has one, else graded now from its values (an audit written
    before the grades, with no gap sizes, so the tear counts alone decide)."""
    if not verdict: return None
    if verdict.get("grade") in RANK: return verdict["grade"]
    vals = {k: c.get("value") for k, c in (verdict.get("checks") or {}).items()}
    lim = {k: c.get("limit") for k, c in (verdict.get("checks") or {}).items() if c.get("limit") is not None}
    return grade_audit(vals, dict(allowances or {}, **lim))["grade"] if vals else (PASS if verdict.get("pass") else FAIL)


def severity(row):
    """A sort key, worst first, for a table of audits: grade (an audit that did not run first), then tears, then the
    widest gap, then bleed."""
    return (-RANK.get(row.get("grade"), 3),
            -((row.get("combined_tears") or 0) + (row.get("bend_tears") or 0)),
            -(row.get("worst_gap_pct") or 0), -(row.get("bleed_pct") or 0), row.get("model") or "")


def summary(model, audit, allowances=None):
    """One row of a collection table from an audit's JSON (None when the audit is missing or unreadable)."""
    if not audit: return {"model": model, "grade": None}
    v = audit.get("verdict") or {}
    c = {k: x.get("value") for k, x in (v.get("checks") or {}).items()}
    t = audit.get("tears") or {}
    return {"model": model, "grade": grade_of(v, allowances), "pass": v.get("pass"),
            "combined_tears": c.get("combined_tears"), "bend_tears": c.get("bend_tears"),
            "worst_gap_pct": t.get("worst_gap_pct"), "bleed_pct": c.get("bleed_pct"), "head_pct": c.get("head_pct"),
            "max_influences": c.get("max_influences"), "worst_bone": t.get("worst_bone"),
            "bones_tearing": t.get("bones_tearing"),
            "failing": [k for k, x in (v.get("checks") or {}).items() if x.get("grade", PASS if x.get("ok") else FAIL) != PASS]}

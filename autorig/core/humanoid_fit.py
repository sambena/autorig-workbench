# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a person's humanoid section (rig.json "humanoid": heights and spans) measured from the mesh, for
# rerig_humanoid.py. Plain numpy, so it is tested without Blender (tests/test_humanoid_fit.py).
import math
import numpy as np

DEFAULT_HUMANOID = {
    "forward": [0, -1, 0],
    "z": {"top": 1.0, "head": 0.87, "neck": 0.83, "arm": 0.77, "spine2": 0.72, "spine1": 0.65, "spine": 0.57, "hip": 0.47, "knee": 0.28, "ankle": 0.08},
    "x": {"shoulder": 0.38, "elbow": 0.23, "wrist": 0.11, "knuckle": 0.05, "tip": 0.0}
}


def _runs(vals, gap):
    """1-D clusters of the values, split where sorted neighbours are more than gap apart: [(lo, hi, count)]."""
    v = np.sort(np.asarray(vals, dtype=float))
    if len(v) == 0:
        return []
    cut = np.where(np.diff(v) > gap)[0]
    starts, ends = np.r_[0, cut + 1], np.r_[cut, len(v) - 1]
    return [(float(v[s]), float(v[e]), int(e - s + 1)) for s, e in zip(starts, ends)]


def wants_auto(h):
    """Whether a humanoid section's heights and spans are measured from the mesh: "auto": true says so, and a section
    that is DEFAULT_HUMANOID's numbers unchanged (what Auto rig and the Human tile write for a fresh upload) is one
    nobody tuned. "auto": false keeps the numbers whatever they are."""
    if not isinstance(h, dict):
        return False
    if "auto" in h:
        return bool(h["auto"])
    same = lambda a, b: set(a) <= set(b) and all(abs(float(a.get(k, -1)) - float(v)) < 1e-6 for k, v in b.items())
    try:
        return same(h.get("z") or {}, DEFAULT_HUMANOID["z"]) and same(h.get("x") or {}, DEFAULT_HUMANOID["x"])
    except (TypeError, ValueError):
        return False


def auto_section(h, co, lo, size, main=None, log=None):
    """h with auto_landmarks' heights and spans over its own (whatever it does not measure keeps h's value)."""
    lm = auto_landmarks(co, lo, size, main, h, log)
    out = dict(h, z=dict(h.get("z") or {}, **lm["z"]), x=dict(h.get("x") or {}, **lm["x"]))
    for k in ("robe", "straight_arms"):
        if lm.get(k) is not None and k not in h:
            out[k] = lm[k]
    return out


# auto_landmarks' proportions, from the Mixamo-named skeletons that came with 18 Heartroot people (Tripo's rigs:
# knights, robes, coats, a jester, a barrel skeleton, a robot), in fractions of the height. The hips were the
# default's worst guess: 0.47 is 8% of the height under where those skeletons (and an adult's anatomy) put them.
AUTO = {
    "hip_share": 0.62,             # hip = ankle + this x (neck - ankle): 0.61-0.67 on 17 of the 18; Mixamo's 0.60-0.63
    "knee_share": 0.48,            # knee = ankle + this x (hip - ankle)
    "spine_shares": (0.19, 0.45, 0.69),   # Spine, Spine1, Spine2 from the hip to the neck
    "neck_over_arm": 0.035,        # the neck's base above the shoulder joints (Tripo's 0.02, Mixamo's 0.05)
    "head_over_neck": 0.06,
    "arm_radius": 0.02,            # the shoulder joint this far under the arm's top edge (its forearm half)
    "shoulder_half": 0.093,        # the shoulder joint out from the middle, when the chest cannot be measured
    "upper_arm": 0.40, "forearm": 0.33,   # shares of the shoulder-to-fingertip length (the hand is the rest)
    "knuckle": 0.55,               # the knuckle this far from the wrist to the fingertip
}


def auto_landmarks(co, lo, size, main=None, h=None, log=None):
    """The humanoid section's heights and spans measured from the mesh, for a person nobody tuned (a fresh upload got
    DEFAULT_HUMANOID: one set of proportions for everyone, which put robed legs out at the hem and every hip low).
    co is best a dense sampling of the surface (surface_points). In units of the height:
      arms:  each arm's top edge, followed in from the fingertip; the shoulder joint is that edge, less the arm's
             radius, where it passes over the shoulder (the chest's half width under the armpit, when a run of the
             body there is the chest's own and not a sleeve or a robe merged with it). The edge, not the middle: a
             bell sleeve or a drape hangs under an arm, never over it.
      neck, head, spine, hips, knees: from the shoulders and the ankles by the body's proportions (AUTO), which hold
             from a chibi jester to a robot; the hip is never read off the mesh, where a tunic's hem looks like a crotch.
      legs:  tracked up from the feet, each the run of points continuing the one below, until the two merge or one
             widens into a hem. Their spacing is what places the legs ("leg"/"leg_band": slices are read only in a band
             around each, so a robe's hem never splays them); "robe" when something covers them below the hip.
    The arms' joints between the shoulder and the fingertip are left to measure()'s tracker, which follows a bell
    sleeve's middle: the arm's own line (straight_arms) is truer to the skeletons it was checked on (5.6% of the height
    off against 5.8%), but bone heat then gave the sleeve's hanging cloth to the torso, and it tore when the arms came
    down (Heartroot's stoker: 87 torn edges in idle became 262).
    Returns {"z": {...}, "x": {...}[, "robe": ...]} as the section's fractions (x counted in from each side's edge).
    log (a dict) gets what was found."""
    log = {} if log is None else log
    lo = np.asarray(lo, dtype=float); size = np.asarray(size, dtype=float)
    Hh = float(size[2]); wx = float(size[0]) / Hh
    P = (np.asarray(co, dtype=float) - lo) / Hh
    if main is not None and len(main) == len(P) and np.any(main):
        P = P[main]
    gap = 0.012

    def slab(z, half=0.006):
        return P[np.abs(P[:, 2] - z) < half]

    def run_at(z, x, half=0.006):
        rr = [r for r in _runs(slab(z, half)[:, 0], gap) if r[0] - 0.01 <= x <= r[1] + 0.01]
        return rr[0] if rr else None

    mid = P[(P[:, 2] > 0.5) & (P[:, 2] < 0.7)]
    cx = float(np.median(mid[:, 0])) if len(mid) > 20 else wx / 2
    r = run_at(0.6, cx)
    if r:
        cx = (r[0] + r[1]) / 2

    # ---- legs, from the floor up
    tracks = {1: [], -1: []}
    stop_z, stop_kind, prev, miss = None, None, {}, 0
    for z in np.arange(0.012, 0.8, 0.008):
        s = slab(z, 0.005)
        runs = [r for r in _runs(s[:, 0], gap) if r[2] >= 4]
        if not runs:
            continue
        if not prev:
            # the first level with a foot each side of the middle; one run across the middle is feet together or a
            # robe on the floor: keep looking a little higher
            L = [r for r in runs if (r[0] + r[1]) / 2 > cx and r[0] > cx - 0.01]
            R = [r for r in runs if (r[0] + r[1]) / 2 < cx and r[1] < cx + 0.01]
            if L and R:
                prev = {1: max(L, key=lambda r: r[2]), -1: max(R, key=lambda r: r[2])}
            elif z > 0.2:
                break
            continue
        match = {}
        for sg in (1, -1):
            p = prev[sg]
            cand = [r for r in runs if r[1] >= p[0] - 0.02 and r[0] <= p[1] + 0.02]
            match[sg] = max(cand, key=lambda r: min(r[1], p[1]) - max(r[0], p[0])) if cand else None
        if match[1] is None or match[-1] is None:
            miss += 1
            if miss > 3:
                stop_z, stop_kind = z, "lost"
                break
            continue
        miss = 0
        if match[1] == match[-1]:
            union = max(prev[1][1], prev[-1][1]) - min(prev[1][0], prev[-1][0])
            m = match[1]
            stop_z, stop_kind = z, ("merge" if (m[1] - m[0]) <= union * 1.3 + 0.01 else "hem")
            break
        widened = False
        for sg in (1, -1):
            ws = [t["w"] for t in tracks[sg] if t["z"] > 0.1]
            ref = float(np.median(ws)) if len(ws) >= 3 else None
            if ref is not None and match[sg][1] - match[sg][0] > max(2.0 * ref, ref + 0.05):
                widened = True
        if widened:
            stop_z, stop_kind = z, "hem"
            break
        for sg in (1, -1):
            r = match[sg]
            pts = s[(s[:, 0] >= r[0]) & (s[:, 0] <= r[1])]
            tracks[sg].append({"z": float(z), "c": (r[0] + r[1]) / 2, "w": r[1] - r[0],
                               "yd": float(np.percentile(pts[:, 1], 95) - np.percentile(pts[:, 1], 5)) if len(pts) > 3 else 0.0})
        prev = match
    legs_seen = bool(tracks[1] and tracks[-1] and float(stop_z or 0.8) > 0.12)
    log["legs"] = {"stop": round(float(stop_z), 3) if stop_z else None, "kind": stop_kind, "seen": legs_seen}

    # ankle: coming down the shin, where the leg's depth front to back grows into the foot
    ankle = 0.06
    if legs_seen:
        top = min(0.25, float(stop_z or 0.8) - 0.02)
        shin = [t["yd"] for sg in (1, -1) for t in tracks[sg] if 0.11 <= t["z"] <= top]
        if shin:
            sd = float(np.median(shin))
            tops = [max([t["z"] for t in tracks[sg] if t["z"] < 0.16 and t["yd"] > 1.4 * sd] or [0.05]) for sg in (1, -1)]
            ankle = min(0.12, max(0.03, float(np.mean(tops)) + 0.01))

    # ---- arms: the top edge, from the fingertip in
    arms = []
    for sg in (1, -1):
        side = P[(P[:, 0] - cx) * sg > 0.02]
        if len(side) < 50:
            continue
        reach = float(((side[:, 0] - cx) * sg).max())
        zc = float(np.median(side[(side[:, 0] - cx) * sg > reach - 0.01][:, 2]))
        edge = []
        for d in np.arange(reach - 0.004, 0.03, -0.006):
            col = side[np.abs((side[:, 0] - cx) * sg - d) < 0.003]
            rr = [r for r in _runs(col[:, 2], gap) if r[2] >= 3]
            near = [r for r in rr if r[0] - 0.04 <= zc <= r[1] + 0.04]
            if not near:
                continue
            r = min(near, key=lambda r: abs(r[1] - zc))
            edge.append((float(d), r[1]))
            zc = r[1] - 0.5 * min(r[1] - r[0], 0.06)
        if len(edge) >= 6:
            arms.append((sg, reach, np.array(edge)))
    # the shoulder joint out from the middle: the chest's half width under the armpits. Read below where the upper
    # arm's top edge passes, at the first levels where the run through the middle is the torso alone: narrower than
    # any chest with a sleeve or a robe merged into it. A T-pose's chest is right under its arms; an A-pose's arms
    # hang against the ribs until they part from them (Mixamo's adults: 0.12 of the height out; Tripo's T-posed
    # Heartroot people: 0.09)
    upper = [float(np.interp(0.2, e[::-1, 0], e[::-1, 1])) for _, rch, e in arms if rch > 0.25]
    start = (min(upper) if upper else 0.8) - 0.03
    widths = []
    for z in np.arange(start, max(0.35, start - 0.35), -0.01):
        r = run_at(z, cx)
        if r and (r[1] - r[0]) / 2 < 0.16:
            widths.append((r[1] - r[0]) / 2)
            if len(widths) == 4:
                break
    sh_d = float(np.clip(np.median(widths[:4]), 0.075, 0.2)) if len(widths) >= 3 else AUTO["shoulder_half"]
    out = {"z": {}, "x": {}}
    to_frac = lambda d: max(0.0, min(0.49, 0.5 - d / wx))   # half span -> fraction in from the edge (measure mirrors)
    arm_z = None
    if arms:
        # the top edge as a line along the upper arm and forearm, run in to the shoulder: right at the shoulder the
        # edge already rises into the trapezius, a cape or a pauldron
        # (the middle is the radius under the edge across the arm: further straight down when the arm slopes)
        tops = []
        for _, rch, e in arms:
            seg = e[(e[:, 0] >= sh_d + 0.3 * (rch - sh_d)) & (e[:, 0] <= sh_d + 0.7 * (rch - sh_d))]
            if len(seg) >= 4:
                k, c = np.polyfit(seg[:, 0], seg[:, 1], 1)
                tops.append(float(k * sh_d + c) - AUTO["arm_radius"] * math.sqrt(1.0 + min(k * k, 4.0)))
            else:
                tops.append(float(np.interp(sh_d, e[::-1, 0], e[::-1, 1])) - AUTO["arm_radius"])
        arm_z = float(np.mean(tops))
        reach = float(np.mean([a[1] for a in arms]))
        L = max(0.05, reach - sh_d)
        el_d = sh_d + AUTO["upper_arm"] * L
        wr_d = el_d + AUTO["forearm"] * L
        kn_d = wr_d + AUTO["knuckle"] * (reach - wr_d)
        out["x"] = {"shoulder": round(to_frac(sh_d), 4), "elbow": round(to_frac(el_d), 4),
                    "wrist": round(to_frac(wr_d), 4), "knuckle": round(to_frac(kn_d), 4), "tip": round(to_frac(reach), 4)}
        log["arms"] = {"shoulder_half": round(sh_d, 3), "chest_widths": len(widths), "reach": round(reach, 3),
                       "arm_z": round(arm_z, 3)}
    if arm_z is None or not (0.4 < arm_z < 0.95):
        arm_z = 0.8
    neck = min(0.95, arm_z + AUTO["neck_over_arm"])
    hip = ankle + AUTO["hip_share"] * (neck - ankle)
    knee = ankle + AUTO["knee_share"] * (hip - ankle)
    sp = AUTO["spine_shares"]
    out["z"] = {"top": 1.0, "head": round(min(0.99, neck + AUTO["head_over_neck"]), 4), "neck": round(neck, 4),
                "arm": round(arm_z, 4), "spine2": round(hip + sp[2] * (neck - hip), 4),
                "spine1": round(hip + sp[1] * (neck - hip), 4), "spine": round(hip + sp[0] * (neck - hip), 4),
                "hip": round(hip, 4), "knee": round(knee, 4), "ankle": round(ankle, 4)}
    if legs_seen:
        lx = [float(np.median([t["c"] for t in tracks[sg] if 0.08 < t["z"] < 0.3] or [tracks[sg][-1]["c"]])) for sg in (1, -1)]
        lw = float(np.median([t["w"] for sg in (1, -1) for t in tracks[sg] if t["z"] > 0.08] or [0.06]))
        out["x"]["leg"] = round(abs(lx[0] - lx[1]) / 2.0 / wx, 4)
        out["x"]["leg_band"] = round(max(0.01, 0.6 * lw / wx), 4)
        if float(stop_z or 0.8) < hip - 0.08:
            out["robe"] = round(float(stop_z), 4)   # something covers the legs from here up: read them under it
    else:
        out["robe"] = True                 # no legs to be seen at all: a robe to the floor
    log["landmarks"] = out
    return out

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one 0-100 quality score for a rigged, animated model, from the numbers steps/score.py measures.
# Plain Python (no bpy, no numpy), so the Blender step, cli/score_all.py, the GUI and the tests all score the same way.
#
# Every check maps its measured value onto a penalty between 0 (at or better than GOOD) and its weight (at or worse
# than BAD), straight-line in between. The score is 100 minus the penalties, floored at 0. PASS is 90 or more: a model
# passes with a few small faults, never with one of the heavy checks (weight 15 or more) at its worst.
# docs/QUALITY.md says what each check measures and how the limits were calibrated.
import re

PASS_SCORE = 90
CHECK_SCORE = 75          # 75-89: CHECK, look at the sheets; below 75: FAIL

# check: (good, bad, weight). Lower is better for every check. Lengths are % of the model's height; edge counts are
# per thousand edges (so a 2k-triangle prop and a 30k-triangle person compare); the clip checks take the worst clip.
CHECKS = {
    # the rigged mesh at rest against the source mesh (nearest surface, after lining the two up): the rest should look
    # like the model as sculpted. A person resting in the modelled A-pose with mirrored arms moves 1-2.5% on average.
    "rest_mean_pct":      (2.5, 6.0, 10),
    "rest_p99_pct":       (5.0, 12.0, 25),
    # edges a clip stretches past 3x (and opening a gap of 0.4% of the size), per thousand edges
    "stretch3_permille":  (5.5, 12.0, 20),
    "max_stretch":        (12.0, 60.0, 5),
    # edges a clip squashes under a third of their length, per thousand edges
    "squash_permille":    (13.0, 30.0, 5),
    # forearms and hands inside the body, legs inside the other legs or the body: % of the limb's vertices (those
    # clear of the body at rest) that go more than 1% of the height under the surface in some frame
    "arm_in_torso_pct":   (15.0, 60.0, 15),
    "leg_cross_pct":      (23.0, 45.0, 10),
    # foot skate (foot_contact.foot_skate: thousandths of the size a frame) in the worst grounded clip
    "foot_skate":         (50.0, 150.0, 10),
    # travelling clips whose feet never reach the floor, and the mesh below the floor (% of the height)
    "hover_pct":          (1.5, 6.0, 5),
    "sink_pct":           (6.5, 15.0, 5),
    # the existing audit (steps/audit.py), its two numbers that hold up: bleed and the head's share of the surface
    "audit_bleed_pct":    (3.0, 10.0, 5),
    "audit_head_short":   (0.0, 2.0, 5),     # how far the head share is under 1.5%, in points
}

# A rigged model with no clips at all scores its rest only, and loses this much: CHECK at best.
NO_CLIPS = 11

# The checks a clip has no say in (a prop or a model with no clips scores only on these).
REST_CHECKS = ("rest_mean_pct", "rest_p99_pct", "audit_bleed_pct", "audit_head_short")


def penalty(key, value):
    if value is None: return 0.0
    good, bad, weight = CHECKS[key]
    if value <= good: return 0.0
    if value >= bad: return float(weight)
    return weight * (value - good) / (bad - good)


def grade(score):
    if score is None: return None
    return "PASS" if score >= PASS_SCORE else "CHECK" if score >= CHECK_SCORE else "FAIL"


# Clips that leave the ground or end on it: their feet, floor, sinking and limbs folded against the body say nothing
# about the rig.
AIRBORNE = ("jump", "roll", "death", "die", "dead", "fall", "fly", "flap", "glide", "swim", "dive", "land", "leap",
            "hover", "knock", "lie", "sleep", "crawl_floor")
GROUND_CHECKS = ("foot_skate", "hover_pct", "sink_pct", "arm_in_torso_pct", "leg_cross_pct")


def grounded(clip):
    n = (clip.get("name") or "").lower()
    return not any(w in n for w in AIRBORNE)


def worst_clip(clips, key):
    vals = [c.get(key) for c in clips if c.get(key) is not None and (key not in GROUND_CHECKS or grounded(c))]
    return max(vals) if vals else None


def values(measured, audit=None, rest_by_design=False):
    """{check: value} from steps/score.py's JSON and an audit JSON (either may lack parts: those checks are None).
    rest_by_design: the rig rests in its template's pose on purpose (rig.json m2m.rest "template": a snake laid
    straight along the template's line), so the rest checks are left out."""
    rest = {} if rest_by_design else (measured.get("rest") or {})
    clips = measured.get("clips") or []
    out = {"rest_mean_pct": rest.get("mean_pct"), "rest_p99_pct": rest.get("p99_pct")}
    for k in ("stretch3_permille", "max_stretch", "squash_permille", "arm_in_torso_pct", "leg_cross_pct",
              "foot_skate", "hover_pct", "sink_pct"):
        out[k] = worst_clip(clips, k)
    out["audit_bleed_pct"] = out["audit_head_short"] = None
    checks = ((audit or {}).get("verdict") or {}).get("checks") or {}
    if "bleed_pct" in checks: out["audit_bleed_pct"] = checks["bleed_pct"].get("value")
    head = (checks.get("head_pct") or {}).get("value")
    if head is not None: out["audit_head_short"] = max(0.0, 1.5 - head)
    return out


def score(measured, audit=None, rest_by_design=False):
    """{"score", "grade", "values", "penalties", "worst"}: the score, every check's value and what it cost, and the
    three checks that cost most (what to look at first)."""
    vals = values(measured, audit, rest_by_design)
    pens = {k: round(penalty(k, v), 2) for k, v in vals.items()}
    if not measured.get("clips"): pens["no_clips"] = NO_CLIPS     # a rig with nothing to play is not done
    total = max(0.0, 100.0 - sum(pens.values()))
    worst = [k for k, p in sorted(pens.items(), key=lambda kv: -kv[1]) if p > 0][:3]
    return {"score": round(total, 1), "grade": grade(total), "values": vals, "penalties": pens, "worst": worst}


# ---- body parts from bone names, for the self-intersection proxies ----

_SIDE_RE = [
    (re.compile(r"left|[._\-\s]l$|^l[._\-]|[._\-]l[._\-]", re.I), "L"),
    (re.compile(r"right|[._\-\s]r$|^r[._\-]|[._\-]r[._\-]", re.I), "R"),
]
_CAMEL = re.compile(r"^(L|R)(?=[A-Z])")
ARM_WORDS = ("arm", "hand", "elbow", "wrist", "finger", "thumb", "index", "middle", "ring", "pinky", "palm", "clavicle",
             "shoulder", "collar")
LEG_WORDS = ("leg", "thigh", "shin", "calf", "knee", "foot", "ankle", "toe", "hoof", "paw", "heel")
TORSO_WORDS = ("hip", "pelvis", "spine", "chest", "torso", "body", "stomach", "belly", "abdomen", "waist", "root",
               "back", "ribs")
HEAD_WORDS = ("neck", "head", "jaw", "ear", "nose", "eye", "chin", "skull", "beak", "mouth", "tongue", "brow")
ARM_DISTAL = ("fore", "lower", "hand", "wrist", "elbow", "finger", "thumb", "index", "middle", "ring", "pinky", "palm")
LEG_PROXIMAL = ("pelvis", "hip", "shoulder", "scapula", "clavicle", "girdle")


def side_of(name):
    n = name.split(":")[-1]
    m = _CAMEL.match(n)
    if m: return m.group(1)
    for rx, s in _SIDE_RE:
        if rx.search(n): return s
    return None


def _has(n, words): return any(w in n for w in words)


def part_of(name):
    """torso, head, arm, leg, tail, wing or other, from one bone name."""
    n = name.split(":")[-1].lower()
    if "tail" in n: return "tail"
    if "wing" in n or "feather" in n: return "wing"
    if _has(n, LEG_WORDS) and not _has(n, ("hand", "finger")): return "leg"
    if "upleg" in n: return "leg"
    if _has(n, ARM_WORDS): return "arm"
    if _has(n, HEAD_WORDS): return "head"
    if _has(n, TORSO_WORDS): return "torso"
    return "other"


def end_of(name):
    """front, back or None for a leg (a four-legged rig's two pairs)."""
    n = name.lower()
    if _has(n, ("front", "fore")): return "front"
    if _has(n, ("back", "hind", "rear")): return "back"
    return None


def parts(bones):
    """{bone: (part, side, distal, end)} for a list of (name, parent name or None), with parts inherited down the
    chain: a bone with no telling word is the part of its parent (a finger tip named "bone.012" is still the hand).
    distal: an arm's forearm and hand; a leg below its girdle (pelvis, shoulder blade)."""
    parent = dict(bones)
    out = {}

    def resolve(b, depth=0):
        if b in out: return out[b]
        p = part_of(b)
        par = parent.get(b)
        up = resolve(par, depth + 1) if par in parent and depth < 200 else None
        if p == "other" and up is not None: p = up[0]
        side = side_of(b) or (up[1] if up and up[0] == p else None)
        n = b.split(":")[-1].lower()
        if p == "arm":
            distal = _has(n, ARM_DISTAL) or bool(up and up[0] == "arm" and up[2])
        elif p == "leg":
            distal = not _has(n, LEG_PROXIMAL) or bool(up and up[0] == "leg" and up[2])
        else:
            distal = False
        end = end_of(b) if p in ("leg", "arm") else None
        if end is None and up and up[0] == p: end = up[3]
        out[b] = (p, side, distal, end)
        return out[b]

    for b, _ in bones: resolve(b)
    return out

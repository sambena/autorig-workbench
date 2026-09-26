# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: grades for baked clips (steps/clip_audit.py). The skin audit bends joints by fixed angles and
# never plays a clip, so foot slide, feet through the floor, pops and loop seams went unmeasured: these are the
# things that show at game zoom. Pure numpy, so the tests grade synthetic motion the same way the step grades
# Blender's.
#
# Per clip, from what the step samples every frame:
#   feet      {name: (F, 3)} each foot per frame: the middle of the skin it owns across the ground, and the lowest
#             point of that skin for its height; rest {name: (3,)} the same at rest
#   root      (F, 3) the body bone's head, to tell a clip that carries its own travel (root motion) from one played
#             in place while the engine moves the body
#   rots      (F, B, 4) every deform bone's armature-space rotation as quaternions (w, x, y, z)
#
# Checks (each PASS / CHECK / FAIL, the clip's grade the worst of them):
#   slide     a planted foot's speed over the ground, as a share of the clip's walk speed (locomotion clips whose
#             rate follows ground speed, with a known speed)
#   floor     the deepest a foot goes under where it stands at rest, and on locomotion and idle clips how high the
#             lowest foot hovers, both in % of the model's height
#   pops      frame-to-frame turns far above that bone's usual step
#   seam      a loop whose last pose (or its motion into it) does not meet its first
#   symmetry  left and right feet reaching different distances (locomotion)
import numpy as np

PASS, CHECK, FAIL = "PASS", "CHECK", "FAIL"
RANK = {PASS: 0, CHECK: 1, FAIL: 2}

# (check limit, fail limit): at or under the first PASS, under the second CHECK, else FAIL
LIMITS = {
    "slide": (0.10, 0.25),            # median planted-foot speed / walk speed
    "penetration_pct": (0.75, 2.0),   # % of height under the rest contact
    "hover_pct": (2.0, 5.0),          # % of height the lowest foot hovers (median over the clip)
    "pops": (0, 2),                   # frames with a pop: 0 PASS, 1-2 CHECK, 3+ FAIL
    "seam_deg": (1.0, 5.0),           # worst bone's turn between a loop's last and first pose
    "seam_jump_deg": (8.0, 20.0),     # worst bone's change of per-frame turn across the seam
    "asymmetry": (0.15, 0.35),        # |left reach - right reach| / mean reach
}

POP_MIN_DEG = 25.0         # a step this big (and several times the bone's usual step) is a pop
POP_MIN_DEG_SUDDEN = 45.0  # hits and deaths are meant to be sudden: they get more room
POP_FACTOR = 4.0


def grade(key, value):
    """PASS / CHECK / FAIL for one measured value (None: the check does not apply, PASS)."""
    if value is None:
        return PASS
    ok, bad = LIMITS[key]
    return PASS if value <= ok else CHECK if value <= bad else FAIL


def worst(grades):
    return max(grades, key=lambda g: RANK[g]) if grades else PASS


# ---------------------------------------------------------------- quaternion helpers

def _qnorm(q):
    q = np.asarray(q, dtype=float)
    return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


def angle_between(q1, q2):
    """Degrees between two sets of rotations (any sign of either quaternion)."""
    d = np.abs(np.sum(_qnorm(q1) * _qnorm(q2), axis=-1))
    return np.degrees(2.0 * np.arccos(np.clip(d, 0.0, 1.0)))


def _qmul(a, b):
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def _qinv(q):
    q = _qnorm(q)
    return q * np.array([1.0, -1.0, -1.0, -1.0])


# ---------------------------------------------------------------- checks

def pops(rots, sudden=False, loops=False):
    """Frames where some bone turns much further than it usually does in one frame: (count, worst step in degrees,
    [frame, bone index] of the worst)."""
    rots = _qnorm(rots)
    if len(rots) < 3:
        return 0, 0.0, None
    step = angle_between(rots[1:], rots[:-1])                    # (F-1, B)
    usual = np.median(step, axis=0)                              # each bone's typical step
    floor = POP_MIN_DEG_SUDDEN if sudden else POP_MIN_DEG
    popped = (step > np.maximum(POP_FACTOR * usual, floor))
    frames = np.nonzero(popped.any(axis=1))[0]
    f, b = np.unravel_index(int(np.argmax(step)), step.shape)
    return int(len(frames)), round(float(step.max()), 2), [int(f) + 1, int(b)]


def seam(rots):
    """For a loop sampled 0..last where last should be frame 0 again: the worst bone's pose difference across the
    seam, and how much its per-frame turn changes there (a hitch even when the poses meet)."""
    rots = _qnorm(rots)
    if len(rots) < 3:
        return 0.0, 0.0
    pose = float(angle_between(rots[-1], rots[0]).max())
    end = _qmul(rots[-1], _qinv(rots[-2]))                       # the turn into the seam
    start = _qmul(rots[1], _qinv(rots[0]))                       # the turn out of it
    at_seam = angle_between(end, start)                          # (B,) how much the turn changes there
    # a curving swing changes its turn every frame; a hitch is a change bigger than anywhere else in the clip
    steps = _qmul(rots[1:], _qinv(rots[:-1]))                    # (F-1, B)
    inside = angle_between(steps[1:], steps[:-1]).max(axis=0) if len(steps) > 1 else np.zeros(rots.shape[1])
    jump = float(np.maximum(at_seam - inside, 0.0).max())
    return round(pose, 2), round(jump, 2)


def contact(feet, rest, height, locomotion_or_idle=True):
    """(penetration %, hover %) of the model's height. Heights are each foot's tip above where it stands at rest."""
    if not feet:
        return None, None
    h = np.stack([np.asarray(feet[n])[:, 2] - float(rest[n][2]) for n in feet], axis=1)   # (F, feet)
    pen = max(0.0, float(-h.min())) / max(height, 1e-9) * 100.0
    hover = None
    if locomotion_or_idle:
        hover = max(0.0, float(np.median(h.min(axis=1)))) / max(height, 1e-9) * 100.0
    return round(pen, 3), (round(hover, 3) if hover is not None else None)


def slide(feet, rest, root, fps, speed_units, height, forward=(0.0, -1.0, 0.0)):
    """The median speed of planted feet over the ground, as a share of the walk speed; the share of foot-frames that
    were planted; and whether the clip carries its own travel.

    A clip played in place is moved by the engine at `speed_units` (model units a second) along `forward`; a clip
    that carries its own travel (its root goes at least half as far as that speed would take it) is taken as it is.
    A foot is planted while its tip is within 2% of the height of its lowest point in the clip."""
    if not feet or not speed_units or speed_units <= 0:
        return None, None, None
    root = np.asarray(root, dtype=float)
    frames = len(root)
    t = np.arange(frames) / float(fps)
    fwd = np.asarray(forward, dtype=float)
    travel = float(np.linalg.norm((root[-1] - root[0])[:2]))
    carried = travel >= 0.5 * speed_units * (frames - 1) / float(fps)
    moved = np.zeros((frames, 3)) if carried else np.outer(t * speed_units, fwd)
    speeds = []
    planted_n = total = 0
    for n in feet:
        p = np.asarray(feet[n], dtype=float) + moved
        z = p[:, 2]
        planted = z <= z.min() + 0.02 * height
        planted_n += int(planted.sum()); total += len(planted)
        both = planted[1:] & planted[:-1]
        v = np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1) * fps
        speeds.extend(v[both].tolist())
    if not speeds:
        return None, round(planted_n / max(1, total), 3), carried
    return round(float(np.median(speeds)) / speed_units, 3), round(planted_n / max(1, total), 3), carried


def asymmetry(feet, root, forward=(0.0, -1.0, 0.0), side_of=None):
    """|left reach - right reach| / mean reach over the left/right foot pairs (reach: how far the foot travels along
    the forward axis relative to the body), or None when the feet do not pair up."""
    if not feet or side_of is None:
        return None
    root = np.asarray(root, dtype=float)
    fwd = np.asarray(forward, dtype=float)
    reach = {}
    for n, p in feet.items():
        along = (np.asarray(p, dtype=float) - root) @ fwd
        reach[n] = float(along.max() - along.min())
    pairs = []
    for n in reach:
        if side_of(n) != "L":
            continue
        mate = _mirror(n, reach)
        if mate:
            pairs.append((reach[n], reach[mate]))
    if not pairs:
        return None
    return round(max(abs(a - b) / max(1e-9, (a + b) / 2.0) for a, b in pairs), 3)


def _mirror(name, names):
    for a, b in ((".L", ".R"), ("_L", "_R"), ("Left", "Right"), (".l", ".r"), ("_l", "_r")):
        if a in name:
            m = name.replace(a, b)
            if m in names:
                return m
    return None


def grade_clip(slot, loops, rate_follows_speed, fps, rots, feet=None, rest=None, root=None, speed_units=None,
               height=1.0, side_of=None, forward=(0.0, -1.0, 0.0), grounded=True):
    """Every check that applies to one clip: {"grade", "checks": {name: {"value", "grade", ...}}}. `grounded`: the
    creature walks (has a locomotion clip whose rate follows ground speed); a flyer's idle hovers on purpose."""
    checks = {}
    locomotion = slot == "locomotion"
    walking = locomotion and rate_follows_speed
    n_pops, worst_step, where = pops(rots, sudden=slot in ("hit", "death"), loops=loops)
    checks["pops"] = {"value": n_pops, "grade": grade("pops", n_pops), "worst_step_deg": worst_step, "at": where}
    if loops:
        s_pose, s_jump = seam(rots)
        checks["seam"] = {"value": s_pose, "grade": worst([grade("seam_deg", s_pose), grade("seam_jump_deg", s_jump)]),
                          "jump_deg": s_jump}
    if feet and rest is not None and (walking or not locomotion):     # a flyer's fly keeps its feet off the floor
        pen, hover = contact(feet, rest, height, locomotion_or_idle=walking or (slot == "idle" and grounded))
        checks["floor"] = {"value": pen, "grade": worst([grade("penetration_pct", pen), grade("hover_pct", hover)]),
                           "hover_pct": hover}
    if walking and feet and root is not None:
        ratio, planted, carried = slide(feet, rest, root, fps, speed_units, height, forward)
        if ratio is not None:
            checks["slide"] = {"value": ratio, "grade": grade("slide", ratio), "planted_share": planted,
                               "root_motion": carried}
        elif speed_units:
            checks["slide"] = {"value": None, "grade": CHECK, "planted_share": planted,
                               "note": "no foot stays planted for two frames running"}
        asym = asymmetry(feet, root, forward, side_of)
        if asym is not None:
            checks["symmetry"] = {"value": asym, "grade": grade("asymmetry", asym)}
    return {"grade": worst([c["grade"] for c in checks.values()]), "checks": checks}


def grade_model(clips):
    """The model's clip grade: the worst clip's, not counting clips in the "extra" slot (authored for engines that
    want them), which are graded and listed but do not decide it."""
    decisive = [c["grade"] for c in clips if c.get("slot") != "extra"]
    return worst(decisive)

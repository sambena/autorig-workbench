# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: skeleton geometry rules shared by the rig builders (steps/rerig.py, rerig_humanoid.py).
#
# Plain Python on 3-tuples (no bpy, no numpy), so every rule here is unit-tested without Blender:
#   - roll references: one bend plane per limb chain, so a limb's hinges all turn about the same local axis and the
#     two sides mirror (a per-bone rule flipped the roll 180 degrees between neighbours either side of a threshold)
#   - IK layout: which bone is the foot, where the IK chain starts, how many bones it spans
#   - pre-bend: a straight limb's middle joint nudged the way the joint bends, so IK never solves it backwards
#   - naming: sides from mirror partners, bone-name clashes resolved on the auto-named bone, never the spec's own
#
# Creature space as rerig.normalise leaves it: facing -Y, Z up, X to the model's left.
import math

FORWARD = (0.0, -1.0, 0.0)
UP = (0.0, 0.0, 1.0)


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _len(a): return math.sqrt(_dot(a, a))


def _norm(a):
    L = _len(a)
    return (a[0] / L, a[1] / L, a[2] / L) if L > 1e-12 else (0.0, 0.0, 0.0)


def _perp(v, d):
    """v with its component along unit d removed."""
    return _sub(v, _mul(d, _dot(v, d)))


def bend_direction(points, index=1):
    """The way a chain bends at joint `index` (its first hinge by default): from the chord between the chain's ends
    out to that joint, as a unit vector; None when the chain is straight there (under 1% of its length)."""
    if len(points) < 3 or not 0 < index < len(points) - 1:
        return None
    H, T = points[0], points[-1]
    chord = _sub(T, H)
    L = _len(chord)
    if L < 1e-9:
        return None
    d = _mul(chord, 1.0 / L)
    off = _perp(_sub(points[index], H), d)
    return _norm(off) if _len(off) > 0.01 * L else None


def roll_refs(points, limb=False, hinge=1):
    """A roll reference (the vector each bone's local Z is aligned to, Blender's align_roll) for every bone of a
    chain through `points`.

    One reference for the whole chain, so neighbouring bones never disagree:
      - a limb (IK leg or arm) that bends: Z points the way its first hinge bends (the knee's forward, the elbow's
        back), the same plane its IK pole uses, so a bend about local X is a bend of the hinge on every bone;
      - anything else, or a straight limb: forward (-Y) for a chain that runs mostly up and down, up (+Z) for one that
        runs along the body, decided once from the chain's overall direction.
    Each bone takes the reference with its own direction removed; a bone lying along it takes its neighbour's.
    The references have no X of their own unless the geometry does, so mirrored chains get mirrored rolls.
    `hinge` is the index of the limb's first hinge (2 when index 0 is a girdle bone)."""
    n = len(points) - 1
    if n < 1:
        return []
    ref = bend_direction(points[hinge - 1:], 1) if limb else None
    if ref is None:
        overall = _norm(_sub(points[-1], points[0]))
        ref = FORWARD if abs(overall[2]) > 0.7 else UP
    out, prev = [], None
    for i in range(n):
        d = _norm(_sub(points[i + 1], points[i]))
        z = _perp(ref, d)
        if _len(z) < 0.2:                               # the bone lies along the reference: borrow a neighbour's
            z = _perp(prev, d) if prev is not None else _perp(UP if ref != UP else FORWARD, d)
        z = _norm(z)
        if prev is not None and _dot(z, prev) < 0:
            z = _mul(z, -1.0)
        out.append(z)
        prev = z
    return out


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _percentile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo = int(math.floor(k))
    hi = min(len(s) - 1, lo + 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def centre_ring(ring, c0, tangent, sectors=8, iterations=2, q=0.2):
    """The middle of one cross-section of a limb or body: c0 moved so that, in the plane across `tangent` (the
    local direction of the limb at this station), the wall is about as far away on every side.

    Each of `sectors` directions takes a low percentile (q) of its points' distances, not the single nearest point,
    so one stray vertex cannot pull the centre over; a direction with no points on either side is left out of the
    balance; and the whole move is capped at half the ring's median radius, so a lopsided slice cannot throw the
    joint out of the limb. ring: points (3-tuples). Returns the centre as a 3-tuple."""
    t = _norm(tangent)
    if _len(t) < 1e-9 or len(ring) < 4:
        return tuple(c0)
    ref = UP if abs(t[2]) < 0.85 else (1.0, 0.0, 0.0)
    u = _norm(_cross(t, ref))
    v = _norm(_cross(t, u))
    c = tuple(c0)
    mean = _mul((sum(p[0] for p in ring), sum(p[1] for p in ring), sum(p[2] for p in ring)), 1.0 / len(ring))
    radii0 = [_len(_perp(_sub(p, mean), t)) for p in ring]     # the ring's own size, about its centroid
    cap = 0.5 * (_percentile(radii0, 0.5) or 0.0)
    for _ in range(iterations):
        bins = [[] for _ in range(sectors)]
        for p in ring:
            dlt = _sub(p, c)
            pu, pv = _dot(dlt, u), _dot(dlt, v)
            ang = math.atan2(pv, pu)
            bins[int((ang + math.pi) / (2 * math.pi) * sectors) % sectors].append(math.hypot(pu, pv))
        su = sv = 0.0
        half = sectors // 2
        for s in range(half):
            r1, r2 = _percentile(bins[s], q), _percentile(bins[s + half], q)
            if r1 is None or r2 is None:
                continue
            mid = -math.pi + (s + 0.5) * (2 * math.pi / sectors)
            delta = (r1 - r2) * 0.5
            su += delta * math.cos(mid)
            sv += delta * math.sin(mid)
        c = _add(c, _add(_mul(u, su / 2.0), _mul(v, sv / 2.0)))
    move = _sub(c, tuple(c0))
    if cap > 0 and _len(move) > cap:
        c = _add(tuple(c0), _mul(move, cap / _len(move)))
    return c


def station_tangent(points, k, fallback):
    """The local direction of a line of stations at station k (neighbours either side), or `fallback`."""
    if len(points) >= 2:
        a = points[max(0, k - 1)]
        b = points[min(len(points) - 1, k + 1)]
        d = _sub(b, a)
        if _len(d) > 1e-9:
            return _norm(d)
    return _norm(fallback)


def smooth_stations(points, passes=1):
    """Eases a long chain's joints (a tail, a spine of many links) without straightening a limb: chains of three
    bones or fewer are returned as they are (their bends are the knee and the elbow), and on longer ones each joint
    only moves across the chain, never along it, so joints keep their spacing. The ends stay put."""
    pts = [tuple(p) for p in points]
    if len(pts) <= 4:
        return pts
    for _ in range(passes):
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            target = _mul(_add(_add(pts[i - 1], _mul(pts[i], 2.0)), pts[i + 1]), 0.25)
            t = station_tangent(pts, i, _sub(pts[-1], pts[0]))
            move = _perp(_sub(target, pts[i]), t)
            out.append(_add(pts[i], move))
        out.append(pts[-1])
        pts = out
    return pts


def humanoid_roll_ref(bone_name):
    """The humanoid convention (rerig_humanoid.py) by bone, not by a slope threshold: arms, hands, fingers, feet and
    toes roll with Z up; legs, spine, neck and head with Z forward. An A-pose arm at 45 degrees no longer lands on
    one side of a threshold and its forearm on the other."""
    n = bone_name.lower()
    if any(k in n for k in ("shoulder", "arm", "hand", "thumb", "index", "middle", "ring", "pinky", "foot", "toe")):
        return UP
    return FORWARD


def ik_layout(n_bones, girdle=False):
    """(has_foot, first, count) for an IK limb of n_bones (a girdle bone, when there is one, is index 0).

    has_foot  the last bone is a foot that copies the IK control's rotation (three or more limb bones)
    first     index of the first bone the IK moves (the girdle is never in the chain)
    count     the IK constraint's chain_count, on the bone before the foot (or the last bone)."""
    g = 1 if girdle else 0
    limb = n_bones - g
    has_foot = limb >= 3
    count = limb - 1 if has_foot else limb
    return has_foot, g, max(1, count)


def is_front_limb(root, body_mid_y, role="", upright=False):
    """Any arm is a front limb. On an upright body (its spine runs up and down, so front and back of the body are not
    along it) every other limb is a hind leg; otherwise a limb whose root is ahead of the body's middle (creatures
    face -Y) is a front one."""
    if "arm" in (role or "").lower():
        return True
    if upright:
        return False
    return root[1] < body_mid_y


def spine_is_upright(points):
    """True when a body chain runs mostly up and down (a biped's spine), not along the body."""
    if len(points) < 2:
        return False
    d = _sub(points[-1], points[0])
    return abs(d[2]) > 0.7 * max(1e-12, _len(d))


def pre_bend(points, front, amount=0.025, arm=False):
    """Nudges the middle joints of a nearly straight limb (every joint within 1.5% of its length of the straight
    line) the way the joint bends, and returns the new points; a limb that already bends is returned unchanged.

    The nudge is perpendicular to the limb: forward (-Y) for a hind leg's knee, back (+Y) for a front leg's elbow;
    for a leg lying mostly flat (an insect's sprawling legs, where forward is along the limb), the knee goes up. An
    arm (`arm`) always bends its elbow back, however flat it lies: a T-posed arm is horizontal too.
    A three-bone hind leg bends its knee forward and its hock back (digitigrade)."""
    pts = [tuple(p) for p in points]
    if len(pts) < 3:
        return pts
    H, T = pts[0], pts[-1]
    chord = _sub(T, H)
    L = _len(chord)
    if L < 1e-9:
        return pts
    d = _mul(chord, 1.0 / L)
    if max(_len(_perp(_sub(p, H), d)) for p in pts[1:-1]) >= 0.015 * L:
        return pts                                      # it already bends: that bend is the one to keep
    flat = abs(d[2]) < 0.5 and not arm
    if flat:                                            # a leg lying flat: the knee rises
        way = _norm(_perp(UP, d))
    else:
        way = _norm(_perp((0.0, 1.0, 0.0) if front else FORWARD, d))
        if _len(way) < 1e-9:
            way = _norm(_perp(UP, d))
    b = amount * L
    n = len(pts) - 1
    if n == 2:
        pts[1] = _add(pts[1], _mul(way, b))
    elif n >= 3:
        if front or flat:
            for k in range(1, n):
                pts[k] = _add(pts[k], _mul(way, b * k / (n - 1)))
        else:                                           # digitigrade hind leg: knee forward, hock back
            pts[1] = _add(pts[1], _mul(way, b * 1.2))
            pts[2] = _add(pts[2], _mul(way, -b * 0.8))
    return pts


def mirror_partners(chains_x, roles, tol=0.5, min_abs=0.0):
    """For each chain (its mean X, its role), whether another chain of the same role mirrors it on the other side
    (opposite sign, |X| within a factor 1/(1-tol)). A mirrored pair keeps its sides however near the middle it is.
    Chains within min_abs of the centre never pair (a dorsal and a ventral fin, two tails a hair either side)."""
    out = []
    for i, (x, r) in enumerate(zip(chains_x, roles)):
        found = False
        for j, (x2, r2) in enumerate(zip(chains_x, roles)):
            if i == j or r2 != r or x == 0 or x * x2 >= 0 or abs(x) <= min_abs or abs(x2) <= min_abs:
                continue
            a, b = abs(x), abs(x2)
            if min(a, b) >= (1.0 - tol) * max(a, b):
                found = True
                break
        out.append(found)
    return out


# ---------------------------------------------------------------- skeleton checks (audit.py warnings)

_SWAPS = ((".L", ".R"), ("_L", "_R"), (".l", ".r"), ("_l", "_r"))


def mirror_name(name):
    """The other side's name for a sided bone (arm_1.L -> arm_1.R, LeftArm -> RightArm), or None if unsided."""
    for a, b in (("Left", "Right"), ("Right", "Left")):
        if a in name:
            return name.replace(a, b, 1)
    base = name.split(".0")[0] if ".0" in name[-4:] else name      # ".001" does not hide the side
    for a, b in _SWAPS:
        if base.endswith(a):
            return base[:-len(a)] + b
        if base.endswith(b):
            return base[:-len(b)] + a
    return None


def symmetry_issues(heads, centre_x, size, tol=0.03):
    """Mirrored joints that do not mirror: [(left, right, off)] where off is how far the right-side joint is from
    the left-side one mirrored across X = centre_x, as a fraction of the model's size, over tol."""
    out, done = [], set()
    for n, h in heads.items():
        m = mirror_name(n)
        if not m or m not in heads or m in done:
            continue
        done.add(n)
        g = heads[m]
        mirrored = (2 * centre_x - h[0], h[1], h[2])
        off = _len(_sub(mirrored, g)) / max(1e-9, size)
        if off > tol:
            out.append((n, m, round(off, 4)))
    return out


def roll_issues(bones):
    """Rolls that disagree. bones: {name: {"parent": name or None, "z": local Z axis (3-tuple), "chain": key}}.
    Returns (flips, mirror): flips are bones whose Z points against their parent's in the same chain (a hinge that
    bends the other way from its neighbour's); mirror are sided pairs whose Z axes are not mirror images."""
    flips = []
    for n, b in bones.items():
        p = b.get("parent")
        if p in bones and bones[p].get("chain") == b.get("chain") and _dot(_norm(b["z"]), _norm(bones[p]["z"])) < 0:
            flips.append(n)
    mirror, done = [], set()
    for n, b in bones.items():
        m = mirror_name(n)
        if not m or m not in bones or m in done:
            continue
        done.add(n)
        z1, z2 = _norm(b["z"]), _norm(bones[m]["z"])
        if _dot((-z1[0], z1[1], z1[2]), z2) < 0.5:
            mirror.append((n, m))
    return sorted(flips), mirror


def naming_issues(lengths, size, tol=1e-4):
    """Zero-length bones (shorter than tol x the model's size) and names that carry a clash suffix (_v2, .001),
    which mean two bones were given one name. lengths: {name: bone length}."""
    import re
    short = sorted(n for n, L in lengths.items() if L < tol * size)
    clash = sorted(n for n in lengths if re.search(r"(_v\d+(\.[LR])?$)|(\.\d{3}$)", n))
    return short, clash


def dedupe_names(chains, reserved=("root",)):
    """Makes every bone name unique. Names a spec gave (a chain with "names") are kept as they are; a clash with one
    renames the auto-named bone. Two spec names that clash (a spec mistake) are renamed the same way rather than
    stopping the rig. Returns [(old, new), ...] for the log.
    Each chain is a dict with "bones" and optionally "named" (True when the spec gave its names)."""
    used = set(reserved)
    renames = []
    for c in chains:
        if c.get("named"):
            # a spec that gives one name twice (or names a bone "root") is renamed like an auto name, as before,
            # and logged: the rig still builds, and the audit reports the _v name
            for i, b in enumerate(c["bones"]):
                nb, k = b, 1
                while nb in used:
                    k += 1
                    stem, suf = (b[:-2], b[-2:]) if b[-2:] in (".L", ".R") else (b, "")
                    nb = "%s_v%d%s" % (stem, k, suf)
                if nb != b:
                    renames.append((b, nb))
                    c["bones"][i] = nb
                used.add(nb)
    for c in chains:
        if c.get("named"):
            continue
        for i, b in enumerate(c["bones"]):
            nb, k = b, 1
            while nb in used:
                k += 1
                stem, suf = (b[:-2], b[-2:]) if b[-2:] in (".L", ".R") else (b, "")
                nb = "%s_v%d%s" % (stem, k, suf)
            if nb != b:
                renames.append((b, nb))
            c["bones"][i] = nb
            used.add(nb)
    return renames

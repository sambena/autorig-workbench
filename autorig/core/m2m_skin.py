# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's skin-weight solver, ported from TypeScript to NumPy, so the M2b bake-off can skin
# the same fitted skeleton both ways and let the audit pick the winner.
#
# Ported from Mesh2Motion (https://github.com/Mesh2Motion/mesh2motion-app), by Scott Petrovic and contributors, MIT
# licence (https://github.com/Mesh2Motion/mesh2motion-app/blob/main/LICENSE-MIT.MD). The source is our fork's pinned
# submodule, third_party/mesh2motion-app, src/lib/solvers/:
#   SkinningAlgorithm.ts         the order of the passes                       -> solve()
#   WeightCalculator.ts          closest bone midpoint, the human crotch rule  -> _closest_bones(), _crotch_depth()
#   ExtremityWeightCorrector.ts  knuckles back to the hand                     -> _extremity_correction()
#   ArmWeightCorrector.ts        the optional arm plane                        -> _arm_plane_correction()
#   WeightSmoother.ts            torso rings 50/25/10%, limbs toward the child -> _smooth()
#   WeightNormalizer.ts          sums back to 1                                -> _normalize()
#   HeadWeightCorrector.ts       the optional head plane (chibi heads)         -> _head_correction()
#   BoneClassifier.ts, RigConfig.ts, enums/SkeletonType.ts, Utilities.ts (is_leaf_bone, bone_midpoint_to_child)
# The port is faithful on purpose, quirks included (the crotch rule compares a depth with a height; index 0, the root,
# marks an empty slot; later boundary pairs overwrite earlier ones): it is the method being measured, not a better one.
# scripts/m2m_skin_parity.py runs the TypeScript solvers under Node on M2M's own sample models and checks this file
# against them (weights within 1e-4).
#
# Plain Python and NumPy only, so it runs in Blender's bundled Python (no scipy, no trimesh). Coordinates are
# Mesh2Motion's: glTF axes, Y up, metres, world space (the mesh baked, the joints at their world heads). A Blender step
# converts first: (x, y, z) Blender -> (x, z, -y); see autorig/core/m2m_skin_blender.py.
#
#   rig = Rig([(name, head_xyz, parent_name_or_None), ...])     the joints, any order; children keep the input order
#   idx, w = solve(rig, V, F, "human", head_height=None, arm_offset=None)
#       V (n, 3) vertices, F (m, 3) triangles (None: a non-indexed mesh, which M2M gives no smoothing), template one
#       of TEMPLATES. head_height turns the head plane on at that height (M2M's default 1.4); arm_offset turns the arm
#       plane on, that far outboard of the shoulder joint (M2M's default 0.0). Both are off by default, as in M2M.
#       idx (n, 4) indices into rig.names, w (n, 4) weights summing to 1; a slot with weight 0 is empty.
# M2M skins each mesh of a model on its own (adjacency and the crotch ray see one mesh): call solve() once per mesh.
from decimal import Decimal, ROUND_HALF_UP

import numpy as np

# RigConfig.ts: position_tracking_bone_name per skeleton type. "shark" is M2M's Fish.
TRACKING = {"human": "pelvis", "fox": "hips", "bird": "hips", "dragon": "hips", "kaiju": "hips", "spider": "hips",
            "snake": "head", "fish": "pelvis", "horse": "hips"}
TEMPLATES = tuple(TRACKING) + ("shark",)

TORSO, LIMB, EXTREMITY, ROOT, OTHER = "torso", "limb", "extremity", "root", "other"
_EXTREMITY_KW = ('hand', 'foot', 'feet', 'toe', 'ball', 'thumb', 'index', 'middle', 'ring', 'pinky', 'finger', 'teeth',
                 'eye', 'tongue', 'wing', 'feather', 'leaf', 'ear', 'horn')
_LIMB_KW = ('arm', 'upperarm', 'lowerarm', 'forearm', 'elbow', 'wrist', 'nose', 'shoulder', 'clavicle', 'ankle', 'fin',
            'head', 'chin', 'jaw', 'mouth', 'thigh', 'calf', 'shin', 'knee', 'leg', 'upleg', 'lowleg', 'neck', 'humerus')
_TORSO_KW = ('spine', 'chest', 'hips', 'pelvis', 'torso', 'abdomen', 'body', 'tail', 'stomach', 'collar', 'scapula',
             'ribcage')
# HeadWeightCorrector.ts
_HEAD_NAMES = ('DEF-head', 'head', 'Head', 'HEAD')
_HEAD_ARM_KW = ('arm', 'shoulder', 'hand', 'finger', 'thumb', 'elbow', 'forearm', 'upperarm', 'thumb', 'index',
                'middle', 'ring', 'pinky')


def template_type(template):
    t = template.lower()
    t = "fish" if t == "shark" else t
    if t not in TRACKING:
        raise ValueError("m2m_skin: unknown template %r (one of %s)" % (template, ", ".join(TEMPLATES)))
    return t


class Rig:
    """The bone hierarchy as M2M walks it: depth first from the root, each bone before its children, children in the
    order given (three.js's Object3D.traverse). heads (B, 3) world joint positions; parent[i] -1 for a root."""

    def __init__(self, bones):
        bones = [tuple(b) for b in bones]
        given = [b[0] for b in bones]
        if len(set(given)) != len(given):
            raise ValueError("m2m_skin: bone names must be unique")
        kids = {n: [] for n in given}
        roots = []
        for b in bones:
            p = b[2] if len(b) > 2 else None
            if p is None: roots.append(b[0])
            elif p not in kids: raise ValueError("m2m_skin: %s's parent %s is not a bone" % (b[0], p))
            else: kids[p].append(b[0])
        order = []
        stack = list(reversed(roots))
        while stack:
            n = stack.pop()
            order.append(n)
            stack.extend(reversed(kids[n]))
        if len(order) != len(given):
            raise ValueError("m2m_skin: the bone parents form a loop")
        at = {n: i for i, n in enumerate(order)}
        head_of = {b[0]: b[1] for b in bones}
        parent_of = {b[0]: (b[2] if len(b) > 2 else None) for b in bones}
        self.names = order
        self.heads = np.array([[float(c) for c in head_of[n]] for n in order], dtype=np.float64).reshape(-1, 3)
        self.parent = np.array([-1 if parent_of[n] is None else at[parent_of[n]] for n in order], dtype=np.int64)
        self.children = [[at[k] for k in kids[n]] for n in order]

    def __len__(self):
        return len(self.names)

    def is_leaf(self, i):
        """Utility.is_leaf_bone: childless and named as a tip."""
        if self.children[i]: return False
        n = self.names[i].lower()
        return 'leaf' in n or 'tip' in n

    def midpoints(self):
        """Utility.bone_midpoint_to_child: halfway to the first child (lerpVectors(a, b, 0.5)), or the head."""
        mid = self.heads.copy()
        for i, ch in enumerate(self.children):
            if ch:
                a, b = self.heads[i], self.heads[ch[0]]
                mid[i] = a + (b - a) * 0.5
        return mid

    def descendants(self, i):
        out, stack = [], [i]
        while stack:
            j = stack.pop()
            out.append(j)
            stack.extend(self.children[j])
        return out

    def category(self, i):
        """BoneClassifier.classify_bone."""
        n = self.names[i].lower()
        if any(k in n for k in _EXTREMITY_KW): return EXTREMITY
        if any(k in n for k in _LIMB_KW): return LIMB
        if any(k in n for k in _TORSO_KW): return TORSO
        if self.parent[i] < 0: return ROOT
        return OTHER


def _dist(mid, P):
    """Vector3.distanceTo(mid, p) for every (point, bone): (len(P), len(mid)), the same float operations."""
    dx = mid[None, :, 0] - P[:, None, 0]
    dy = mid[None, :, 1] - P[:, None, 1]
    dz = mid[None, :, 2] - P[:, None, 2]
    return np.sqrt(dx * dx + dy * dy + dz * dz)


def _ray_down(origin, V, F):
    """Raycaster.intersectObject straight down (0, -1, 0), double sided: the distance to the nearest hit, or None.
    Ray.intersectTriangle with the same operations, for every triangle at once."""
    if F is None or len(F) == 0: return None
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    d = np.array([0.0, -1.0, 0.0])

    def cross(u, v):
        return np.stack([u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1],
                         u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2],
                         u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]], axis=1)

    def dot(u, v):
        return u[:, 0] * v[0] + u[:, 1] * v[1] + u[:, 2] * v[2]

    e1, e2 = b - a, c - a
    n = cross(e1, e2)
    ddn = d[0] * n[:, 0] + d[1] * n[:, 1] + d[2] * n[:, 2]
    sign = np.where(ddn > 0, 1.0, -1.0)
    ok = ddn != 0
    ddn = np.abs(ddn)
    diff = origin[None, :] - a
    ddqxe2 = sign * dot(cross(diff, e2), d)
    dde1xq = sign * dot(cross(e1, diff), d)
    qdn = -sign * (diff[:, 0] * n[:, 0] + diff[:, 1] * n[:, 1] + diff[:, 2] * n[:, 2])
    ok &= (ddqxe2 >= 0) & (dde1xq >= 0) & ~(ddqxe2 + dde1xq > ddn) & (qdn >= 0)
    if not ok.any(): return None
    with np.errstate(divide='ignore', invalid='ignore'):
        t = qdn[ok] / ddn[ok]
    # Ray.at(t): origin + direction * t, then Vector3.distanceTo(origin)
    px, py, pz = origin[0] + d[0] * t, origin[1] + d[1] * t, origin[2] + d[2] * t
    ex, ey, ez = px - origin[0], py - origin[1], pz - origin[2]
    return float(np.min(np.sqrt(ex * ex + ey * ey + ez * ez)))


def _crotch_depth(rig, mid, V, F, ttype):
    """WeightCalculator.calculate_distance_to_bottom_of_position_tracking_bone: from the tracking bone's midpoint
    straight down to the mesh, plus 10%. 0 when the ray misses."""
    key = TRACKING[ttype].lower()
    track = next((i for i, n in enumerate(rig.names) if key in n.lower()), None)
    if track is None:
        raise ValueError("m2m_skin: position tracking bone not found (%s needs a bone named like %r)"
                         % (ttype, TRACKING[ttype]))
    hit = _ray_down(mid[track], V, F)
    return (hit if hit is not None else 0.0) * 1.1


def _closest_bones(rig, mid, V, ttype, depth, chunk=20000):
    """WeightCalculator.calculate_median_bone_weights: each vertex to the bone whose midpoint is nearest (strictly
    nearer than 1000, first bone wins a tie), never the root or a leaf. The human crotch rule: a hips or pelvis bone
    is ignored for a vertex whose height is above the crotch depth (M2M compares the two as they are)."""
    skip = np.array([n == 'root' or rig.is_leaf(i) for i, n in enumerate(rig.names)], dtype=bool)
    hips = np.array([('hips' in n or 'pelvis' in n) for n in rig.names], dtype=bool) if ttype == "human" \
        else np.zeros(len(rig), dtype=bool)
    out = np.zeros(len(V), dtype=np.int64)
    for s in range(0, len(V), chunk):
        P = V[s:s + chunk]
        D = _dist(mid, P)
        D[:, skip] = np.inf
        if hips.any():
            above = depth < P[:, 1]
            D[np.ix_(above, hips)] = np.inf
        best = np.argmin(D, axis=1)
        far = ~(D[np.arange(len(P)), best] < 1000)
        best[far] = 0
        out[s:s + chunk] = best
    return out


def _extremity_correction(rig, V, si):
    """ExtremityWeightCorrector: a vertex owned by an extremity bone (finger, toe) that sits behind the bone's start
    joint, along the bone toward its first child (or away from its parent for a childless bone), goes to the parent."""
    for i in range(len(rig)):
        if rig.category(i) != EXTREMITY or rig.parent[i] < 0: continue
        head = rig.heads[i]
        if rig.children[i]: v = rig.heads[rig.children[i][0]] - head
        else: v = head - rig.heads[rig.parent[i]]
        length = np.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        direction = v * (1.0 / (length if length else 1.0))
        own = np.nonzero(si[:, 0] == i)[0]
        if not len(own): continue
        t = V[own] - head
        behind = (t[:, 0] * direction[0] + t[:, 1] * direction[1] + t[:, 2] * direction[2]) < 0
        si[own[behind], 0] = rig.parent[i]


def _normalize_one(sw, o):
    total = sw[o] + sw[o + 1] + sw[o + 2] + sw[o + 3]
    if total > 0:
        for k in range(4): sw[o + k] /= total


def _arm_plane_correction(rig, mid, V, si, sw, offset):
    """ArmWeightCorrector: inboard of a vertical plane at the shoulder joint's |x| (plus offset, mirrored), arm
    weights (the upperarm and everything below it, not the clavicle) go to the nearest non-arm bone."""
    shoulder = None
    for kw in ('upperarm', 'shoulder', 'arm'):
        shoulder = next((i for i, n in enumerate(rig.names) if kw in n.lower()), None)
        if shoulder is not None: break
    if shoulder is None: return
    plane_x = abs(rig.heads[shoulder][0]) + offset
    if plane_x <= 0: return
    arm = set()
    for i, n in enumerate(rig.names):
        if 'upperarm' in n.lower(): arm.update(rig.descendants(i))
    if not arm: return
    fallback = [i for i, n in enumerate(rig.names) if i not in arm and not (n == 'root' or rig.is_leaf(i))]
    if not fallback: return
    fb_mid = mid[fallback]
    for v in np.nonzero(np.abs(V[:, 0]) < plane_x)[0]:
        o = v * 4
        stolen, freed = 0.0, -1
        for j in range(4):
            if si[o + j] not in arm or not sw[o + j] > 0: continue
            stolen += sw[o + j]
            sw[o + j] = 0.0; si[o + j] = 0
            if freed == -1: freed = j
        if not stolen > 0: continue
        d = _dist(fb_mid, V[v:v + 1])[0]
        best, bd = fallback[0], np.inf
        for k, dk in enumerate(d):
            if dk < bd: bd, best = dk, fallback[k]
        slot = next((j for j in range(4) if si[o + j] == best and sw[o + j] > 0), -1)
        if slot == -1:
            slot = freed
            si[o + slot] = best
        sw[o + slot] += stolen
        _normalize_one(sw, o)


def _js_fixed6(x):
    """Number.prototype.toFixed(6): ties round away from zero (Python's format rounds them to even), -0 is '0'."""
    if x == 0: return "0.000000"
    return str(Decimal(x).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _smooth(rig, V, F, si, sw):
    """WeightSmoother.smooth_bone_weight_boundaries. Boundary pairs are adjacent vertices wholly owned by different
    bones; torso boundaries blend 50% at the seam then 25% and 10% one and two rings out, limb boundaries blend only
    the child's side (50%), other boundaries 50/50 both sides, extremity-extremity ones not at all. Every write goes
    to all vertices sharing the position (to 6 decimals). Passes run torso, limb, standard, each pair in order."""
    n = len(V)
    adj = [dict() for _ in range(n)]   # insertion-ordered sets, as JS Sets iterate
    if F is not None:
        for a, b, c in F.tolist():
            adj[a][b] = None; adj[a][c] = None
            adj[b][a] = None; adj[b][c] = None
            adj[c][a] = None; adj[c][b] = None
    keys = [_js_fixed6(x) + "," + _js_fixed6(y) + "," + _js_fixed6(z) for x, y, z in V.tolist()]
    shared = {}
    for i, k in enumerate(keys): shared.setdefault(k, []).append(i)

    def same_place(v):
        return shared.get(keys[v]) or [v]

    cat = [rig.category(i) for i in range(len(rig))]

    def write(vs, primary, secondary, w2):
        for idx in vs:
            o = idx * 4
            si[o] = primary; si[o + 1] = secondary; sw[o] = 1.0 - w2; sw[o + 1] = w2
            si[o + 2] = 0; si[o + 3] = 0; sw[o + 2] = 0; sw[o + 3] = 0

    def pair_blend(va, vb, ba, bb, w2):
        write(same_place(va), ba, bb, w2)
        write(same_place(vb), bb, ba, w2)

    # Pass 1: the boundary pairs, classified
    pairs, seen = [], set()
    for i in range(n):
        o = i * 4
        ba = si[o]
        if sw[o] != 1.0: continue
        for j in adj[i]:
            bb = si[j * 4]
            if ba == bb or sw[j * 4] != 1.0: continue
            key = (i, j) if i < j else (j, i)
            if key in seen: continue
            seen.add(key)
            ca, cb = cat[ba], cat[bb]
            if (ca == TORSO or cb == TORSO) and ca != EXTREMITY and cb != EXTREMITY: kind = TORSO
            elif ca == LIMB or cb == LIMB: kind = LIMB
            elif ca == EXTREMITY and cb == EXTREMITY: kind = EXTREMITY
            else: kind = "standard"
            pairs.append((i, j, ba, bb, kind))

    # Pass 2: torso rings
    torso = [p for p in pairs if p[4] == TORSO]
    if torso:
        rings = (0.5, 0.25, 0.10)
        processed, current = set(), {}
        for va, vb, ba, bb, _ in torso:
            pair_blend(va, vb, ba, bb, rings[0])
            processed.add(va); processed.add(vb)
            current[va] = None; current[vb] = None
        for w2 in rings[1:]:
            nxt = {}
            for v in current:
                primary = si[v * 4]
                for nb in adj[v]:
                    if nb in processed: continue
                    if si[nb * 4] != primary: continue
                    if sw[nb * 4] != 1.0: continue
                    other = si[v * 4 + 1]
                    if other == primary or other == 0: continue
                    write(same_place(nb), primary, other, w2)
                    processed.add(nb)
                    nxt[nb] = None
            current = nxt

    # Pass 3: limbs, toward the child only
    for va, vb, ba, bb, kind in pairs:
        if kind != LIMB: continue
        if rig.parent[bb] == ba: write(same_place(vb), bb, ba, 0.5)
        elif rig.parent[ba] == bb: write(same_place(va), ba, bb, 0.5)
        else: pair_blend(va, vb, ba, bb, 0.5)

    # Pass 4: the rest, 50/50 (extremity-extremity pairs are left alone)
    for va, vb, ba, bb, kind in pairs:
        if kind == "standard": pair_blend(va, vb, ba, bb, 0.5)


def _normalize(sw, n):
    """WeightNormalizer.normalize_weights: a vertex whose weights miss 1 by over 1e-4 gets a third of the shortfall
    added to each non-zero slot."""
    for i in range(n):
        o = i * 4
        s = sw[o] + sw[o + 1] + sw[o + 2] + sw[o + 3]
        if abs(s - 1.0) > 1e-4:
            s = 0 + sw[o] + sw[o + 1] + sw[o + 2] + sw[o + 3]
            add = (1 - s) / 3.0
            for k in range(4):
                if sw[o + k] != 0: sw[o + k] += add


def _head_correction(rig, V, si, sw, height):
    """HeadWeightCorrector: above the plane, a vertex mostly on an arm bone goes wholly to the head; otherwise each
    arm influence over 0.3 becomes the head's (or adds to it), then the vertex is normalised."""
    head = -1
    for i, name in enumerate(rig.names):
        low = name.lower()
        if name in _HEAD_NAMES or any(h.lower() in low for h in _HEAD_NAMES):
            head = i
            break
    if head == -1: return
    arm = {i for i, name in enumerate(rig.names) if any(k in name.lower() for k in _HEAD_ARM_KW)}
    if not arm: return
    for v in np.nonzero(V[:, 1] > height)[0]:
        o = v * 4
        if si[o] in arm and sw[o] > 0.5:
            si[o:o + 4] = [head, 0, 0, 0]
            sw[o:o + 4] = [1.0, 0.0, 0.0, 0.0]
            continue
        for j in range(4):
            w = sw[o + j]
            if si[o + j] in arm and w > 0.3:
                slot = next((k for k in range(4) if si[o + k] == head), -1)
                if slot == -1:
                    si[o + j] = head
                else:
                    sw[o + slot] += w
                    sw[o + j] = 0; si[o + j] = 0
        _normalize_one(sw, o)


def solve(rig, V, F, template, head_height=None, arm_offset=None):
    """SkinningAlgorithm.calculate_indexes_and_weights for one mesh: (idx (n, 4) int64, w (n, 4) float64)."""
    ttype = template_type(template)
    V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
    n = len(V)
    if F is not None:
        F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    ray_F = F if F is not None else np.arange(n - n % 3, dtype=np.int64).reshape(-1, 3)
    mid = rig.midpoints()
    depth = _crotch_depth(rig, mid, V, ray_F, ttype)

    closest = _closest_bones(rig, mid, V, ttype, depth)
    si4 = np.zeros((n, 4), dtype=np.int64); si4[:, 0] = closest
    _extremity_correction(rig, V, si4)
    si = si4.reshape(-1).tolist()
    sw = [1.0, 0.0, 0.0, 0.0] * n
    if arm_offset is not None:
        _arm_plane_correction(rig, mid, V, si, sw, float(arm_offset))
    _smooth(rig, V, F, si, sw)
    _normalize(sw, n)
    if head_height is not None:
        _head_correction(rig, V, si, sw, float(head_height))
    return np.array(si, dtype=np.int64).reshape(n, 4), np.array(sw, dtype=np.float64).reshape(n, 4)

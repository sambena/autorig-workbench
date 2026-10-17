# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: body-part rules and skinning passes for hand-placed rigs (kind: "placed").
#
# Generalizes hand-placed winged and creature builders:
#   1. rip_welds: split coincident/welded vertices along seams between parts that move apart
#      (e.g. forearm to thigh, wing tips to tail) so bone heat and mesh trim do not pull across the gap.
#   2. membranes: wing/web membranes riding only wing bones by distance gradients between spars,
#      and cut free from the flank/body outside the wing root.
#   3. parts: body-part rules specifying which bones each part may use, preventing cross-bleed
#      (e.g. no wing weight on an arm, torso restricted to spine).
#   4. blends: smooth weight blending at joins (e.g. limb root to torso capsule).
#   5. rigid_islands: loose pieces or armour plates (pauldrons) riding a single bone whole without bending.
import fnmatch
import math
import re
import os
import numpy as np


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(1e-9, (e1 - e0)), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _seg_dist(P, a, b):
    """Distance from points P (n, 3) to segment a-b, and projection fraction t (0..1)."""
    ab = b - a
    L2 = max(1e-12, float(ab @ ab))
    t = np.clip(((P - a) @ ab) / L2, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(P - proj, axis=1), t


_SIDE_SUFFIX = re.compile(r"[._]([LlRr])$")
_SIDE_INNER = re.compile(r"[._]([LlRr])[._]")


def bone_side(name):
    """"L", "R" or "" for a bone name, read from its side token only: a Left/Right prefix (LeftArm, mixamorig:Left..),
    a .L/_L/.l/_l suffix, or a lone l/r token inside (leg_l_2). A substring test ("_l" in name) called _lower,
    _rear and _leg bones left or right."""
    n = name.split(":")[-1]
    if n.startswith("Left"): return "L"
    if n.startswith("Right"): return "R"
    m = _SIDE_SUFFIX.search(n) or _SIDE_INNER.search(n)
    return m.group(1).upper() if m else ""


def name_tokens(name):
    """The words of a bone name, lower case: "LeftForeArm" -> [left, fore, arm], "ear_1.L" -> [ear, 1, l]. Role
    words are matched against these, so "ear" never matches ForeArm, nor "leg" a word that merely contains it."""
    return [t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[0-9]+|[A-Z]+(?![a-z])", name.split(":")[-1])]


def has_token(name, *words):
    """True when a word of the bone's name starts with one of `words` (ear, ears; antenna, antennae)."""
    toks = name_tokens(name)
    return any(t.startswith(w) for t in toks for w in words)


def read_weights(mesh, bone_names):
    """(n, len(bone_names)) weight matrix of the deform bones' vertex groups."""
    vg = mesh.vertex_groups
    verts = mesh.data.vertices
    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((len(verts), len(bone_names)), dtype=float)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight
    return W


def write_weights(mesh, bone_names, W_old, W_new, eps=1e-3, locked=None):
    """Writes back the rows of W_new that changed from W_old (by more than eps). A bone that gains weight but has no
    vertex group yet gets one (its weight used to be dropped, leaving the row short); a row in `locked` (vertices a
    deliberate cut owns, spec["_locked"]) is never changed. Returns how many rows were written.

    Batched by bone: one remove call for the rows a bone leaves, and one add call per distinct weight (rigid rows
    share 1.0). An entry that is already exactly the new weight is not written again (REPLACE with the same value
    changed nothing). It used to be one add or remove call per row per bone, most of them no-ops."""
    vg = mesh.vertex_groups
    diff = np.abs(W_new - W_old).sum(axis=1)
    rows = np.where(diff > eps)[0]
    if locked:
        rows = rows[~np.isin(rows, np.fromiter(locked, dtype=np.int64, count=len(locked)))]
    if len(rows) == 0:
        return 0
    for bi, name in enumerate(bone_names):
        new = W_new[rows, bi]
        keep = new > 1e-4
        g = vg.get(name)
        made = g is None
        if made:
            if not keep.any():
                continue
            g = vg.new(name=name)
        gone = rows[~keep]
        if len(gone):
            try:
                g.remove(gone.tolist())
            except RuntimeError:
                pass
        put = keep if made else keep & (new != W_old[rows, bi])
        if not put.any():
            continue
        vals = new[put].astype(np.float32)        # what the group stores: one call per distinct stored value
        idx = rows[put]
        uniq, inv = np.unique(vals, return_inverse=True)
        if len(uniq) == len(vals):
            for vi, w in zip(idx.tolist(), vals.tolist()):
                g.add([vi], w, 'REPLACE')
        else:
            for k, w in enumerate(uniq.tolist()):
                g.add(idx[inv == k].tolist(), w, 'REPLACE')
    return int(len(rows))


def islands_from_edges(n, edges):
    """Connected pieces of an n-vertex mesh from its (m, 2) edge array: a list of vertex-index lists, each sorted,
    ordered by each piece's lowest vertex (the order the old one-edge-at-a-time union-find gave). Vectorised: roots
    hook onto the smaller root across every edge at once, then labels jump to their roots, until nothing changes."""
    lab = np.arange(n, dtype=np.int64)
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    if n == 0:
        return []
    if len(edges):
        a, b = edges[:, 0], edges[:, 1]
        while True:
            la, lb = lab[a], lab[b]
            lo, hi = np.minimum(la, lb), np.maximum(la, lb)
            moved = lo != hi
            if not moved.any():
                break
            np.minimum.at(lab, hi[moved], lo[moved])
            while True:
                nxt = lab[lab]
                if np.array_equal(nxt, lab):
                    break
                lab = nxt
    order = np.argsort(lab, kind="stable")
    cuts = np.flatnonzero(np.diff(lab[order])) + 1
    return [part.tolist() for part in np.split(order, cuts)]


_ISLANDS = {}


def mesh_islands(mesh):
    """islands_from_edges for a Blender mesh, remembered for as long as its topology stays the same (the skin passes
    asked for the same pieces five or six times per rig). A fresh copy of the lists each call."""
    me = mesh.data
    n = len(me.vertices)
    ed = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ed)
    key = (me.as_pointer(), n, hash(ed.tobytes()))
    if key not in _ISLANDS:
        _ISLANDS.clear()
        _ISLANDS[key] = islands_from_edges(n, ed.reshape(-1, 2))
    return [list(part) for part in _ISLANDS[key]]


def floating_pieces(co, islands, gap):
    """The loose pieces that float: those not reachable from the body (the largest piece) through pieces each within
    `gap` of one already reached (a hand on a sleeve on a robe is reached), as (vertex indices, distance to the
    nearest reached piece), farthest first. A trimmed generated model leaves scraps behind (a sleeve cut short left
    two 15-17 cm off a robe, riding on the hips); a rosary or a cross lying on the body (under a centimetre), or a
    model built of separate parts (Tripo's), is kept whole."""
    co = np.asarray(co, dtype=np.float64)
    if len(islands) < 2:
        return []
    body_i = max(range(len(islands)), key=lambda k: len(islands[k]))
    reached = [co[islands[body_i]][::max(1, len(islands[body_i]) // 20000)]]
    left = {k for k in range(len(islands)) if k != body_i}
    near = {}

    def dist(k):
        pts = co[islands[k]]
        best = np.inf
        for R in reached:
            for s in range(0, len(pts), 256):
                best = min(best, float(np.sqrt(((pts[s:s + 256, None, :] - R[None, :, :]) ** 2).sum(-1).min())))
                if best <= gap:
                    return best
        return best

    grew = True
    while grew and left:
        grew = False
        for k in sorted(left):
            d = dist(k)
            near[k] = d
            if d <= gap:
                reached.append(co[islands[k]][::max(1, len(islands[k]) // 5000)])
                left.discard(k)
                grew = True
    return sorted(((list(islands[k]), near[k]) for k in left), key=lambda p: -p[1])


def limb_groups(chains):
    """(front_bones, hind_bones): the bones of each leg or arm chain, sorted by where the limb leaves the body (its
    first point against the middle of the body chain; arms are front, and on an upright body legs are hind)."""
    try:
        import rig_geom
    except ImportError:
        from autorig.core import rig_geom
    if not chains or not chains[0].get("points"):
        return [], []
    body = [tuple(p) for p in chains[0]["points"]]
    mid_y = (min(p[1] for p in body) + max(p[1] for p in body)) * 0.5
    upright = rig_geom.spine_is_upright(body)
    front, hind = [], []
    for c in chains[1:]:
        role = (c.get("role") or "").lower()
        if not ("leg" in role or "arm" in role) or role.endswith(("_toe", "_b")) or not c.get("bones") \
                or not c.get("points"):
            continue
        root = tuple(c["points"][1] if c.get("girdle") and len(c["points"]) > 1 else c["points"][0])
        (front if rig_geom.is_front_limb(root, mid_y, role, upright) else hind).extend(c["bones"])
    return front, hind


def body_plan(chains):
    """"biped", "quadruped", "multi" (three or more legs a side) or "other", from the leg chains per side (the side
    of a chain's first bone). Passes that assume a standing two-legged body run for bipeds only. None (no chains
    given) keeps every pass's legacy behaviour."""
    if chains is None:
        return None                               # no chains to go by: the passes keep their old, plan-free rules
    per = {"L": 0, "R": 0}
    for c in chains:
        role = (c.get("role") or "").lower()
        # a Tripo leg's toe or side branch (roles leg_toe, leg_b) is part of that leg, not another one
        if "leg" not in role or role.endswith(("_toe", "_b")) or not c.get("bones"):
            continue
        s = bone_side(c["bones"][0]) or ("L" if c.get("side") == ".L" else "R" if c.get("side") == ".R" else "")
        if s in per:
            per[s] += 1
    legs = max(per.values())
    return "biped" if legs == 1 else "quadruped" if legs == 2 else "multi" if legs >= 3 else "other"


# ---------------------------------------------------------------------------------------------------------------
# Pure mathematical helpers (testable without Blender)
# ---------------------------------------------------------------------------------------------------------------

def compute_membrane_weights(coords, spar_segments, exponent=2.0):
    """Computes normalized distance gradient weights for membrane vertices across wing spar segments.
    coords: (n, 3) array of vertex positions.
    spar_segments: list of (head, tail) tuples, one per spar bone.
    Returns: (n, len(spar_segments)) array of weights summing to 1.0 per row."""
    n = len(coords)
    m = len(spar_segments)
    if n == 0 or m == 0:
        return np.zeros((n, m))
    dists = np.zeros((n, m))
    for j, (h, t) in enumerate(spar_segments):
        dists[:, j], _ = _seg_dist(coords, np.array(h), np.array(t))
    # Inverse distance weighting with small epsilon
    inv_d = 1.0 / np.maximum(dists, 1e-5) ** exponent
    sums = inv_d.sum(axis=1, keepdims=True)
    return inv_d / np.maximum(sums, 1e-9)


def filter_part_weights(weights, bone_names, parts_rules):
    """Enforces body-part rules on a weight matrix.
    weights: (n, num_bones) array of vertex weights.
    bone_names: list of bone names matching columns of weights.
    parts_rules: list of dicts:
      {"name": str, "bones": [str], "allow": [str], "deny": [str]}
    Returns: (n, num_bones) cleaned and normalized weights."""
    W = np.array(weights, copy=True)
    n, num_bones = W.shape
    if n == 0 or num_bones == 0 or not parts_rules:
        return W
    col = {name: i for i, name in enumerate(bone_names)}

    for rule in parts_rules:
        primary = [col[b] for b in rule.get("bones", []) if b in col]
        if not primary:
            continue
        # Vertices where this part has the highest collective weight or nearest assignment
        part_weight = W[:, primary].sum(axis=1)
        belongs = part_weight > 0.35  # vertex predominantly belongs to this part

        # Deny list
        denied_cols = set()
        for pat in rule.get("deny", []):
            for name, idx in col.items():
                if fnmatch.fnmatch(name, pat):
                    denied_cols.add(idx)
        # Allow list (if specified, anything not in allow is denied)
        if "allow" in rule:
            allowed_cols = set()
            for pat in rule["allow"]:
                for name, idx in col.items():
                    if fnmatch.fnmatch(name, pat):
                        allowed_cols.add(idx)
            for idx in range(num_bones):
                if idx not in allowed_cols:
                    denied_cols.add(idx)

        denied_indices = list(denied_cols - set(primary))
        if denied_indices and np.any(belongs):
            W[np.ix_(belongs, denied_indices)] = 0.0

    # Renormalize rows
    row_sums = W.sum(axis=1, keepdims=True)
    has_sum = (row_sums > 1e-6).ravel()
    W[has_sum] /= row_sums[has_sum]
    return W


def compute_join_blend(coords, joint_pos, child_dir, radius, fade=0.4):
    """Computes a smooth 0..1 transition factor between parent (0) and child (1) along a joint.
    coords: (n, 3) vertex positions.
    joint_pos: (3,) position of the joint (head of child bone).
    child_dir: (3,) normalized direction of the child bone.
    radius: sphere of influence radius around the joint.
    fade: fraction of radius over which blend transitions.
    Returns: (n,) blend factors in 0..1."""
    rel = coords - np.array(joint_pos)
    d = np.linalg.norm(rel, axis=1)
    s = rel @ np.array(child_dir)
    in_range = d <= radius
    blend = np.zeros(len(coords))
    half = radius * fade
    val = _smooth(-half, half, s)
    blend[in_range] = val[in_range]
    return blend


# ---------------------------------------------------------------------------------------------------------------
# Blender mesh passes
# ---------------------------------------------------------------------------------------------------------------

def rip_welds_pass(mesh, arm, chains, spec, size, log):
    """Splits welded vertices along seams between specified parts/bones.
    spec['rip_welds'] = [["arm_3.L", "leg_1.L"], {"bones": ["wing_finger1_3.L", "tail_4"], "dist": 0.05}]"""
    rules = spec.get("rip_welds", [])
    if not rules:
        return
    import bmesh

    bones = arm.data.bones
    me = mesh.data
    bm = bmesh.new()
    bm.from_mesh(me)

    max_dim = max(size)
    total_ripped = 0
    cut_layer = bm.verts.layers.float.get("_AUTORIG_CUT") or bm.verts.layers.float.new("_AUTORIG_CUT")

    for rule in rules:
        if isinstance(rule, (list, tuple)) and len(rule) >= 2:
            bA_name, bB_name = rule[0], rule[1]
            dist_limit = max_dim * 0.15
        elif isinstance(rule, dict) and "bones" in rule and len(rule["bones"]) >= 2:
            bA_name, bB_name = rule["bones"][0], rule["bones"][1]
            dist_limit = rule.get("dist", 0.15) * max_dim
        else:
            continue

        if bA_name not in bones or bB_name not in bones:
            continue

        bA, bB = bones[bA_name], bones[bB_name]
        hA, tA = np.array(bA.head_local[:]), np.array(bA.tail_local[:])
        hB, tB = np.array(bB.head_local[:]), np.array(bB.tail_local[:])

        # Each face within reach of both bones goes to the nearer one; the seam is every edge with an A face on
        # one side and a B face on the other. One split along those edges gives each side its own copy of the
        # seam vertices (UVs and face data kept), instead of re-making faces vertex by vertex.
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        if not len(bm.faces):
            continue
        fc = np.array([f.calc_center_median()[:] for f in bm.faces])
        dA, _ = _seg_dist(fc, hA, tA)
        dB, _ = _seg_dist(fc, hB, tB)
        near = (dA <= dist_limit) & (dB <= dist_limit)
        side_b = dB < dA
        seam = []
        for e in bm.edges:
            lf = e.link_faces
            if len(lf) != 2:
                continue
            i, j = lf[0].index, lf[1].index
            if near[i] and near[j] and side_b[i] != side_b[j]:
                seam.append(e)
        if seam:
            for edge in seam:
                for vertex in edge.verts: vertex[cut_layer] = 1.0
            bmesh.ops.split_edges(bm, edges=seam)
            total_ripped += len(seam)

    # the skin passes run after this and weight every vertex, the new copies included
    if total_ripped > 0:
        bm.to_mesh(me)
        me.update()
    bm.free()
    log["rip_welds_split"] = total_ripped


def membrane_pass(mesh, arm, chains, spec, size, log):
    """Enforces wing/web membranes riding strictly wing spar bones with distance gradients,
    and cleanly cuts the flank connection outside the wing root."""
    membranes = spec.get("membranes", [])
    if not membranes:
        return
    bones = arm.data.bones
    vg = mesh.vertex_groups
    verts = mesh.data.vertices
    n = len(verts)
    P = np.empty(n * 3)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    total_membrane_verts = 0

    for m in membranes:
        spar_names = [b for b in m.get("bones", []) if b in bones]
        if not spar_names:
            # Fallback: discover all wing bones from chains
            spar_names = [b for c in chains if "wing" in c.get("role", "") or "wing" in c.get("name", "") for b in c.get("bones", []) if b in bones]
        if not spar_names:
            continue

        spar_segs = [(np.array(bones[b].head_local[:]), np.array(bones[b].tail_local[:])) for b in spar_names]
        root_name = m.get("root_bone", spar_names[0])
        root_head = np.array(bones[root_name].head_local[:]) if root_name in bones else spar_segs[0][0]

        # Calculate distances to spars and to torso/spine
        spar_dists = np.stack([_seg_dist(P, h, t)[0] for h, t in spar_segs], axis=1)
        min_spar_dist = spar_dists.min(axis=1)

        torso_bones = [b for c in chains if c["role"] in ("spine", "abdomen", "hips", "body") for b in c["bones"] if b in bones]
        if torso_bones:
            torso_dists = np.stack([_seg_dist(P, np.array(bones[b].head_local[:]), np.array(bones[b].tail_local[:]))[0] for b in torso_bones], axis=1).min(axis=1)
        else:
            torso_dists = np.full(n, np.inf)

        # Membrane vertices: close to wing spars and outside the spine core
        max_reach = m.get("reach", 0.4) * max(size)
        is_membrane = (min_spar_dist <= max_reach) & (min_spar_dist < torso_dists * 1.5)

        if not np.any(is_membrane):
            continue

        # Distance gradient weights
        exp = m.get("exponent", 2.0)
        sub_dists = spar_dists[is_membrane]
        inv_d = 1.0 / np.maximum(sub_dists, 1e-4) ** exp
        sub_weights = inv_d / np.maximum(inv_d.sum(axis=1, keepdims=True), 1e-9)

        # Non-wing groups to clear
        clear_groups = [g for g in vg if g.name not in spar_names]
        mem_indices = np.nonzero(is_membrane)[0]

        for g in clear_groups:
            for idx in mem_indices:
                g.remove([int(idx)])

        for j, bname in enumerate(spar_names):
            g = vg.get(bname) or vg.new(name=bname)
            w_col = sub_weights[:, j]
            for idx, w in zip(mem_indices, w_col):
                if w > 1e-4:
                    g.add([int(idx)], float(w), 'REPLACE')

        total_membrane_verts += int(is_membrane.sum())

    log["membrane_verts"] = total_membrane_verts


def parts_rules_pass(mesh, arm, chains, spec, size, log):
    """Enforces body-part rules restricting which bones each part may use."""
    parts = spec.get("parts")
    if not parts:
        return
    # Support dict {"arm.L": ["arm_1.L", "arm_2.L"]} or list of rule objects
    rules = []
    if isinstance(parts, dict):
        for name, defn in parts.items():
            if isinstance(defn, list):
                rules.append({"name": name, "bones": defn, "allow": defn})
            elif isinstance(defn, dict):
                r = dict(defn)
                r["name"] = name
                rules.append(r)
    elif isinstance(parts, list):
        rules = parts

    vg = mesh.vertex_groups
    n = len(mesh.data.vertices)
    names = [g.name for g in vg]
    col = {nm: i for i, nm in enumerate(names)}
    W = np.zeros((n, len(names)))
    for v in mesh.data.vertices:
        for g in v.groups:
            W[v.index, g.group] = g.weight

    new_W = filter_part_weights(W, names, rules)

    changed = 0
    diff = np.abs(new_W - W).max(axis=1)
    affected = np.nonzero(diff > 1e-3)[0]
    for idx in affected:
        v = mesh.data.vertices[int(idx)]
        for g in vg:
            w = new_W[idx, g.index]
            if w > 1e-4:
                g.add([v.index], float(w), 'REPLACE')
            else:
                g.remove([v.index])
        changed += 1

    log["parts_rule_verts"] = changed


def blend_joins_pass(mesh, arm, chains, spec, size, log):
    """Smooths weight transitions across specified joins."""
    blends = spec.get("blends", [])
    if not blends:
        return
    bones = arm.data.bones
    vg = mesh.vertex_groups
    verts = mesh.data.vertices
    n = len(verts)
    P = np.empty(n * 3)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    blended_count = 0
    max_dim = max(size)

    for item in blends:
        if isinstance(item, dict):
            b_child = item.get("bone")
            b_parent = item.get("with")
            radius = item.get("radius", 0.15) * max_dim
            fade = item.get("fade", 0.4)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            b_child, b_parent = item[0], item[1]
            radius = (item[2] if len(item) > 2 else 0.15) * max_dim
            fade = 0.4
        else:
            continue

        if b_child not in bones or b_parent not in bones:
            continue

        cg = vg.get(b_child)
        pg = vg.get(b_parent)
        if not cg or not pg:
            continue

        j_pos = np.array(bones[b_child].head_local[:])
        tail_pos = np.array(bones[b_child].tail_local[:])
        c_dir = tail_pos - j_pos
        c_dir_len = np.linalg.norm(c_dir)
        if c_dir_len < 1e-9:
            continue
        c_dir /= c_dir_len

        factor = compute_join_blend(P, j_pos, c_dir, radius, fade)
        active = np.nonzero(factor > 1e-4)[0]

        for i in active:
            v_idx = int(i)
            f = float(factor[i])
            w_c = 0.0
            w_p = 0.0
            for g in verts[v_idx].groups:
                if g.group == cg.index:
                    w_c = g.weight
                elif g.group == pg.index:
                    w_p = g.weight
            tot = w_c + w_p
            if tot > 1e-4:
                new_c = tot * f
                new_p = tot * (1.0 - f)
                cg.add([v_idx], new_c, 'REPLACE')
                pg.add([v_idx], new_p, 'REPLACE')
                blended_count += 1

    log["blend_joins_verts"] = blended_count


def compute_hinge_laplacian_smoothing(
    weights, coords, edges, hinge_pairs, bone_names,
    bone_heads=None, bone_tails=None, passes=6,
    max_gradient=0.28, radius_scale=0.45, model_size=None
):
    """Applies localized edge-aware Laplacian smoothing to skinning weights across joint hinges.
    Prevents mesh tearing and creasing during bending and twisting by smoothing the relative weight
    ratio between parent and child articulating pairs within each joint's hinge capsule.
    
    weights: (N, num_bones) array of vertex weights.
    coords: (N, 3) vertex positions.
    edges: (E, 2) edge vertex indices.
    hinge_pairs: list of (parent_bone, child_bone) names.
    bone_names: list of bone names corresponding to columns in weights.
    bone_heads: dict of {bone_name: (3,)} joint head positions.
    bone_tails: dict of {bone_name: (3,)} joint tail positions.
    passes: number of Laplacian smoothing iterations.
    max_gradient: maximum allowable weight difference across any single edge in the hinge zone.
    radius_scale: fraction of bone length used for the hinge capsule radius.
    model_size: optional (3,) bounding box dimensions of the model.
    Returns: (N, num_bones) smoothed and normalized weights.
    """
    W = np.array(weights, copy=True, dtype=float)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0 or len(edges) == 0:
        return W

    col = {name: i for i, name in enumerate(bone_names)}
    E = np.asarray(edges, dtype=int)
    P = np.asarray(coords, dtype=float)

    if model_size is not None:
        S = max(model_size)
    else:
        span = P.max(axis=0) - P.min(axis=0) if len(P) else np.array([1.0, 1.0, 1.0])
        S = max(span) if len(span) else 1.0
    S = max(1e-4, float(S))

    bone_heads = bone_heads or {}
    bone_tails = bone_tails or {}

    for p_name, c_name in hinge_pairs:
        if p_name not in col or c_name not in col:
            continue
        p_idx = col[p_name]
        c_idx = col[c_name]

        # Determine joint pivot and radius
        if c_name in bone_heads:
            j_pos = np.array(bone_heads[c_name], dtype=float)
        else:
            continue

        if c_name in bone_tails:
            t_pos = np.array(bone_tails[c_name], dtype=float)
            L = float(np.linalg.norm(t_pos - j_pos))
        else:
            L = 0.15 * S

        R = max(1e-6, max(0.05 * max(S, 1e-6), radius_scale * max(1e-3, L)))
        dists = np.linalg.norm(P - j_pos, axis=1)
        in_zone = dists <= R
        if not np.any(in_zone):
            continue

        w_sum = W[:, p_idx] + W[:, c_idx]
        active = in_zone & (w_sum > 0.04)
        if not np.any(active):
            continue

        t = np.zeros(N, dtype=float)
        t[active] = W[active, c_idx] / w_sum[active]

        # Find edges where both endpoints are active
        edge_active = active[E[:, 0]] & active[E[:, 1]]
        if not np.any(edge_active):
            continue
        E_active = E[edge_active]

        # Iterative edge-gradient relaxation and Laplacian smoothing
        for _ in range(passes):
            u_idx = E_active[:, 0]
            v_idx = E_active[:, 1]
            dt = t[u_idx] - t[v_idx]
            steep = np.abs(dt) > max_gradient
            if not np.any(steep):
                break

            for e_k in np.where(steep)[0]:
                u = u_idx[e_k]
                v = v_idx[e_k]
                diff = t[u] - t[v]
                falloff_u = max(0.0, 1.0 - (dists[u] / R)) ** 2
                falloff_v = max(0.0, 1.0 - (dists[v] / R)) ** 2
                shift = 0.35 * (diff - np.sign(diff) * max_gradient)
                t[u] = np.clip(t[u] - shift * falloff_u, 0.0, 1.0)
                t[v] = np.clip(t[v] + shift * falloff_v, 0.0, 1.0)

        # Update weights from relaxed t
        active_indices = np.where(active)[0]
        W[active_indices, c_idx] = w_sum[active_indices] * t[active_indices]
        W[active_indices, p_idx] = w_sum[active_indices] * (1.0 - t[active_indices])

    # Renormalize rows
    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def joint_hinge_smoothing_pass(mesh, arm, chains, spec, size, log):
    """Blender mesh pass: applies localized joint hinge Laplacian smoothing across articulating
    parent-child pairs (knees, elbows, hips, shoulders, neck) to eliminate bend and twist tears."""
    if not spec.get("hinge_smoothing", True):
        return

    vg = mesh.vertex_groups
    verts = mesh.data.vertices
    n = len(verts)
    if n == 0 or len(mesh.data.edges) == 0:
        return

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return

    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((n, len(bone_names)), dtype=float)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight

    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    edges = np.array([tuple(e.vertices) for e in mesh.data.edges], dtype=int)
    bone_heads = {b.name: tuple(b.head_local) for b in arm.data.bones}
    bone_tails = {b.name: tuple(b.tail_local) for b in arm.data.bones}

    # Extract hinge pairs from spec or arm structure
    hinge_pairs = []
    if "hinges" in spec:
        for item in spec["hinges"]:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                hinge_pairs.append((item[0], item[1]))
            elif isinstance(item, dict) and "parent" in item and "child" in item:
                hinge_pairs.append((item["parent"], item["child"]))
    else:
        for b in arm.data.bones:
            if b.use_deform and b.parent and b.parent.use_deform:
                hinge_pairs.append((b.parent.name, b.name))

    passes = int(spec.get("hinge_passes", 8))
    max_gradient = float(spec.get("hinge_max_gradient", 0.28))
    radius_scale = float(spec.get("hinge_radius_scale", 0.45))
    h_span = [float(size[k]) if hasattr(size, '__getitem__') else float(getattr(size, 'xyz'[k])) for k in range(3)]

    cleaned_W = compute_hinge_laplacian_smoothing(
        W, P, edges, hinge_pairs, bone_names,
        bone_heads=bone_heads, bone_tails=bone_tails,
        passes=passes, max_gradient=max_gradient,
        radius_scale=radius_scale, model_size=h_span
    )

    diff = np.abs(cleaned_W - W).sum(axis=1)
    corrected_count = int((diff > 1e-3).sum())

    if corrected_count > 0:
        group_objs = {col[name]: vg.get(name) for name in bone_names if vg.get(name)}
        for vi in np.where(diff > 1e-3)[0]:
            for b_idx, g_obj in group_objs.items():
                w_new = float(cleaned_W[vi, b_idx])
                if w_new > 1e-4:
                    g_obj.add([int(vi)], w_new, 'REPLACE')
                else:
                    try:
                        g_obj.remove([int(vi)])
                    except RuntimeError:
                        pass

    log["joint_hinge_smoothed_verts"] = corrected_count


def compute_twist_shaft_relaxation(weights, coords, edges, shaft_pairs, bone_names,
                                   bone_heads=None, bone_tails=None,
                                   max_twist_gradient=0.25, passes=12, radius_scale=0.55):
    """Relaxes weight gradients along kinematic bone shafts (spine, neck, shoulders, arms)
    to eliminate axial twist shearing and candy-wrapper tearing artifacts.
    Preserves total joint partition of unity (W_P + W_C) while smoothing the relative blend t."""
    if len(weights) == 0 or len(edges) == 0 or not shaft_pairs:
        return weights

    W = np.array(weights, copy=True, dtype=float)
    N = len(W)
    col = {name: i for i, name in enumerate(bone_names)}
    P = np.array(coords, dtype=float)
    E = np.array(edges, dtype=int)
    bone_heads = bone_heads or {}
    bone_tails = bone_tails or {}

    for p_name, c_name in shaft_pairs:
        if p_name not in col or c_name not in col:
            continue
        p_idx = col[p_name]
        c_idx = col[c_name]

        # Determine transition zone around joint pivot and along shaft
        if c_name in bone_heads:
            j_pos = np.array(bone_heads[c_name], dtype=float)
        else:
            continue

        if c_name in bone_tails:
            t_pos = np.array(bone_tails[c_name], dtype=float)
            shaft_vec = t_pos - j_pos
            L = float(np.linalg.norm(shaft_vec))
        else:
            L = 0.15 * max(1e-3, float(np.linalg.norm(P.max(0) - P.min(0))))

        R = radius_scale * max(1e-3, L)          # (was max(0.06 L, 0.55 L), which is always 0.55 L)
        dists = np.linalg.norm(P - j_pos, axis=1)
        in_zone = dists <= R

        w_sum = W[:, p_idx] + W[:, c_idx]
        active = in_zone & (w_sum > 0.05)
        if not np.any(active):
            continue

        t = np.zeros(N, dtype=float)
        t[active] = W[active, c_idx] / w_sum[active]

        edge_active = active[E[:, 0]] & active[E[:, 1]]
        if not np.any(edge_active):
            continue
        E_active = E[edge_active]

        for _ in range(passes):
            u_idx = E_active[:, 0]
            v_idx = E_active[:, 1]
            dt = t[u_idx] - t[v_idx]
            steep = np.abs(dt) > max_twist_gradient
            if not np.any(steep):
                break

            for e_k in np.where(steep)[0]:
                u = u_idx[e_k]
                v = v_idx[e_k]
                diff = t[u] - t[v]
                excess = 0.5 * (diff - np.sign(diff) * max_twist_gradient)
                t[u] = np.clip(t[u] - 0.5 * excess, 0.0, 1.0)
                t[v] = np.clip(t[v] + 0.5 * excess, 0.0, 1.0)

        active_indices = np.where(active)[0]
        W[active_indices, c_idx] = w_sum[active_indices] * t[active_indices]
        W[active_indices, p_idx] = w_sum[active_indices] * (1.0 - t[active_indices])

    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def twist_shaft_relaxation_pass(mesh, arm, chains, spec, size, log):
    """Blender mesh pass: relaxes weight gradients along spine, neck, and shoulder/arm shafts
    to eliminate axial twist shearing and candy-wrapper tearing artifacts."""
    if not spec.get("twist_relaxation", True):
        return

    vg = mesh.vertex_groups
    verts = mesh.data.vertices
    n = len(verts)
    if n == 0 or len(mesh.data.edges) == 0:
        return

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return

    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((n, len(bone_names)), dtype=float)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight

    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    edges = np.array([tuple(e.vertices) for e in mesh.data.edges], dtype=int)
    bone_heads = {b.name: tuple(b.head_local) for b in arm.data.bones}
    bone_tails = {b.name: tuple(b.tail_local) for b in arm.data.bones}

    shaft_pairs = []
    if "twist_pairs" in spec:
        for item in spec["twist_pairs"]:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                shaft_pairs.append((item[0], item[1]))
            elif isinstance(item, dict) and "parent" in item and "child" in item:
                shaft_pairs.append((item["parent"], item["child"]))
    else:
        for b in arm.data.bones:
            if not b.use_deform or not b.parent or not b.parent.use_deform:
                continue
            p_name, c_name = b.parent.name, b.name
            p_lower, c_lower = p_name.lower(), c_name.lower()
            is_shaft = (
                any(k in c_lower for k in ("spine", "neck", "head", "shoulder", "clavicle", "arm", "forearm", "upleg", "thigh", "leg")) or
                any(k in p_lower for k in ("spine", "hips", "neck", "shoulder", "clavicle", "arm", "upleg", "thigh"))
            )
            if is_shaft:
                shaft_pairs.append((p_name, c_name))

    passes = int(spec.get("twist_passes", 12))
    max_gradient = float(spec.get("twist_max_gradient", 0.25))

    cleaned_W = compute_twist_shaft_relaxation(
        W, P, edges, shaft_pairs, bone_names,
        bone_heads=bone_heads, bone_tails=bone_tails,
        max_twist_gradient=max_gradient, passes=passes
    )

    diff = np.abs(cleaned_W - W).sum(axis=1)
    corrected_count = int((diff > 1e-3).sum())

    if corrected_count > 0:
        group_objs = {col[name]: vg.get(name) for name in bone_names if vg.get(name)}
        for vi in np.where(diff > 1e-3)[0]:
            for b_idx, g_obj in group_objs.items():
                w_new = float(cleaned_W[vi, b_idx])
                if w_new > 1e-4:
                    g_obj.add([int(vi)], w_new, 'REPLACE')
                else:
                    try:
                        g_obj.remove([int(vi)])
                    except RuntimeError:
                        pass

    log["twist_shaft_relaxed_verts"] = corrected_count


SHAFT_CHILD_WORDS = ("spine", "neck", "head", "shoulder", "clavicle", "arm", "forearm", "upleg", "thigh", "leg")
SHAFT_PARENT_WORDS = ("spine", "hips", "neck", "shoulder", "clavicle", "arm", "upleg", "thigh")


def split_joint_pairs(pairs):
    """(shaft_pairs, hinge_pairs): each parent-child joint once. Along a spine, neck, arm or leg (a shaft that twists)
    it is relaxed with the twist settings; anywhere else (tails, wings, tentacles, fingers) with the hinge settings.
    The two passes used to go over the same joints one after the other."""
    shafts, hinges = [], []
    for p, c in pairs:
        if has_token(c, *SHAFT_CHILD_WORDS) or has_token(p, *SHAFT_PARENT_WORDS):
            shafts.append((p, c))
        else:
            hinges.append((p, c))
    return shafts, hinges


def joint_relaxation_pass(mesh, arm, chains, spec, size, log):
    """Blender mesh pass: eases steep weight gradients across every articulating joint, once each (split_joint_pairs):
    hinge_smoothing (on by default) for the hinges, twist_relaxation (on by default) for the shafts. `hinges` or
    `twist_pairs` in the spec name the pairs outright."""
    do_hinge = spec.get("hinge_smoothing", True)
    do_twist = spec.get("twist_relaxation", True)
    if not do_hinge and not do_twist:
        return
    verts = mesh.data.vertices
    n = len(verts)
    if n == 0 or len(mesh.data.edges) == 0:
        return
    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return

    def given(key):
        out = []
        for item in spec.get(key) or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((item[0], item[1]))
            elif isinstance(item, dict) and "parent" in item and "child" in item:
                out.append((item["parent"], item["child"]))
        return out

    all_pairs = [(b.parent.name, b.name) for b in arm.data.bones
                 if b.use_deform and b.parent and b.parent.use_deform]
    shafts, hinges = split_joint_pairs(all_pairs)
    if "twist_pairs" in spec:
        shafts = given("twist_pairs")
    if "hinges" in spec:
        # joints the spec lists as hinges are hinges, whatever the names say: they leave the shafts
        hinges = given("hinges")
        shafts = [p for p in shafts if p not in set(hinges)]

    W = read_weights(mesh, bone_names)
    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)
    ed = np.empty(len(mesh.data.edges) * 2, dtype=int)
    mesh.data.edges.foreach_get("vertices", ed)
    edges = ed.reshape(-1, 2)
    heads = {b.name: tuple(b.head_local) for b in arm.data.bones}
    tails = {b.name: tuple(b.tail_local) for b in arm.data.bones}
    span = [float(size[k]) for k in range(3)]

    W2 = W
    if do_twist and shafts:
        W2 = compute_twist_shaft_relaxation(W2, P, edges, shafts, bone_names, bone_heads=heads, bone_tails=tails,
                                            max_twist_gradient=float(spec.get("twist_max_gradient", 0.25)),
                                            passes=int(spec.get("twist_passes", 12)),
                                            radius_scale=float(spec.get("twist_radius_scale", 0.55)))
    if do_hinge and hinges:
        W2 = compute_hinge_laplacian_smoothing(W2, P, edges, hinges, bone_names, bone_heads=heads, bone_tails=tails,
                                               passes=int(spec.get("hinge_passes", 8)),
                                               max_gradient=float(spec.get("hinge_max_gradient", 0.28)),
                                               radius_scale=float(spec.get("hinge_radius_scale", 0.45)),
                                               model_size=span)
    log["joint_relaxed_verts"] = write_weights(mesh, bone_names, W, W2, locked=spec.get("_locked"))
    log["joint_relaxed_pairs"] = {"shafts": len(shafts) if do_twist else 0, "hinges": len(hinges) if do_hinge else 0}


def find_nearest_bone_segment(point, bone_segments):
    """Finds the nearest bone segment to a 3D point.
    bone_segments: list of (name, head_xyz, tail_xyz)
    Returns: (best_bone_name, min_distance)
    """
    best_name = None
    best_dist = float("inf")
    p = np.asarray(point, dtype=float)
    for name, h, t in bone_segments:
        h_arr = np.asarray(h, dtype=float)
        t_arr = np.asarray(t, dtype=float)
        ab = t_arr - h_arr
        denom = max(1e-12, float(np.dot(ab, ab)))
        s = max(0.0, min(1.0, float(np.dot(p - h_arr, ab)) / denom))
        proj = h_arr + s * ab
        d = float(np.linalg.norm(p - proj))
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name, best_dist


def auto_isolate_disconnected_islands(mesh, arm, chains, spec, size, log, already_assigned=None):
    """Automatically identifies topologically disconnected sub-mesh islands (armor plates, holsters,
    buckles, props) with 0 vertex connections to the body and binds them 100% to their single nearest bone."""
    import rerig
    isl = rerig.islands_of(mesh)
    if len(isl) <= 1:
        return 0

    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    total_verts = len(verts)
    sizes = [len(i) for i in isl]
    max_size = max(sizes)

    # Main body islands: the largest component, plus any major component (>35% of total vertices)
    body_islands = {k for k, i in enumerate(isl) if len(i) == max_size or len(i) >= 0.35 * total_verts}

    # Soft chains to respect:
    soft_chains = set(spec.get("soft", []))
    soft_bones = {
        b
        for c in chains
        if c.get("name") in soft_chains or c.get("role") in soft_chains
        for b in c.get("bones", [])
    }

    # Deform bones as (name, head, tail)
    dbones = [b for b in arm.data.bones if b.use_deform]
    if not dbones:
        dbones = list(arm.data.bones)
    segments = [(b.name, np.array(b.head_local), np.array(b.tail_local)) for b in dbones]

    assigned_set = set(already_assigned or [])
    auto_count = 0
    # What counts as a rigid accessory: a small piece (a plate, a buckle, a holster), not a garment. A separately
    # modelled pair of trousers or a jacket is a loose piece too, but it spans joints and must bend with them.
    all_co = np.array([v.co[:] for v in verts]) if total_verts else np.zeros((0, 3))
    model_extent = float(np.max(all_co.max(0) - all_co.min(0))) if total_verts else 1.0
    max_share = float(spec.get("rigid_island_max_share", 0.10))       # of all vertices
    max_extent = float(spec.get("rigid_island_max_extent", 0.25))     # of the model's longest side
    skipped_large = 0
    locked = spec.setdefault("_locked", set()) if isinstance(spec, dict) else set()

    for k, idxs in enumerate(isl):
        if k in body_islands:
            continue
        # Skip if already handled by explicit rule, or bound on purpose by an earlier pass (a boot that feet_on_legs
        # gave to the shin and foot must not become one rigid piece on a single bone)
        if any(i in assigned_set for i in idxs) or any(i in locked for i in idxs):
            continue

        # Island vertex coords
        coords = np.array([verts[i].co[:] for i in idxs])
        if len(idxs) > max_share * total_verts or \
                float(np.max(coords.max(0) - coords.min(0))) > max_extent * model_extent:
            skipped_large += 1
            continue
        center = coords.mean(axis=0)

        # Convert center to armature space if matrices differ
        if hasattr(mesh, "matrix_world") and hasattr(arm, "matrix_world"):
            from mathutils import Vector
            arm_center = np.array((arm.matrix_world.inverted() @ mesh.matrix_world @ Vector(center))[:3])
        else:
            arm_center = center

        # Check if the island already has a clear dominant accumulated bone from initial weighting
        acc_weights = {}
        for i in idxs:
            for g in verts[i].groups:
                if g.group < len(vg):
                    g_name = vg[g.group].name
                    if g_name in arm.data.bones and g_name not in soft_bones:
                        acc_weights[g_name] = acc_weights.get(g_name, 0.0) + g.weight

        if acc_weights:
            top_bone, top_w = max(acc_weights.items(), key=lambda kv: kv[1])
            tot_w = sum(acc_weights.values())
            if tot_w > 0 and (top_w / tot_w) >= 0.50:
                best_bone_name = top_bone
            else:
                best_bone_name, best_dist = find_nearest_bone_segment(arm_center, segments)
        else:
            best_bone_name, best_dist = find_nearest_bone_segment(arm_center, segments)

        if not best_bone_name or best_bone_name in soft_bones:
            continue

        # Bind 100% to this single nearest bone
        target_group = vg.get(best_bone_name) or vg.new(name=best_bone_name)
        for i in idxs:
            for g in list(verts[i].groups):
                vg[g.group].remove([i])
            target_group.add([i], 1.0, "REPLACE")

        assigned_set.update(idxs)
        locked.update(int(i) for i in idxs)        # rigid on purpose: the healer leaves it
        auto_count += 1

    log["auto_rigid_islands"] = auto_count
    if skipped_large:
        log["auto_rigid_islands_skipped_large"] = skipped_large
    return auto_count


def bind_rigid_armor_islands(weights, island_indices_list, host_bone_indices):
    """Pure mathematical helper: binds specified island vertex sets 100% to single host bones.
    weights: (N, num_bones) array of vertex weights.
    island_indices_list: list of lists of vertex indices.
    host_bone_indices: list of host bone column indices corresponding to each island.
    Returns: (N, num_bones) updated weights array."""
    W = np.array(weights, copy=True, dtype=float)
    for idxs, b_idx in zip(island_indices_list, host_bone_indices):
        if len(idxs) == 0:
            continue
        W[idxs, :] = 0.0
        W[idxs, b_idx] = 1.0
    return W


def rigid_islands_pass(mesh, arm, chains, spec, size, log):
    """Enforces 100% rigid binding for armour pieces, pauldrons, shields, scabbards, and accessories."""
    rigid_val = spec.get("rigid_islands")
    rigid_armor = spec.get("rigid_armor", False)
    armor_rules = spec.get("armor") or spec.get("accessories") or rigid_val
    if not rigid_val and not rigid_armor and not armor_rules:
        return
    import rerig
    isl = rerig.islands_of(mesh)
    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    lo, hi = rerig.bounds(mesh)
    span = np.maximum(hi - lo, 1e-9)

    rigid_count = 0
    assigned_indices = set()

    if isinstance(armor_rules, list):
        for rule in armor_rules:
            target_bone = rule.get("bone")
            if not target_bone or target_bone not in arm.data.bones:
                continue
            target_group = vg.get(target_bone) or vg.new(name=target_bone)

            matched_indices = []
            if "island" in rule and 0 <= rule["island"] < len(isl):
                matched_indices = isl[rule["island"]]
            elif "at" in rule:
                # 0..1 target bounds position
                target_pt = np.array(lo) + np.array(span) * np.array(rule["at"])
                # Find nearest island center
                best_idx = None
                best_dist = float("inf")
                for idxs in isl:
                    coords = np.array([verts[i].co[:] for i in idxs])
                    center = coords.mean(axis=0)
                    d = np.linalg.norm(center - target_pt)
                    if d < best_dist:
                        best_dist = d
                        best_idx = idxs
                if best_idx is not None:
                    matched_indices = best_idx
            elif "box" in rule and len(rule["box"]) == 2:
                b0, b1 = np.array(rule["box"][0]), np.array(rule["box"][1])
                for idxs in isl:
                    coords = np.array([verts[i].co[:] for i in idxs])
                    norm_coords = (coords.mean(axis=0) - lo) / span
                    if np.all(b0 <= norm_coords) and np.all(norm_coords <= b1):
                        matched_indices = idxs
                        break

            if matched_indices:
                for i in matched_indices:
                    for g in list(verts[i].groups):
                        vg[g.group].remove([i])
                    target_group.add([i], 1.0, 'REPLACE')
                assigned_indices.update(matched_indices)
                spec.setdefault("_locked", set()).update(int(i) for i in matched_indices)
                rigid_count += 1

    do_auto = (rigid_val in (True, "auto") or rigid_armor or armor_rules is True or
               (isinstance(armor_rules, list) and any(isinstance(r, dict) and r.get("auto") for r in armor_rules)))
    if do_auto:
        auto_count = auto_isolate_disconnected_islands(mesh, arm, chains, spec, size, log, assigned_indices)
        rigid_count += auto_count

    log["rigid_islands_assigned"] = rigid_count


def apply_longitudinal_flank_barrier(weights, coords, bone_names, bone_heads=None, y_mid=None, blend_width=0.08,
                                     front_bones=None, hind_bones=None, min_sep=0.05, bone_tails=None):
    """Enforces longitudinal separation between front limbs and hindquarters on quadrupeds/creatures.
    Prevents distal front leg/shoulder bones from stealing weights from the rear flank/pelvis,
    and hind leg bones from stealing weights from the chest/ribcage.

    front_bones / hind_bones: the limbs' bones sorted by where each limb leaves the body (limb_groups() works this
    out from the chains). Without them, names decide, which misreads segment numbers (leg1_2.L) and a biped's feet.
    min_sep: how far apart the two groups must be for the barrier to apply, in the model's units (scaled by the
    caller)."""
    W = np.array(weights, copy=True, dtype=float)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0 or not bone_heads:
        return W

    col = {name: i for i, name in enumerate(bone_names)}
    given = front_bones is not None and hind_bones is not None

    def is_front_leg(name):
        lower = name.lower()
        return (any(k in lower for k in ("leg_front", "front_leg", "arm", "foreleg", "forearm")) or
                ("leg" in lower and any(k in lower for k in ("_1.", "_1_")))) and "hind" not in lower and "back" not in lower

    def is_hind_leg(name):
        lower = name.lower()
        return (any(k in lower for k in ("leg_hind", "leg_back", "hind_leg", "hindleg", "thigh", "shin", "foot", "toe", "ankle", "hock")) or
                ("leg" in lower and any(k in lower for k in ("_2.", "_2_", "_back", "_hind")))) and "front" not in lower and "arm" not in lower

    def is_torso(name):
        lower = name.lower()
        return any(k in lower for k in ("spine", "hips", "chest", "root", "body", "pelvis"))

    if given:
        front_cols = [col[b] for b in front_bones if b in col and b in bone_heads]
        hind_cols = [col[b] for b in hind_bones if b in col and b in bone_heads]
    else:
        front_cols = [col[b] for b in bone_names if is_front_leg(b) and b in bone_heads]
        hind_cols = [col[b] for b in bone_names if is_hind_leg(b) and b in bone_heads]

    if not front_cols or not hind_cols:
        return W

    torso_cols = [col[b] for b in bone_names if is_torso(b)]
    if not torso_cols:
        return W

    y_front_vals = [bone_heads[bone_names[c]][1] for c in front_cols]
    y_hind_vals = [bone_heads[bone_names[c]][1] for c in hind_cols]
    y_front_mean = float(np.mean(y_front_vals))
    y_hind_mean = float(np.mean(y_hind_vals))

    if abs(y_hind_mean - y_front_mean) < min_sep:
        return W

    front_is_negative_y = y_front_mean < y_hind_mean
    mid_y = y_mid if y_mid is not None else 0.5 * (y_front_mean + y_hind_mean)
    half_span = abs(y_hind_mean - y_front_mean)
    bw = max(0.02, blend_width * half_span)

    Y = coords[:, 1]

    hips_targets = [col[b] for b in bone_names if any(k in b.lower() for k in ("hips", "pelvis", "root"))]
    hips_target = hips_targets[0] if hips_targets else torso_cols[0]
    chest_targets = [col[b] for b in bone_names if any(k in b.lower() for k in ("spine_2", "spine2", "chest", "spine_1"))]
    chest_target = chest_targets[0] if chest_targets else torso_cols[-1]

    # Faded over the middle half between the two limb groups, not cut: the old rule left a dead band 8% of the
    # spacing wide and zeroed everything past it, a line across the flank that tore when the spine bent (on the
    # suggested rooster, a third of its tears). What the limbs give up goes to the torso bones beside the vertex
    # (share_freed) when the bones' tails are known: always to the hips or always to the chest sent a rump's weight
    # to a hips bone at the far end of the body on a rig whose "hips" is not at the back.
    B = max(bw, 0.25 * half_span)
    r = (Y - mid_y) if front_is_negative_y else (mid_y - Y)          # how far toward the hind limbs
    k_front = _smooth(-B, B, r)              # the share of the front limbs' weight taken: none at -B, all at +B
    k_hind = _smooth(-B, B, -r)
    from_front = W[:, front_cols].sum(1) * k_front
    from_hind = W[:, hind_cols].sum(1) * k_hind
    W[:, front_cols] *= (1.0 - k_front)[:, None]
    W[:, hind_cols] *= (1.0 - k_hind)[:, None]
    tc = [c for c in torso_cols if bone_names[c] in bone_heads and bone_names[c] in (bone_tails or {})]
    if tc:
        D = np.stack([_seg_dist(coords, np.asarray(bone_heads[bone_names[c]], float),
                                np.asarray(bone_tails[bone_names[c]], float))[0] for c in tc], 1)
        L = float(np.median([np.linalg.norm(np.subtract(bone_tails[bone_names[c]], bone_heads[bone_names[c]]))
                             for c in tc]))
        W[:, tc] += share_freed(W[:, tc], from_front + from_hind, D, 0.2 * L)
    else:
        W[:, hips_target] += from_front
        W[:, chest_target] += from_hind

    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def apply_tail_isolation_barrier(weights, coords, bone_names, bone_heads=None):
    """Prevents tail vertebrae from bleeding onto buttocks, thighs, and hamstrings,
    eliminating severe stretching and tearing when the tail articulates."""
    W = np.array(weights, copy=True, dtype=float)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0:
        return W

    col = {name: i for i, name in enumerate(bone_names)}
    tail_cols = [col[b] for b in bone_names if "tail" in b.lower()]
    if not tail_cols:
        return W

    distal_tail_cols = [
        col[b] for b in bone_names
        if "tail" in b.lower() and not any(b.lower().endswith(s) for s in ("_1", "1", "_base", "base"))
    ]
    if not distal_tail_cols:
        distal_tail_cols = tail_cols[1:] if len(tail_cols) > 1 else []

    if not distal_tail_cols:
        return W

    def is_hind_leg(name):
        return has_token(name, "leg", "thigh", "shin", "foot", "upleg") and not has_token(name, "tail")

    hind_cols = [col[b] for b in bone_names if is_hind_leg(b)]
    hips_targets = [col[b] for b in bone_names if any(k in b.lower() for k in ("hips", "pelvis", "root", "spine"))]
    hips_target = hips_targets[0] if hips_targets else None
    if hips_target is None:
        return W

    if hind_cols:
        hind_weight = W[:, hind_cols].sum(axis=1)
        on_leg = hind_weight > 0.25
        if np.any(on_leg):
            leg_indices = np.where(on_leg)[0]
            for idx in leg_indices:
                w_tail = float(W[idx, distal_tail_cols].sum())
                if w_tail > 1e-4:
                    W[idx, distal_tail_cols] = 0.0
                    W[idx, hips_target] += w_tail

    if bone_heads:
        # Along the tail's own direction, not by height: the vertices behind the tail's root (on the body's side of
        # it) keep no distal tail weight. "Below the tail base" made a tail that hangs or drags on the ground ride
        # the pelvis.
        base_tail = [b for b in bone_names if "tail" in b.lower() and b in bone_heads]
        if len(base_tail) >= 2:
            base = np.array(bone_heads[base_tail[0]], dtype=float)
            nxt = np.array(bone_heads[base_tail[1]], dtype=float)
            d = nxt - base
            L = float(np.linalg.norm(d))
            if L > 1e-9:
                d /= L
                t = (coords - base) @ d
                span = float(np.linalg.norm(coords.max(0) - coords.min(0))) if len(coords) else 1.0
                # and not on the tail itself: a tail that curls forward over the body (a scorpion's, a squirrel's)
                # has its far end ahead of its root, and keeps it
                pts = [np.array(bone_heads[b], dtype=float) for b in base_tail]
                near_tail = np.full(len(coords), np.inf)
                for a, b in zip(pts[:-1], pts[1:]):
                    dseg, _ = _seg_dist(coords, a, b)
                    near_tail = np.minimum(near_tail, dseg)
                behind = (t < -0.04 * span) & (near_tail > 0.08 * span)
                for idx in np.where(behind)[0]:
                    w_tail = float(W[idx, distal_tail_cols].sum())
                    if w_tail > 1e-4:
                        W[idx, distal_tail_cols] = 0.0
                        W[idx, hips_target] += w_tail

    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def apply_radial_limb_sector_isolation(weights, coords, bone_names, bone_heads=None, center_xy=None, max_sector_angle=1.1):
    """For multi-legged creatures (hexapods, octopods, beetles, crabs), isolates legs into radial angular sectors.
    Prevents adjacent legs (e.g. leg1 vs leg2 vs leg3) from bleeding into each other across narrow crevices."""
    W = np.array(weights, copy=True, dtype=float)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0 or not bone_heads:
        return W

    col = {name: i for i, name in enumerate(bone_names)}

    # One key per leg, not per bone: a hexapod's legs are leg1_1.L, leg1_2.L ... (rerig.name_chains), or leg1.L for a
    # single-bone leg. The old pattern (leg_?N.L) matched a biped's own leg_1.L, leg_2.L, leg_3.L as three "legs"
    # and never matched a hexapod's.
    leg_pat = re.compile(r"^leg(\d+)(?:_\d+)?[._]([LR])$", re.IGNORECASE)
    chain_bones = {}
    for b in bone_names:
        m = leg_pat.match(b)
        if m:
            key = (int(m.group(1)), m.group(2).upper())
            chain_bones.setdefault(key, []).append(col[b])

    if len(chain_bones) < 4:
        return W

    torso_cols = [col[b] for b in bone_names if any(k in b.lower() for k in ("body", "spine", "abdomen", "thorax", "hips", "root"))]
    if not torso_cols:
        return W
    body_target = torso_cols[0]

    if center_xy is not None:
        cx, cy = center_xy
    else:
        torso_heads = [bone_heads[b] for b in bone_names if col[b] in torso_cols and b in bone_heads]
        if torso_heads:
            cx = float(np.mean([h[0] for h in torso_heads]))
            cy = float(np.mean([h[1] for h in torso_heads]))
        else:
            cx = float(np.median(coords[:, 0]))
            cy = float(np.median(coords[:, 1]))

    X = coords[:, 0]
    Y = coords[:, 1]
    vert_angles = np.arctan2(Y - cy, X - cx)

    for key, cols in chain_bones.items():
        root_bone = bone_names[cols[0]]
        if root_bone not in bone_heads:
            continue
        rx, ry = bone_heads[root_bone][0], bone_heads[root_bone][1]
        leg_angle = math.atan2(ry - cy, rx - cx)

        diff = np.abs(np.arctan2(np.sin(vert_angles - leg_angle), np.cos(vert_angles - leg_angle)))
        outside_sector = diff > max_sector_angle
        if np.any(outside_sector):
            out_indices = np.where(outside_sector)[0]
            for idx in out_indices:
                w_leg = float(W[idx, cols].sum())
                if w_leg > 1e-4:
                    W[idx, cols] = 0.0
                    W[idx, body_target] += w_leg

    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def compute_sibling_appendage_isolation(weights, coords, bone_names, bone_heads=None, chains=None):
    """Prevents adjacent sibling appendages (e.g. tentacle1, tentacle2 or leg1, leg2 on same side)
    from cross-contaminating each other's distal surfaces."""
    if len(weights) == 0 or len(coords) == 0 or not bone_heads:
        return weights

    W = np.array(weights, copy=True, dtype=float)
    col = {b: i for i, b in enumerate(bone_names)}

    groups = {}
    if chains:
        for c in chains:
            role = c.get("role", "")
            bones = [b for b in c.get("bones", []) if b in col]
            if len(bones) < 2:
                continue
            sides = {bone_side(b) for b in bones} - {""}
            side = ".L" if sides == {"L"} else ".R" if sides == {"R"} else ""
            category = None
            if any(k in role.lower() for k in ("tentacle", "tendril", "streamer")):
                category = f"tentacles{side}"
            elif any(k in role.lower() for k in ("leg", "claw", "fin")):
                category = f"legs{side}"
            elif any(k in role.lower() for k in ("wing", "spar")):
                category = f"wings{side}"
            if category:
                groups.setdefault(category, []).append(bones)
    else:
        prefix_pat = re.compile(r"^([a-zA-Z]+)(\d+)[._](.+)$")
        pat_groups = {}
        for b in bone_names:
            m = prefix_pat.match(b)
            if m:
                kind, num, rest = m.groups()
                side = {"L": ".L", "R": ".R"}.get(bone_side(b), "")
                cat = f"{kind.lower()}{side}"
                chain_key = f"{cat}_{num}"
                pat_groups.setdefault(cat, {}).setdefault(chain_key, []).append(b)
        for cat, ch_dict in pat_groups.items():
            if len(ch_dict) >= 2:
                groups[cat] = list(ch_dict.values())

    for cat, chain_list in groups.items():
        if len(chain_list) < 2:
            continue
        for i in range(len(chain_list)):
            for j in range(i + 1, len(chain_list)):
                c1_bones = chain_list[i]
                c2_bones = chain_list[j]
                c1_distal = c1_bones[1:]
                c2_distal = c2_bones[1:]
                if not c1_distal or not c2_distal:
                    continue
                c1_cols = [col[b] for b in c1_distal]
                c2_cols = [col[b] for b in c2_distal]

                w1 = W[:, c1_cols].sum(axis=1)
                w2 = W[:, c2_cols].sum(axis=1)
                overlap = (w1 > 1e-4) & (w2 > 1e-4)
                if not np.any(overlap):
                    continue

                ov_idx = np.where(overlap)[0]
                ov_co = coords[ov_idx]

                pts1 = np.array([bone_heads[b] for b in c1_bones if b in bone_heads])
                pts2 = np.array([bone_heads[b] for b in c2_bones if b in bone_heads])
                if len(pts1) == 0 or len(pts2) == 0:
                    continue

                d1 = np.min(np.linalg.norm(ov_co[:, None, :] - pts1[None, :, :], axis=2), axis=1)
                d2 = np.min(np.linalg.norm(ov_co[:, None, :] - pts2[None, :, :], axis=2), axis=1)

                on_c1 = d1 < d2
                if np.any(on_c1):
                    for vi in ov_idx[on_c1]:
                        freed = float(W[vi, c2_cols].sum())
                        W[vi, c2_cols] = 0.0
                        best_col = c1_cols[int(np.argmax(W[vi, c1_cols]))]
                        W[vi, best_col] += freed

                on_c2 = d2 <= d1
                if np.any(on_c2):
                    for vi in ov_idx[on_c2]:
                        freed = float(W[vi, c1_cols].sum())
                        W[vi, c1_cols] = 0.0
                        best_col = c2_cols[int(np.argmax(W[vi, c2_cols]))]
                        W[vi, best_col] += freed

    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def compute_closed_loop_laplacian_healing(weights, coords, edges, max_gradient=0.20, passes=3, blend_factor=0.5,
                                          locked=None, edge_scale=True):
    """Closed-loop Laplacian tear healer:
    Identifies connected mesh edges across which weight gradients exceed max_gradient (which
    causes tears/holes in QA bend tests), and applies localized topological Laplacian relaxation
    to diffuse the gradient smoothly along the mesh surface without jumping across air gaps.

    weights: (N, M) array of vertex bone weights.
    coords: (N, 3) array of vertex coordinates.
    edges: list or array of (u, v) vertex index pairs.
    max_gradient: maximum allowable weight difference on any bone across a connected edge (default 0.20).
    passes: number of relaxation iterations (default 3).
    blend_factor: blend weight towards neighbor average per pass (default 0.5).
    locked: vertex indices never changed (a hard split, a shell, a rigid piece: cuts made on purpose, which the
      healer used to blur because it ran last).
    edge_scale: the limit on an edge grows with its length (against the median edge, x1 .. x4): a weight step
      spread over a long edge is not the tear a step over a short one is, and a fixed limit blurred every joint of a
      low-poly mesh.

    Returns: (healed_weights, modified_vert_count)
    """
    if len(weights) == 0 or len(edges) == 0:
        return weights, 0

    W = np.array(weights, copy=True, dtype=float)
    N, M = W.shape
    modified_mask = np.zeros(N, dtype=bool)

    edges_arr = np.array(edges, dtype=int)
    if edges_arr.ndim != 2 or edges_arr.shape[1] != 2:
        return weights, 0

    u_idx = edges_arr[:, 0]
    v_idx = edges_arr[:, 1]
    valid_mask = (u_idx >= 0) & (u_idx < N) & (v_idx >= 0) & (v_idx < N) & (u_idx != v_idx)
    u_idx = u_idx[valid_mask]
    v_idx = v_idx[valid_mask]
    if len(u_idx) == 0:
        return weights, 0

    limit = np.full(len(u_idx), float(max_gradient))
    P = np.asarray(coords, dtype=float)
    if edge_scale and len(P) == N:
        lens = np.linalg.norm(P[u_idx] - P[v_idx], axis=1)
        med = float(np.median(lens)) if len(lens) else 0.0
        if med > 1e-12:
            limit = max_gradient * np.clip(lens / med, 1.0, 4.0)     # never stricter than max_gradient
    lock_mask = np.zeros(N, dtype=bool)
    if locked:
        li = np.array([i for i in locked if 0 <= int(i) < N], dtype=int)
        lock_mask[li] = True

    # Build adjacency list: adj[u] = list of neighbor vertex indices
    adj = [[] for _ in range(N)]
    for u, v in zip(u_idx, v_idx):
        adj[u].append(v)
        adj[v].append(u)

    for p in range(passes):
        edge_diffs = np.max(np.abs(W[u_idx] - W[v_idx]), axis=1)
        violating = edge_diffs > limit
        if not np.any(violating):
            break

        violating_u = u_idx[violating]
        violating_v = v_idx[violating]
        violating_verts = np.unique(np.concatenate([violating_u, violating_v]))
        violating_verts = violating_verts[~lock_mask[violating_verts]]
        if len(violating_verts) == 0:
            break

        new_W_vals = np.zeros((len(violating_verts), M), dtype=float)
        for i, u in enumerate(violating_verts):
            nbrs = adj[u]
            if nbrs:
                avg = np.mean(W[nbrs], axis=0)
                new_W_vals[i] = (1.0 - blend_factor) * W[u] + blend_factor * avg
            else:
                new_W_vals[i] = W[u]

        W[violating_verts] = new_W_vals
        modified_mask[violating_verts] = True

        row_sums = W[violating_verts].sum(axis=1, keepdims=True)
        valid = (row_sums > 1e-6).ravel()
        if np.any(valid):
            v_valid = violating_verts[valid]
            W[v_valid] /= row_sums[valid]

    mod_indices = np.where(modified_mask)[0]
    if len(mod_indices) > 0:
        sums = W[mod_indices].sum(axis=1, keepdims=True)
        valid = (sums > 1e-6).ravel()
        if np.any(valid):
            m_valid = mod_indices[valid]
            W[m_valid] /= sums[valid]

    return W, int(np.sum(modified_mask))


def soft_nearest(D, soft):
    """Rows of weights from rows of distances: the nearest gets the most, and any within about `soft` of it share,
    falling off smoothly (exp(-(d - d_min) / soft)). soft 0: all to the nearest."""
    D = np.asarray(D, dtype=float)
    out = np.zeros_like(D)
    if not D.size: return out
    if soft <= 0:
        out[np.arange(len(D)), D.argmin(1)] = 1.0
        return out
    E = np.exp(-(D - D.min(1, keepdims=True)) / soft)
    E[E < 1e-3] = 0.0
    return E / E.sum(1, keepdims=True)


def root_fade(s, L1, torso_len, fade=1 / 3.0):
    """The root mask's fade-in of a limb's weight by s, the signed distance in front of its root plane: none from a
    tenth of the span behind the plane, all from `fade` of it in front. The span is the first bone's length, but
    never shorter than a torso bone's: a 2 cm first bone on a 1 m body (the suggested rooster's legs) made the fade a
    cut (half of its 2120 bend tears)."""
    Lb = max(float(L1), float(torso_len))
    return _smooth(-0.1 * Lb, Lb * fade, np.asarray(s, dtype=float))


def share_freed(T, freed, D, soft):
    """How weight a pass takes off a vertex goes to a group of bones (the torso): rows of weights to add, (N, k).
    T: the vertex's own weights on those bones, (N, k); freed: the weight to hand over, (N,); D: the vertex's distance
    to each of them, (N, k); soft: soft_nearest's band. The vertex's own bones keep their say in proportion to how
    much of them it already has, and the rest goes by distance: (T + freed * nearest) / (sum T + freed). Handing it
    all over in the vertex's own ratio cut a seam wherever that ratio flipped on a trace of weight (0.01 of one spine
    bone against 0.002 of the next), and all to the nearest bone only where it had none at all switched at 1e-6."""
    T = np.asarray(T, dtype=float)
    freed = np.asarray(freed, dtype=float)
    if not T.size:
        return np.zeros_like(T)
    share = (T + freed[:, None] * soft_nearest(D, soft)) / np.maximum(T.sum(1) + freed, 1e-12)[:, None]
    return share * freed[:, None]


def apply_geodesic_skin_barrier(weights, coords, bone_names, bone_heads=None, sym_plane=0.0,
                                crotch_threshold=0.04, armpit_barrier=True, height_span=None,
                                flank_barrier=True, tail_barrier=True, radial_barrier=True,
                                plan=None, front_bones=None, hind_bones=None, bone_tails=None):
    """Enforces geodesic, vertical, and air-gap anatomical barriers on skin weights:
    1. Crotch / Bilateral barrier: eliminates opposite-leg cross-bleed across the air gap between legs.
    2. Arm & Shoulder vertical isolation: strictly prevents arms, hands, shoulders, and clavicles
       from pulling on pelvis, hips, thighs, knees, and feet across the air gap.
    3. Shoulder-Neck and Shoulder-Spine boundary: prevents shoulder clavicles from bleeding onto the
       central neck column or middle/lower spine.
    4. Upper-Torso-to-Leg isolation: prevents upper chest/spine (Spine1, Spine2) and neck from bleeding
       onto the lower limbs.
    5. Distal leg height isolation: prevents foot/toe bones from bleeding onto thighs or pelvis.
    6. Leg-to-Upper-Body isolation: prevents leg bones from bleeding onto the chest and upper body.
    7. Armpit / Flank barrier: prevents distal arm bones from pulling rib/torso vertices across underarm gaps.
    8. Non-humanoid appendage isolation: prevents ears, antennae, flukes, flippers, and wisps from bleeding
       into distant body segments.
    weights: (N, num_bones) array of vertex weights.
    coords: (N, 3) array of vertex positions.
    bone_names: list of bone names matching columns of weights.
    bone_heads: optional dict or array of bone head positions (num_bones, 3).
    sym_plane: X coordinate of symmetry plane (default 0.0).
    crotch_threshold: distance from sym_plane beyond which opposite-leg weights are completely zeroed.
    armpit_barrier: whether to clean distal arm weights off chest/torso vertices.
    height_span: optional total model height.
    plan: the body plan (body_plan()): the rules that assume a standing two-legged body (3, 4, 5, 7, 8, 11, 11b: by
      height above the floor, arms beside the hips, the head on top) run only for "biped" (or None, as before
      plans were known). On a quadruped they tore its lower ribcage off the chest, its withers off its girdles and a
      grazing head onto a foot.
    front_bones, hind_bones: the front and hind limbs' bones, by where each limb leaves the body (for the flank
      barrier); without them the flank barrier falls back to names.
    Returns: (N, num_bones) cleaned and normalized weights."""
    W = np.array(weights, copy=True)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0:
        return W
    biped = plan in (None, "biped")

    col = {name: i for i, name in enumerate(bone_names)}

    def is_left(name):
        return bone_side(name) == "L"

    def is_right(name):
        return bone_side(name) == "R"

    def is_leg(name):
        lower = name.lower()
        return any(k in lower for k in ("leg", "thigh", "foot", "toe", "shin", "upleg", "calf", "ankle"))

    def is_distal_leg(name):
        lower = name.lower()
        return any(k in lower for k in ("foot", "toe", "toebase", "ankle"))

    def is_distal_arm(name):
        lower = name.lower()
        return any(k in lower for k in ("forearm", "hand", "finger", "wrist", "arm_2", "arm_3", "arm2", "arm3"))

    def is_arm(name):
        lower = name.lower()
        return any(k in lower for k in ("arm", "forearm", "hand", "finger", "wrist")) and "shoulder" not in lower and "clavicle" not in lower

    def is_shoulder(name):
        lower = name.lower()
        return any(k in lower for k in ("shoulder", "clavicle"))

    def is_torso(name):
        lower = name.lower()
        return any(k in lower for k in ("spine", "hips", "chest", "root", "body", "pelvis", "neck"))

    def is_head_or_neck(name):
        lower = name.lower()
        return any(k in lower for k in ("head", "neck"))

    def is_head(name):
        lower = name.lower()
        return any(k in lower for k in ("head", "jaw"))

    def is_neck(name):
        return "neck" in name.lower()

    def is_appendage(name):
        # by word, not substring: "ear" is in ForeArm, and a forearm is not an ear
        return has_token(name, "ear", "antenna", "fluke", "flipper", "wisp", "filament")

    X = coords[:, 0]
    Z = coords[:, 2]
    h = height_span or float(Z.max() - Z.min()) if len(Z) else 1.0
    z_min = float(Z.min()) if len(Z) else 0.0

    left_leg_cols = [col[b] for b in bone_names if is_left(b) and is_leg(b)]
    right_leg_cols = [col[b] for b in bone_names if is_right(b) and is_leg(b)]

    # Target hips / pelvis bone
    hips_cols = [col[b] for b in bone_names if is_torso(b) and any(k in b.lower() for k in ("hips", "pelvis"))]
    if not hips_cols:
        hips_cols = [col[b] for b in bone_names if is_torso(b) and "root" in b.lower()]
    if not hips_cols:
        hips_cols = [col[b] for b in bone_names if is_torso(b) and "spine" in b.lower()]
    hips_target = hips_cols[0] if hips_cols else None

    # Landmark Z coordinates
    z_hips = None
    if bone_heads:
        hips_cands = [bone_heads[b][2] for b in bone_names if any(k in b.lower() for k in ("hips", "pelvis")) and b in bone_heads]
        if hips_cands:
            z_hips = max(hips_cands)
        else:
            sp_cands = [bone_heads[b][2] for b in bone_names if "spine" in b.lower() and b in bone_heads]
            if sp_cands:
                z_hips = min(sp_cands)
    if z_hips is None:
        z_hips = z_min + 0.45 * h

    z_neck = None
    if bone_heads:
        neck_cands = [bone_heads[b][2] for b in bone_names if is_neck(b) and b in bone_heads]
        if neck_cands:
            z_neck = min(neck_cands)
        elif any(is_head_or_neck(b) for b in bone_names):
            hn_cands = [bone_heads[b][2] for b in bone_names if is_head_or_neck(b) and b in bone_heads]
            if hn_cands:
                z_neck = min(hn_cands)
    if z_neck is None:
        z_neck = z_min + 0.80 * h

    z_head = None
    if bone_heads:
        head_cands = [bone_heads[b][2] for b in bone_names if is_head(b) and b in bone_heads]
        if head_cands:
            z_head = min(head_cands)
    if z_head is None:
        z_head = z_neck + 0.04 * h

    head_cols = [col[b] for b in bone_names if is_head(b)]
    head_target = head_cols[0] if head_cols else None
    neck_cols = [col[b] for b in bone_names if is_neck(b)]
    neck_target = neck_cols[0] if neck_cols else head_target

    z_shoulder = None
    if bone_heads:
        sh_cands = [bone_heads[b][2] for b in bone_names if is_shoulder(b) and b in bone_heads]
        if sh_cands:
            z_shoulder = min(sh_cands)

    # 1. Crotch / Leg bilateral separation
    if left_leg_cols and right_leg_cols:
        # Contralateral elimination: Right leg bones never own vertices on left side (X > sym_plane),
        # Left leg bones never own vertices on right side (X < sym_plane).
        # Any opposing leg weights are cleanly transferred to Hips.
        left_side = X > sym_plane
        if np.any(left_side):
            if hips_target is not None:
                W[left_side, hips_target] += W[left_side][:, right_leg_cols].sum(axis=1)
            W[np.ix_(left_side, right_leg_cols)] = 0.0

        right_side = X < sym_plane
        if np.any(right_side):
            if hips_target is not None:
                W[right_side, hips_target] += W[right_side][:, left_leg_cols].sum(axis=1)
            W[np.ix_(right_side, left_leg_cols)] = 0.0

        # C1 Hermite smooth sagittal anchoring: as vertices approach the sagittal seam (|X - sym_plane| < crotch_threshold),
        # leg weights fade smoothly to 0 at the seam, transferring influence to Hips/pelvis to prevent seam tearing.
        transition_mask = np.abs(X - sym_plane) <= crotch_threshold
        if np.any(transition_mask) and hips_target is not None:
            trans_indices = np.where(transition_mask)[0]
            for idx in trans_indices:
                x_val = X[idx]
                t = abs(x_val - sym_plane) / max(1e-6, crotch_threshold)
                k_fade = t * t * (3.0 - 2.0 * t)  # 0 at seam, 1 at boundary
                cols = left_leg_cols if x_val >= sym_plane else right_leg_cols
                w_legs = float(W[idx, cols].sum())
                if w_legs > 1e-5:
                    W[idx, cols] *= k_fade
                    freed = w_legs - float(W[idx, cols].sum())
                    W[idx, hips_target] += freed

    # 2. Arm & Shoulder bilateral isolation
    left_arm_cols = [col[b] for b in bone_names if is_left(b) and is_arm(b)]
    right_arm_cols = [col[b] for b in bone_names if is_right(b) and is_arm(b)]
    arm_margin = crotch_threshold * 0.5
    if left_arm_cols and right_arm_cols:
        l_side = X > (sym_plane + arm_margin)
        if np.any(l_side):
            W[np.ix_(l_side, right_arm_cols)] = 0.0
        r_side = X < (sym_plane - arm_margin)
        if np.any(r_side):
            W[np.ix_(r_side, left_arm_cols)] = 0.0

    sh_margin = crotch_threshold * 0.5
    for b in bone_names:
        if is_shoulder(b):
            c_idx = col[b]
            if is_left(b):
                r_side = X < (sym_plane - sh_margin)
                if np.any(r_side):
                    W[r_side, c_idx] = 0.0
            elif is_right(b):
                l_side = X > (sym_plane + sh_margin)
                if np.any(l_side):
                    W[l_side, c_idx] = 0.0

    # 3. Arm & Shoulder vertical isolation (strictly prevent arm/shoulder bleed onto pelvis, hips, and legs)
    all_arm_cols = [col[b] for b in bone_names if is_shoulder(b) or is_arm(b)]
    if biped and all_arm_cols and (hips_target is not None or left_leg_cols or right_leg_cols):
        z_arm_cutoff = z_hips + 0.03 * h
        below_pelvis = Z < z_arm_cutoff
        if np.any(below_pelvis):
            bp_indices = np.where(below_pelvis)[0]
            arm_pts = np.array([bone_heads[b] for b in bone_names if (is_shoulder(b) or is_arm(b)) and b in bone_heads]) if bone_heads else None
            leg_pts = np.array([bone_heads[b] for b in bone_names if is_leg(b) and b in bone_heads]) if bone_heads else None

            for idx in bp_indices:
                x_val = X[idx]
                target = hips_target
                if x_val > (sym_plane + crotch_threshold) and left_leg_cols:
                    target = left_leg_cols[0]
                elif x_val < (sym_plane - crotch_threshold) and right_leg_cols:
                    target = right_leg_cols[0]

                # Determine whether vertex belongs to leg/thigh or hanging hand/arm
                is_on_leg = True
                if arm_pts is not None and leg_pts is not None and len(arm_pts) > 0 and len(leg_pts) > 0:
                    pt = coords[idx]
                    d_arm = float(np.min(np.linalg.norm(pt - arm_pts, axis=1)))
                    d_leg = float(np.min(np.linalg.norm(pt - leg_pts, axis=1)))
                    is_on_leg = d_leg <= d_arm
                else:
                    is_on_leg = abs(x_val - sym_plane) < (crotch_threshold * 2.5)

                if is_on_leg and target is not None:
                    w_arms = float(W[idx, all_arm_cols].sum())
                    if w_arms > 1e-5:
                        W[idx, all_arm_cols] = 0.0
                        W[idx, target] += w_arms
                elif not is_on_leg:
                    # Vertex is on hand/forearm: quench accidental leg weights and restore to arm
                    leg_all = left_leg_cols + right_leg_cols
                    w_legs = float(W[idx, leg_all].sum())
                    if w_legs > 1e-5:
                        W[idx, leg_all] = 0.0
                        best_arm = all_arm_cols[int(np.argmax(W[idx, all_arm_cols]))] if float(W[idx, all_arm_cols].sum()) > 0 else all_arm_cols[0]
                        W[idx, best_arm] += w_legs

    # 4. Shoulder Clavicle Containment: Shoulders cannot own neck or mid/lower spine. Both limits fade in over a
    # band instead of cutting at a line: the hard box (12% of the height either side of the middle, from just
    # under the neck up) reached the collarbones themselves on a broad-shouldered model, and a shrug tore the
    # armoured guard's neck and shoulders open along its edges (55 of its 55 bend tears came from 4b).
    def ramp(t):
        t = np.clip(t, 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    shoulder_cols = [col[b] for b in bone_names if is_shoulder(b)]
    if biped and shoulder_cols:
        w_sh_all = W[:, shoulder_cols].sum(axis=1)
        if z_shoulder is not None:
            z_sh_low = z_shoulder - 0.12 * h
            k = 1.0 - ramp((Z - (z_sh_low - 0.04 * h)) / (0.08 * h))      # all of it 4% below the line, none 4% above
            rows = np.where((k > 1e-3) & (w_sh_all > 1e-5))[0]
            if len(rows):
                spine_cand_cols = [col[b] for b in bone_names if is_torso(b) and any(k_ in b.lower() for k_ in ("spine2", "spine1", "chest", "spine"))]
                torso_target = spine_cand_cols[0] if spine_cand_cols else hips_target
                moved = W[rows][:, shoulder_cols].sum(axis=1) * k[rows]
                W[np.ix_(rows, shoulder_cols)] *= (1.0 - k[rows])[:, None]
                if torso_target is not None:
                    W[rows, torso_target] += moved

        if neck_target is not None or head_target is not None:
            dx = np.abs(X - sym_plane)
            kx = 1.0 - ramp((dx - 0.06 * h) / (0.06 * h))                    # all of it within 6%, none past 12%
            kz = ramp((Z - (z_neck - 0.06 * h)) / (0.06 * h))                 # none 6% under the neck, all at it
            k = kx * kz
            w_sh_all = W[:, shoulder_cols].sum(axis=1)
            rows = np.where((k > 1e-3) & (w_sh_all > 1e-5))[0]
            if len(rows):
                z_head_cut = (z_head - 0.01 * h) if z_head is not None else (z_neck + 0.04 * h)
                moved = w_sh_all[rows] * k[rows]
                W[np.ix_(rows, shoulder_cols)] *= (1.0 - k[rows])[:, None]
                for idx, m in zip(rows.tolist(), moved.tolist()):
                    target = head_target if (Z[idx] >= z_head_cut and head_target is not None) else neck_target
                    if target is not None:
                        W[idx, target] += m

    # 5. Upper Torso (Spine1, Spine2, Chest) to Leg Barrier
    upper_torso_cols = [col[b] for b in bone_names if is_torso(b) and any(k in b.lower() for k in ("spine1", "spine2", "spine_2", "spine_3", "chest"))]
    # Only on an upright torso: a horizontal one (a wyvern's two-legged body) starts its chest at the hips'
    # height, so "below the hips" took in the underside of its chest and handed patches of it to the hips.
    up_heads = [bone_heads[b][2] for b in bone_names if b in (bone_heads or {}) and col[b] in upper_torso_cols]
    upright = not up_heads or max(up_heads) - z_hips > 0.05 * h
    if biped and upright and upper_torso_cols and hips_target is not None:
        z_leg_zone = z_hips + 0.04 * h
        in_leg_zone = (Z < z_leg_zone) & (np.abs(X - sym_plane) > crotch_threshold)
        if np.any(in_leg_zone):
            lz_indices = np.where(in_leg_zone)[0]
            for idx in lz_indices:
                w_up = float(W[idx, upper_torso_cols].sum())
                if w_up > 1e-5:
                    W[idx, upper_torso_cols] = 0.0
                    W[idx, hips_target] += w_up

    # 6. Distal leg height isolation (feet / toes cannot own thighs or hips)
    distal_leg_cols = [col[b] for b in bone_names if is_distal_leg(b)]
    if distal_leg_cols and bone_heads:
        z_feet = [bone_heads[b][2] for b in bone_names if is_distal_leg(b) and b in bone_heads]
        if z_feet:
            z_ankle_thresh = max(z_feet) + (height_span * 0.08 if height_span else 0.08)
            too_high = Z > z_ankle_thresh
            if np.any(too_high):
                th_indices = np.where(too_high)[0]
                for idx in th_indices:
                    w_fl = float(W[idx, distal_leg_cols].sum())
                    if w_fl > 1e-5:
                        W[idx, distal_leg_cols] = 0.0
                        if hips_target is not None:
                            W[idx, hips_target] += w_fl

    # 7. Leg-to-Upper-Body isolation: Leg bones cannot own chest/ribs/shoulders
    all_leg_cols = [col[b] for b in bone_names if is_leg(b)]
    if biped and all_leg_cols and hips_target is not None:
        z_torso_top = z_hips + 0.15 * h
        above_pelvis = Z > z_torso_top
        if np.any(above_pelvis):
            ap_indices = np.where(above_pelvis)[0]
            for idx in ap_indices:
                w_legs = float(W[idx, all_leg_cols].sum())
                if w_legs > 1e-5:
                    W[idx, all_leg_cols] = 0.0
                    W[idx, hips_target] += w_legs

    # 8. Arm-to-Head/Neck isolation: Arm bones cannot own central Head and Neck vertices
    arm_cols = [col[b] for b in bone_names if is_arm(b)]
    if biped and arm_cols and (neck_target is not None or head_target is not None):
        neck_zone = (Z >= z_neck - 0.01 * h) & (np.abs(X - sym_plane) < 0.06 * h)
        if np.any(neck_zone):
            nz_indices = np.where(neck_zone)[0]
            z_head_cut = (z_head - 0.01 * h) if z_head is not None else (z_neck + 0.04 * h)
            for idx in nz_indices:
                w_arm = float(W[idx, arm_cols].sum())
                if w_arm > 1e-5:
                    W[idx, arm_cols] = 0.0
                    target = head_target if (Z[idx] >= z_head_cut and head_target is not None) else neck_target
                    if target is not None:
                        W[idx, target] += w_arm

    # 9. Armpit / Flank barrier
    if armpit_barrier:
        torso_cols = [col[b] for b in bone_names if is_torso(b)]
        distal_arm_cols = [col[b] for b in bone_names if is_distal_arm(b)]
        if torso_cols and distal_arm_cols:
            torso_weight = W[:, torso_cols].sum(axis=1)
            torso_dominant = torso_weight > 0.35
            if np.any(torso_dominant):
                W[np.ix_(torso_dominant, distal_arm_cols)] = 0.0

            distal_weight = W[:, distal_arm_cols].sum(axis=1)
            distal_dominant = distal_weight > 0.40
            if np.any(distal_dominant):
                W[np.ix_(distal_dominant, torso_cols)] = 0.0

    # 10. Appendage Isolation (ears, antennae, flukes, flippers, wisps)
    appendage_cols = [col[b] for b in bone_names if is_appendage(b)]
    if appendage_cols and bone_heads:
        for b_idx in appendage_cols:
            b_name = bone_names[b_idx]
            if b_name in bone_heads:
                b_pos = np.array(bone_heads[b_name])
                dists = np.linalg.norm(coords - b_pos, axis=1)
                too_far = dists > 0.28 * h
                if np.any(too_far):
                    tf_indices = np.where(too_far)[0]
                    for idx in tf_indices:
                        w_app = float(W[idx, b_idx])
                        if w_app > 1e-5:
                            W[idx, b_idx] = 0.0
                            if hips_target is not None:
                                W[idx, hips_target] += w_app

    # 11. Head / Neck boundary refinement: Vertices above z_head belong predominantly to Head
    if biped and head_target is not None and neck_cols and z_head is not None:
        above_neck = (Z >= (z_head + 0.005 * h)) & (np.abs(X - sym_plane) < 0.20 * h)
        if np.any(above_neck):
            an_indices = np.where(above_neck)[0]
            for idx in an_indices:
                w_neck = float(W[idx, neck_cols].sum())
                if w_neck > 0.05:
                    transfer = w_neck * 0.40
                    W[idx, neck_cols] -= transfer
                    W[idx, head_target] += transfer

    # 11b. Snout / Head vs. Paws / Hooves vertical & distance isolation:
    # Paw/hoof bones cannot own head/jaw/snout vertices, and head/jaw bones cannot own paws.
    if biped and head_target is not None and distal_leg_cols and bone_heads:
        z_head_min = (z_head - 0.05 * h) if z_head is not None else (z_neck + 0.02 * h)
        on_head = Z >= z_head_min
        if np.any(on_head):
            oh_indices = np.where(on_head)[0]
            for idx in oh_indices:
                w_paws = float(W[idx, distal_leg_cols].sum())
                if w_paws > 1e-4:
                    W[idx, distal_leg_cols] = 0.0
                    W[idx, head_target] += w_paws

        z_foot_max = z_min + 0.15 * h
        head_neck_all = head_cols + neck_cols
        if head_neck_all:
            on_feet = Z <= z_foot_max
            if np.any(on_feet):
                of_indices = np.where(on_feet)[0]
                for idx in of_indices:
                    w_hn = float(W[idx, head_neck_all].sum())
                    if w_hn > 1e-4:
                        W[idx, head_neck_all] = 0.0
                        best_foot = distal_leg_cols[0]
                        W[idx, best_foot] += w_hn

    # 12. Longitudinal Flank barrier for quadrupeds/creatures (never a biped: its "front" arms and "hind" feet are
    # not a front and a back end of the body)
    if flank_barrier and bone_heads and plan in (None, "quadruped", "multi"):
        W = apply_longitudinal_flank_barrier(W, coords, bone_names, bone_heads=bone_heads,
                                             front_bones=front_bones, hind_bones=hind_bones,
                                             min_sep=0.05 * max(h, 1e-6), bone_tails=bone_tails)

    # 13. Tail-to-Hindquarters / Buttocks isolation
    if tail_barrier:
        W = apply_tail_isolation_barrier(W, coords, bone_names, bone_heads=bone_heads)

    # 14. Radial limb sector isolation for multi-legged creatures (hexapods/octopods)
    if radial_barrier and bone_heads and plan in (None, "multi"):
        W = apply_radial_limb_sector_isolation(W, coords, bone_names, bone_heads=bone_heads)

    # Renormalize rows
    row_sums = W.sum(axis=1, keepdims=True)
    valid = (row_sums > 1e-6).ravel()
    W[valid] /= row_sums[valid]
    return W


def geodesic_barrier_pass(mesh, arm, chains, spec, size, log):
    """Enforces geodesic and air-gap barriers on mesh vertex groups:
    Prevents opposite-limb cross-bleed across the crotch and underarm gaps."""
    if not spec.get("barrier", True):
        return
    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    n = len(verts)
    if n == 0:
        return

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return

    W = read_weights(mesh, bone_names)
    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    crotch_thresh = float(spec.get("crotch_barrier", max(size) * 0.03))
    if "crotch_barrier" not in spec:
        # never wider than the legs are apart: a seam fade reaching past a leg's own axis hands the leg (feet and
        # boots too, at every height) to the hips. Legs close together (a robe's) got swallowed by 3% of the size.
        roots = [abs(c["points"][0][0] - float(spec.get("sym_plane", 0.0))) for c in (chains or [])
                 if c.get("role") == "leg" and c.get("points")]
        # and the leg joints themselves (hip, knee, ankle), by name: a placed biped's legs are chains named leg.L
        # with no "leg" role, and its knees can stand closer together than its hips
        legs = ("leg", "thigh", "shin", "calf", "upleg", "knee", "foot", "ankle")
        roots += [abs(b.head_local[0] - float(spec.get("sym_plane", 0.0))) for b in arm.data.bones
                  if b.use_deform and bone_side(b.name) in ("L", "R") and any(k in b.name.lower() for k in legs)]
        roots = [r for r in roots if r > 1e-6]
        if roots:
            crotch_thresh = min(crotch_thresh, 0.5 * min(roots))
            log["crotch_barrier_auto"] = round(crotch_thresh, 4)
    sym_plane = float(spec.get("sym_plane", 0.0))
    bone_heads = {b.name: list(b.head_local) for b in arm.data.bones}
    h_span = float(size[2]) if hasattr(size, '__getitem__') else float(size.z)
    front, hind = limb_groups(chains)

    cleaned_W = apply_geodesic_skin_barrier(
        W, P, bone_names,
        bone_heads=bone_heads,
        sym_plane=sym_plane,
        crotch_threshold=crotch_thresh,
        armpit_barrier=spec.get("armpit_barrier", True),
        height_span=h_span,
        flank_barrier=spec.get("flank_barrier", True),
        tail_barrier=spec.get("tail_barrier", True),
        radial_barrier=spec.get("radial_barrier", True),
        plan=spec.get("_plan") or body_plan(chains),
        front_bones=front or None, hind_bones=hind or None,
        bone_tails={b.name: list(b.tail_local) for b in arm.data.bones},
    )
    if os.environ.get("AUTORIG_DEBUG_BARRIER"):          # the barrier's input and output, to replay it outside Blender
        np.savez(os.environ["AUTORIG_DEBUG_BARRIER"], W=W, P=P, names=np.array(bone_names), out=cleaned_W,
                 heads=np.array([bone_heads[b] for b in bone_names]), crotch=crotch_thresh, sym=sym_plane, h=h_span,
                 plan=str(spec.get("_plan") or body_plan(chains)))
    log["geodesic_barrier_fixed_verts"] = write_weights(mesh, bone_names, W, cleaned_W, locked=spec.get("_locked"))


def find_central_pelvis_bone(arm, chains=None):
    """Finds the central pelvis/hips/root spine bone on an armature:
    1. Common ancestor of left and right leg roots.
    2. Bone named 'hips', 'pelvis', 'spine', or 'body'.
    3. Armature root bone.
    """
    def is_left_leg(b):
        nm = b.name.lower()
        return ("left" in nm or nm.endswith(".l") or "_l" in nm) and any(k in nm for k in ("leg", "thigh", "upleg"))

    def is_right_leg(b):
        nm = b.name.lower()
        return ("right" in nm or nm.endswith(".r") or "_r" in nm) and any(k in nm for k in ("leg", "thigh", "upleg"))

    left_legs = [b for b in arm.data.bones if is_left_leg(b)]
    right_legs = [b for b in arm.data.bones if is_right_leg(b)]
    if left_legs and right_legs:
        l_top = left_legs[0]
        while l_top.parent and is_left_leg(l_top.parent):
            l_top = l_top.parent
        r_top = right_legs[0]
        while r_top.parent and is_right_leg(r_top.parent):
            r_top = r_top.parent

        ancestors_l = []
        curr = l_top.parent
        while curr:
            ancestors_l.append(curr.name)
            curr = curr.parent
        curr = r_top.parent
        while curr:
            if curr.name in ancestors_l:
                return curr.name
            curr = curr.parent

    for pat in ("hips", "pelvis", "spine", "body"):
        for b in arm.data.bones:
            if pat in b.name.lower():
                return b.name

    if chains:
        for c in chains:
            if c.get("role") in ("spine", "root") and c.get("bones"):
                return c["bones"][0]
    return arm.data.bones[0].name if arm.data.bones else "Hips"


def skirt_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """A long robe, gown or skirt sculpted as one mesh with the legs (spec "skirt": true, or a dict of the settings
    below). Bone heat gives each half of the skirt wholly to the leg on that side, so a stride rips it open down the
    front and back seams. Below the hips, cloth that lies away from the legs is shared instead:

      - within `radius` of a leg's axis (hip-knee-ankle) nothing changes: the leg itself, the boots;
      - beyond it the leg's share falls smoothly over `fade` to `floor` (the far hem and a trailing train still
        follow the legs a little, so a kick does not push the shin through a rigid cone), the rest goes to the hips;
      - across the middle (`seam` either side of the sagittal plane) the share is split between the two legs, so
        the front and back seams are one smooth sheet, not a cut;
      - below the knee the share moves from the thigh to the shin over `knee_blend` of the leg's length.

    Distances are fractions of the model's height. Locked vertices (deliberate cuts) are left alone."""
    if spec is None: spec = {}
    if log is None: log = {}
    cfg = spec.get("skirt")
    if not cfg: return 0
    cfg = cfg if isinstance(cfg, dict) else {}
    legs = {}
    for c in chains or []:
        if c.get("role") != "leg" or len(c.get("bones", [])) < 2: continue
        side = "L" if c["bones"][0].lower().startswith("left") or c["bones"][0].endswith((".L", "_L")) else "R"
        legs[side] = c
    hips = find_central_pelvis_bone(arm, chains)
    if len(legs) != 2 or not hips: return 0

    verts = mesh.data.vertices
    n = len(verts)
    P = np.empty(n * 3, dtype=np.float64); verts.foreach_get("co", P); P = P.reshape(n, 3)
    height = max(1e-6, float(P[:, 2].max() - P[:, 2].min()))
    radius = float(cfg.get("radius", 0.045)) * height
    fade = float(cfg.get("fade", 0.12)) * height
    floor = float(cfg.get("floor", 0.3))
    seam = float(cfg.get("seam", 0.04)) * height
    sym = float(spec.get("sym_plane", 0.0))

    def axis(c):
        pts = [np.array(p[:3], dtype=np.float64) for p in c["points"][:3]]     # hip, knee, ankle
        return pts

    def dist_to_polyline(Q, pts):
        best = np.full(len(Q), np.inf); tpar = np.zeros(len(Q))
        total = sum(np.linalg.norm(b - a) for a, b in zip(pts[:-1], pts[1:])); run = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            ab = b - a; L2 = max(1e-12, float(ab @ ab))
            t = np.clip(((Q - a) @ ab) / L2, 0.0, 1.0)
            d = np.linalg.norm(Q - (a + t[:, None] * ab), axis=1)
            better = d < best
            best[better] = d[better]
            tpar[better] = (run + t[better] * np.sqrt(L2)) / max(1e-9, total)
            run += np.sqrt(L2)
        return best, tpar

    ax = {s: axis(c) for s, c in legs.items()}
    hip_z = max(ax["L"][0][2], ax["R"][0][2])
    knee_t = {s: np.linalg.norm(ax[s][1] - ax[s][0]) / max(1e-9, np.linalg.norm(ax[s][1] - ax[s][0]) + np.linalg.norm(ax[s][2] - ax[s][1])) for s in ax}
    knee_blend = float(cfg.get("knee_blend", 0.15))

    cand = np.where(P[:, 2] < hip_z)[0]
    locked = spec.get("_locked") or set()
    if locked:
        cand = cand[~np.isin(cand, np.fromiter(locked, dtype=np.int64, count=len(locked)))]
    if len(cand) == 0: return 0
    Q = P[cand]
    dL, tL = dist_to_polyline(Q, ax["L"]); dR, tR = dist_to_polyline(Q, ax["R"])
    # the feet are leg too: a boot reaches forward past the ankle, and shared with the hips it tears when the foot
    # points (ankle -> ball -> toe, when the chain has them)
    feet = [[np.array(p[:3], dtype=np.float64) for p in c["points"][2:5]] for c in legs.values() if len(c["points"]) >= 4]
    d_foot = np.min([dist_to_polyline(Q, f)[0] for f in feet], axis=0) if feet else np.full(len(Q), np.inf)
    near_leg = (np.minimum(dL, dR) <= radius) | (d_foot <= radius)
    Q, cand, dL, dR, tL, tR = Q[~near_leg], cand[~near_leg], dL[~near_leg], dR[~near_leg], tL[~near_leg], tR[~near_leg]
    if len(cand) == 0: return 0

    smooth = lambda u: (lambda c: c * c * (3.0 - 2.0 * c))(np.clip(u, 0.0, 1.0))
    d_near = np.minimum(dL, dR)
    share = floor + (1.0 - floor) * (1.0 - smooth((d_near - radius) / max(1e-9, fade)))
    left = smooth((Q[:, 0] - sym + seam) / max(1e-9, 2.0 * seam))          # creature's left is +X

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    col = {nm: i for i, nm in enumerate(bone_names)}
    need = [hips] + [b for c in legs.values() for b in c["bones"][:2]]
    if any(b not in col for b in need): return 0
    W_all = read_weights(mesh, bone_names)
    W_old = W_all[cand].copy()
    W_new = np.zeros_like(W_old)
    for s, frac, t in (("L", left, tL), ("R", 1.0 - left, tR)):
        up, low = legs[s]["bones"][0], legs[s]["bones"][1]
        to_low = smooth((t - knee_t[s] + knee_blend * 0.5) / max(1e-9, knee_blend))
        w = share * frac
        W_new[:, col[up]] += w * (1.0 - to_low)
        W_new[:, col[low]] += w * to_low
    W_new[:, col[hips]] += 1.0 - share

    W_full_new = W_all.copy(); W_full_new[cand] = W_new
    written = write_weights(mesh, bone_names, W_all, W_full_new, locked=locked)
    log["skirt_verts"] = int(written)
    return written


LIMB_GROUPS = (("LeftArm", ("LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand"), ("Spine2",)),
               ("RightArm", ("RightShoulder", "RightArm", "RightForeArm", "RightHand"), ("Spine2",)),
               ("LeftLeg", ("LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"), ("Hips",)),
               ("RightLeg", ("RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"), ("Hips",)),
               ("Body", ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"), ()))


def limb_piece_weights(W, bone_names, pieces):
    """Each piece (index array) keeps only the bones of the limb that carries most of it, plus where that limb joins
    the body; what the others held goes to the piece's own bones in proportion (or, where it held none, to the
    forearm, the shin or the spine). Humanoid names (Mixamo's; a hand's fingers count as the hand). Returns the new W and per-piece limbs."""
    W = W.copy()
    def group_of(b):
        for g, own, _ in LIMB_GROUPS:
            if any(b == o or (o.endswith("Hand") and b.startswith(o)) for o in own): return g
        return None
    groups = [group_of(b) for b in bone_names]
    limbs = []
    for idx in pieces:
        sub = W[idx]
        tot = {g: float(sub[:, [k for k, gg in enumerate(groups) if gg == g]].sum()) for g, _, _ in LIMB_GROUPS}
        g = max(tot, key=tot.get)
        attach = next(a for gg, _, a in LIMB_GROUPS if gg == g)
        keep = np.array([groups[k] == g or bone_names[k] in attach for k in range(len(bone_names))])
        kept = sub * keep
        s = kept.sum(1, keepdims=True)
        main = {"LeftArm": "LeftForeArm", "RightArm": "RightForeArm", "LeftLeg": "LeftLeg", "RightLeg": "RightLeg"}.get(g, "Spine")
        main_k = next((k for k, b in enumerate(bone_names) if b == main), None)   # a sleeve hangs off the forearm
        empty = (s[:, 0] < 1e-6)
        kept[~empty] = kept[~empty] / s[~empty]
        if main_k is not None and empty.any():
            kept[empty] = 0; kept[empty, main_k] = 1.0
        W[idx] = kept
        limbs.append(g)
    return W, limbs


def limb_pieces_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """`limb_pieces: true` (humanoids): every loose piece but the largest (a sleeve, a boot, hair, a hood made as parts
    of their own) belongs to one limb: the one carrying most of it, plus where that limb joins the body. Bone heat lets
    a bell sleeve's hem hanging by the hips take the hips and a thigh, and a boot standing by the other boot take
    both feet; then they tear as the limbs move apart. Locked, so the healer leaves it."""
    if spec is None: spec = {}
    if log is None: log = {}
    if not spec.get("limb_pieces"): return 0
    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    W = read_weights(mesh, bone_names); W0 = W.copy()
    isl = mesh_islands(mesh)
    if len(isl) < 2: return 0
    body = max(range(len(isl)), key=lambda k: len(isl[k]))
    locked = spec.setdefault("_locked", set())
    pieces = [np.asarray([i for i in p if i not in locked], dtype=np.int64) for k, p in enumerate(isl) if k != body]
    pieces = [p for p in pieces if len(p)]
    W, limbs = limb_piece_weights(W, bone_names, pieces)
    write_weights(mesh, bone_names, W0, W)
    for p in pieces: locked.update(int(i) for i in p)
    log["limb_pieces"] = {g: limbs.count(g) for g in set(limbs)}
    return sum(len(p) for p in pieces)


def follow_weights(P, W, targets, body, k=8):
    """Each target vertex takes the mean weights of the k nearest body vertices: a piece laid over the body (a panel
    down a robe's front, a hood over the back of a head) then moves as the surface under it. The mean, not the single
    nearest: one vertex can be the robe's inside or the other leg's hem, and the panel tore 15x. P (n, 3), W (n,
    bones); targets and body are index arrays. Returns the new W."""
    W = W.copy()
    B = P[body]
    k = max(1, min(k, len(body)))
    for s in range(0, len(targets), 256):
        t = targets[s:s + 256]
        d2 = ((P[t][:, None, :] - B[None, :, :]) ** 2).sum(-1)
        near = np.argpartition(d2, k - 1, axis=1)[:, :k] if k < len(body) else np.tile(np.arange(len(body)), (len(t), 1))
        W[t] = W[body[near]].mean(1)
    return W


def follow_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """`follow`: [[[x0,y0,z0], [x1,y1,z1]], ...] boxes (0..1 of the model's bounds). Loose pieces whose middle is in a
    box take the weights of the body (the largest piece) under them, after every other pass, so a piece laid over the
    body never parts from it (a rigid piece on one bone parted 12 cm from the neck-weighted surface
    it covered)."""
    if spec is None: spec = {}
    if log is None: log = {}
    boxes = spec.get("follow") or []
    if not boxes: return 0
    verts = mesh.data.vertices; n = len(verts)
    P = np.empty(n * 3); verts.foreach_get("co", P); P = P.reshape(n, 3)
    lo, hi = P.min(0), P.max(0); span = np.maximum(hi - lo, 1e-9)
    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    W = read_weights(mesh, bone_names); W0 = W.copy()
    isl = mesh_islands(mesh)
    if len(isl) < 2: return 0
    body_k = max(range(len(isl)), key=lambda k: len(isl[k]))
    in_body = np.zeros(n, bool); in_body[isl[body_k]] = True
    N = (P - lo) / span
    done = 0
    for a0, a1 in boxes:
        # whole pieces whose middle is in the box (half a piece following would tear it at the box's edge)
        feet = spec.get("_feet") or set()   # a boot standing in the box stays on its leg
        inside = lambda c: bool(np.all((c >= np.array(a0)) & (c < np.array(a1))))
        targets = np.array([i for k, piece in enumerate(isl) if k != body_k and inside(N[piece].mean(0))
                            for i in piece if int(i) not in feet], dtype=np.int64)
        if not len(targets): continue
        pad = 0.05 * float(span.max())
        tlo, thi = P[targets].min(0) - pad, P[targets].max(0) + pad
        body = np.where(in_body & np.all((P >= tlo) & (P <= thi), axis=1))[0]
        if not len(body): continue
        W = follow_weights(P, W, targets, body)
        # a small piece (a cross, a buckle) keeps its shape: one set of weights for all of it, the mean of its own,
        # so it rides the surface under it whole instead of being pulled apart by it
        tset = set(int(i) for i in targets)
        for piece in isl:
            mine = [i for i in piece if i in tset]
            if mine and float((P[mine].max(0) - P[mine].min(0)).max()) < 0.1 * float(span.max()):
                W[mine] = W[mine].mean(0)
        spec.setdefault("_locked", set()).update(int(i) for i in targets)
        done += len(targets)
    write_weights(mesh, bone_names, W0, W)
    log["followed"] = done
    return done


def _loose_pieces(mesh, P):
    """Every loose piece but the largest (the body), as vertex index arrays."""
    try:
        isl = mesh_islands(mesh)
    except Exception:
        return []
    if len(isl) < 2:
        return []
    body = max(range(len(isl)), key=lambda k: len(isl[k]))
    return [np.asarray(p, dtype=np.int64) for k, p in enumerate(isl) if k != body]


def body_parts_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """Body-part rules for a dressed humanoid, each off unless the spec sets it (fractions are of the height):

      arm_reach     arm bones (shoulder, arm, forearm, hand, fingers) only own vertices within this distance of the
                    arm's own bones; past it, their share goes to the nearest spine, neck or head bone. Long hair over
                    the shoulders and a robe's chest stop lifting with a raised arm.
      feet_on_legs  below the knee, vertices within this distance of a shin, foot or toe bone belong to those alone
                    (shin above the ankle, foot below, blended over 6% of the height): a boot holds together. A loose
                    piece below the knee near a leg (a boot cut free of the robe) belongs to that leg whole, and what
                    this rule sets is locked, so rigid islands and the healer leave it.
      pin           [[bone, [x0,y0,z0], [x1,y1,z1]], ...]: vertices in the box (0..1 of the model's bounds) ride the
                    bone whole. For pieces welded into the rest (a rosary, a belt buckle): rigid_to needs loose pieces.

    Locked vertices are left alone."""
    if spec is None: spec = {}
    if log is None: log = {}
    reach = spec.get("arm_reach"); feet = spec.get("feet_on_legs"); pins = spec.get("pin") or []
    if not (reach or feet or pins): return 0
    verts = mesh.data.vertices; n = len(verts)
    P = np.empty(n * 3); verts.foreach_get("co", P); P = P.reshape(n, 3)
    lo, hi = P.min(0), P.max(0); span = np.maximum(hi - lo, 1e-9); height = float(span[2])
    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    col = {b: k for k, b in enumerate(bone_names)}
    W = read_weights(mesh, bone_names); W0 = W.copy()
    locked = spec.get("_locked") or set()
    free = np.ones(n, bool)
    if locked: free[np.fromiter(locked, dtype=np.int64, count=len(locked))] = False
    AW = np.array(arm.matrix_world)
    head = {b.name: (AW[:3, :3] @ np.array(b.head_local) + AW[:3, 3]) for b in arm.data.bones}
    tail = {b.name: (AW[:3, :3] @ np.array(b.tail_local) + AW[:3, 3]) for b in arm.data.bones}

    def seg(Q, b):
        a0, a1 = head[b], tail[b]; ab = a1 - a0
        t = np.clip(((Q - a0) @ ab) / max(1e-12, ab @ ab), 0, 1)
        return np.linalg.norm(Q - (a0 + t[:, None] * ab), axis=1)

    done = 0
    if reach:
        r = float(reach if not isinstance(reach, bool) else 0.12) * height
        ARM = ("shoulder", "arm", "hand", "clavicle", "finger", "thumb", "index", "middle", "ring", "pinky")
        arm_bones = [b for b in bone_names if any(w in b.lower() for w in ARM) and "leg" not in b.lower()]
        body = [b for b in bone_names if any(w in b.lower() for w in ("spine", "neck", "head", "chest", "hips"))]
        # reach is measured from the arm itself (upper arm, forearm, hand): hair lying over the collarbone is near
        # the shoulder bone but not near the arm, and must still come off it
        limb = [b for b in arm_bones if not any(w in b.lower() for w in ("shoulder", "clavicle"))] or arm_bones
        if arm_bones and body:
            d_arm = np.min([seg(P, b) for b in limb], axis=0)
            ai = [col[b] for b in arm_bones]
            far = free & (d_arm > r) & (W[:, ai].sum(1) > 1e-4)
            # the torso column: nearer the middle than the shoulder joints and below them is chest and back (and
            # hair hanging over them), never arm, however near the shoulder bone it lies
            joints = [head[b] for b in limb if b.lower().endswith("arm") and "fore" not in b.lower()]
            if joints:
                jx = min(abs(j[0]) for j in joints); jz = min(j[2] for j in joints)
                far |= free & (np.abs(P[:, 0]) < 0.9 * jx) & (P[:, 2] < jz - 0.02 * height) & (W[:, ai].sum(1) > 1e-4)
            if far.any():
                d_body = np.stack([seg(P[far], b) for b in body], axis=1)
                near_b = np.array([col[body[k]] for k in d_body.argmin(1)])
                moved = W[np.ix_(far, ai)].sum(1)
                W[np.ix_(far, ai)] = 0
                W[np.where(far)[0], near_b] += moved
                log["arm_reach_verts"] = int(far.sum()); done += int(far.sum())
    if feet:
        r = float(feet if not isinstance(feet, bool) else 0.035) * height
        for side in ("Left", "Right"):
            shin, foot, toe = side + "Leg", side + "Foot", side + "ToeBase"
            if not all(b in col for b in (shin, foot, toe)): continue
            ds, df, dt = seg(P, shin), seg(P, foot), seg(P, toe)
            knee_z = head[shin][2]
            near = free & (P[:, 2] < knee_z) & (np.minimum(np.minimum(ds, df), dt) < r)
            # a boot modelled (or cut) as a piece of its own is the leg's whole, however far its shaft stands off
            # the bone: a loose piece below the knee whose middle is nearer this leg than any other bone's reach
            other = ("Right" if side == "Left" else "Left")
            def leg_dist(c, sd):
                return min(seg(c[None], sd + b)[0] for b in ("Leg", "Foot", "ToeBase") if sd + b in col)
            for piece in _loose_pieces(mesh, P):
                c = P[piece].mean(0)
                d = leg_dist(c, side)
                # the nearer leg's, and only when near it: two boots stand closer together than a leg's reach
                if c[2] < knee_z and d < 2.5 * r and d <= leg_dist(c, other):
                    near[piece] = free[piece]
            idx = np.where(near)[0]
            up = np.clip((P[idx, 2] - head[foot][2]) / (0.06 * height) + 0.5, 0, 1)
            to_toe = np.clip((df[idx] - dt[idx]) / (0.015 * height) + 0.5, 0, 1) * (1 - up)
            W[idx] = 0
            W[idx, col[shin]] = up; W[idx, col[foot]] = (1 - up) * (1 - to_toe); W[idx, col[toe]] = (1 - up) * to_toe
            # the passes after this one (rigid islands, the healer, follow) leave a boot as it is set here
            spec.setdefault("_locked", set()).update(int(i) for i in idx)
            spec.setdefault("_feet", set()).update(int(i) for i in idx)
            log["feet_on_legs_" + side] = int(len(idx)); done += int(len(idx))
    for b, a0, a1 in pins:
        if b not in col: continue
        N = (P - lo) / span
        box = free & np.all((N >= np.array(a0)) & (N < np.array(a1)), axis=1)
        W[box] = 0; W[box, col[b]] = 1.0
        log.setdefault("pinned", {})[b] = log.get("pinned", {}).get(b, 0) + int(box.sum()); done += int(box.sum())
    write_weights(mesh, bone_names, W0, W, locked=locked)
    return done


def centerline_armor_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """Universal anti-tear skinning for skirts, faulds, belts, groin armor, and centerline accessories:
    1. Detects disconnected mesh islands crossing the sagittal plane (X=sym_plane) in the pelvic/hip region
       and binds them 100% to the central hips/pelvis/spine bone.
    2. Enforces smooth sagittal seam anchoring for pelvic/thigh geometry so centerline vertices bind to
       Hips/pelvis rather than shearing across left and right leg bones.
    """
    if spec is None:
        spec = {}
    if log is None:
        log = {}
    if not spec.get("centerline_armor", True):
        return 0

    verts = mesh.data.vertices
    n = len(verts)
    if n == 0:
        return 0

    vg = mesh.vertex_groups
    vg_names = {g.index: g.name for g in vg}
    hips_name = find_central_pelvis_bone(arm, chains)
    if not hips_name:
        return 0
    hips_vg = vg.get(hips_name) or vg.new(name=hips_name)

    P = np.empty(n * 3, dtype=np.float32)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    min_x, max_x = float(P[:, 0].min()), float(P[:, 0].max())
    span_x = max(1e-6, max_x - min_x)
    min_z, max_z = float(P[:, 2].min()), float(P[:, 2].max())
    span_z = max(1e-6, max_z - min_z)
    height = span_z

    sym_plane = float(spec.get("sym_plane", 0.0))
    plan = spec.get("_plan") or body_plan(chains)

    LEG_WORDS = ("leg", "thigh", "upleg", "foot", "toe", "shin", "calf")

    def is_left_leg_name(nm):
        return bone_side(nm) == "L" and has_token(nm, *LEG_WORDS)

    def is_right_leg_name(nm):
        return bone_side(nm) == "R" and has_token(nm, *LEG_WORDS)

    # Estimate pelvic and lower garment height range from armature hips/legs/ankles
    hips_bone = arm.data.bones.get(hips_name)
    knee_bones = [b for b in arm.data.bones if any(k in b.name.lower() for k in ("knee", "leg")) and not any(k in b.name.lower() for k in ("upleg", "thigh"))]
    if knee_bones:
        z_knee = float(knee_bones[0].head_local[2])
    elif hips_bone:
        z_knee = float(hips_bone.head_local[2]) - 0.25 * height
    else:
        z_knee = min_z + 0.30 * height

    ankle_bones = [b for b in arm.data.bones if any(k in b.name.lower() for k in ("foot", "ankle", "toe"))]
    if ankle_bones:
        z_ankle = float(ankle_bones[0].head_local[2])
    else:
        z_ankle = min_z + 0.08 * height

    z_hips = float(hips_bone.head_local[2]) if hips_bone else min_z + 0.55 * height
    z_bot = max(min_z, z_ankle + 0.05 * height)
    # The crotch is between the thighs: the band stops halfway down to the knee. Down to the ankle it laid a few per
    # cent of the hips on each shin, and a bent thigh left that share behind at the knee (the biped sample tore
    # there). The knee is where the thigh's child starts; the name guess above finds the thigh itself on
    # leg.L_1/leg.L_2 rigs. Not on a humanoid (a re-rigged Mixamo skeleton): there the long band holds armour
    # faulds and skirts together (the armoured guard tore more without it).
    thighs = [b for b in arm.data.bones if is_left_leg_name(b.name) and b.parent is not None
              and not is_left_leg_name(b.parent.name) and b.children]
    if thighs and spec.get("kind") != "humanoid" and spec.get("skeleton") != "humanoid":
        z_real_knee = float(thighs[0].children[0].head_local[2])
        if z_real_knee < z_hips:
            z_bot = max(z_bot, z_hips - 0.5 * (z_hips - z_real_knee))
    z_mid = z_hips
    z_top = min(max_z, z_hips + 0.15 * height)
    z_pelvis_min = z_bot
    z_pelvis_max = z_top

    # --- Part 1: Disconnected Centerline Islands ---
    import rerig
    isl = rerig.islands_of(mesh)
    island_delta = float(spec.get("island_delta", 0.005 * span_x))
    bound_islands = 0

    max_island_len = max(len(i) for i in isl) if isl else 0
    locked = spec.setdefault("_locked", set()) if isinstance(spec, dict) else set()
    for idxs in isl:
        if len(idxs) == max_island_len or len(idxs) >= 0.35 * n or \
                len(idxs) > float(spec.get("rigid_island_max_share", 0.10)) * n:
            continue  # the body, a major component, or a whole garment: only small pieces (a buckle, a fauld)
        island_P = P[idxs]
        ix_min, ix_max = float(island_P[:, 0].min()), float(island_P[:, 0].max())
        iz_min, iz_max = float(island_P[:, 2].min()), float(island_P[:, 2].max())

        # Accessory must be localized to pelvic region, not span the body or reach chest/head
        if (iz_max - iz_min) > 0.35 * height:
            continue
        if iz_max > (z_hips + 0.25 * height):
            continue

        # Check if straddling sagittal plane in pelvic / waist region
        if ix_min < (sym_plane - island_delta) and ix_max > (sym_plane + island_delta):
            if iz_min <= z_pelvis_max and iz_max >= z_pelvis_min:
                # Bind 100% to Hips
                for i in idxs:
                    for g in list(verts[i].groups):
                        vg[g.group].remove([i])
                    hips_vg.add([i], 1.0, 'REPLACE')
                locked.update(int(i) for i in idxs)         # a deliberate rigid bind: the healer leaves it
                bound_islands += 1

    log["centerline_islands_bound"] = bound_islands

    # --- Part 2: Sagittal Centerline Pelvic / Crotch Anti-Tear Protection ---
    # A standing biped's crotch only. On a quadruped the same height band is its whole torso, and front-leg
    # weight under the chest went to the rear pelvis.
    if plan not in (None, "biped"):              # None: no chains to go by, the legacy behaviour
        log["centerline_verts_anchored"] = 0
        return bound_islands
    left_hip = [b for b in arm.data.bones if is_left_leg_name(b.name) and any(k in b.name.lower() for k in ("up", "thigh", "1"))]
    x_hip_spacing = float(abs(left_hip[0].head_local[0] - sym_plane)) if left_hip else 0.05 * height
    # the band is the hips' own spread: the model's width in a T-pose is its arm span, and 18% of that reached
    # halfway down both thighs
    x_span = max(x_hip_spacing * 1.8, 0.03 * height)

    X = P[:, 0]
    Z = P[:, 2]
    dx = np.abs(X - sym_plane)

    # C1 smooth Hermite profile in X: peak at centerline seam (X=sym_plane), smoothly tapering outward to x_span
    u = np.clip(dx / max(1e-6, x_span), 0.0, 1.0)
    fx = 1.0 - (u * u * (3.0 - 2.0 * u))

    # C1 smooth Hermite profile in Z: peak at hips, gentle descent toward ankles, gentle taper above hips
    gz = np.zeros_like(Z)
    lower = (Z >= z_bot) & (Z <= z_mid)
    if np.any(lower):
        v = (Z[lower] - z_bot) / max(1e-6, z_mid - z_bot)
        gz[lower] = v * v * (3.0 - 2.0 * v)
    upper = (Z > z_mid) & (Z <= z_top)
    if np.any(upper):
        w = (z_top - Z[upper]) / max(1e-6, z_top - z_mid)
        gz[upper] = w * w * (3.0 - 2.0 * w)

    K = fx * gz

    in_pelvis_zone = (dx <= x_span) & (Z >= z_bot) & (Z <= z_top)
    pelvis_indices = np.where(in_pelvis_zone)[0]

    adjusted_verts = 0
    for idx in pelvis_indices:
        if int(idx) in locked:
            continue
        v = verts[int(idx)]
        w_left = sum(g.weight for g in v.groups if is_left_leg_name(vg_names.get(g.group, "")))
        w_right = sum(g.weight for g in v.groups if is_right_leg_name(vg_names.get(g.group, "")))
        w_legs = w_left + w_right
        if w_legs > 0.01:
            conflict = 2.0 * min(w_left, w_right) / w_legs
            k = max(K[idx], conflict)
            if k > 0.01:
                trans = w_legs * k
                for g in list(v.groups):
                    nm = vg_names.get(g.group, "")
                    if is_left_leg_name(nm) or is_right_leg_name(nm):
                        new_w = float(g.weight * (1.0 - k))
                        if new_w > 1e-4:
                            vg[g.group].add([v.index], new_w, 'REPLACE')
                        else:
                            vg[g.group].remove([v.index])

                w_h = sum(g.weight for g in v.groups if vg_names.get(g.group) == hips_name)
                hips_vg.add([v.index], w_h + trans, 'REPLACE')
                adjusted_verts += 1

    log["centerline_verts_anchored"] = adjusted_verts
    return bound_islands + adjusted_verts


def sibling_appendage_pass(mesh, arm, chains=None, spec=None, size=None, log=None):
    """Enforces sibling appendage isolation (e.g. tentacles, spider legs, crab legs):
    Prevents adjacent parallel appendages from cross-contaminating each other's distal vertices."""
    if spec is None:
        spec = {}
    if log is None:
        log = {}
    if not spec.get("sibling_isolation", True):
        return 0

    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    n = len(verts)
    if n == 0:
        return 0

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return 0

    W = read_weights(mesh, bone_names)
    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    bone_heads = {b.name: list(b.head_local) for b in arm.data.bones}
    cleaned_W = compute_sibling_appendage_isolation(
        W, P, bone_names, bone_heads=bone_heads, chains=chains
    )
    corrected_count = write_weights(mesh, bone_names, W, cleaned_W, locked=spec.get("_locked"))
    log["sibling_appendage_fixed_verts"] = corrected_count
    return corrected_count


def closed_loop_healing_pass(mesh, arm, spec=None, size=None, log=None):
    """Closed-loop tear healing pass on mesh vertex groups:
    Identifies connected mesh edges across which weight gradients exceed max_gradient,
    and diffuses the gradient along topological edges to prevent tears during joint bends."""
    if spec is None:
        spec = {}
    if log is None:
        log = {}
    if not spec.get("auto_heal", True):
        return 0

    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    n = len(verts)
    if n == 0 or len(mesh.data.edges) == 0:
        return 0

    bone_names = [b.name for b in arm.data.bones if b.use_deform]
    if not bone_names:
        return 0

    W = read_weights(mesh, bone_names)
    ed = np.empty(len(mesh.data.edges) * 2, dtype=int)
    mesh.data.edges.foreach_get("vertices", ed)
    edges = ed.reshape(-1, 2)
    max_gradient = float(spec.get("heal_max_gradient", 0.20))
    passes = int(spec.get("heal_passes", 4))
    blend_factor = float(spec.get("heal_blend", 0.5))

    P = np.empty(n * 3, dtype=float)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    locked = spec.get("_locked") or set()
    healed_W, modified_count = compute_closed_loop_laplacian_healing(
        W, P, edges, max_gradient=max_gradient, passes=passes, blend_factor=blend_factor, locked=locked
    )
    # A weight gradient is a candidate, not proof of a better bend.
    before = None
    if modified_count and spec.get("heal_validate", True):
        from rig_quality import sample_skin
        before = sample_skin(mesh, arm)
    corrected_count = write_weights(mesh, bone_names, W, healed_W, locked=locked)
    if before is not None:
        from deformation import no_regression
        after = sample_skin(mesh, arm)
        accepted = no_regression(before, after)
        log["heal_validation"] = {"accepted": accepted, "poses": len(before), "before": before, "after": after}
        if not accepted:
            write_weights(mesh, bone_names, healed_W, W, locked=locked)
            corrected_count = 0
    log["closed_loop_healed_verts"] = corrected_count
    return corrected_count


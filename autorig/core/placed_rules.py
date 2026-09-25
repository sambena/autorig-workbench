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
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    max_dim = max(size)
    total_ripped = 0

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

        # Find vertices that have adjacent faces on both sides
        verts_to_rip = []
        for v in bm.verts:
            if not v.link_faces:
                continue
            co = np.array(v.co[:])
            dA, _ = _seg_dist(co[None, :], hA, tA)
            dB, _ = _seg_dist(co[None, :], hB, tB)
            if dA[0] > dist_limit or dB[0] > dist_limit:
                continue

            # Classify link faces by proximity to bone A vs bone B
            facesA, facesB = [], []
            for f in v.link_faces:
                fc = np.array(f.calc_center_median()[:])
                dfA, _ = _seg_dist(fc[None, :], hA, tA)
                dfB, _ = _seg_dist(fc[None, :], hB, tB)
                if dfA[0] <= dfB[0]:
                    facesA.append(f)
                else:
                    facesB.append(f)

            if facesA and facesB:
                verts_to_rip.append((v, facesB))

        # Separate side B faces onto a duplicate vertex
        if verts_to_rip:
            dlayer = bm.verts.layers.deform.verify()
            for v, b_faces in verts_to_rip:
                v_copy = bm.verts.new(v.co)
                # Copy existing vertex weights if any
                v_weights = v[dlayer]
                for g_idx, weight in v_weights.items():
                    v_copy[dlayer][g_idx] = weight

                # Remake side B faces using v_copy
                for f in b_faces:
                    vert_seq = [v_copy if u == v else u for u in f.verts]
                    try:
                        new_f = bm.faces.new(vert_seq)
                        new_f.material_index = f.material_index
                        new_f.smooth = f.smooth
                        bm.faces.remove(f)
                    except ValueError:
                        pass
                total_ripped += 1

            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

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
                                   max_twist_gradient=0.25, passes=12):
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

        R = max(0.06 * L, 0.55 * max(1e-3, L))
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

    for k, idxs in enumerate(isl):
        if k in body_islands:
            continue
        # Skip if already handled by explicit rule
        if any(i in assigned_set for i in idxs):
            continue

        # Island vertex coords
        coords = np.array([verts[i].co[:] for i in idxs])
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
        auto_count += 1

    log["auto_rigid_islands"] = auto_count
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
                rigid_count += 1

    do_auto = (rigid_val in (True, "auto") or rigid_armor or armor_rules is True or
               (isinstance(armor_rules, list) and any(isinstance(r, dict) and r.get("auto") for r in armor_rules)))
    if do_auto:
        auto_count = auto_isolate_disconnected_islands(mesh, arm, chains, spec, size, log, assigned_indices)
        rigid_count += auto_count

    log["rigid_islands_assigned"] = rigid_count


def apply_longitudinal_flank_barrier(weights, coords, bone_names, bone_heads=None, y_mid=None, blend_width=0.08):
    """Enforces longitudinal separation between front limbs and hindquarters on quadrupeds/creatures.
    Prevents distal front leg/shoulder bones from stealing weights from the rear flank/pelvis,
    and hind leg bones from stealing weights from the chest/ribcage."""
    W = np.array(weights, copy=True, dtype=float)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0 or not bone_heads:
        return W

    col = {name: i for i, name in enumerate(bone_names)}

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

    if abs(y_hind_mean - y_front_mean) < 0.05:
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

    if front_is_negative_y:
        in_rear = Y > (mid_y + bw)
        if np.any(in_rear):
            rear_indices = np.where(in_rear)[0]
            for idx in rear_indices:
                w_front = float(W[idx, front_cols].sum())
                if w_front > 1e-4:
                    W[idx, front_cols] = 0.0
                    W[idx, hips_target] += w_front

        in_front = Y < (mid_y - bw)
        if np.any(in_front):
            front_indices = np.where(in_front)[0]
            for idx in front_indices:
                w_hind = float(W[idx, hind_cols].sum())
                if w_hind > 1e-4:
                    W[idx, hind_cols] = 0.0
                    W[idx, chest_target] += w_hind
    else:
        in_rear = Y < (mid_y - bw)
        if np.any(in_rear):
            rear_indices = np.where(in_rear)[0]
            for idx in rear_indices:
                w_front = float(W[idx, front_cols].sum())
                if w_front > 1e-4:
                    W[idx, front_cols] = 0.0
                    W[idx, hips_target] += w_front

        in_front = Y > (mid_y + bw)
        if np.any(in_front):
            front_indices = np.where(in_front)[0]
            for idx in front_indices:
                w_hind = float(W[idx, hind_cols].sum())
                if w_hind > 1e-4:
                    W[idx, hind_cols] = 0.0
                    W[idx, chest_target] += w_hind

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
        lower = name.lower()
        return any(k in lower for k in ("leg_hind", "leg_back", "hind_leg", "thigh", "shin", "foot", "upleg", "leg")) and "tail" not in lower

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
        base_tail = [b for b in bone_names if "tail" in b.lower() and b in bone_heads]
        if base_tail:
            z_tail_base = bone_heads[base_tail[0]][2]
            Z = coords[:, 2]
            well_below_tail = Z < (z_tail_base - 0.08 * (float(Z.max() - Z.min()) if len(Z) else 1.0))
            if np.any(well_below_tail):
                below_indices = np.where(well_below_tail)[0]
                for idx in below_indices:
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

    leg_pat = re.compile(r"^leg_?(\d+)[._]([LR])$", re.IGNORECASE)
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
            if any(".l" in b.lower() or "left" in b.lower() for b in bones):
                side = ".L"
            elif any(".r" in b.lower() or "right" in b.lower() for b in bones):
                side = ".R"
            else:
                side = ""
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
                side = ".L" if (".l" in b.lower() or "left" in b.lower()) else (".R" if (".r" in b.lower() or "right" in b.lower()) else "")
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


def compute_closed_loop_laplacian_healing(weights, coords, edges, max_gradient=0.20, passes=3, blend_factor=0.5):
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

    # Build adjacency list: adj[u] = list of neighbor vertex indices
    adj = [[] for _ in range(N)]
    for u, v in zip(u_idx, v_idx):
        adj[u].append(v)
        adj[v].append(u)

    for p in range(passes):
        edge_diffs = np.max(np.abs(W[u_idx] - W[v_idx]), axis=1)
        violating = edge_diffs > max_gradient
        if not np.any(violating):
            break

        violating_u = u_idx[violating]
        violating_v = v_idx[violating]
        violating_verts = np.unique(np.concatenate([violating_u, violating_v]))

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


def apply_geodesic_skin_barrier(weights, coords, bone_names, bone_heads=None, sym_plane=0.0,
                                crotch_threshold=0.04, armpit_barrier=True, height_span=None,
                                flank_barrier=True, tail_barrier=True, radial_barrier=True):
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
    Returns: (N, num_bones) cleaned and normalized weights."""
    W = np.array(weights, copy=True)
    N, num_bones = W.shape
    if N == 0 or num_bones == 0:
        return W

    col = {name: i for i, name in enumerate(bone_names)}

    def is_left(name):
        return name.endswith(".L") or name.startswith("Left") or "_l" in name.lower() or ".l" in name.lower()

    def is_right(name):
        return name.endswith(".R") or name.startswith("Right") or "_r" in name.lower() or ".r" in name.lower()

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
        lower = name.lower()
        return any(k in lower for k in ("ear", "antenna", "fluke", "flipper", "wisp", "filament"))

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
    if all_arm_cols and (hips_target is not None or left_leg_cols or right_leg_cols):
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

    # 4. Shoulder Clavicle Containment: Shoulders cannot own neck or mid/lower spine
    shoulder_cols = [col[b] for b in bone_names if is_shoulder(b)]
    if shoulder_cols:
        if z_shoulder is not None:
            z_sh_low = z_shoulder - 0.12 * h
            too_low_sh = Z < z_sh_low
            if np.any(too_low_sh):
                tl_indices = np.where(too_low_sh)[0]
                spine_cand_cols = [col[b] for b in bone_names if is_torso(b) and any(k in b.lower() for k in ("spine2", "spine1", "chest", "spine"))]
                torso_target = spine_cand_cols[0] if spine_cand_cols else hips_target
                for idx in tl_indices:
                    w_sh = float(W[idx, shoulder_cols].sum())
                    if w_sh > 1e-5:
                        W[idx, shoulder_cols] = 0.0
                        if torso_target is not None:
                            W[idx, torso_target] += w_sh

        if neck_target is not None or head_target is not None:
            neck_zone = (Z >= z_neck - 0.03 * h) & (np.abs(X - sym_plane) < 0.12 * h)
            if np.any(neck_zone):
                nz_indices = np.where(neck_zone)[0]
                z_head_cut = (z_head - 0.01 * h) if z_head is not None else (z_neck + 0.04 * h)
                for idx in nz_indices:
                    w_sh = float(W[idx, shoulder_cols].sum())
                    if w_sh > 1e-5:
                        W[idx, shoulder_cols] = 0.0
                        target = head_target if (Z[idx] >= z_head_cut and head_target is not None) else neck_target
                        if target is not None:
                            W[idx, target] += w_sh

    # 5. Upper Torso (Spine1, Spine2, Chest) to Leg Barrier
    upper_torso_cols = [col[b] for b in bone_names if is_torso(b) and any(k in b.lower() for k in ("spine1", "spine2", "spine_2", "spine_3", "chest"))]
    if upper_torso_cols and hips_target is not None:
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
    if all_leg_cols and hips_target is not None:
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
    if arm_cols and (neck_target is not None or head_target is not None):
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
    if head_target is not None and neck_cols and z_head is not None:
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
    if head_target is not None and distal_leg_cols and bone_heads:
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

    # 12. Longitudinal Flank barrier for quadrupeds/creatures
    if flank_barrier and bone_heads:
        W = apply_longitudinal_flank_barrier(W, coords, bone_names, bone_heads=bone_heads)

    # 13. Tail-to-Hindquarters / Buttocks isolation
    if tail_barrier:
        W = apply_tail_isolation_barrier(W, coords, bone_names, bone_heads=bone_heads)

    # 14. Radial limb sector isolation for multi-legged creatures (hexapods/octopods)
    if radial_barrier and bone_heads:
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

    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((n, len(bone_names)), dtype=np.float32)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight

    P = np.empty(n * 3, dtype=np.float32)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    crotch_thresh = float(spec.get("crotch_barrier", max(size) * 0.03))
    sym_plane = float(spec.get("sym_plane", 0.0))
    bone_heads = {b.name: list(b.head_local) for b in arm.data.bones}
    h_span = float(size[2]) if hasattr(size, '__getitem__') else float(size.z)

    cleaned_W = apply_geodesic_skin_barrier(
        W, P, bone_names,
        bone_heads=bone_heads,
        sym_plane=sym_plane,
        crotch_threshold=crotch_thresh,
        armpit_barrier=spec.get("armpit_barrier", True),
        height_span=h_span,
        flank_barrier=spec.get("flank_barrier", True),
        tail_barrier=spec.get("tail_barrier", True),
        radial_barrier=spec.get("radial_barrier", True)
    )

    diff = np.abs(cleaned_W - W).sum(axis=1)
    corrected_count = int((diff > 1e-3).sum())

    if corrected_count > 0:
        group_objs = {col[name]: vg.get(name) for name in bone_names if vg.get(name)}
        for vi in np.where(diff > 1e-3)[0]:
            for bi, g_obj in group_objs.items():
                w = float(cleaned_W[vi, bi])
                if w > 1e-4:
                    g_obj.add([int(vi)], w, 'REPLACE')
                else:
                    g_obj.remove([int(vi)])

    log["geodesic_barrier_fixed_verts"] = corrected_count


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

    def is_left_leg_name(nm):
        l = nm.lower()
        return ("left" in l or l.endswith(".l") or "_l" in l) and any(k in l for k in ("leg", "thigh", "upleg", "foot", "toe", "shin", "calf"))

    def is_right_leg_name(nm):
        l = nm.lower()
        return ("right" in l or l.endswith(".r") or "_r" in l) and any(k in l for k in ("leg", "thigh", "upleg", "foot", "toe", "shin", "calf"))

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
    for idxs in isl:
        if len(idxs) == max_island_len or len(idxs) >= 0.35 * n:
            continue  # Main body or major component, not a loose accessory
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
                bound_islands += 1

    log["centerline_islands_bound"] = bound_islands

    # --- Part 2: Sagittal Centerline Pelvic / Crotch Anti-Tear Protection ---
    left_hip = [b for b in arm.data.bones if is_left_leg_name(b.name) and any(k in b.name.lower() for k in ("up", "thigh", "1"))]
    x_hip_spacing = float(abs(left_hip[0].head_local[0] - sym_plane)) if left_hip else 0.08 * span_x
    x_core = max(x_hip_spacing * 0.4, 0.03 * span_x)
    x_span = max(x_hip_spacing * 1.8, 0.18 * span_x)

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

    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((n, len(bone_names)), dtype=np.float32)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight

    P = np.empty(n * 3, dtype=np.float32)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    bone_heads = {b.name: list(b.head_local) for b in arm.data.bones}
    cleaned_W = compute_sibling_appendage_isolation(
        W, P, bone_names, bone_heads=bone_heads, chains=chains
    )

    diff = np.abs(cleaned_W - W).sum(axis=1)
    corrected_count = int((diff > 1e-3).sum())

    if corrected_count > 0:
        group_objs = {col[name]: vg.get(name) for name in bone_names if vg.get(name)}
        for vi in np.where(diff > 1e-3)[0]:
            for bi, g_obj in group_objs.items():
                w = float(cleaned_W[vi, bi])
                if w > 1e-4:
                    g_obj.add([int(vi)], w, 'REPLACE')
                else:
                    g_obj.remove([int(vi)])

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

    col = {nm: i for i, nm in enumerate(bone_names)}
    W = np.zeros((n, len(bone_names)), dtype=np.float32)
    gi = {g.index: col.get(g.name) for g in vg}
    for v in verts:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None:
                W[v.index, k] += g.weight

    edges = [(e.vertices[0], e.vertices[1]) for e in mesh.data.edges]
    max_gradient = float(spec.get("heal_max_gradient", 0.20))
    passes = int(spec.get("heal_passes", 4))
    blend_factor = float(spec.get("heal_blend", 0.5))

    P = np.empty(n * 3, dtype=np.float32)
    verts.foreach_get("co", P)
    P = P.reshape(n, 3)

    healed_W, modified_count = compute_closed_loop_laplacian_healing(
        W, P, edges, max_gradient=max_gradient, passes=passes, blend_factor=blend_factor
    )

    diff = np.abs(healed_W - W).sum(axis=1)
    corrected_count = int((diff > 1e-3).sum())

    if corrected_count > 0:
        group_objs = {col[name]: vg.get(name) for name in bone_names if vg.get(name)}
        for vi in np.where(diff > 1e-3)[0]:
            for bi, g_obj in group_objs.items():
                w = float(healed_W[vi, bi])
                if w > 1e-4:
                    g_obj.add([int(vi)], w, 'REPLACE')
                else:
                    g_obj.remove([int(vi)])

    log["closed_loop_healed_verts"] = corrected_count
    return corrected_count


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


def rigid_islands_pass(mesh, arm, chains, spec, size, log):
    """Enforces 100% rigid binding for armour pieces, pauldrons, and specified islands."""
    rigid_rules = spec.get("rigid_islands", [])
    if not rigid_rules:
        return
    import rerig
    isl = rerig.islands_of(mesh)
    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    lo, hi = rerig.bounds(mesh)
    span = np.maximum(hi - lo, 1e-9)

    rigid_count = 0
    for rule in rigid_rules:
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
            rigid_count += 1

    log["rigid_islands_assigned"] = rigid_count

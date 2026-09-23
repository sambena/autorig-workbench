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


def rigid_islands_pass(mesh, arm, chains, spec, size, log):
    """Enforces 100% rigid binding for armour pieces, pauldrons, and specified islands."""
    rigid_val = spec.get("rigid_islands")
    rigid_armor = spec.get("rigid_armor", False)
    if not rigid_val and not rigid_armor:
        return
    import rerig
    isl = rerig.islands_of(mesh)
    verts = mesh.data.vertices
    vg = mesh.vertex_groups
    lo, hi = rerig.bounds(mesh)
    span = np.maximum(hi - lo, 1e-9)

    rigid_count = 0
    assigned_indices = set()

    if isinstance(rigid_val, list):
        for rule in rigid_val:
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

    do_auto = (rigid_val in (True, "auto") or rigid_armor or
               (isinstance(rigid_val, list) and any(isinstance(r, dict) and r.get("auto") for r in rigid_val)))
    if do_auto:
        auto_count = auto_isolate_disconnected_islands(mesh, arm, chains, spec, size, log, assigned_indices)
        rigid_count += auto_count

    log["rigid_islands_assigned"] = rigid_count


def apply_geodesic_skin_barrier(weights, coords, bone_names, bone_heads=None, sym_plane=0.0,
                                crotch_threshold=0.04, armpit_barrier=True, height_span=None):
    """Enforces geodesic and air-gap barriers on skin weights:
    1. Crotch / Bilateral barrier: eliminates opposite-leg cross-bleed across the air gap between legs.
    2. Armpit / Flank barrier: prevents arm bones from pulling torso/rib/neck vertices across the underarm/collar gaps.
    3. Clavicle / Shoulder bilateral isolation: prevents shoulder bones from affecting the contralateral side.
    4. Distal leg height isolation: prevents foot/toe bones from bleeding onto thighs or pelvis.
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

    X = coords[:, 0]
    Z = coords[:, 2]

    left_leg_cols = [col[b] for b in bone_names if is_left(b) and is_leg(b)]
    right_leg_cols = [col[b] for b in bone_names if is_right(b) and is_leg(b)]

    # 1. Crotch / Leg bilateral separation
    if left_leg_cols and right_leg_cols:
        left_mask = X > (sym_plane + crotch_threshold)
        if np.any(left_mask):
            W[np.ix_(left_mask, right_leg_cols)] = 0.0

        right_mask = X < (sym_plane - crotch_threshold)
        if np.any(right_mask):
            W[np.ix_(right_mask, left_leg_cols)] = 0.0

        transition_mask = np.abs(X - sym_plane) <= crotch_threshold
        hips_cols = [col[b] for b in bone_names if is_torso(b) and any(k in b.lower() for k in ("hips", "pelvis", "root"))]
        hips_target = hips_cols[0] if hips_cols else None
        if np.any(transition_mask):
            trans_indices = np.where(transition_mask)[0]
            for idx in trans_indices:
                x_val = X[idx]
                if x_val > sym_plane:
                    fade = (x_val - sym_plane) / max(1e-6, crotch_threshold)
                    k_fade = fade * fade * (3.0 - 2.0 * fade)
                    old_w = float(W[idx, right_leg_cols].sum())
                    W[idx, right_leg_cols] *= (1.0 - k_fade)
                    freed = old_w - float(W[idx, right_leg_cols].sum())
                    if hips_target is not None and freed > 1e-5:
                        W[idx, hips_target] += freed
                elif x_val < sym_plane:
                    fade = (sym_plane - x_val) / max(1e-6, crotch_threshold)
                    k_fade = fade * fade * (3.0 - 2.0 * fade)
                    old_w = float(W[idx, left_leg_cols].sum())
                    W[idx, left_leg_cols] *= (1.0 - k_fade)
                    freed = old_w - float(W[idx, left_leg_cols].sum())
                    if hips_target is not None and freed > 1e-5:
                        W[idx, hips_target] += freed

    # 2. Clavicle / Shoulder bilateral isolation
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

    # 3. Distal leg height isolation (feet / toes cannot own thighs or hips)
    distal_leg_cols = [col[b] for b in bone_names if is_distal_leg(b)]
    if distal_leg_cols and bone_heads:
        z_feet = [bone_heads[b][2] for b in bone_names if is_distal_leg(b) and b in bone_heads]
        if z_feet:
            z_ankle_thresh = max(z_feet) + (height_span * 0.08 if height_span else 0.08)
            too_high = Z > z_ankle_thresh
            if np.any(too_high):
                W[np.ix_(too_high, distal_leg_cols)] = 0.0

    # 4. Arm-to-Head/Neck isolation: Arm bones cannot own central Head and Neck vertices
    arm_cols = [col[b] for b in bone_names if is_arm(b)]
    head_neck_cols = [col[b] for b in bone_names if is_head_or_neck(b)]
    if arm_cols and bone_heads:
        z_neck_heads = [bone_heads[b][2] for b in bone_names if is_head_or_neck(b) and b in bone_heads]
        if z_neck_heads:
            z_neck = min(z_neck_heads)
            h = height_span or float(coords[:, 2].max() - coords[:, 2].min())
            neck_zone = (Z >= z_neck - 0.03 * h) & (np.abs(X - sym_plane) < 0.15 * h)
            if np.any(neck_zone):
                W[np.ix_(neck_zone, arm_cols)] = 0.0

    # 5. Armpit / Flank barrier
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
        height_span=h_span
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

    for idxs in isl:
        if len(idxs) >= 0.6 * n:
            continue  # Main body, not a loose accessory
        island_P = P[idxs]
        ix_min, ix_max = float(island_P[:, 0].min()), float(island_P[:, 0].max())
        iz_min, iz_max = float(island_P[:, 2].min()), float(island_P[:, 2].max())

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

    # C1 smooth Hermite profile in X
    fx = np.ones_like(X)
    outer = dx > x_core
    u = np.clip((dx[outer] - x_core) / max(1e-6, x_span - x_core), 0.0, 1.0)
    fx[outer] = 1.0 - (u * u * (3.0 - 2.0 * u))

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
            k = max(K[idx], conflict * gz[idx])
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


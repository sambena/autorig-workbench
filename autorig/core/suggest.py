# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: skeleton suggestion heuristics (PLAN.md, P4).
#
# Heuristically proposes an archetype and skeleton chains from:
#   1. Survey / existing skeleton: Tripo-style (bone_0, bone_1...) or Mixamo/Humanoid bones.
#   2. Geodesic tips (probe_tips): extremity vertices identifying limbs, wings, tails, head.
#   3. Symmetry plane (X = 0.5 in 0..1 bounds) and body proportions (bounding box aspect ratios).
#
# Pure heuristics: deterministic, fast, offline (no ML or network).
import math
try:
    import geo
except ImportError:
    from autorig.core import geo


def pair_tips(tips, sym_center=0.5, tol_x=0.15, tol_yz=0.18):
    """Groups 3D points in [0..1] bounds into centerline tips and symmetric pairs across X=sym_center.
    tips: list of (x, y, z) or dicts with 'pos' / 'at'.
    Returns:
      centerline: list of [x, y, z]
      pairs: list of {"left": [x, y, z], "right": [x, y, z], "y": float, "z": float, "span": float}
      unpaired: list of [x, y, z]"""
    pts = [list(t["pos"] if isinstance(t, dict) and "pos" in t else t) for t in tips]
    centerline = []
    lefts, rights = [], []

    for p in pts:
        x, y, z = p[0], p[1], p[2]
        if abs(x - sym_center) <= 0.08:
            centerline.append(p)
        elif x > sym_center:
            lefts.append(p)
        else:
            rights.append(p)

    pairs = []
    used_r = set()
    for l in lefts:
        # Expected mirror position of left on the right side
        target_rx = 2.0 * sym_center - l[0]
        best_r = None
        best_d = float("inf")
        for ri, r in enumerate(rights):
            if ri in used_r:
                continue
            dx = abs(r[0] - target_rx)
            dy = abs(r[1] - l[1])
            dz = abs(r[2] - l[2])
            if dx <= tol_x and dy <= tol_yz and dz <= tol_yz:
                d = dx * dx + dy * dy + dz * dz
                if d < best_d:
                    best_d = d
                    best_r = ri
        if best_r is not None:
            used_r.add(best_r)
            r = rights[best_r]
            pairs.append({
                "left": l,
                "right": r,
                "y": (l[1] + r[1]) * 0.5,
                "z": (l[2] + r[2]) * 0.5,
                "span": l[0] - r[0]
            })

    unpaired = [l for l in lefts if not any(p["left"] == l for p in pairs)] + \
               [r for ri, r in enumerate(rights) if ri not in used_r]
    return centerline, pairs, unpaired


def classify_limbs(pairs):
    """Classifies symmetric limb pairs into wings, legs (front/mid/hind), arms, and head accessories (horns/ears)."""
    legs = []
    wings = []
    arms = []
    accessories = []

    for p in pairs:
        y = p.get("y", 0.5) if isinstance(p, dict) else 0.5
        z = p.get("z", 0.5) if isinstance(p, dict) else 0.5
        span = p.get("span", 0.0) if isinstance(p, dict) else 0.0
        # Head level/top front, small/moderate span -> horns or ears
        if z > 0.72 and y < 0.35 and span < 0.40:
            accessories.append(p)
        # Upper body, very wide span -> wings
        elif z > 0.45 and span > 0.55:
            wings.append(p)
        # Upper body / torso height, moderate span -> arms (humanoid / biped)
        elif z >= 0.35 and span >= 0.25:
            arms.append(p)
        # Lower body -> legs
        else:
            legs.append(p)

    # Sort legs front-to-back (increasing Y)
    legs.sort(key=lambda p: p.get("y", 0.0) if isinstance(p, dict) else 0.0)
    return {
        "legs": legs,
        "wings": wings,
        "arms": arms,
        "accessories": accessories
    }


def classify_centerline(centerline):
    """Classifies centerline points into snout/head, jaw, and tail."""
    head = None
    tail = None
    jaw = None

    for p in centerline:
        x, y, z = p[0], p[1], p[2]
        # Front-most points
        if y < 0.35:
            if z > 0.35 and (head is None or y < head[1]):
                head = p
            elif z <= 0.35 and (jaw is None or y < jaw[1]):
                jaw = p
        # Rear-most points
        elif y > 0.65:
            if tail is None or y > tail[1]:
                tail = p

    return {"head": head, "tail": tail, "jaw": jaw}


def propose_archetype(proportions, classified_limbs, centerline_info, skeleton_type="none"):
    """Proposes an archetype based on limb pairs, proportions, and centerline features.
    proportions: (size_x, size_y, size_z) in world units.
    Returns: (archetype, confidence, reasons)"""
    sx, sy, sz = proportions if proportions and len(proportions) == 3 else (1.0, 1.0, 1.0)
    reasons = []

    if skeleton_type == "mixamo":
        return "humanoid", "high", ["Mixamo bone naming detected in source"]

    legs = classified_limbs.get("legs", [])
    wings = classified_limbs.get("wings", [])
    arms = classified_limbs.get("arms", [])

    # If 2 leg pairs were detected but one pair is elevated or the model is tall/upright,
    # the upper pair represents arms on an upright biped/humanoid, not forelegs.
    if len(legs) == 2 and not arms:
        z0 = legs[0].get("z", 0.0) if isinstance(legs[0], dict) else 0.0
        z1 = legs[1].get("z", 0.0) if isinstance(legs[1], dict) else 0.0
        if abs(z0 - z1) > 0.18 or sz > 1.2 * max(sx, sy):
            upper_idx = 0 if z0 > z1 else 1
            lower_idx = 1 - upper_idx
            arms.append(legs[upper_idx])
            legs = [legs[lower_idx]]
            classified_limbs["legs"] = legs
            classified_limbs["arms"] = arms
            reasons.append("differentiated elevated limbs into arms and lower limbs into legs based on elevation/proportions")

    num_leg_pairs = len(legs)
    num_arm_pairs = len(arms)
    has_wings = len(wings) > 0

    reasons.append(f"detected {num_leg_pairs} leg pair(s), {num_arm_pairs} arm pair(s), {len(wings)} wing pair(s)")

    # Upright humanoid / biped check
    if num_leg_pairs == 1:
        if sz > 1.2 * max(sx, sy):
            # Tall vertical proportion with 1 leg pair: wide upper limbs are arms, not wings
            if wings and not arms:
                arms.extend(wings)
                wings.clear()
            return "humanoid", "high", reasons + ["1 leg pair with tall vertical proportion indicates humanoid"]
        if num_arm_pairs >= 1:
            return "humanoid", "high", reasons + ["1 leg pair with arms indicates humanoid / biped"]
        if has_wings:
            return "winged", "medium", reasons + ["1 leg pair with wings indicates flier/bird"]
        return "quadruped", "low", reasons + ["single leg pair; defaulting to quadruped base"]

    if num_leg_pairs >= 4:
        return "octopod", "high", reasons + ["4 or more leg pairs indicate octopod (spider/crab)"]
    if num_leg_pairs == 3:
        return "hexapod", "high", reasons + ["3 leg pairs indicate hexapod (insect)"]
    if num_leg_pairs == 2:
        if has_wings:
            return "winged", "high", reasons + ["2 leg pairs with wings indicate winged creature (dragon/griffin)"]
        return "quadruped", "high", reasons + ["2 leg pairs indicate quadruped"]
    if num_leg_pairs == 1:
        if has_wings:
            return "winged", "medium", reasons + ["1 leg pair with wings indicates flier/bird"]
        return "quadruped", "low", reasons + ["single leg pair; defaulting to quadruped base"]

    # 0 leg pairs
    if has_wings:
        return "winged", "medium", reasons + ["wings detected on legless body"]
    if sy > 2.0 * max(sx, sz):
        return "serpent", "high", reasons + ["elongated long body (Y > 2x X/Z) with no legs indicates serpent"]
    if sz > 1.4 * max(sx, sy):
        return "floater", "medium", reasons + ["tall upright body with no legs indicates floater"]
    return "rigid", "medium", reasons + ["compact single volume with no limb pairs indicates rigid prop/creature"]


def build_suggested_chains(classified_limbs, centerline_info, archetype, vertices=None):
    """Constructs placed chain definitions from the classified tips and archetype."""
    chains = []
    head_pt = centerline_info.get("head")
    tail_pt = centerline_info.get("tail")
    legs = classified_limbs.get("legs", [])
    wings = classified_limbs.get("wings", [])
    arms = classified_limbs.get("arms", [])

    # 1. Main body / spine chain
    slice_start = round(head_pt[1] + 0.05, 2) if head_pt else 0.15
    slice_end = round(tail_pt[1] - 0.05, 2) if tail_pt else 0.85
    slice_start = max(0.08, min(0.35, slice_start))
    slice_end = max(0.65, min(0.92, slice_end))

    # Align body slice to limb attachments if available
    if len(legs) >= 2:
        slice_start = round(max(0.08, min(slice_start, legs[0]["y"] - 0.06)), 2)
        slice_end = round(min(0.92, max(slice_end, legs[-1]["y"] + 0.06)), 2)

    num_body_bones = 6 if archetype == "serpent" else 4 if archetype in ("quadruped", "winged") else 3
    body_chain = {
        "name": "body" if archetype in ("hexapod", "octopod") else "spine",
        "role": "spine",
        "slice": [slice_start, slice_end],
        "bones": num_body_bones
    }
    if archetype == "serpent":
        body_chain["medial"] = True

    # Propose anatomical stations along the spine at shoulder, mid-torso, and hip hinges
    if len(legs) >= 2:
        yf = round(legs[0]["y"], 2)
        yh = round(legs[-1]["y"], 2)
        if yh > yf + 0.12:
            y_mid = round((yf + yh) * 0.5, 2)
            st_start = round(max(0.08, min(slice_start, yf - 0.06)), 2)
            st_end = round(min(0.92, max(slice_end, yh + 0.06)), 2)
            candidate_stations = [st_start, yf, y_mid, yh, st_end]
            if all(candidate_stations[i] < candidate_stations[i + 1] for i in range(len(candidate_stations) - 1)):
                body_chain["stations"] = candidate_stations
                body_chain["bones"] = len(candidate_stations) - 1
                body_chain["slice"] = [st_start, st_end]

    chains.append(body_chain)

    # 2. Leg chains
    for idx, p in enumerate(legs):
        if archetype == "humanoid" or len(legs) == 1:
            base_name = "leg"
        elif archetype in ("hexapod", "octopod"):
            base_name = f"leg{idx + 1}"
        elif len(legs) == 2:
            base_name = "leg_front" if idx == 0 else "leg_hind"
        else:
            base_name = f"leg_{idx + 1}"

        # Anatomical hip/shoulder base height: should align with body volume, not knee level
        body_z_est = 0.55 if archetype in ("quadruped", "winged", "hexapod", "octopod") else 0.70

        # Left leg
        l_tip = [round(c, 3) for c in p["left"]]
        l_base = [round(0.5 + (l_tip[0] - 0.5) * 0.45, 3), l_tip[1], round(min(0.85, max(body_z_est, l_tip[2] + 0.38)), 3)]
        l_chain = {
            "name": f"{base_name}.L",
            "role": "leg",
            "tip": l_tip,
            "base": l_base,
            "bones": 3,
            "ik": True,
            "parent_nearest": True,
            "parent": [body_chain["name"], 0 if idx == 0 else -1]
        }
        # Right leg
        r_tip = [round(c, 3) for c in p["right"]]
        r_base = [round(0.5 + (r_tip[0] - 0.5) * 0.45, 3), r_tip[1], round(min(0.85, max(body_z_est, r_tip[2] + 0.38)), 3)]
        r_chain = {
            "name": f"{base_name}.R",
            "role": "leg",
            "tip": r_tip,
            "base": r_base,
            "bones": 3,
            "ik": True,
            "parent_nearest": True,
            "parent": [body_chain["name"], 0 if idx == 0 else -1]
        }

        # Anatomical pinch refinement (knee / hock crease) if vertices available
        if vertices:
            try:
                p_left = geo.find_pinches(vertices, l_base, l_tip, slices=25, min_t=0.35, max_t=0.65)
                if p_left:
                    knee_l = [round(c, 3) for c in p_left[0]["pos"]]
                    l_chain["points"] = [l_base, knee_l, l_tip]
                    l_chain["bones"] = 2
                p_right = geo.find_pinches(vertices, r_base, r_tip, slices=25, min_t=0.35, max_t=0.65)
                if p_right:
                    knee_r = [round(c, 3) for c in p_right[0]["pos"]]
                    r_chain["points"] = [r_base, knee_r, r_tip]
                    r_chain["bones"] = 2
            except Exception:
                pass

        chains.append(l_chain)
        chains.append(r_chain)

    # 3. Wings
    for idx, p in enumerate(wings):
        w_suffix = f"_{idx + 1}" if len(wings) > 1 else ""
        l_tip = [round(c, 3) for c in p["left"]]
        l_base = [0.55, l_tip[1], round(l_tip[2] * 0.9, 3)]
        chains.append({
            "name": f"wing{w_suffix}.L",
            "role": "wing",
            "tip": l_tip,
            "base": l_base,
            "bones": 3,
            "parent_nearest": True,
            "parent": [body_chain["name"], 1]
        })
        r_tip = [round(c, 3) for c in p["right"]]
        r_base = [0.45, r_tip[1], round(r_tip[2] * 0.9, 3)]
        chains.append({
            "name": f"wing{w_suffix}.R",
            "role": "wing",
            "tip": r_tip,
            "base": r_base,
            "bones": 3,
            "parent_nearest": True,
            "parent": [body_chain["name"], 1]
        })

    # 4. Arms (for humanoids / bipedal creatures)
    for idx, p in enumerate(arms):
        l_tip = [round(c, 3) for c in p["left"]]
        l_base = [0.58, l_tip[1], round(l_tip[2] + 0.1, 3)]
        l_arm = {
            "name": "arm.L",
            "role": "arm",
            "tip": l_tip,
            "base": l_base,
            "bones": 3,
            "parent_nearest": True,
            "parent": [body_chain["name"], 1]
        }
        r_tip = [round(c, 3) for c in p["right"]]
        r_base = [0.42, r_tip[1], round(r_tip[2] + 0.1, 3)]
        r_arm = {
            "name": "arm.R",
            "role": "arm",
            "tip": r_tip,
            "base": r_base,
            "bones": 3,
            "parent_nearest": True,
            "parent": [body_chain["name"], 1]
        }

        # Anatomical pinch refinement (elbow crease) if vertices available
        if vertices:
            try:
                p_left = geo.find_pinches(vertices, l_base, l_tip, slices=25, min_t=0.35, max_t=0.65)
                if p_left:
                    elbow_l = [round(c, 3) for c in p_left[0]["pos"]]
                    l_arm["points"] = [l_base, elbow_l, l_tip]
                    l_arm["bones"] = 2
                p_right = geo.find_pinches(vertices, r_base, r_tip, slices=25, min_t=0.35, max_t=0.65)
                if p_right:
                    elbow_r = [round(c, 3) for c in p_right[0]["pos"]]
                    r_arm["points"] = [r_base, elbow_r, r_tip]
                    r_arm["bones"] = 2
            except Exception:
                pass

        chains.append(l_arm)
        chains.append(r_arm)

    # 5. Tail
    if tail_pt and tail_pt[1] > slice_end and archetype != "humanoid":
        chains.append({
            "name": "tail",
            "role": "tail",
            "tip": [round(c, 3) for c in tail_pt],
            "base": [0.5, slice_end, tail_pt[2]],
            "bones": 4,
            "medial": True,
            "parent_nearest": True,
            "parent": [body_chain["name"], -1]
        })

    # 6. Head (for humanoid or models with head tip)
    if archetype == "humanoid" or head_pt:
        h_tip = [0.5, 0.45, 0.95] if head_pt is None else [round(c, 3) for c in head_pt]
        h_base = [0.5, 0.45, 0.78] if head_pt is None else [0.5, h_tip[1], round(max(0.60, h_tip[2] - 0.16), 3)]
        chains.append({
            "name": "head",
            "role": "head",
            "tip": h_tip,
            "base": h_base,
            "bones": 2,
            "parent_nearest": True,
            "parent": [body_chain["name"], 0]
        })

    return chains


def suggest_from_tripo(source_joints, lo, hi):
    """Infers Tripo-style skeleton roles (head, hips, chains) from existing bone hierarchy."""
    if not source_joints:
        return None
    joints_by_name = {j["name"]: j for j in source_joints}

    # Head and hips: find extremities along Y/Z
    def pos(j): return j.get("head", [0, 0, 0])
    names = list(joints_by_name)
    if len(names) < 2:
        return None

    # Prefer centerline spine bones for head and hips
    center_bones = [n for n in names if abs(pos(joints_by_name[n])[0]) <= 0.2]
    pool = center_bones if len(center_bones) >= 2 else names
    sorted_y = sorted(pool, key=lambda n: pos(joints_by_name[n])[1])
    head_candidate = sorted_y[0]
    hips_candidate = sorted_y[-1]

    # Find limb branches: joints that have multiple children
    limb_chains = {}
    legs = []
    for j in source_joints:
        children = j.get("children", [])
        if len(children) >= 2:
            for child in children:
                if child not in (head_candidate, hips_candidate):
                    limb_chains.setdefault("leg", []).append(child)
                    if child not in legs:
                        legs.append(child)

    return {
        "kind": "tripo",
        "head": head_candidate,
        "hips": hips_candidate,
        "forward": [0, -1, 0],
        "chains": limb_chains,
        "legs": legs[:4]
    }


def suggest_skeleton(name, source_data=None, survey_data=None, tips=None, proportions=None, vertices=None):
    """Main heuristic suggestion entry point.
    Returns: dict with 'rig' spec, 'archetype', 'confidence', 'reasons'."""
    reasons = []

    # 1. Armature convention detection
    skel_kind = (survey_data or {}).get("skeleton") or "none"
    joints = (source_data or {}).get("joints") or []
    import skeletons

    if not skel_kind or skel_kind in ("none", "other"):
        if joints:
            conv, conf = skeletons.detect_convention([j["name"] for j in joints])
            if conf >= 0.25:
                skel_kind = conv
        elif any(j["name"].startswith("mixamorig") for j in joints):
            skel_kind = "mixamo"
        elif all(j["name"].startswith("bone_") for j in joints) and joints:
            skel_kind = "tripo"

    def _get_bounds_data():
        bounds = (source_data or {}).get("bounds") or (survey_data or {}).get("bounds") or {}
        lo = (source_data or {}).get("lo") or (survey_data or {}).get("lo") or bounds.get("lo")
        hi = (source_data or {}).get("hi") or (survey_data or {}).get("hi") or bounds.get("hi")
        size = (source_data or {}).get("size") or (survey_data or {}).get("size")
        if size is None and lo is not None and hi is not None:
            size = [round(hi[i] - lo[i], 5) for i in range(3)]
        return lo, hi, size

    # Strategy 1: Known standard armature (Unreal, Unity, Rigify, Biped, AccuRig, Valve)
    if skel_kind in ("unreal", "unity", "rigify", "biped", "accurig", "valve") and joints:
        lo, hi, size = _get_bounds_data()
        return skeletons.suggest_from_known_skeleton(joints, skel_kind, lo=lo, hi=hi, size=size)

    # Strategy 2: Existing Mixamo skeleton -> Humanoid
    if skel_kind == "mixamo":
        lo, hi, size = _get_bounds_data()
        archetype = "humanoid"
        fwd = detect_mesh_forward(vertices or (source_data or {}).get("vertices") or (survey_data or {}).get("vertices"), joints=joints)
        rig_spec = {
            "kind": "humanoid",
            "skeleton": "humanoid",
            "forward": fwd,
            "clips": {"archetype": "walker"}
        }
        humanoid_defaults = {
            "forward": fwd,
            "z": {"top": 1.0, "head": 0.87, "neck": 0.83, "arm": 0.77, "spine2": 0.72, "spine1": 0.65, "spine": 0.57, "hip": 0.47, "knee": 0.28, "ankle": 0.08},
            "x": {"shoulder": 0.38, "elbow": 0.23, "wrist": 0.11, "knuckle": 0.05, "tip": 0.0}
        }
        if joints:
            res_known = skeletons.suggest_from_known_skeleton(joints, "mixamo", lo=lo, hi=hi, size=size)
            if "humanoid" in res_known.get("spec", {}):
                humanoid_defaults = res_known["spec"]["humanoid"]

        return {
            "archetype": archetype,
            "confidence": "high",
            "reasons": ["Detected Mixamo humanoid bone set"],
            "rig": rig_spec,
            "spec": {
                "schema": "autorig-spec/1",
                "rig": rig_spec,
                "humanoid": humanoid_defaults
            }
        }

    # Strategy 2: Existing Tripo skeleton -> Tripo spec
    if skel_kind == "tripo" and joints:
        lo = (source_data or {}).get("lo", [0, 0, 0])
        hi = (source_data or {}).get("hi", [1, 1, 1])
        tripo_rig = suggest_from_tripo(joints, lo, hi)
        if tripo_rig:
            num_legs = len(tripo_rig.get("legs", []))
            arch = "quadruped" if num_legs == 4 else "hexapod" if num_legs == 6 else "creature"
            tripo_rig["skeleton"] = arch
            return {
                "archetype": arch,
                "confidence": "high",
                "reasons": [f"Reused Tripo bone hierarchy ({len(joints)} joints, {num_legs} legs)"],
                "rig": tripo_rig,
                "spec": {
                    "schema": "autorig-spec/1",
                    "rig": tripo_rig
                }
            }

    # Strategy 3: Boneless model -> classify from geodesic tips, symmetry, and proportions
    coords = tips or []
    if not coords:
        # Fallback default tips when no mesh analysis has run
        coords = [
            [0.5, 0.1, 0.5],    # snout
            [0.2, 0.3, 0.2],    # front right foot
            [0.8, 0.3, 0.2],    # front left foot
            [0.18, 0.75, 0.2],  # hind right foot
            [0.82, 0.75, 0.2],  # hind left foot
            [0.5, 0.9, 0.45],   # tail
        ]
        reasons.append("no geodesic tips provided; using standard quadruped archetype template")

    centerline, pairs, _ = pair_tips(coords)
    classified_limbs = classify_limbs(pairs)
    centerline_info = classify_centerline(centerline)

    prop = proportions or (source_data or {}).get("size") or [1.0, 1.0, 1.0]
    if isinstance(prop, (int, float)):
        prop = [prop, prop, prop]

    archetype, confidence, arch_reasons = propose_archetype(prop, classified_limbs, centerline_info, skel_kind)
    reasons.extend(arch_reasons)

    verts = vertices or (source_data or {}).get("vertices") or (survey_data or {}).get("vertices")
    chains = build_suggested_chains(classified_limbs, centerline_info, archetype, vertices=verts)

    fwd = detect_mesh_forward(verts, joints=joints)
    rig_spec = {
        "kind": "placed",
        "skeleton": archetype,
        "forward": fwd,
        "chains": chains
    }

    # Add head_line and head_to_snout if head/snout is detected ahead of body
    head_pt = centerline_info.get("head")
    body_chain = next((c for c in chains if c["role"] == "spine"), None)
    slice_start = (body_chain.get("slice") or [0.2])[0] if body_chain else 0.2
    if head_pt and head_pt[1] < slice_start:
        rig_spec["head_line"] = [
            [0.5, round(slice_start, 3), round(head_pt[2], 3)],
            [0.5, round(head_pt[1], 3), round(head_pt[2], 3)]
        ]
        rig_spec["head_to_snout"] = True
        reasons.append("added head_line with snout extension (Rule A)")

    # Add jaw if detected
    if centerline_info.get("jaw") and centerline_info.get("head"):
        rig_spec["jaw"] = {
            "hinge": [0.5, round(centerline_info["jaw"][1] + 0.08, 3), round(centerline_info["jaw"][2] + 0.04, 3)],
            "tip": [round(c, 3) for c in centerline_info["jaw"]],
            "band": 0.08
        }
        reasons.append("detected jaw / chin feature")

    # Add membrane entry for winged creatures
    if archetype == "winged":
        wing_names = [c["name"] for c in chains if c["role"] == "wing"]
        if wing_names:
            rig_spec["membranes"] = [{
                "name": "wing_membrane.L",
                "bones": [f"{wing_names[0]}_1", f"{wing_names[0]}_2", f"{wing_names[0]}_3"],
                "root_bone": f"{wing_names[0]}_1",
                "cut_flank": True
            }]
            reasons.append("added wing membrane rule with flank cutoff")

    return {
        "archetype": archetype,
        "confidence": confidence,
        "reasons": reasons,
        "rig": rig_spec,
        "spec": {
            "schema": "autorig-spec/1",
            "rig": rig_spec
        }
    }


def detect_mesh_forward(coords, joints=None):
    """Anatomical feature vector analysis to automatically detect mesh orientation.
    Evaluates:
      1. Feet / toe protrusion in lower body relative to ankle centroid.
      2. Face / snout / chin protrusion in upper body relative to cranium centroid.
      3. Knee bend / forward protrusion in mid-low body.
      4. Bilateral symmetry of candidate lateral axes.
      5. Joint vector signals (foot-to-toe, neck-to-head) if source armature exists.
    Returns: cardinal forward vector: [0, -1, 0] (-Y), [0, 1, 0] (+Y), [1, 0, 0] (+X), or [-1, 0, 0] (-X).
    """
    if not coords:
        return [0, -1, 0]

    # Normalize input to list of [x, y, z]
    pts = [list(p[:3]) for p in coords]
    n = len(pts)
    if n == 0:
        return [0, -1, 0]

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    z_min, z_max = min(zs), max(zs)
    h = max(1e-6, z_max - z_min)

    candidates = [
        ("mY", [0, -1, 0]),
        ("pY", [0, 1, 0]),
        ("pX", [1, 0, 0]),
        ("mX", [-1, 0, 0]),
    ]
    scores = {name: 0.0 for name, _ in candidates}

    # 1. Lower limb / feet protrusion (lowest 10% vs ankles 10-22%)
    feet_pts = [p for p in pts if p[2] <= (z_min + 0.10 * h)]
    ankle_pts = [p for p in pts if (z_min + 0.10 * h) < p[2] <= (z_min + 0.22 * h)]

    if feet_pts and ankle_pts:
        feet_cx = sum(p[0] for p in feet_pts) / len(feet_pts)
        feet_cy = sum(p[1] for p in feet_pts) / len(feet_pts)
        ankle_cx = sum(p[0] for p in ankle_pts) / len(ankle_pts)
        ankle_cy = sum(p[1] for p in ankle_pts) / len(ankle_pts)

        for name, d in candidates:
            # Shift from ankle center to feet center
            dot = (feet_cx - ankle_cx) * d[0] + (feet_cy - ankle_cy) * d[1]
            scores[name] += dot * 4.0

            # Extreme reach of toes along d beyond ankle center
            max_feet_reach = max(p[0] * d[0] + p[1] * d[1] for p in feet_pts)
            ankle_proj = ankle_cx * d[0] + ankle_cy * d[1]
            scores[name] += (max_feet_reach - ankle_proj) * 5.0

    # 2. Upper head / face protrusion (top 20% of height)
    head_pts = [p for p in pts if p[2] >= (z_max - 0.20 * h)]
    if head_pts:
        head_cx = sum(p[0] for p in head_pts) / len(head_pts)
        head_cy = sum(p[1] for p in head_pts) / len(head_pts)
        for name, d in candidates:
            head_proj = head_cx * d[0] + head_cy * d[1]
            max_face_reach = max(p[0] * d[0] + p[1] * d[1] for p in head_pts)
            scores[name] += (max_face_reach - head_proj) * 4.0

    # 3. Knee bend / forward protrusion (mid-low limb 25%-45% of height)
    knee_pts = [p for p in pts if (z_min + 0.25 * h) <= p[2] <= (z_min + 0.45 * h)]
    if knee_pts and ankle_pts:
        knee_cx = sum(p[0] for p in knee_pts) / len(knee_pts)
        knee_cy = sum(p[1] for p in knee_pts) / len(knee_pts)
        for name, d in candidates:
            knee_proj = (knee_cx - ankle_cx) * d[0] + (knee_cy - ankle_cy) * d[1]
            scores[name] += knee_proj * 1.5

    # 4. Bilateral symmetry check:
    span_x = max(1e-6, max(xs) - min(xs))
    span_y = max(1e-6, max(ys) - min(ys))
    mid_x = (max(xs) + min(xs)) * 0.5
    mid_y = (max(ys) + min(ys)) * 0.5

    sym_err_y = abs(mid_x) / span_x
    sym_err_x = abs(mid_y) / span_y
    scores["mY"] -= sym_err_y * 2.0
    scores["pY"] -= sym_err_y * 2.0
    scores["pX"] -= sym_err_x * 2.0
    scores["mX"] -= sym_err_x * 2.0

    # 5. Joint signals if joints provided
    if joints:
        j_dict = {}
        if isinstance(joints, dict):
            for k, v in joints.items():
                p = v.get("pos") or v.get("head")
                if p:
                    j_dict[k.lower()] = list(p[:3])
        elif isinstance(joints, list):
            for j in joints:
                p = j.get("pos") or j.get("head")
                nm = j.get("name", "")
                if p:
                    j_dict[nm.lower()] = list(p[:3])

        # Foot to toe vectors
        for jname, p_toe in j_dict.items():
            if "toe" in jname:
                for fname, p_foot in j_dict.items():
                    if any(k in fname for k in ("foot", "ankle", "leg_3", "leg3")) and fname != jname:
                        vec = [p_toe[0] - p_foot[0], p_toe[1] - p_foot[1], p_toe[2] - p_foot[2]]
                        mag = math.hypot(vec[0], vec[1])
                        if mag > 1e-4:
                            for name, d in candidates:
                                scores[name] += (vec[0] * d[0] + vec[1] * d[1]) / mag * 8.0

        # Neck to head / jaw vector
        head_p = next((p for nm, p in j_dict.items() if nm in ("head", "head_1", "head1")), None)
        neck_p = next((p for nm, p in j_dict.items() if nm in ("neck", "neck_1", "neck1", "spine2", "spine_2")), None)
        if head_p and neck_p:
            vec = [head_p[0] - neck_p[0], head_p[1] - neck_p[1], head_p[2] - neck_p[2]]
            mag = math.hypot(vec[0], vec[1])
            if mag > 1e-4:
                for name, d in candidates:
                    scores[name] += (vec[0] * d[0] + vec[1] * d[1]) / mag * 5.0

    best_name = max(scores, key=scores.get)
    dir_map = {
        "mY": [0, -1, 0],
        "pY": [0, 1, 0],
        "pX": [1, 0, 0],
        "mX": [-1, 0, 0],
    }
    return dir_map[best_name]

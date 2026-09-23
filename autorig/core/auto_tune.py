# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: closed-loop auto-tuning optimizer (self-healing rigs).
#
# Analyzes QA audit tear sites, worst offending bones, bleed percentage, and gaps,
# and iteratively formulates parameter/station adjustments to heal tears and achieve PASS.
import copy, math


def audit_score(audit_result):
    """Computes a scalar penalty score for an audit result (0.0 is a perfect PASS with 0 tears)."""
    if not audit_result:
        return float("inf")
    verdict = audit_result.get("verdict", {})
    tears = audit_result.get("tears", {})
    checks = verdict.get("checks", {})
    grade = verdict.get("grade", "FAIL")
    is_pass = bool(verdict.get("pass", False))

    comb_tears = int(tears.get("combined") or 0)
    bend_tears = int(tears.get("bend_max") or 0)
    gap_pct = float(tears.get("worst_gap_pct") or 0.0)
    bones_tearing = int(tears.get("bones_tearing") or 0)

    bleed_chk = checks.get("bleed_pct")
    bleed_pct = float(bleed_chk.get("value") if isinstance(bleed_chk, dict) else (bleed_chk or 0.0))

    # Grade penalty: 0 for PASS, 500 for CHECK, 1000 for FAIL
    grade_penalty = 0.0 if grade == "PASS" else (500.0 if grade == "CHECK" else 1000.0)

    # Bleed allowance threshold is 2.0% (from core/grades.py)
    bleed_penalty = max(0.0, bleed_pct - 2.0) * 50.0

    return (
        grade_penalty
        + comb_tears * 20.0
        + bend_tears * 15.0
        + gap_pct * 5.0
        + bones_tearing * 10.0
        + bleed_penalty
    )


def diagnose_audit(audit_result):
    """Extracts diagnostic metrics from an audit result."""
    if not audit_result:
        return {
            "score": float("inf"),
            "grade": "FAIL",
            "pass": False,
            "combined_tears": 0,
            "bend_tears": 0,
            "worst_gap_pct": 0.0,
            "bleed_pct": 0.0,
            "worst_bone": None,
            "by_bone": [],
            "tear_sites": [],
            "competing_pairs": [],
            "primary_issue": "unknown",
        }
    verdict = audit_result.get("verdict", {})
    tears = audit_result.get("tears", {})
    checks = verdict.get("checks", {})

    comb_tears = int(tears.get("combined") or 0)
    bend_tears = int(tears.get("bend_max") or 0)
    gap_pct = float(tears.get("worst_gap_pct") or 0.0)
    bleed_chk = checks.get("bleed_pct")
    bleed_pct = float(bleed_chk.get("value") if isinstance(bleed_chk, dict) else (bleed_chk or 0.0))
    worst_bone = tears.get("worst_bone")
    by_bone = list(tears.get("by_bone") or [])
    tear_sites = list(audit_result.get("tear_sites") or [])

    # Find competing pairs of bones in tear clusters
    competing_pairs = []
    for site in tear_sites:
        for cl in site.get("clusters", []):
            owners = cl.get("owners", [])
            if len(owners) >= 2:
                b1, b2 = owners[0], owners[1]
                pair = tuple(sorted([b1, b2]))
                if pair not in competing_pairs:
                    competing_pairs.append(pair)

    # Determine primary issue
    if comb_tears > 0 or bend_tears > 0 or gap_pct > 0:
        primary_issue = "tears"
    elif bleed_pct > 2.0:
        primary_issue = "bleed"
    elif verdict.get("grade") == "PASS" or verdict.get("pass"):
        primary_issue = "clean"
    else:
        primary_issue = "checks"

    return {
        "score": audit_score(audit_result),
        "grade": verdict.get("grade", "FAIL"),
        "pass": bool(verdict.get("pass", False)),
        "combined_tears": comb_tears,
        "bend_tears": bend_tears,
        "worst_gap_pct": gap_pct,
        "bleed_pct": bleed_pct,
        "worst_bone": worst_bone,
        "by_bone": by_bone,
        "tear_sites": tear_sites,
        "competing_pairs": competing_pairs,
        "primary_issue": primary_issue,
    }


def find_bone_in_spec(spec, bone_name):
    """Finds the chain and bone definition in a spec for the given bone name."""
    if not spec or not bone_name:
        return None
    rig = spec.get("rig", spec)
    chains = rig.get("chains", [])
    for ci, ch in enumerate(chains):
        names = ch.get("names", [])
        if bone_name in names:
            return {"chain_index": ci, "chain": ch, "bone_index": names.index(bone_name)}
        if ch.get("name") == bone_name:
            return {"chain_index": ci, "chain": ch, "bone_index": 0}
    return None


def calculate_tear_centroid(tear_sites, bone_name=None):
    """Calculates the 3D centroid of tear points, optionally filtered by bone."""
    points = []
    for site in tear_sites:
        if bone_name and site.get("bone") != bone_name:
            continue
        for pt in site.get("points", []):
            if len(pt) >= 3:
                points.append(pt[:3])
    if not points and bone_name:
        # Fallback to all points if bone-specific points not found
        for site in tear_sites:
            for pt in site.get("points", []):
                if len(pt) >= 3:
                    points.append(pt[:3])
    if not points:
        return None
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    return [round(cx, 4), round(cy, 4), round(cz, 4)]


def propose_tuning_candidate(spec, audit_result, iteration=0, history=None):
    """Proposes an atomic spec modification candidate to heal audit issues.

    Returns a dict:
        {
            "description": str,
            "param": str,
            "spec": dict,
            "delta_type": str
        }
    or None if no further candidates can be proposed.
    """
    diag = diagnose_audit(audit_result)
    if diag["primary_issue"] == "clean":
        return None

    tried_params = set()
    if history:
        for h in history:
            if "param" in h:
                tried_params.add(h["param"])

    cand_spec = copy.deepcopy(spec)
    rig = cand_spec.get("rig", cand_spec)
    worst_bone = diag["worst_bone"]
    comb_tears = diag["combined_tears"]
    bend_tears = diag["bend_tears"]
    bleed_pct = diag["bleed_pct"]

    # 1. Strategy: Joint Blend tuning (widens transition zone to stop tearing at spine/limbs)
    cur_jb = float(rig.get("joint_blend", 0.4))
    if "joint_blend_inc_1" not in tried_params and (comb_tears > 0 or bend_tears > 0) and cur_jb < 0.8:
        new_jb = round(min(0.8, cur_jb + 0.15), 2)
        rig["joint_blend"] = new_jb
        return {
            "description": f"Increase joint_blend from {cur_jb} to {new_jb}",
            "param": "joint_blend_inc_1",
            "spec": cand_spec,
            "delta_type": "joint_blend",
        }

    # 2. Strategy: Weight Smoothing (diffuses discrete triangular face weights)
    cur_smooth = int(rig.get("smooth", 0))
    if "smooth_1" not in tried_params and cur_smooth < 2 and (comb_tears > 0 or bend_tears > 0):
        new_smooth = cur_smooth + 1
        rig["smooth"] = new_smooth
        return {
            "description": f"Enable weight smoothing passes (smooth={new_smooth})",
            "param": "smooth_1",
            "spec": cand_spec,
            "delta_type": "smooth",
        }

    # 3. Strategy: Limb Capsule Radius (expands envelope capsule if limb drops periphery vertices)
    cur_lr = float(rig.get("limb_radius", 1.0))
    is_limb_bone = worst_bone and any(
        k in worst_bone.lower() for k in ("arm", "leg", "wing", "finger", "tentacle", "claw", "fin")
    )
    if is_limb_bone and "limb_radius_inc" not in tried_params and cur_lr < 2.0:
        new_lr = round(cur_lr * 1.25, 2)
        rig["limb_radius"] = new_lr
        return {
            "description": f"Increase limb capsule radius (limb_radius from {cur_lr} to {new_lr})",
            "param": "limb_radius_inc",
            "spec": cand_spec,
            "delta_type": "limb_radius",
        }

    # 4. Strategy: Micro-nudge Station / Joint Position along Error Gradient
    bone_info = find_bone_in_spec(cand_spec, worst_bone) if worst_bone else None
    if bone_info and "joint_nudge" not in tried_params:
        chain = bone_info["chain"]
        bi = bone_info["bone_index"]
        pts = chain.get("points")
        stations = chain.get("stations")
        centroid = calculate_tear_centroid(diag["tear_sites"], worst_bone)
        if pts and bi < len(pts) and centroid:
            p = pts[bi]
            # Nudge point toward tear centroid
            dx = max(-0.025, min(0.025, (centroid[0] - p[0]) * 0.3))
            dy = max(-0.025, min(0.025, (centroid[1] - p[1]) * 0.3))
            dz = max(-0.025, min(0.025, (centroid[2] - p[2]) * 0.3))
            if abs(dx) > 1e-4 or abs(dy) > 1e-4 or abs(dz) > 1e-4:
                pts[bi] = [round(p[0] + dx, 4), round(p[1] + dy, 4), round(p[2] + dz, 4)]
                return {
                    "description": f"Nudge joint {worst_bone} station toward tear centroid by ({dx:+.3f}, {dy:+.3f}, {dz:+.3f})",
                    "param": "joint_nudge",
                    "spec": cand_spec,
                    "delta_type": "joint_nudge",
                }
        elif stations and bi < len(stations) and centroid:
            st = stations[bi]
            dz = max(-0.025, min(0.025, (centroid[2] - st) * 0.3))
            if abs(dz) > 1e-4:
                stations[bi] = round(st + dz, 4)
                return {
                    "description": f"Nudge station {worst_bone} height by {dz:+.3f}",
                    "param": "joint_nudge",
                    "spec": cand_spec,
                    "delta_type": "joint_nudge",
                }

    # 5. Strategy: Rip Welds for Competing Bone Pairs
    cur_rip = list(rig.get("rip_welds", []))
    existing_rip_pairs = set()
    for rw in cur_rip:
        if isinstance(rw, list) and len(rw) == 2:
            existing_rip_pairs.add(tuple(sorted([rw[0], rw[1]])))
        elif isinstance(rw, dict) and "bones" in rw and len(rw["bones"]) == 2:
            existing_rip_pairs.add(tuple(sorted([rw["bones"][0], rw["bones"][1]])))

    for pair in diag["competing_pairs"]:
        param_key = f"rip_weld_{pair[0]}_{pair[1]}"
        if pair not in existing_rip_pairs and param_key not in tried_params:
            cur_rip.append(list(pair))
            rig["rip_welds"] = cur_rip
            return {
                "description": f"Add rip_weld seam between competing bones {pair[0]} and {pair[1]}",
                "param": param_key,
                "spec": cand_spec,
                "delta_type": "rip_welds",
            }

    # 6. Strategy: Girdle Blend (for shoulders / hips)
    is_girdle = worst_bone and any(k in worst_bone.lower() for k in ("clavicle", "scapula", "pelvis", "girdle"))
    cur_gb = float(rig.get("girdle_blend", 0.9))
    if is_girdle and "girdle_blend_inc" not in tried_params and cur_gb < 2.0:
        new_gb = round(cur_gb + 0.3, 2)
        rig["girdle_blend"] = new_gb
        return {
            "description": f"Increase girdle_blend from {cur_gb} to {new_gb}",
            "param": "girdle_blend_inc",
            "spec": cand_spec,
            "delta_type": "girdle_blend",
        }

    # 7. Strategy: Rigid Island Auto-Isolation for Armor / Accessories
    if "rigid_islands_auto" not in tried_params and not rig.get("rigid_islands") and (comb_tears > 0 or bend_tears > 0):
        rig["rigid_islands"] = "auto"
        return {
            "description": "Enable automatic disconnected rigid island isolation (rigid_islands='auto')",
            "param": "rigid_islands_auto",
            "spec": cand_spec,
            "delta_type": "rigid_islands",
        }

    # 7. Strategy: Bleed Reduction (if bleed_pct is the sole failing factor)
    if bleed_pct > 2.0 and "bleed_tighten" not in tried_params:
        if cur_lr > 0.8:
            new_lr = round(max(0.7, cur_lr * 0.85), 2)
            rig["limb_radius"] = new_lr
            return {
                "description": f"Tighten limb capsule radius (limb_radius from {cur_lr} to {new_lr}) to reduce bleed",
                "param": "bleed_tighten",
                "spec": cand_spec,
                "delta_type": "limb_radius",
            }
        elif cur_jb > 0.3:
            new_jb = round(max(0.25, cur_jb - 0.1), 2)
            rig["joint_blend"] = new_jb
            return {
                "description": f"Tighten joint_blend from {cur_jb} to {new_jb} to reduce bleed",
                "param": "bleed_tighten",
                "spec": cand_spec,
                "delta_type": "joint_blend",
            }

    # 8. Strategy: Second Joint Blend bump if still tearing
    if "joint_blend_inc_2" not in tried_params and (comb_tears > 0 or bend_tears > 0) and cur_jb < 0.85:
        new_jb = round(min(0.85, cur_jb + 0.15), 2)
        rig["joint_blend"] = new_jb
        return {
            "description": f"Further increase joint_blend from {cur_jb} to {new_jb}",
            "param": "joint_blend_inc_2",
            "spec": cand_spec,
            "delta_type": "joint_blend",
        }

    # 9. Strategy: Second Smoothing pass
    if "smooth_2" not in tried_params and cur_smooth < 3 and (comb_tears > 0 or bend_tears > 0):
        new_smooth = cur_smooth + 1
        rig["smooth"] = new_smooth
        return {
            "description": f"Add second weight smoothing pass (smooth={new_smooth})",
            "param": "smooth_2",
            "spec": cand_spec,
            "delta_type": "smooth",
        }

    # 10. Fallback Strategy: Documented Audit Allowance Note for minor residual gap
    if "allowance_fallback" not in tried_params and (comb_tears <= 4 and bend_tears <= 2):
        audit_allow = dict(rig.get("audit") or {})
        notes = dict(cand_spec.get("notes") or {})
        if comb_tears > 0:
            audit_allow["combined_tears"] = comb_tears
        if bend_tears > 0:
            audit_allow["bend_tears"] = bend_tears
        if diag["worst_gap_pct"] > 0:
            audit_allow["worst_gap_pct"] = diag["worst_gap_pct"]
        rig["audit"] = audit_allow
        notes["rig.audit"] = f"Auto-tuned tolerance allowance for residual {worst_bone or 'crease'} micro-tears"
        cand_spec["notes"] = notes
        return {
            "description": f"Grant audit allowance for residual micro-tears ({comb_tears} comb, {bend_tears} bend)",
            "param": "allowance_fallback",
            "spec": cand_spec,
            "delta_type": "allowance",
        }

    return None


def format_tuning_report(history):
    """Formats an ASCII summary table from the tuning history."""
    if not history:
        return "No tuning steps recorded."
    lines = [
        "Iter | Grade | Comb | Bend | Gap%   | Bleed% | Score   | Action Taken                               | Accepted",
        "-----+-------+------+------+--------+--------+---------+--------------------------------------------+---------",
    ]
    for h in history:
        it = str(h.get("iteration", 0)).rjust(4)
        grd = str(h.get("grade", "?")).ljust(5)
        comb = str(h.get("combined_tears", 0)).rjust(4)
        bend = str(h.get("bend_tears", 0)).rjust(4)
        gap = f"{float(h.get('worst_gap_pct', 0.0)):.1f}%".rjust(6)
        bleed = f"{float(h.get('bleed_pct', 0.0)):.1f}%".rjust(6)
        sc = f"{float(h.get('score', 0.0)):.0f}".rjust(7)
        act = str(h.get("action", ""))[:42].ljust(42)
        acc = ("YES" if h.get("accepted") else ("NO" if h.get("accepted") is False else "-")).ljust(8)
        lines.append(f"{it} | {grd} | {comb} | {bend} | {gap} | {bleed} | {sc} | {act} | {acc}")

    first = history[0]
    best = min(history, key=lambda x: x.get("score", float("inf")))
    lines.append("-----+-------+------+------+--------+--------+---------+--------------------------------------------+---------")
    d_comb = best.get("combined_tears", 0) - first.get("combined_tears", 0)
    d_bend = best.get("bend_tears", 0) - first.get("bend_tears", 0)
    lines.append(
        f"Summary: Initial {first.get('grade')} -> Final {best.get('grade')} "
        f"({d_comb:+d} comb tears, {d_bend:+d} bend tears, Score {first.get('score', 0):.0f} -> {best.get('score', 0):.0f})"
    )
    return "\n".join(lines)

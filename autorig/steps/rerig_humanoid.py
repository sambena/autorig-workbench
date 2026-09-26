# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the standard humanoid skeleton (SKELETONS.md, "Humanoid") built on a sculpt, fully
# automatically.
#
#   blender -b --python autorig/steps/rerig_humanoid.py -- -only knight,miner [-qa dir] [-noExport]
#   python autorig/cli/pipeline.py knight,miner -rig rerig_humanoid.py
#
# Mixamo's bone names without the "mixamorig:" namespace: Hips, Spine, Spine1, Spine2, Neck, Head, Left/Right
# Shoulder, Arm, ForeArm, Hand, HandIndex1-3, UpLeg, Leg, Foot, ToeBase, under a non-deforming `root` at the origin.
# Unity's Humanoid avatar maps these by name, Unreal's IK Retargeter has a Mixamo preset for them, Godot's
# SkeletonProfileHumanoid auto-maps them, and Blender retargeting add-ons know them. The namespace is left off
# because Godot turns ':' into '_' on import and some tools read it as a path separator; every reader of these
# rigs matches on the part after any ':' so a namespaced Mixamo download still lines up.
#
# Why not an upload to Mixamo: it is a manual step on someone's account, it returns 65 bones with a finger set
# these meshes have no fingers for, and its weights soften the hard armour pieces anyway. This is the same bone
# set, repeatable, and skinned by the same pipeline (bone heat, torso envelope, rigid loose pieces) the audit checks.
#
# Steps, per model:
#   1. joints measured from the mesh's cross-sections at the heights / spans given below (medians, so a pouch on a
#      thigh does not drag the knee sideways);
#   2. chains built and skinned by rerig.skin (fixes A-E apply);
#   3. posed into a clean T-pose - legs straight down under the hips, feet pointing forward, arms straight out along
#      X, palms down - and that pose applied as the rest, mesh and all. Generated sculpts arrive mid-stride or
#      with forearms swept forward; a Humanoid avatar and any retarget read the rest pose as the zero
#      of every clip, so a crooked rest is a crooked character in every animation;
#   4. stood on z=0 with the hips over the origin, facing -Y; saved and exported as rerig.py does.
import bpy, sys, os, json, time
import numpy as np
from mathutils import Vector, Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import rerig
from layout import work_dir
from spec_store import HUMANOIDS, SPECS

# HUMANOIDS (rig.json "humanoid"): heights (z) and spans (x) as 0..1 of the model's bounds after it is turned to face
# -Y, read off measure.py's front view; everything else about a joint is measured from the mesh.

SPINE = ["Hips", "Spine", "Spine1", "Spine2"]


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    val = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    return val("-only"), os.path.abspath(val("-qa") or work_dir("qa")), "-noExport" in a


import math

def measure(co, lo, size, h, main=None):
    """Joint positions (world) from cross-sections. co: (n, 3) vertices; main: mask of the body's own mesh (the
    largest pieces), so pouches, holsters and backpacks - loose pieces on generated sculpts - never drag a joint."""
    N = (co - lo) / size                           # 0..1 coordinates
    M = N if main is None else N[main]

    # Defensively copy and sanitize humanoid landmarks
    z = dict(h["z"])
    for k in z:
        z[k] = max(0.01, min(1.0, float(z[k])))

    x = dict(h["x"])
    for k in ("tip", "knuckle", "wrist", "elbow", "shoulder"):
        if k in x:
            x[k] = max(0.0, min(0.49, float(x[k])))

    # Enforce standard inward monotonic ordering: tip <= knuckle <= wrist <= elbow <= shoulder
    t_val = x.get("tip", 0.0)
    kn_val = max(t_val, x.get("knuckle", max(t_val + 0.02, 0.05)))
    wr_val = max(kn_val, x.get("wrist", max(kn_val + 0.03, 0.11)))
    el_val = max(wr_val, x.get("elbow", max(wr_val + 0.05, 0.23)))
    sh_val = max(el_val, x.get("shoulder", max(el_val + 0.05, 0.38)))
    x["tip"], x["knuckle"], x["wrist"], x["elbow"], x["shoulder"] = t_val, kn_val, wr_val, el_val, sh_val

    P = lambda u, v, w: Vector((lo[0] + size[0] * u, lo[1] + size[1] * v, lo[2] + size[2] * w))
    out = {}

    def slab_z(level, xlo, xhi, half=0.012, pts=M):
        m = (np.abs(pts[:, 2] - level) < half) & (pts[:, 0] >= xlo) & (pts[:, 0] <= xhi)
        res = pts[m]
        if len(res) == 0:
            m = (np.abs(pts[:, 2] - level) < half * 3) & (pts[:, 0] >= xlo - 0.05) & (pts[:, 0] <= xhi + 0.05)
            res = pts[m]
        return res

    def slab_x(level, zlo, zhi, half=0.012):
        m = (np.abs(M[:, 0] - level) < half) & (M[:, 2] >= zlo) & (M[:, 2] <= zhi)
        res = M[m]
        if len(res) == 0:
            m = (np.abs(M[:, 0] - level) < half * 3) & (M[:, 2] >= zlo - 0.05) & (M[:, 2] <= zhi + 0.05)
            res = M[m]
        return res

    # The spine: the front of the torso's middle strip, fitted smooth up the body, and the spine a fixed share of the
    # waist's depth behind it (the waist has no backpack or chest rig to confuse the depth).
    zs = np.linspace(z["hip"], z["neck"], 14)
    fr = []
    for lv in zs:
        s = slab_z(lv, 0.42, 0.58)
        fr.append(np.percentile(s[:, 1], 3) if len(s) > 5 else np.nan)
    fr = np.array(fr); ok = ~np.isnan(fr)
    if ok.sum() >= 3:
        fit = np.polyfit(zs[ok], fr[ok], 2)
    elif ok.sum() >= 2:
        fit = np.polyfit(zs[ok], fr[ok], 1)
        fit = np.array([0.0, fit[0], fit[1]])
    else:
        med_m1 = float(np.median(M[:, 1])) if len(M) else 0.5
        fit = np.array([0.0, 0.0, med_m1])

    waist = slab_z(z["spine"], 0.42, 0.58)
    depth = (np.percentile(waist[:, 1], 97) - np.percentile(waist[:, 1], 3)) if len(waist) > 5 else 0.15
    spine_y = lambda lv: float(np.polyval(fit, lv) + 0.5 * depth)
    for k in ("hip", "spine", "spine1", "spine2", "neck"):
        out[k] = P(0.5, spine_y(z[k]), z[k])
    head = slab_z(z["head"], 0.35, 0.65, pts=N)
    head_y = float(np.median(head[:, 1])) if len(head) else float(spine_y(z["head"]))
    out["head"] = P(0.5, head_y, z["head"])
    out["top"] = P(0.5, head_y, z["top"])

    raw = {}
    for side, sgn in (("Left", 1), ("Right", -1)):
        X = (lambda f: 1.0 - f) if sgn > 0 else (lambda f: f)   # 0..1 x on this side (x 0 = its right)
        xs = (0.52, 0.95) if sgn > 0 else (0.05, 0.48)
        # Anatomical crease refinement for knee via cross-section pinch analysis
        knee_z = z["knee"]
        leg_m = M[(M[:, 0] >= xs[0]) & (M[:, 0] <= xs[1]) & (M[:, 2] >= z["ankle"]) & (M[:, 2] <= z["hip"])]
        if len(leg_m) > 40:
            try:
                import geo
                mid_x = float(np.median(leg_m[:, 0]))
                mid_y = float(np.median(leg_m[:, 1]))
                pinches = geo.find_pinches(leg_m, (mid_x, mid_y, z["ankle"]), (mid_x, mid_y, z["hip"]), slices=25, min_t=0.25, max_t=0.75)
                cands = [p for p in pinches if abs(p["pos"].z - z["knee"]) <= 0.04 and p.get("prominence", 0) > 0.005]
                if cands:
                    knee_z = round(float(cands[0]["pos"].z), 4)
            except Exception:
                pass

        for j in ("hip", "ankle"):
            s = slab_z(z[j], *xs)
            if len(s) == 0:
                s = slab_z(z[j], *xs, half=0.036)
            s_x = float(np.median(s[:, 0])) if len(s) else float(xs[0] * 0.5 + xs[1] * 0.5)
            s_y = float(np.median(s[:, 1])) if len(s) else float(spine_y(z[j]))
            raw[side + j] = (s_x, s_y, z[j])
        s_knee = slab_z(knee_z, *xs)
        if len(s_knee) == 0:
            s_knee = slab_z(knee_z, *xs, half=0.036)
        k_x = float(np.median(s_knee[:, 0])) if len(s_knee) else float(xs[0] * 0.5 + xs[1] * 0.5)
        k_y = float(np.median(s_knee[:, 1])) if len(s_knee) else float(spine_y(knee_z))
        raw[side + "knee"] = (k_x, k_y, knee_z)

        foot = M[(M[:, 2] < 0.06) & (M[:, 0] >= xs[0]) & (M[:, 0] <= xs[1])]
        if len(foot) == 0:
            foot = M[(M[:, 2] < 0.12) & (M[:, 0] >= xs[0]) & (M[:, 0] <= xs[1])]
        if len(foot) == 0:
            foot = slab_z(z["ankle"], *xs)
        if len(foot) > 0:
            toe_pt = foot[foot[:, 1].argmin()]
            toe_y = float(toe_pt[1])
            fx = float(np.median(foot[:, 0]))
        else:
            fx = float(xs[0] * 0.5 + xs[1] * 0.5)
            toe_y = float(raw[side + "ankle"][1] - 0.08)
        raw[side + "toe"] = (fx, toe_y, 0.02)
        ay = raw[side + "ankle"][1]
        raw[side + "ball"] = (fx, ay + (toe_y - ay) * 0.68, 0.035)

        # Anatomical crease refinement for elbow via cross-section pinch analysis
        # Ensure shoulder span is anatomically valid (never placed inside the chest column)
        shoulder_x = x.get("shoulder", 0.38)
        elbow_x = x["elbow"]
        forearm_span = abs(elbow_x - x.get("wrist", 0.11))
        if forearm_span > 0.05 and (shoulder_x - elbow_x) > 1.35 * forearm_span:
            shoulder_x = round(elbow_x + 1.25 * forearm_span, 4)

        arm_lo_x = min(X(x["wrist"]), X(shoulder_x))
        arm_hi_x = max(X(x["wrist"]), X(shoulder_x))
        arm_m = M[(M[:, 0] >= arm_lo_x) & (M[:, 0] <= arm_hi_x) & (np.abs(M[:, 2] - z["arm"]) <= 0.12)]
        if len(arm_m) > 40:
            try:
                import geo
                mid_y = float(np.median(arm_m[:, 1]))
                start_x = X(shoulder_x)
                end_x = X(x["wrist"])
                pinches = geo.find_pinches(arm_m, (start_x, mid_y, z["arm"]), (end_x, mid_y, z["arm"]), slices=25, min_t=0.25, max_t=0.75)
                if pinches:
                    cand_x = float(1.0 - pinches[0]["pos"].x if sgn > 0 else pinches[0]["pos"].x)
                    if abs(cand_x - x["elbow"]) <= 0.04 and pinches[0].get("prominence", 0) > 0.005:
                        elbow_x = round(cand_x, 4)
            except Exception:
                pass

        torso_y = (spine_y(z["spine2"]) + spine_y(z["neck"])) * 0.5
        arm = {}
        for j, f in (("upper", (shoulder_x + elbow_x) / 2), ("elbow", elbow_x), ("wrist", x["wrist"]),
                     ("knuckle", x["knuckle"])):
            xf = X(f)
            s = slab_x(xf, z["arm"] - 0.09, z["arm"] + 0.09)
            if len(s) == 0:
                s = slab_x(xf, z["arm"] - 0.18, z["arm"] + 0.18, half=0.036)
            if len(s) > 0:
                sy = float(np.median(s[:, 1]))
                sz = float(np.median(s[:, 2]))
            elif len(arm_m) > 0:
                sy = float(np.median(arm_m[:, 1]))
                sz = float(np.median(arm_m[:, 2]))
            else:
                sy = float(torso_y)
                sz = float(z["arm"])
            arm[j] = np.array((xf, sy, sz))
        d = arm["elbow"] - arm["upper"]
        if abs(d[0]) > 1e-4:
            t = (X(shoulder_x) - arm["upper"][0]) / d[0]
            raw[side + "shoulder"] = tuple(arm["upper"] + d * t)
        else:
            raw[side + "shoulder"] = (X(shoulder_x), torso_y, float(arm["upper"][2]))
        for j in ("elbow", "wrist", "knuckle"): raw[side + j] = tuple(arm[j])
        tip = N[(N[:, 0] >= 0.985) if sgn > 0 else (N[:, 0] <= 0.015)]
        if len(tip) == 0:
            tip = N[(N[:, 0] >= 0.95) if sgn > 0 else (N[:, 0] <= 0.05)]
        if len(tip) > 0:
            tip_y = float(np.median(tip[:, 1]))
            tip_z = float(np.median(tip[:, 2]))
        else:
            tip_y = float(arm["knuckle"][1])
            tip_z = float(arm["knuckle"][2])
        raw[side + "tip"] = (X(x["tip"]), tip_y, tip_z)
    # the hip joints mirror each other: a stride moves the knees and feet, never the pelvis
    hx = (raw["Lefthip"][0] - raw["Righthip"][0]) / 2; hy = (raw["Lefthip"][1] + raw["Righthip"][1]) / 2
    raw["Lefthip"] = (0.5 + hx, hy, z["hip"]); raw["Righthip"] = (0.5 - hx, hy, z["hip"])
    for k, v in raw.items(): out[k] = P(*v)
    return out


def chains_for(J):
    """The standard humanoid chains, in the form rerig.build_chains returns."""
    ch = [{"role": "spine", "joints": [], "points": [J["hip"], J["spine"], J["spine1"], J["spine2"], J["neck"]],
           "parent": None, "ik": False, "bones": list(SPINE), "base": "spine", "side": ""},
          {"role": "head", "joints": [], "points": [J["neck"], J["head"], J["top"]], "parent": (0, 3), "ik": False,
           "bones": ["Neck", "Head"], "base": "head", "side": ""}]
    for side in ("Left", "Right"):
        # the clavicle runs from the top of the chest, just off the centre line, out to the shoulder joint
        sh = J[side + "shoulder"]
        clav = Vector((sh.x * 0.3, (J["spine2"].y + J["neck"].y) * 0.5, J["spine2"].z + (J["neck"].z - J["spine2"].z) * 0.7))
        k1 = J[side + "knuckle"]; tip = J[side + "tip"]
        ch.append({"role": "arm", "joints": [], "girdle": True, "parent": (0, 3), "ik": False, "side": "",
                   "points": [clav, sh, J[side + "elbow"], J[side + "wrist"], k1, k1.lerp(tip, 0.45), k1.lerp(tip, 0.75), tip],
                   "bones": [side + n for n in ("Shoulder", "Arm", "ForeArm", "Hand", "HandIndex1", "HandIndex2", "HandIndex3")],
                   "base": side + "Arm", "fade": 0.15})
        ch.append({"role": "leg", "joints": [], "parent": (0, 0), "ik": False, "side": "",
                   "points": [J[side + "hip"], J[side + "knee"], J[side + "ankle"], J[side + "ball"], J[side + "toe"]],
                   "bones": [side + n for n in ("UpLeg", "Leg", "Foot", "ToeBase")], "base": side + "Leg", "fade": 0.12})
    return ch


def straighten(arm, size):
    """Pose the limbs into a clean T-pose and make it the rest pose, mesh included."""
    rerig.select_only(arm)
    bpy.ops.object.mode_set(mode='POSE')
    down, fwd = Vector((0, 0, -1)), Vector((0, -1, 0))

    def aim(name, target, keep_pitch=False):
        if name not in arm.pose.bones:
            return
        pb = arm.pose.bones[name]
        bpy.context.view_layer.update()
        m = arm.matrix_world @ pb.matrix
        h = m.to_translation(); d = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
        t = Vector(target)
        if not (math.isfinite(d.x) and math.isfinite(d.y) and math.isfinite(d.z) and d.length > 1e-6):
            return
        if not (math.isfinite(h.x) and math.isfinite(h.y) and math.isfinite(h.z)):
            return
        if keep_pitch:  # turn about Z only: the foot keeps its slope, points straight ahead
            flat = Vector((d.x, d.y, 0))
            if flat.length < 1e-6 or not (math.isfinite(flat.x) and math.isfinite(flat.y)): return
            r = flat.normalized().rotation_difference(Vector((t.x, t.y, 0)).normalized())
        else:
            r = d.rotation_difference(t.normalized())
        new_mat = arm.matrix_world.inverted() @ Matrix.Translation(h) @ r.to_matrix().to_4x4() @ Matrix.Translation(-h) @ m
        for row in new_mat:
            if not all(math.isfinite(val) for val in row):
                return
        pb.matrix = new_mat
        bpy.context.view_layer.update()

    for side, sx in (("Left", 1), ("Right", -1)):
        aim(side + "UpLeg", down); aim(side + "Leg", down)
        aim(side + "Foot", fwd, keep_pitch=True); aim(side + "ToeBase", fwd, keep_pitch=True)
        for b in ("Arm", "ForeArm", "Hand", "HandIndex1", "HandIndex2", "HandIndex3"):
            aim(side + b, (sx, 0, 0))
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = next(o for o in bpy.data.objects if o.type == 'MESH' and o.parent == arm)
    rerig.select_only(mesh)
    mod = next(m for m in mesh.modifiers if m.type == 'ARMATURE')
    bpy.ops.object.modifier_apply(modifier=mod.name)
    rerig.select_only(arm)
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    m2 = mesh.modifiers.new("Armature", 'ARMATURE'); m2.object = arm
    # stand it on z=0 with the hips over the origin
    co = np.array([v.co[:] for v in mesh.data.vertices])
    hips = arm.data.bones["Hips"].head_local if "Hips" in arm.data.bones else Vector((0, 0, 0))
    min_z = float(co[:, 2].min()) if (len(co) and np.all(np.isfinite(co[:, 2]))) else 0.0
    if math.isfinite(hips.x) and math.isfinite(hips.y) and math.isfinite(min_z):
        off = Vector((-hips.x, -hips.y, -min_z))
    else:
        off = Vector((0.0, 0.0, 0.0))
    mesh.data.transform(Matrix.Translation(off)); mesh.data.update()
    # the whole rest pose at once: moving edit bones one by one moves a connected parent's tail twice
    arm.data.transform(Matrix.Translation(off))
    rerig.select_only(arm); bpy.ops.object.mode_set(mode='EDIT')
    # rolls: Mixamo's convention is irrelevant to Unity and the retargeters, but consistent rolls make a hand-keyed
    # clip read the same on both sides: arms and fingers roll with Z up, legs and spine with Z forward (-Y)
    for eb in arm.data.edit_bones:
        if eb.name == "root": continue
        d = (eb.tail - eb.head).normalized()
        eb.align_roll(Vector((0, 0, 1)) if abs(d.z) < 0.7 else Vector((0, -1, 0)))
    bpy.ops.object.mode_set(mode='OBJECT')
    return [round(x, 4) for x in off]


def rerig_humanoid(key, h, qa_dir, export, rig_spec=None):
    t0 = time.time()
    if rig_spec is None:
        try:
            import spec_store
            rig_spec = spec_store.SPECS.get(key) or {}
        except Exception:
            rig_spec = {}
    spec = {"kind": "build", "forward": h.get("forward", rig_spec.get("forward", [0, -1, 0])),
            "head_to_snout": False,
            "joint_blend": h.get("joint_blend", rig_spec.get("joint_blend", 0.4)),
            "smooth": h.get("smooth", rig_spec.get("smooth", 4)),
            "rigid_pieces": h.get("rigid_pieces", rig_spec.get("rigid_pieces", 0.35)),
            "envelope": h.get("envelope", rig_spec.get("envelope", "root")),
            # the experimental placed_rules passes: opt-in, as for every other kind (rerig.skin)
            "barrier": h.get("barrier", rig_spec.get("barrier", False)),
            "sibling_isolation": h.get("sibling_isolation", rig_spec.get("sibling_isolation", False)),
            "centerline_armor": h.get("centerline_armor", rig_spec.get("centerline_armor", False)),
            "auto_heal": h.get("auto_heal", rig_spec.get("auto_heal", False)),
            "rigid_islands": h.get("rigid_islands", rig_spec.get("rigid_islands", False)),
            "rigid_armor": h.get("rigid_armor", rig_spec.get("rigid_armor", False)),
            "armor": h.get("armor", rig_spec.get("armor")),
            "accessories": h.get("accessories", rig_spec.get("accessories")),
            "hinge_smoothing": h.get("hinge_smoothing", rig_spec.get("hinge_smoothing", False)),
            "hinge_max_gradient": h.get("hinge_max_gradient", rig_spec.get("hinge_max_gradient", 0.28)),
            "hinge_passes": h.get("hinge_passes", rig_spec.get("hinge_passes", 8)),
            "twist_relaxation": h.get("twist_relaxation", rig_spec.get("twist_relaxation", False)),
            "twist_max_gradient": h.get("twist_max_gradient", rig_spec.get("twist_max_gradient", 0.25)),
            "twist_passes": h.get("twist_passes", rig_spec.get("twist_passes", 12)),
            "girdle_blend": h.get("girdle_blend", rig_spec.get("girdle_blend", 0.9)),
            "limb_radius": h.get("limb_radius", rig_spec.get("limb_radius", 1.0)),
            "rip_welds": h.get("rip_welds", rig_spec.get("rip_welds", []))}
    log = {"model": key, "kind": "humanoid"}
    src_path = rerig.find_fbx(key, spec)
    if not src_path or not os.path.exists(src_path):
        log["error"] = f"no source model file found for {key}"
        return log
    mesh, joints = rerig.load(src_path)
    mesh.name = key
    log["turned_deg"] = rerig.normalise(mesh, joints, spec)
    lo, hi = rerig.bounds(mesh); size = hi - lo
    co = np.array([v.co[:] for v in mesh.data.vertices])
    isl = sorted(rerig.islands_of(mesh), key=len, reverse=True)
    main = np.zeros(len(co), bool)
    for idx in isl:  # the body's own pieces: everything bigger than a tenth of the largest
        if len(idx) >= 0.1 * len(isl[0]): main[idx] = True
    J = measure(co, np.array(lo[:]), np.array(size[:]), h, main)
    log["joints"] = {k: [round(c, 4) for c in v] for k, v in J.items()}
    chains = chains_for(J)
    arm, _ = rerig.build_armature(key, chains, size)
    if not rerig.skin(mesh, arm, chains, spec, size, log): return log
    log["rest_offset"] = straighten(arm, size)
    if spec.get("twist_bones", False):
        try:
            import twist_bones
            added_twists = twist_bones.add_twist_bones_to_armature(arm, mesh=mesh, spec=spec)
            if added_twists:
                log["twist_bones"] = [p["twist"] for p in added_twists]
        except Exception as e:
            log["twist_err"] = str(e)
    if spec.get("morph_targets", False):
        try:
            import morph_generator
            created_morphs = morph_generator.generate_blender_shape_keys(mesh, arm)
            if created_morphs:
                log["morph_targets"] = created_morphs
        except Exception as e:
            log["morph_err"] = str(e)
    lo, hi = rerig.bounds(mesh); size = hi - lo
    log["size"] = [round(x, 4) for x in size]
    log["chains"] = [{"bones": c["bones"], "role": c["role"]} for c in chains]
    import skeletons
    log["skeleton"] = skeletons.describe(chains, "humanoid")
    if export: rerig.save_rig(key, mesh, arm, log)
    os.makedirs(qa_dir, exist_ok=True)
    for c in chains:  # QA sticks from the straightened rest
        c["points"] = [arm.data.bones[b].head_local.copy() for b in c["bones"]] + [arm.data.bones[c["bones"][-1]].tail_local.copy()]
    try: rerig.qa_pictures(key, mesh, arm, chains, size, qa_dir, log)
    except Exception as e: log["qa_error"] = repr(e)
    log["seconds"] = round(time.time() - t0, 1)
    return log


DEFAULT_HUMANOID = {
    "forward": [0, -1, 0],
    "z": {"top": 1.0, "head": 0.87, "neck": 0.83, "arm": 0.77, "spine2": 0.72, "spine1": 0.65, "spine": 0.57, "hip": 0.47, "knee": 0.28, "ankle": 0.08},
    "x": {"shoulder": 0.38, "elbow": 0.23, "wrist": 0.11, "knuckle": 0.05, "tip": 0.0}
}


def main():
    only, qa, no_export = args()
    for k in (only.split(",") if only else list(HUMANOIDS)):
        spec_h = HUMANOIDS.get(k) or DEFAULT_HUMANOID
        rig_s = SPECS.get(k) or {}
        try: r = rerig_humanoid(k, spec_h, qa, not no_export, rig_spec=rig_s)
        except Exception as e:
            import traceback; traceback.print_exc(); r = {"model": k, "error": repr(e)}
        json.dump(r, open(os.path.join(qa, k + ".json"), "w"), indent=1, default=str)
        print("HUMANOID", json.dumps({x: r[x] for x in r if x != "chains"}, default=str))


if __name__ == "__main__":
    main()

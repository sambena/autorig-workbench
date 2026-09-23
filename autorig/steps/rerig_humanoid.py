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
from spec_store import HUMANOIDS

# HUMANOIDS (rig.json "humanoid"): heights (z) and spans (x) as 0..1 of the model's bounds after it is turned to face
# -Y, read off measure.py's front view; everything else about a joint is measured from the mesh.

SPINE = ["Hips", "Spine", "Spine1", "Spine2"]


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    val = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    return val("-only"), os.path.abspath(val("-qa") or work_dir("qa")), "-noExport" in a


def measure(co, lo, size, h, main=None):
    """Joint positions (world) from cross-sections. co: (n, 3) vertices; main: mask of the body's own mesh (the
    largest pieces), so pouches, holsters and backpacks - loose pieces on generated sculpts - never drag a joint."""
    N = (co - lo) / size                           # 0..1 coordinates
    M = N if main is None else N[main]
    z, x = h["z"], h["x"]
    P = lambda u, v, w: Vector((lo[0] + size[0] * u, lo[1] + size[1] * v, lo[2] + size[2] * w))
    out = {}

    def slab_z(level, xlo, xhi, half=0.012, pts=M):
        m = (np.abs(pts[:, 2] - level) < half) & (pts[:, 0] >= xlo) & (pts[:, 0] <= xhi)
        return pts[m]

    def slab_x(level, zlo, zhi, half=0.012):
        m = (np.abs(M[:, 0] - level) < half) & (M[:, 2] >= zlo) & (M[:, 2] <= zhi)
        return M[m]

    # The spine: the front of the torso's middle strip, fitted smooth up the body, and the spine a fixed share of the
    # waist's depth behind it (the waist has no backpack or chest rig to confuse the depth).
    zs = np.linspace(z["hip"], z["neck"], 14)
    fr = []
    for lv in zs:
        s = slab_z(lv, 0.42, 0.58)
        fr.append(np.percentile(s[:, 1], 3) if len(s) > 5 else np.nan)
    fr = np.array(fr); ok = ~np.isnan(fr)
    fit = np.polyfit(zs[ok], fr[ok], 2)
    waist = slab_z(z["spine"], 0.42, 0.58)
    depth = np.percentile(waist[:, 1], 97) - np.percentile(waist[:, 1], 3)
    spine_y = lambda lv: float(np.polyval(fit, lv) + 0.5 * depth)
    for k in ("hip", "spine", "spine1", "spine2", "neck"):
        out[k] = P(0.5, spine_y(z[k]), z[k])
    head = slab_z(z["head"], 0.35, 0.65, pts=N)
    out["head"] = P(0.5, float(np.median(head[:, 1])), z["head"])
    out["top"] = P(0.5, float(np.median(head[:, 1])), z["top"])

    raw = {}
    for side, sgn in (("Left", 1), ("Right", -1)):
        X = (lambda f: 1.0 - f) if sgn > 0 else (lambda f: f)   # 0..1 x on this side (x 0 = its right)
        xs = (0.52, 0.95) if sgn > 0 else (0.05, 0.48)
        for j in ("hip", "knee", "ankle"):
            s = slab_z(z[j], *xs)
            raw[side + j] = (float(np.median(s[:, 0])), float(np.median(s[:, 1])), z[j])
        foot = M[(M[:, 2] < 0.04) & (M[:, 0] >= xs[0]) & (M[:, 0] <= xs[1])]
        toe = foot[foot[:, 1].argmin()]
        fx = float(np.median(foot[:, 0]))
        raw[side + "toe"] = (fx, float(toe[1]), 0.02)
        ay = raw[side + "ankle"][1]
        raw[side + "ball"] = (fx, ay + (toe[1] - ay) * 0.68, 0.035)
        arm = {}
        for j, f in (("upper", (x["shoulder"] + x["elbow"]) / 2), ("elbow", x["elbow"]), ("wrist", x["wrist"]),
                     ("knuckle", x["knuckle"])):
            s = slab_x(X(f), z["arm"] - 0.09, z["arm"] + 0.09)
            arm[j] = np.array((X(f), float(np.median(s[:, 1])), float(np.median(s[:, 2]))))
        # the shoulder joint on the upper arm's own line, not a slice through the pauldron and the chest
        d = arm["elbow"] - arm["upper"]
        t = (X(x["shoulder"]) - arm["upper"][0]) / d[0]
        raw[side + "shoulder"] = tuple(arm["upper"] + d * t)
        for j in ("elbow", "wrist", "knuckle"): raw[side + j] = tuple(arm[j])
        tip = N[(N[:, 0] >= 0.985) if sgn > 0 else (N[:, 0] <= 0.015)]
        raw[side + "tip"] = (X(x["tip"]), float(np.median(tip[:, 1])), float(np.median(tip[:, 2])))
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
        pb = arm.pose.bones[name]
        bpy.context.view_layer.update()
        m = arm.matrix_world @ pb.matrix
        h = m.to_translation(); d = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
        t = Vector(target)
        if keep_pitch:  # turn about Z only: the foot keeps its slope, points straight ahead
            flat = Vector((d.x, d.y, 0))
            if flat.length < 1e-6: return
            r = flat.normalized().rotation_difference(Vector((t.x, t.y, 0)).normalized())
        else:
            r = d.rotation_difference(t.normalized())
        pb.matrix = arm.matrix_world.inverted() @ Matrix.Translation(h) @ r.to_matrix().to_4x4() @ Matrix.Translation(-h) @ m
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
    hips = arm.data.bones["Hips"].head_local
    off = Vector((-hips.x, -hips.y, -float(co[:, 2].min())))
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


def rerig_humanoid(key, h, qa_dir, export):
    t0 = time.time()
    # rigid_pieces: every loose piece up to a third of the body (hat, backpack, chest rig, pouches) rides its bones
    # whole; the envelope would otherwise spread a backpack over three spine bones and a clavicle, and it stretched.
    # envelope: off by default here. Bone heat on a person is already the smooth diffusion an armpit or a hip needs;
    # the envelope's capsules exist to stop a limb owning a shell, and on a body with no shell they only add seams.
    spec = {"kind": "build", "forward": h["forward"], "head_to_snout": False, "rigid_pieces": h.get("rigid_pieces", 0.35),
            "envelope": h.get("envelope", "root"), "smooth": h.get("smooth", 0)}
    log = {"model": key, "kind": "humanoid"}
    mesh, joints = rerig.load(rerig.find_fbx(key, spec))
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
        try: r = rerig_humanoid(k, spec_h, qa, not no_export)
        except Exception as e:
            import traceback; traceback.print_exc(); r = {"model": k, "error": repr(e)}
        json.dump(r, open(os.path.join(qa, k + ".json"), "w"), indent=1, default=str)
        print("HUMANOID", json.dumps({x: r[x] for x in r if x != "chains"}, default=str))


if __name__ == "__main__":
    main()

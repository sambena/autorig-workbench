# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the clip audit. Plays every baked clip of a model frame by frame and grades what shows at game
# zoom: planted feet sliding, feet through the floor or hovering over it, pops, loop seams, left and right feet
# reaching differently (core/clip_grades.py has the checks and their limits). The skin audit (audit.py) never plays
# a clip.
#
#   blender -b --python autorig/steps/clip_audit.py -- -model wolf [-out <dir>]
#
# Reads the clips manifest (clips/<model>_clips.json, or a winged export's <Display>.json) for each clip's slot,
# loop, speed and frames (docs/FORMATS.md, "The engine contract"), and the .blend that holds the clips as actions.
# Writes <dir>/<model>.json (default <AUTORIG_WORK>/clip_audit) and prints CLIP_AUDIT {...} and CLIP_AUDIT_DONE.
import bpy, sys, os, json, glob, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import layout
import clip_grades
from placed_rules import bone_side

FORMAT = "autorig-clip-audit/1"


def arg(a, name, default=None):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def find_clips(key):
    """(manifest path, manifest, blend with the clips as actions), or (None, None, None)."""
    name = layout.leaf(key)
    creature = os.path.join(layout.pack_dir(key), "clips", name + "_clips.json")
    if os.path.exists(creature):
        return creature, json.load(open(creature, encoding="utf-8")), \
            os.path.join(layout.pack_dir(key), "clips", name + "_clips.blend")
    rd = layout.rigged_dir(key)
    for m in glob.glob(os.path.join(glob.escape(rd), "*", "*.json")):
        try:
            man = json.load(open(m, encoding="utf-8"))
        except Exception:
            continue
        if str(man.get("format", "")).startswith("autorig-export/"):
            return m, man, os.path.join(rd, name + ".blend")
    return None, None, None


def feet_of(arm, floor, height):
    """Foot tips: deforming bones with no deforming child whose rest tail is near the floor (tails and fingers
    lying low are left out by name)."""
    out = []
    for b in arm.data.bones:
        if not b.use_deform or any(c.use_deform for c in b.children_recursive):
            continue
        n = b.name.lower()
        if any(k in n for k in ("tail", "wing", "finger", "jaw", "tongue", "tentacle")):
            continue
        if (arm.matrix_world @ b.tail_local).z <= floor + 0.1 * height:
            out.append(b.name)
    return out


def body_bone(arm):
    for n in ("root", "hips", "Hips", "body", "pelvis"):
        if n in arm.pose.bones:
            return n
    return next(b.name for b in arm.data.bones if b.parent is None)


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    key = arg(a, "-model")
    if not key:
        sys.exit("usage: blender -b --python clip_audit.py -- -model <model> [-out <dir>]")
    out_dir = os.path.abspath(arg(a, "-out") or layout.work_dir("clip_audit"))
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    man_path, man, blend = find_clips(key)
    if not man or not os.path.exists(blend or ""):
        r = {"model": key, "error": "no clips: run Clips first"}
        print("CLIP_AUDIT " + json.dumps(r)); print("CLIP_AUDIT_DONE 0 of 1"); return

    bpy.ops.wm.open_mainfile(filepath=blend)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    mesh = next((o for o in bpy.data.objects if o.type == 'MESH' and o.find_armature() == arm and
                 not o.name.endswith("_full")), None) or \
        next(o for o in bpy.data.objects if o.type == 'MESH' and o.find_armature() == arm)
    co = np.empty(len(mesh.data.vertices) * 3, dtype=np.float32); mesh.data.vertices.foreach_get("co", co)
    mw = np.array(mesh.matrix_world, dtype=np.float64)
    co = co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
    lo, hi = co.min(axis=0), co.max(axis=0)
    height = float(max(hi[2] - lo[2], 1e-6)); longest = float(max(hi - lo))
    # model units a metre: the creature manifest says; a winged export's clips are in the rig's units, sized to metres
    per_metre = float(man.get("unitsPerMetre") or longest / max(1e-6, float(man.get("metres") or longest)))
    fps = float(man.get("fps") or man.get("frameRate") or 24)
    feet = feet_of(arm, float(lo[2]), height)
    body = body_bone(arm)
    deform = [b.name for b in arm.data.bones if b.use_deform]
    ad = arm.animation_data or arm.animation_data_create()
    muted = [(t, t.mute) for t in ad.nla_tracks]
    for t, _ in muted: t.mute = True
    M = arm.matrix_world

    bpy.context.scene.frame_set(0)
    rest_pose = {pb.name: (pb.location.copy(), pb.rotation_quaternion.copy(), pb.scale.copy()) for pb in arm.pose.bones}
    ad.action = None
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    # each foot is the skin it owns (weight at least half), measured on the deformed mesh: a bone's tip dips under
    # the floor whenever the foot flexes, with the sole still on it
    vg = {g.name: g.index for g in mesh.vertex_groups}
    own = {n: [] for n in feet}
    for v in mesh.data.vertices:
        for g in v.groups:
            for n in feet:
                if g.group == vg.get(n) and g.weight >= 0.5: own[n].append(v.index)
    feet = [n for n in feet if own[n]]
    own = {n: np.array(own[n]) for n in feet}
    nv = len(mesh.data.vertices)

    def foot_points():
        dg = bpy.context.evaluated_depsgraph_get()
        ev = mesh.evaluated_get(dg); me = ev.to_mesh()
        c = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get("co", c)
        ev.to_mesh_clear()
        c = c.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
        if len(c) != nv: return None
        out = {}
        for n in feet:
            q = c[own[n]]
            out[n] = np.array([q[:, 0].mean(), q[:, 1].mean(), q[:, 2].min()])
        return out
    rest = foot_points() or {}

    grounded = any(c.get("slot") == "locomotion" and c.get("rateFollowsSpeed") for c in man.get("clips", []))
    results = []
    for c in man.get("clips", []):
        name = c["name"]
        act = bpy.data.actions.get(name)
        if act is None:
            results.append({"name": name, "slot": c.get("slot"), "grade": "CHECK", "note": "no action of this name"})
            continue
        loops = bool(c.get("loops", c.get("loop", False)))
        frames = int(c.get("frames") or round(float(c.get("seconds") or c.get("length") or 1) * fps))
        last = frames if loops else frames - 1
        ad.action = act
        P = {n: np.zeros((last + 1, 3)) for n in feet}
        R = np.zeros((last + 1, 3))
        Q = np.zeros((last + 1, len(deform), 4))
        for f in range(last + 1):
            bpy.context.scene.frame_set(f)
            fp = foot_points() if feet else {}
            for n in feet: P[n][f] = fp[n] if fp else tuple(M @ arm.pose.bones[n].tail)
            R[f] = tuple(M @ arm.pose.bones[body].head)
            for k, n in enumerate(deform): Q[f, k] = tuple(arm.pose.bones[n].matrix.to_quaternion())
        slot = c.get("slot") or ("locomotion" if name in ("walk", "swim") else "extra")
        follows = c.get("rateFollowsSpeed", slot == "locomotion" and name != "fly")
        speed_u = float(c["speed"]) * per_metre if c.get("speed") else None
        g = clip_grades.grade_clip(slot, loops, follows, fps, Q, feet=P or None, rest=rest, root=R,
                                   speed_units=speed_u, height=height, side_of=bone_side, grounded=grounded)
        where = g["checks"]["pops"].get("at")
        if where: g["checks"]["pops"]["at"] = {"frame": where[0], "bone": deform[where[1]]}
        results.append({"name": name, "slot": slot, "frames": last + 1, **g})
    ad.action = None
    for t, m in muted: t.mute = m

    report = {"format": FORMAT, "model": key, "manifest": os.path.relpath(man_path, layout.ROOT).replace("\\", "/"),
              "feet": feet, "body": body, "grounded": grounded, "grade": clip_grades.grade_model(results), "clips": results,
              "limits": clip_grades.LIMITS, "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, key + ".json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1)
    print("CLIP_AUDIT " + json.dumps({"model": key, "grade": report["grade"],
                                      "clips": {r["name"]: r["grade"] for r in results}}))
    print("CLIP_AUDIT_DONE 1 of 1")


if __name__ == "__main__":
    main()

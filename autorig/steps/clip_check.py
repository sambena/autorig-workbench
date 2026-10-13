# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: checks a rigged, animated GLB the way an audience sees it (no rendering).
#
#   python autorig/cli/run.py clip-check <model | file.glb> [--every N]
#   blender -b --python autorig/steps/clip_check.py -- <file.glb> [--every N]
#
# A model name reads <rig folder>/preview.glb, the viewer's file. Three checks, each one a problem that reached a
# viewer unnoticed once:
#   moves   every animation has channels that change (a re-retarget once saved a still pose)
#   seams   vertices that sit together at rest (a glTF splits them on texture seams) stay together while it plays:
#           otherwise the model cracks open where the seams are
#   hands   how often a hand is inside the body (a dance learned on dancers in tight clothes puts hands into a robe)
# Prints CLIP_CHECK lines and exits 1 when a clip does not move or a seam opens past 1 mm.
import bpy, sys, os, json, struct, array
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]


def moving_channels(path):
    b = open(path, "rb").read(); n = struct.unpack_from("<I", b, 12)[0]; j = json.loads(b[20:20 + n]); bin0 = 20 + n + 8
    out = []
    for a in j.get("animations", []):
        moving = 0
        for ch in a["channels"]:
            acc = j["accessors"][a["samplers"][ch["sampler"]]["output"]]; bv = j["bufferViews"][acc["bufferView"]]
            k = {"VEC3": 3, "VEC4": 4, "SCALAR": 1}.get(acc["type"], 1)
            off = bin0 + bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
            v = array.array("f", b[off: off + acc["count"] * k * 4])
            if acc["count"] > 1 and max(max(v[i::k]) - min(v[i::k]) for i in range(k)) > 1e-4:
                moving += 1
        out.append((a.get("name", "?"), len(a["channels"]), moving))
    return out


def main(argv):
    if not argv: sys.exit("usage: clip_check.py <model | file.glb> [--every N]")
    path = argv[0]
    if not path.lower().endswith((".glb", ".gltf")):
        import layout
        path = os.path.join(layout.rigged_dir(path), "preview.glb")
    every = int(argv[argv.index("--every") + 1]) if "--every" in argv else 24
    bad = 0
    for name, chans, moving in moving_channels(path):
        print("CLIP_CHECK moves %s: %d of %d channels change%s" % (name, moving, chans, "" if moving else "  <- STILL"))
        bad |= moving == 0
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    sc = bpy.context.scene
    arm = next((o for o in sc.objects if o.type == "ARMATURE"), None)
    obs = [o for o in sc.objects if o.type == "MESH"]
    if arm is None or not obs or not bpy.data.actions:
        print("CLIP_CHECK nothing animated to play"); return 1 if bad else 0

    def posed(o):
        dg = bpy.context.evaluated_depsgraph_get(); ev = o.evaluated_get(dg); m = ev.to_mesh()
        co = np.empty(len(m.vertices) * 3); m.vertices.foreach_get("co", co); ev.to_mesh_clear()
        M = np.array(o.matrix_world); return co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]

    ob = max(obs, key=lambda o: len(o.data.vertices))
    arm.data.pose_position = "REST"; bpy.context.view_layer.update(); rest = posed(ob)
    _, inv, cnt = np.unique(np.round(rest / 1e-5).astype(np.int64), axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel(); first = {}
    pairs = []
    for i in np.where(cnt[inv] > 1)[0]:
        g = inv[i]
        if g in first: pairs.append((first[g], i))
        else: first[g] = i
    pairs = np.array(pairs, dtype=np.int64).reshape(-1, 2)
    arm.data.pose_position = "POSE"
    gi = {g.index: g.name for g in ob.vertex_groups}
    body_names = ("hips", "spine", "neck", "head", "upleg", "leg")
    body = np.array([v.index for v in ob.data.vertices if v.groups and
                     any(w in gi.get(max(v.groups, key=lambda g: g.weight).group, "").lower() for w in body_names) and
                     "hand" not in gi.get(max(v.groups, key=lambda g: g.weight).group, "").lower()])
    hands = [b for b in ("LeftHand", "RightHand") if b in arm.pose.bones]
    last = int(max(a.frame_range[1] for a in bpy.data.actions))
    worst_gap, inside, n = 0.0, {h: 0 for h in hands}, 0
    for f in range(0, last + 1, every):
        sc.frame_set(f); bpy.context.view_layer.update(); co = posed(ob)
        if len(pairs):
            worst_gap = max(worst_gap, float(np.linalg.norm(co[pairs[:, 0]] - co[pairs[:, 1]], axis=1).max()))
        if len(body) and hands:
            B = co[body]; sp = arm.matrix_world @ arm.pose.bones["Spine1"].head if "Spine1" in arm.pose.bones else None
            for h in hands:
                if sp is None: break
                p = arm.matrix_world @ arm.pose.bones[h].tail
                sl = B[np.abs(B[:, 2] - p.z) < 0.05]
                if len(sl) < 20: continue
                d = np.array([p.x - sp.x, p.y - sp.y]); L = np.linalg.norm(d)
                if L < 1e-4: inside[h] += 1; continue
                u = d / L; rel = sl[:, :2] - np.array([sp.x, sp.y])
                along = rel @ u; across = np.abs(rel @ np.array([-u[1], u[0]]))
                wedge = along[(across < 0.06) & (along > 0)]
                if len(wedge) and L < np.percentile(wedge, 90) - 0.02: inside[h] += 1
        n += 1
    print("CLIP_CHECK seams %d seam twins, widest gap while playing %.2f mm%s" % (
        len(pairs), worst_gap * 1000, "  <- CRACKS" if worst_gap > 0.001 else ""))
    bad |= worst_gap > 0.001
    for h in hands:
        print("CLIP_CHECK hands %s inside the body in %d of %d sampled frames (%.0f%%)" % (h, inside[h], n, 100.0 * inside[h] / max(n, 1)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]))

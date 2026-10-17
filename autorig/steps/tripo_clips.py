# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: clips for a Tripo-rigged animal, on Tripo's own skeleton and skin (Tornado Titans' farm animals,
# 2026-10-17).
#
# Tripo's animals are thousands of fused fragments, so re-skinning them (rig.json kind "tripo") tears them apart while
# Tripo's own weights hold. This keys idle, walk, trot and gallop on its leg chains (<n>_Left_Limb_0.., the front legs
# the pair nearer the head; two legs make a biped) and writes one FBX an engine imports as a Generic rig: metres, foot
# on the ground, facing -Y in Blender (+Z in Unity), the mesh cut to -triangles, its colour and normal maps beside it
# in <name>.fbm/. The GLB is mended first (core/tripo_glb.py: NaN numbers, a skeleton left at the origin).
#
#   blender -b --python autorig/steps/tripo_clips.py -- --in Dog_rigged.glb --name Dog --height 0.62 --out <folder>
#           [--triangles 15000] [--front -y|+y|+x|-x]
#   python autorig/cli/run.py tripo-clips <the same arguments>
#
# Prints one TRIPO_CLIPS line of JSON.
import argparse, json, math, os, struct, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import tripo_glb

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--height", type=float, required=True, help="standing height in metres")
    p.add_argument("--out", required=True)
    p.add_argument("--triangles", type=int, default=15000)
    p.add_argument("--front", default="-y", choices=["-y", "+y", "+x", "-x"])
    return p.parse_args(sys.argv[sys.argv.index("--") + 1:])


def image_for(socket, seen=None):
    seen = set() if seen is None else seen
    for link in socket.links:
        node = link.from_node
        if node in seen:
            continue
        seen.add(node)
        if node.type == "TEX_IMAGE":
            return node.image
        for child in node.inputs:
            found = image_for(child, seen)
            if found:
                return found
    return None


def save_maps(name, folder):
    os.makedirs(folder, exist_ok=True)
    for m in bpy.data.materials:
        if not m.node_tree:
            continue
        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        maps = {}
        for suffix, socket in (("basecolor", "Base Color"), ("normal", "Normal")):
            img = image_for(bsdf.inputs[socket])
            if img is not None:
                img.filepath_raw = os.path.join(folder, "%s_%s.png" % (name, suffix))
                img.file_format = "PNG"
                img.save()
                maps[suffix] = img
        if "basecolor" not in maps:
            continue
        nodes, links = m.node_tree.nodes, m.node_tree.links
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        b = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(b.outputs["BSDF"], out.inputs["Surface"])
        t = nodes.new("ShaderNodeTexImage"); t.image = maps["basecolor"]
        links.new(t.outputs["Color"], b.inputs["Base Color"])
        if "normal" in maps:
            tn = nodes.new("ShaderNodeTexImage"); tn.image = maps["normal"]
            nm = nodes.new("ShaderNodeNormalMap")
            links.new(tn.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs["Normal"], b.inputs["Normal"])


def chains(arm):
    """Leg chains by their first bone: {first: [bones down the chain]}, from Tripo's <n>_<Side>_Limb_<k> names."""
    out = {}
    for b in arm.data.bones:
        n = b.name.split("::")[-1]
        if "_Limb_" in n and n.endswith("_Limb_0"):
            c, x = [b.name], b
            while True:
                kids = [k for k in x.children if "_Limb_" in k.name]
                if not kids:
                    break
                x = kids[0]
                c.append(x.name)
            out[b.name] = c
    return out


def main():
    a = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=tripo_glb.mend(os.path.abspath(a.src)))
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    # the skinned meshes only: a Tripo export can carry a stray unskinned helper (the cat has an Icosphere)
    for o in [o for o in bpy.context.scene.objects if o.type == "MESH" and not any(md.type == "ARMATURE" for md in o.modifiers)]:
        bpy.data.objects.remove(o, do_unlink=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    # a vertex Tripo left at NaN poisons every bound and matrix after it: drop it
    for m in meshes:
        bm = bmesh.new()
        bm.from_mesh(m.data)
        bad = [v for v in bm.verts if any(c != c or abs(c) > 1e6 for c in v.co)]
        if bad:
            bmesh.ops.delete(bm, geom=bad, context="VERTS")
            bm.to_mesh(m.data)
            print("tripo_clips.py: %d bad vertices dropped from %s" % (len(bad), m.name))
        bm.free()
    for act in list(bpy.data.actions):
        bpy.data.actions.remove(act)
    save_maps(a.name, os.path.join(a.out, a.name + ".fbm"))

    # cut to its budget, every mesh by the same share (the decimator keeps the weights)
    total = 0
    for m in meshes:
        m.data.calc_loop_triangles()
        total += len(m.data.loop_triangles)
    if total > a.triangles:
        for m in meshes:
            if m.data.shape_keys:
                m.shape_key_clear()   # a decimator cannot apply over shape keys, and these animals need none
            bpy.context.view_layer.objects.active = m
            d = m.modifiers.new("budget", "DECIMATE")
            d.ratio = a.triangles / float(total)
            d.use_collapse_triangulate = True
            bpy.ops.object.modifier_move_to_index(modifier=d.name, index=0)   # before the armature
            bpy.ops.object.modifier_apply(modifier=d.name)
            print("tripo_clips.py: %s cut to %d faces" % (m.name, len(m.data.polygons)))

    # the decimator can leave a texture coordinate NaN, which the FBX exporter refuses
    for m in meshes:
        for uv in m.data.uv_layers:
            for d in uv.data:
                u, v = d.uv
                if u != u or v != v:
                    d.uv = (0.0 if u != u else u, 0.0 if v != v else v)

    # turn to face -Y, scale to its height, feet on the ground: on the armature, which carries the mesh
    turns = {"-y": 0.0, "+y": math.pi, "+x": -math.pi / 2, "-x": math.pi / 2}
    pts = [m.matrix_world @ Vector(c) for m in meshes for c in m.bound_box]
    h = max(p.z for p in pts) - min(p.z for p in pts)
    s = a.height / max(h, 1e-6)
    arm.matrix_world = Matrix.Scale(s, 4) @ Matrix.Rotation(turns[a.front], 4, "Z") @ arm.matrix_world
    bpy.context.view_layer.update()
    pts = [m.matrix_world @ Vector(c) for m in meshes for c in m.bound_box]
    lo = Vector((sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts), min(p.z for p in pts)))
    arm.matrix_world = Matrix.Translation(Vector((-lo.x, -lo.y, -lo.z))) @ arm.matrix_world
    bpy.context.view_layer.update()
    for o in [arm] + meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # the pose it arrives in is its standing pose (for some, the importer's rest is a guess lying on its side):
    # every clip is keyed on top of it
    base = {}
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        base[pb.name] = (pb.rotation_quaternion.copy(), pb.location.copy())
    legs = chains(arm)
    if not legs:
        raise SystemExit("tripo_clips.py: no Tripo leg chains in " + a.src)
    heady = {k: (arm.data.bones[k].head_local.y) for k in legs}
    mid = sum(heady.values()) / len(heady)
    # front legs: the ones nearer the head (-Y); left: +X
    front = {k: heady[k] < mid for k in legs}
    left = {k: arm.data.bones[k].head_local.x > 0 for k in legs}
    biped = len(legs) <= 2
    spine = [b.name for b in arm.data.bones if "Spine_" in b.name]
    head = [b.name for b in arm.data.bones if "Head_" in b.name]
    tail = [b.name for b in arm.data.bones if "Tail_" in b.name]

    def turn(pb, axis, deg):
        """Turn a pose bone about a world axis (armature space), as if from its rest."""
        rest = pb.bone.matrix_local.to_3x3()
        q = Quaternion(axis, math.radians(deg))
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (rest.inverted() @ q.to_matrix() @ rest).to_quaternion() @ pb.rotation_quaternion

    X, Z = Vector((1, 0, 0)), Vector((0, 0, 1))
    # leg phase in a loop (0..1) for each gait: walk is the four-beat lateral sequence, trot the diagonal pairs,
    # gallop the pairs nearly together; a biped steps left and right
    def phase(gait, k):
        if biped:
            return 0.0 if left[k] else 0.5
        f, l = front[k], left[k]
        if gait == "walk":
            return {(False, True): 0.0, (True, True): 0.25, (False, False): 0.5, (True, False): 0.75}[(f, l)]
        if gait == "trot":
            return 0.0 if f == l else 0.5
        return (0.0 if f else 0.5) + (0.0 if l else 0.08)

    gaits = {
        # frames, hip swing, knee lift, body bob (m per metre of height), head nod, tail sway
        "idle": (48, 0.0, 0.0, 0.004, 4.0, 6.0),
        "walk": (24, 18.0, 22.0, 0.012, 5.0, 8.0),
        "trot": (16, 26.0, 34.0, 0.025, 4.0, 10.0),
        "gallop": (12, 38.0, 48.0, 0.045, 8.0, 14.0),
    }
    scene = bpy.context.scene
    scene.render.fps = 24
    arm.animation_data_create()
    root = arm.pose.bones[spine[0]] if spine else None
    for gait, (frames, swing, knee, bob, nod, sway) in gaits.items():
        act = bpy.data.actions.new(gait)
        arm.animation_data.action = act
        for f in range(frames + 1):
            t = f / float(frames)
            for pb in arm.pose.bones:
                pb.rotation_mode = "QUATERNION"
                pb.rotation_quaternion = base[pb.name][0].copy()
                pb.location = base[pb.name][1].copy()
            for k, chain in legs.items():
                p = (t + phase(gait, k)) * 2 * math.pi
                hip = swing * math.sin(p) * (1.0 if front[k] or biped else -1.0)
                lift = max(0.0, math.cos(p)) * knee
                pbs = [arm.pose.bones[n] for n in chain]
                turn(pbs[0], X, hip)
                if len(pbs) > 1:
                    turn(pbs[1], X, lift if front[k] else -lift)
                if len(pbs) > 2:
                    turn(pbs[2], X, -lift * 0.6 if front[k] else lift * 0.6)
            if gait == "idle":
                breath = math.sin(t * 2 * math.pi)
                for n in spine[:2]:
                    turn(arm.pose.bones[n], X, breath * 1.2)
            if head:
                turn(arm.pose.bones[head[0]], X, nod * math.sin(t * 4 * math.pi if gait != "idle" else t * 2 * math.pi))
            for i, n in enumerate(tail):
                turn(arm.pose.bones[n], Z, sway * math.sin(t * 2 * math.pi - i * 0.6))
            if root is not None:
                root.location = base[root.name][1] + root.bone.matrix_local.to_3x3().inverted() @ Vector((0, 0, a.height * bob * math.cos(t * 4 * math.pi)))
            for pb in arm.pose.bones:
                pb.keyframe_insert("rotation_quaternion", frame=f + 1)
                pb.keyframe_insert("location", frame=f + 1)
        track = arm.animation_data.nla_tracks.new()
        track.name = gait
        track.strips.new(gait, 1, act)
        arm.animation_data.action = None

    os.makedirs(a.out, exist_ok=True)
    target = os.path.join(a.out, a.name + ".fbx")
    bpy.ops.object.select_all(action="DESELECT")
    for o in [arm] + meshes:
        o.select_set(True)
    arm.name = a.name
    bpy.ops.export_scene.fbx(filepath=target, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                             axis_forward="-Z", axis_up="Y", bake_space_transform=True, mesh_smooth_type="FACE",
                             add_leaf_bones=False, bake_anim=True, bake_anim_use_nla_strips=True,
                             bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
                             path_mode="COPY", embed_textures=False)
    tris = sum(len(m.data.polygons) for m in meshes)
    print("TRIPO_CLIPS " + json.dumps({"name": a.name, "legs": len(legs), "plan": "biped" if biped else "quadruped",
                                       "faces": tris, "clips": list(gaits), "fbx": target}))


if __name__ == "__main__":
    main()

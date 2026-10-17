# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: test helper run under Blender, for test_unity_fbx.py.
#
#   blender -b --python tests/unity_fbx_probe.py -- make <out.fbx>
#       a small skinned creature facing -Y (its nose is the only vertex below y = -0.5), two clips as NLA strips,
#       written with the clips step's FBX settings (-Z forward, Y up, bake_space_transform)
#   blender -b --python tests/unity_fbx_probe.py -- sample <in.fbx> <out.json>
#       imports an FBX and writes, per take, the world position of every bone's head and tail and of the deformed
#       mesh's nose at a few frames: two files that sample the same are the same model and motion
import json, math, os, sys

import bpy


def make(out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("critter_rig", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    root = arm.edit_bones.new("root"); root.head = (0, 0, 0); root.tail = (0, 0, 0.3)
    body = arm.edit_bones.new("body"); body.head = (0, 0, 0.3); body.tail = (0, -0.4, 0.5); body.parent = root
    head = arm.edit_bones.new("head"); head.head = (0, -0.4, 0.5); head.tail = (0, -0.7, 0.6); head.parent = body
    bpy.ops.object.mode_set(mode='OBJECT')
    # a wedge pointing down -Y: the nose vertex is the head's
    verts = [(-0.2, 0.2, 0.0), (0.2, 0.2, 0.0), (0.2, 0.2, 0.5), (-0.2, 0.2, 0.5), (0.0, -0.8, 0.55)]
    faces = [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    me = bpy.data.meshes.new("critter")
    me.from_pydata(verts, [], faces)
    mesh = bpy.data.objects.new("critter", me)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = rig
    for name, idx in (("root", [0, 1]), ("body", [2, 3]), ("head", [4])):
        mesh.vertex_groups.new(name=name).add(idx, 1.0, 'REPLACE')
    mesh.modifiers.new("Armature", 'ARMATURE').object = rig
    rig.animation_data_create()
    for clip, (axis, angle, lift) in {"nod": (0, 30, 0.0), "hop": (2, 45, 0.25)}.items():
        act = bpy.data.actions.new(clip)
        rig.animation_data.action = act
        for f, k in ((1, 0.0), (10, 1.0), (20, 0.0)):
            pb = rig.pose.bones["head"]; pb.rotation_mode = 'XYZ'
            e = [0.0, 0.0, 0.0]; e[axis] = math.radians(angle) * k
            pb.rotation_euler = e; pb.keyframe_insert("rotation_euler", frame=f)
            r = rig.pose.bones["root"]; r.location = (0, lift * k, 0); r.keyframe_insert("location", frame=f)
        rig.animation_data.action = None
        track = rig.animation_data.nla_tracks.new(); track.name = clip
        track.strips.new(clip, 1, act).name = clip
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.fbx(filepath=out, use_selection=True, object_types={'ARMATURE', 'MESH'},
                             apply_scale_options='FBX_SCALE_UNITS', bake_space_transform=True, axis_forward='-Z',
                             axis_up='Y', add_leaf_bones=False, use_armature_deform_only=True, bake_anim=True,
                             bake_anim_use_all_actions=False, bake_anim_use_nla_strips=True,
                             bake_anim_force_startend_keying=True, mesh_smooth_type='OFF')


def sample(src, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src, ignore_leaf_bones=False)
    rig = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    mesh = next(o for o in bpy.data.objects if o.type == 'MESH')
    acts = sorted([a for a in bpy.data.actions if a.name.startswith(rig.name + "|")], key=lambda a: a.name)
    res = {}
    for act in acts or [None]:
        rig.animation_data.action = act
        if act is not None and getattr(act, "slots", None):
            rig.animation_data.action_slot = act.slots[0]
        take = act.name.split("|", 1)[1] if act else "rest"
        frames = {}
        for f in (1, 5, 10, 15, 20):
            bpy.context.scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            ev = mesh.evaluated_get(dg)
            pts = [ev.matrix_world @ v.co for v in ev.to_mesh().vertices]
            nose = min(pts, key=lambda p: p.y)
            bones = {b.name: [list(rig.matrix_world @ b.head), list(rig.matrix_world @ b.tail)] for b in rig.pose.bones}
            frames[str(f)] = {"nose": list(nose), "bones": bones}
            ev.to_mesh_clear()
        res[take] = frames
    with open(out, "w") as fh: json.dump(res, fh)


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    if a[0] == "make": make(a[1])
    elif a[0] == "sample": sample(a[1], a[2])

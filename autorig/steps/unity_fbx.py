# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: rewrites an FBX so Unity imports every root object with rotation (0,0,0) and scale 1.
#
#   blender -b --python autorig/steps/unity_fbx.py -- <in.fbx> <out.fbx>
#
# The rig, decimate and clips steps write FBX for every engine (-Z forward, Y up, bake_space_transform). Blender's
# space transform is only baked into meshes: an armature keeps it as its own rotation, so in Unity the rig's root
# shows -90 on X (and a Studio engine FBX written with FBX_SCALE_ALL shows a unit scale Unity turns into 0.01).
# This step imports the FBX into an empty scene and writes it again the way EdyJ's "Unity FBX format" exporter does
# (blender-to-unity-fbx-exporter, MIT, (c) Angel Garcia: https://github.com/EdyJ/blender-to-unity-fbx-exporter):
#   1. every root object's rotation is applied, then a -90 X rotation is applied to its data and the object is
#      given +90 X back (fix_object below, from that exporter), so the world pose is unchanged;
#   2. the FBX exporter's own Z-up to Y-up conversion (-90 X) then cancels that +90 X: the roots are written with
#      rotation 0, and FBX_SCALE_UNITS writes metres as UnitScaleFactor 100 with root scale 1.
# Takes come back as one NLA strip each, named as before, and are written with the clips step's settings. Forward
# stays Blender's: a model facing -Y faces Unity's +Z.
#
# Prints UNITY_FBX {json} with the roots and takes written. core/fbx_inspect.py reads the result back as Unity would.
import json, math, os, sys

import bpy
import mathutils


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(a) != 2: sys.exit("usage: blender -b --python unity_fbx.py -- <in.fbx> <out.fbx>")
    return a


def reset_parent_inverse(ob):
    if ob.parent:
        world = ob.matrix_world.copy()
        ob.matrix_parent_inverse.identity()
        ob.matrix_basis = ob.parent.matrix_world.inverted() @ world


def apply_rotation(ob):
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


def fix_object(ob):
    """EdyJ's fix: a pure -90 X applied to the data, the object's own transform kept with +90 X after it."""
    reset_parent_inverse(ob)
    original = ob.matrix_local.copy()
    ob.matrix_local = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')
    apply_rotation(ob)
    ob.matrix_local = original @ mathutils.Matrix.Rotation(math.radians(90.0), 4, 'X')
    for child in ob.children:
        fix_object(child)


def takes_to_strips():
    """The importer gives each take its own action, "<object>|<take>", and leaves the last one active. Each becomes
    an NLA strip named as the take, the way make_clips.py lays them out for the exporter."""
    takes = []
    for ob in bpy.data.objects:
        if ob.type != 'ARMATURE' and ob.type != 'EMPTY':
            continue
        acts = [a for a in bpy.data.actions if a.name.split("|")[0] == ob.name and "|" in a.name]
        if not acts:
            continue
        ad = ob.animation_data or ob.animation_data_create()
        ad.action = None
        for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)
        for act in acts:
            name = act.name.split("|", 1)[1]
            track = ad.nla_tracks.new()
            track.name = name
            start = act.frame_range[0]
            strip = track.strips.new(name, int(start), act)
            strip.name = name
            takes.append(name)
    return takes


def depth(ob):
    return 0 if ob.parent is None else 1 + depth(ob.parent)


def channelbags(action):
    """Every F-curve container of a layered action (Blender 4.4+ slots; 5.x has no action.fcurves)."""
    for layer in action.layers:
        for strip in layer.strips:
            yield from strip.channelbags


def drop_object_motion():
    """The FBX exporter keys each take's objects too, so the importer brings the rig object's own transform back as
    keys: the old -90 X among them, which would override the fix in every take. Object-level transform curves are
    removed (bones keep theirs, root motion included). Returns the objects whose transform really moved."""
    moved = set()
    for act in bpy.data.actions:
        for cb in channelbags(act):
            for fc in [f for f in cb.fcurves if f.data_path in ("location", "rotation_euler", "rotation_quaternion",
                                                                 "rotation_axis_angle", "scale")]:
                vals = [k.co[1] for k in fc.keyframe_points]
                if vals and max(vals) - min(vals) > 1e-4: moved.add(act.name.split("|")[0])
                cb.fcurves.remove(fc)
    return sorted(moved)


def main():
    src, out = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=os.path.abspath(src), ignore_leaf_bones=False, use_anim=True)
    if bpy.ops.object.mode_set.poll(): bpy.ops.object.mode_set(mode='OBJECT')
    for ob in bpy.data.objects:                           # all visible, single-user data: transform_apply needs both
        ob.hide_set(False); ob.hide_viewport = False
        if ob.data is not None and ob.data.users > 1: ob.data = ob.data.copy()
    # Shape-key takes stay out, as in the clips step (each key's neutral value is written).
    for key in bpy.data.shape_keys:
        if key.animation_data: key.animation_data.action = None
        for kb in key.key_blocks[1:]: kb.value = 0.0
    takes = takes_to_strips()
    moved = drop_object_motion()
    roots = [o for o in bpy.data.objects if o.parent is None and o.type in ('EMPTY', 'MESH', 'ARMATURE')]
    # The importer leaves its Y-up to Z-up conversion on objects as rotations (on the rig, and on a skinned mesh it
    # parents to the rig); fold them in first, parents before children, so every matrix is a translation (and
    # scale) and EdyJ's +90 X is all the exporter has to cancel.
    for ob in sorted(bpy.data.objects, key=depth):
        if ob.matrix_basis.to_quaternion().angle > 1e-6:
            apply_rotation(ob)
    for ob in roots:
        fix_object(ob)
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    for ob in bpy.data.objects: ob.select_set(True)
    animated = bool(takes)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=os.path.abspath(out), use_selection=True, object_types={'EMPTY', 'MESH', 'ARMATURE'},
        apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS', bake_space_transform=False,
        axis_forward='-Z', axis_up='Y', use_custom_props=True, add_leaf_bones=False,
        primary_bone_axis='Y', secondary_bone_axis='X', use_armature_deform_only=False,
        bake_anim=animated, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=animated,
        bake_anim_use_all_bones=True, bake_anim_force_startend_keying=True, bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0, mesh_smooth_type='OFF', path_mode='STRIP', embed_textures=False)
    print("UNITY_FBX " + json.dumps({"in": src, "out": out, "roots": [o.name for o in roots], "takes": takes,
                                    "object_motion_dropped": moved}))


if __name__ == "__main__":
    main()

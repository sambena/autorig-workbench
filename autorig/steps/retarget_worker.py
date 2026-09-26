# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Blender headless worker for mocap retargeting and action baking.

import json
import math
import os
import shutil
import sys
import bpy
from mathutils import Matrix, Vector, Quaternion

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]
from placed_rules import bone_side


def action_fcurves(action):
    """Every F-curve of an action: the action's own list, or (slotted actions, Blender 4.4+) its layers'
    channelbags (as preview_glb.py reads them)."""
    try:
        if len(action.fcurves):
            return list(action.fcurves)
    except AttributeError:
        pass
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out += list(bag.fcurves)
    return out


def topological_sort_bones(arm, bone_names):
    """Sorts bone names so every listed ancestor comes before its descendants, through unlisted bones between them
    (a source with no Spine1 leaves the chest's mapped grandparent behind an unmapped parent)."""
    dbones = arm.data.bones
    listed = set(bone_names)
    order = []
    visited = set()

    def visit(bname):
        if bname in visited:
            return
        b = dbones.get(bname)
        up = b.parent if b else None
        while up is not None and up.name not in listed:
            up = up.parent
        if up is not None:
            visit(up.name)
        visited.add(bname)
        order.append(bname)

    for name in bone_names:
        visit(name)
    return order


def main(argv=None):
    args = argv or sys.argv
    config_idx = -1
    for i, a in enumerate(args):
        if a == "--":
            config_idx = i + 1
            break
    if config_idx == -1 or config_idx >= len(args):
        config_str = args[-1]
    else:
        config_str = args[config_idx]

    config = json.loads(config_str)
    target_blend = os.path.abspath(config["target_blend"])
    source_file = os.path.abspath(config["source_file"])
    clip_name = config.get("clip_name", "retargeted_clip")
    root_motion = bool(config.get("root_motion", True))
    root_pair = config.get("root_pair")
    scale_proportions = bool(config.get("scale_proportions", True))
    solve_offsets = bool(config.get("solve_offsets", True))
    mapping = config.get("mapping", {})
    export_glb = bool(config.get("export_glb", False))
    preview = bool(config.get("preview", False))
    frame_range_override = config.get("frame_range")
    fps_override = config.get("fps")
    out_json = config.get("out_json")

    print(f"RETARGET_WORKER: Opening target blend '{target_blend}'")
    bpy.ops.wm.open_mainfile(filepath=target_blend)

    tgt_arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if not tgt_arm:
        raise ValueError(f"No target armature found in '{target_blend}'")

    print(f"RETARGET_WORKER: Importing source motion '{source_file}'")
    # everything the import brings in (the source armature, any skinned mesh a "with skin" FBX carries, empties,
    # its actions) is removed again before the rig's file is saved
    before_objs = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    ext = os.path.splitext(source_file)[1].lower()
    if ext == ".bvh":
        bpy.ops.import_anim.bvh(filepath=source_file, use_fps_scale=False,
                                update_scene_fps=False, update_scene_duration=True)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=source_file, use_anim=True)
    else:
        raise ValueError(f"Unsupported source format: {ext}")

    src_arm = next((o for o in bpy.data.objects if o != tgt_arm and o.type == "ARMATURE"), None)
    if not src_arm:
        raise ValueError("Failed to locate imported source armature")

    # Determine frame range and FPS
    fps = float(config.get("source_fps") or bpy.context.scene.render.fps or 30)   # the source's; --fps resamples below
    frame_start = 1
    frame_end = 30

    source_action_name = config.get("source_action")
    if source_action_name and bpy.data.actions:
        matched_act = bpy.data.actions.get(source_action_name)
        if not matched_act:
            matched_act = next((a for a in bpy.data.actions if a.name.lower() == source_action_name.lower() or
                                a.name.endswith("|" + source_action_name) or
                                a.name.endswith("/" + source_action_name)), None)
        if matched_act:
            if not src_arm.animation_data:
                src_arm.animation_data_create()
            src_arm.animation_data.action = matched_act
            frame_start = int(matched_act.frame_range[0])
            frame_end = int(matched_act.frame_range[1])
            print(f"RETARGET_WORKER: Bound source action '{matched_act.name}' ({frame_start}..{frame_end})")
    elif src_arm.animation_data and src_arm.animation_data.action:
        src_action = src_arm.animation_data.action
        frame_start = int(src_action.frame_range[0])
        frame_end = int(src_action.frame_range[1])
    elif bpy.data.actions:
        # Check any imported actions
        for act in bpy.data.actions:
            if act.frame_range[1] > act.frame_range[0]:
                frame_start = int(act.frame_range[0])
                frame_end = int(act.frame_range[1])
                break
    else:
        frame_start = int(bpy.context.scene.frame_start)
        frame_end = int(bpy.context.scene.frame_end)

    if frame_range_override and isinstance(frame_range_override, (list, tuple)) and len(frame_range_override) == 2:
        frame_start = max(frame_start, int(frame_range_override[0]))
        frame_end = min(frame_end, int(frame_range_override[1]))

    total_frames = max(1, frame_end - frame_start + 1)
    print(f"RETARGET_WORKER: Source frames {frame_start}..{frame_end} ({total_frames} frames @ {fps:g} fps)")

    # Height proportion scaling
    src_heads = [src_arm.matrix_world @ b.head_local for b in src_arm.data.bones]
    tgt_heads = [tgt_arm.matrix_world @ b.head_local for b in tgt_arm.data.bones]
    h_src = max(v.z for v in src_heads) - min(v.z for v in src_heads) if src_heads else 1.0
    h_tgt = max(v.z for v in tgt_heads) - min(v.z for v in tgt_heads) if tgt_heads else 1.0
    scale_factor = (h_tgt / h_src) if (scale_proportions and h_src > 1e-4) else 1.0
    print(f"RETARGET_WORKER: Scale factor = {scale_factor:.3f} (src_h={h_src:.2f}, tgt_h={h_tgt:.2f})")

    # Filter valid mapped pairs
    valid_pairs = []
    for sb, tb in mapping.items():
        if sb in src_arm.pose.bones and tb in tgt_arm.pose.bones:
            valid_pairs.append((sb, tb))

    # Sort target bones topologically
    tgt_bone_names = [tb for _, tb in valid_pairs]
    sorted_tgt_names = topological_sort_bones(tgt_arm, tgt_bone_names)
    sorted_pairs = []
    for tb in sorted_tgt_names:
        for sb, mapped_tb in valid_pairs:
            if mapped_tb == tb:
                sorted_pairs.append((sb, tb))
                break

    # Cache rest matrices & rest locations
    src_rest_r = {}
    src_rest_l = {}
    for sb, _ in sorted_pairs:
        db = src_arm.data.bones[sb]
        src_rest_r[sb] = (src_arm.matrix_world.to_3x3() @ db.matrix_local.to_3x3())
        src_rest_l[sb] = (src_arm.matrix_world @ db.matrix_local.translation)

    tgt_rest_r = {}
    tgt_rest_l = {}
    for _, tb in sorted_pairs:
        db = tgt_arm.data.bones[tb]
        tgt_rest_r[tb] = (tgt_arm.matrix_world.to_3x3() @ db.matrix_local.to_3x3())
        tgt_rest_l[tb] = (tgt_arm.matrix_world @ db.matrix_local.translation)

    tgt_w_inv = tgt_arm.matrix_world.to_3x3().inverted()

    # Rest alignment, per mapped bone: the world rotation that turns the source bone's rest direction onto the
    # target's (swing only). A T-posed source driving an A-posed target differs by ~45 degrees at the shoulders; the
    # source's motion is applied on top of this, so the target bone points where the source bone points.
    # Limbs only (bones with a side: arms, legs, fingers, wings), within 90 degrees. The root and torso are left as
    # they were: their rest directions differ by rig convention, not by pose (Mixamo's Hips point up, this tool's
    # root lies along -Y), and turning one of those would tip the whole body. Past 90 degrees, and at 180 where the
    # turn's axis is arbitrary, it is a different convention too, not a T-pose against an A-pose.
    align_inv = {}
    for sb, tb in sorted_pairs:
        s_dir = (src_rest_r[sb] @ Vector((0, 1, 0))).normalized()
        t_dir = (tgt_rest_r[tb] @ Vector((0, 1, 0))).normalized()
        limb = bone_side(tb) != "" and not (root_pair and tb == root_pair[1])
        align_inv[tb] = (s_dir.rotation_difference(t_dir).inverted().to_matrix()
                         if solve_offsets and limb and s_dir.dot(t_dir) > 0.0 else Matrix.Identity(3))

    # IK and copy-rotation constraints on the target would drive its legs from their (still) controls over the keys
    # this bakes: silenced while baking, and keyed off (influence 0) in the clip itself, so it plays as baked;
    # make_clips keys them back on in its own clips
    silenced = []
    for pb in tgt_arm.pose.bones:
        for c in pb.constraints:
            if c.type in ("IK", "COPY_ROTATION", "DAMPED_TRACK", "LOCKED_TRACK", "TRACK_TO"):
                silenced.append((pb, c, c.mute))
                c.mute = True

    tgt_rest3 = {b.name: b.matrix_local.to_3x3() for b in tgt_arm.data.bones}

    def orient(bname, cur):
        """Armature-space rotation of a target bone this frame: baked if mapped, else its rest relation to its
        parent's (unmapped bones are not keyed): never the pose's own matrix, which is still last frame's."""
        if bname in cur:
            return cur[bname]
        b = tgt_arm.data.bones[bname]
        r = tgt_rest3[bname] if b.parent is None else \
            orient(b.parent.name, cur) @ (tgt_rest3[b.parent.name].inverted() @ tgt_rest3[bname])
        cur[bname] = r
        return r

    # Frames to bake: every source frame, or resampled to --fps (the output frame k shows the source at time k/fps)
    src_fps = float(config.get("source_fps") or bpy.context.scene.render.fps or 30)
    out_fps = float(fps_override or src_fps)
    span_s = (frame_end - frame_start) / src_fps
    n_out = max(1, int(round(span_s * out_fps)) + 1)
    bpy.context.scene.render.fps = max(1, int(round(out_fps)))
    bpy.context.scene.render.fps_base = bpy.context.scene.render.fps / out_fps
    fps = out_fps
    total_frames = n_out

    # Create target action
    if not tgt_arm.animation_data:
        tgt_arm.animation_data_create()
    old = bpy.data.actions.get(clip_name)
    if old is not None and old not in before_actions:
        old = None
    if old is not None:                      # re-retargeting a clip replaces it
        bpy.data.actions.remove(old)
    new_action = bpy.data.actions.new(name=clip_name)
    new_action.use_fake_user = True
    new_action["autorig_retarget"] = True    # make_clips.py keeps and exports tagged actions on a rebake
    new_action["autorig_retarget_source"] = os.path.basename(source_file)
    tgt_arm.animation_data.action = new_action

    scene = bpy.context.scene

    cur_arm_r = {}
    # Process all frames (only the source moves: the target's pose is computed here, not read back)
    for k in range(n_out):
        src_t = frame_start + k * src_fps / out_fps
        scene.frame_set(int(math.floor(src_t)), subframe=src_t - math.floor(src_t))
        f = frame_start + k                      # keyed here, shifted to start at 0 afterwards
        cur_arm_r.clear()

        for sb, tb in sorted_pairs:
            src_pb = src_arm.pose.bones[sb]
            tgt_pb = tgt_arm.pose.bones[tb]
            db = tgt_arm.data.bones[tb]

            # Source world rotation, and its change from the source's rest (in world space: roll-independent)
            r_src_w = src_arm.matrix_world.to_3x3() @ src_pb.matrix.to_3x3()
            delta_r = r_src_w @ src_rest_r[sb].inverted()
            # the same change, applied to the target's rest after aligning the rests (align_inv)
            r_tgt_w = delta_r @ align_inv[tb] @ tgt_rest_r[tb]

            # Convert to target armature local coordinates
            r_tgt_local = tgt_w_inv @ r_tgt_w
            cur_arm_r[tb] = r_tgt_local

            # Decompose into parent-relative basis matrix, against the parent as computed this frame
            if tgt_pb.parent is None:
                mat_basis = db.matrix_local.to_3x3().inverted() @ r_tgt_local
            else:
                p_name = tgt_pb.parent.name
                rel_rest_r = tgt_rest3[p_name].inverted() @ tgt_rest3[tb]
                p_frame = orient(p_name, cur_arm_r) @ rel_rest_r
                mat_basis = p_frame.inverted() @ r_tgt_local

            tgt_pb.rotation_mode = "QUATERNION"
            tgt_pb.rotation_quaternion = mat_basis.to_quaternion()
            tgt_pb.keyframe_insert("rotation_quaternion", frame=f)

            # Root motion translation
            is_root = root_motion and root_pair and (sb == root_pair[0] and tb == root_pair[1])
            if is_root:
                pos_src_w = src_arm.matrix_world @ src_pb.matrix.translation
                delta_p_src = pos_src_w - src_rest_l[sb]
                delta_p_scaled = delta_p_src * scale_factor
                # Local displacement relative to rest
                delta_p_local = tgt_w_inv @ delta_p_scaled
                tgt_pb.location = db.matrix_local.to_3x3().inverted() @ delta_p_local
                tgt_pb.keyframe_insert("location", frame=f)

    # the constraints come back for every other clip; this one keys them off at its first and last frame
    for pb, c, was in silenced:
        c.mute = was
        c.influence = 0.0
        for fr in (frame_start, frame_start + n_out - 1):
            c.keyframe_insert("influence", frame=fr)
        c.influence = 1.0
    src_range = [frame_start, frame_end]     # reported beside the output's own 0..n_out-1
    frame_end = frame_start + n_out - 1

    # Clean up everything the import brought in: the rig's file keeps only its own objects and the new clip
    for o in [o for o in bpy.data.objects if o not in before_objs]:
        bpy.data.objects.remove(o, do_unlink=True)
    for a in [a for a in bpy.data.actions if a not in before_actions and a != new_action]:
        bpy.data.actions.remove(a)
    new_action.name = clip_name              # it came out "<clip>.001" if an imported take had the same name
    # keyed at the source's own frames while baking (so every bone evaluated at a frame sees that frame's keys);
    # the finished clip starts at frame 0 like every authored clip
    if frame_start:
        for fc in action_fcurves(new_action):
            for kp in fc.keyframe_points:
                kp.co.x -= frame_start
                kp.handle_left.x -= frame_start
                kp.handle_right.x -= frame_start
            fc.update()
    new_action.use_frame_range = True
    new_action.frame_start, new_action.frame_end = 0, frame_end - frame_start
    tgt_arm.animation_data.action = None     # the rest pose stays the bind pose; the clip is kept by its fake user

    scene.frame_set(0)
    bpy.context.view_layer.update()

    # Save target blend
    print(f"RETARGET_WORKER: Saving updated blend '{target_blend}'")
    bpy.ops.wm.save_mainfile(filepath=target_blend)

    # preview.glb is rewritten by the preview step (preview_glb.py), which the retargeter runs after this when asked:
    # it picks the new action up from this file with the viewer's own export settings.
    glb_file = None
    if export_glb:
        glb_file = os.path.splitext(target_blend)[0] + f"_{clip_name}.glb"
        print(f"RETARGET_WORKER: Exporting clip GLB '{glb_file}'")
        tgt_arm.animation_data.action = new_action
        bpy.ops.export_scene.gltf(filepath=glb_file, export_format="GLB", export_animations=True,
                                  export_animation_mode="ACTIVE_ACTIONS")
        tgt_arm.animation_data.action = None

    result = {
        "status": "OK",
        "clip_name": clip_name,
        "frames": total_frames,
        "source_frames": src_range,
        "frame_start": 0,                    # the saved clip's range: it starts at 0 like every authored clip
        "frame_end": n_out - 1,
        "fps": fps,
        "duration": round(total_frames / (fps or 30), 2),
        "scale_factor": round(scale_factor, 3),
        "mapped_bones": len(sorted_pairs),
        "target_blend": target_blend,
        "export_glb": glb_file,
    }

    if out_json:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)

    print("RETARGET_WORKER: Complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

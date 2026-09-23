# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Blender headless worker for mocap retargeting and action baking.

import json
import math
import os
import sys
import bpy
from mathutils import Matrix, Vector, Quaternion

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]


def topological_sort_bones(arm, bone_names):
    """Sorts bone names so parents appear before children."""
    dbones = arm.data.bones
    order = []
    visited = set()

    def visit(bname):
        if bname in visited:
            return
        b = dbones.get(bname)
        if b and b.parent and b.parent.name in bone_names:
            visit(b.parent.name)
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
    mapping = config.get("mapping", {})
    export_glb = bool(config.get("export_glb", False))
    out_json = config.get("out_json")

    print(f"RETARGET_WORKER: Opening target blend '{target_blend}'")
    bpy.ops.wm.open_mainfile(filepath=target_blend)

    tgt_arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if not tgt_arm:
        raise ValueError(f"No target armature found in '{target_blend}'")

    print(f"RETARGET_WORKER: Importing source motion '{source_file}'")
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
    fps = int(config.get("fps") or bpy.context.scene.render.fps or 30)
    bpy.context.scene.render.fps = fps
    frame_start = 1
    frame_end = 30
    if src_arm.animation_data and src_arm.animation_data.action:
        src_action = src_arm.animation_data.action
        frame_start = int(src_action.frame_range[0])
        frame_end = int(src_action.frame_range[1])
    else:
        frame_start = int(bpy.context.scene.frame_start)
        frame_end = int(bpy.context.scene.frame_end)

    total_frames = max(1, frame_end - frame_start + 1)
    print(f"RETARGET_WORKER: Frame range {frame_start}..{frame_end} ({total_frames} frames @ {fps} fps)")

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

    # Cache rest matrices
    src_rest_inv = {}
    src_rest_loc = {}
    for sb, _ in sorted_pairs:
        db = src_arm.data.bones[sb]
        m_w = src_arm.matrix_world.to_3x3() @ db.matrix_local.to_3x3()
        src_rest_inv[sb] = m_w.inverted()
        src_rest_loc[sb] = db.matrix_local.translation.copy()

    tgt_rest_rot = {}
    tgt_rest_loc = {}
    for _, tb in sorted_pairs:
        db = tgt_arm.data.bones[tb]
        m_w = tgt_arm.matrix_world.to_3x3() @ db.matrix_local.to_3x3()
        tgt_rest_rot[tb] = m_w
        tgt_rest_loc[tb] = db.matrix_local.translation.copy()

    tgt_w_inv = tgt_arm.matrix_world.to_3x3().inverted()

    # Create target action
    if not tgt_arm.animation_data:
        tgt_arm.animation_data_create()
    new_action = bpy.data.actions.new(name=clip_name)
    new_action.use_fake_user = True
    tgt_arm.animation_data.action = new_action

    scene = bpy.context.scene

    # Process all frames
    for f in range(frame_start, frame_end + 1):
        scene.frame_set(f)
        bpy.context.view_layer.update()

        for sb, tb in sorted_pairs:
            src_pb = src_arm.pose.bones[sb]
            tgt_pb = tgt_arm.pose.bones[tb]

            # World delta rotation
            r_src_w = src_arm.matrix_world.to_3x3() @ src_pb.matrix.to_3x3()
            delta_r = r_src_w @ src_rest_inv[sb]

            # Desired target world rotation and conversion to local
            r_tgt_w = delta_r @ tgt_rest_rot[tb]
            r_tgt_local = tgt_w_inv @ r_tgt_w

            is_root = root_motion and root_pair and (sb == root_pair[0] and tb == root_pair[1])
            if is_root:
                pos_src = src_pb.matrix.translation
                delta_p = (pos_src - src_rest_loc[sb]) * scale_factor
                pos_tgt = tgt_rest_loc[tb] + delta_p
                m = Matrix.Translation(pos_tgt) @ r_tgt_local.to_4x4()
                tgt_pb.matrix = m
                tgt_pb.keyframe_insert("location", frame=f)
            else:
                pos = tgt_pb.matrix.translation
                m = Matrix.Translation(pos) @ r_tgt_local.to_4x4()
                tgt_pb.matrix = m

            tgt_pb.keyframe_insert("rotation_quaternion", frame=f)
            bpy.context.view_layer.update()

    # Clean up imported source armature
    bpy.data.objects.remove(src_arm, do_unlink=True)

    # Reset frame to 1
    scene.frame_set(frame_start)
    bpy.context.view_layer.update()

    # Save target blend
    print(f"RETARGET_WORKER: Saving updated blend '{target_blend}'")
    bpy.ops.wm.save_mainfile(filepath=target_blend)

    glb_file = None
    if export_glb:
        glb_file = os.path.splitext(target_blend)[0] + f"_{clip_name}.glb"
        print(f"RETARGET_WORKER: Exporting GLB preview '{glb_file}'")
        bpy.ops.export_scene.gltf(filepath=glb_file, export_format="GLB", export_animations=True)

    result = {
        "status": "OK",
        "clip_name": clip_name,
        "frames": total_frames,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "fps": fps,
        "duration": round(total_frames / (fps or 30), 2),
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

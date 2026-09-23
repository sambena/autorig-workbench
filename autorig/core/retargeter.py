# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: External Motion Capture / BVH & FBX Clip Retargeter.
#
# Employs the Universal Semantic Bone Dictionary to map BVH / Mixamo / Rokoko / CMU mocap
# curves onto Autorig skeletons, retargeting bone rotations while preserving proportions,
# rest poses, and twist offsets.

import json
import math
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import blender
    import layout
    import skeletons
    import spec_store
except ImportError:
    from autorig.core import blender
    from autorig.core import layout
    from autorig.core import skeletons
    from autorig.core import spec_store


def parse_bvh_header(filepath_or_content):
    """Pure-Python parser for BVH hierarchy and motion header.
    Returns: dict with root, joints, hierarchy, offsets, channels, frames, frame_time, fps."""
    if os.path.isfile(filepath_or_content):
        with open(filepath_or_content, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = str(filepath_or_content)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].upper().startswith("HIERARCHY"):
        raise ValueError("Not a valid BVH file: missing HIERARCHY section")

    root = None
    joints = []
    hierarchy = {}
    offsets = {}
    channels = {}
    stack = []

    i = 1
    in_motion = False
    frames = 0
    frame_time = 0.033333

    while i < len(lines):
        line = lines[i]
        tokens = line.split()
        tok = tokens[0].upper()

        if tok == "MOTION":
            in_motion = True
            i += 1
            break

        if tok in ("ROOT", "JOINT"):
            jname = tokens[1]
            if tok == "ROOT":
                root = jname
            joints.append(jname)
            if stack:
                parent = stack[-1]
                hierarchy.setdefault(parent, []).append(jname)
            stack.append(jname)

        elif tok == "END":  # End Site
            jname = f"{stack[-1]}_End" if stack else "End_Site"
            joints.append(jname)
            if stack:
                hierarchy.setdefault(stack[-1], []).append(jname)
            stack.append(jname)

        elif tok == "OFFSET":
            if stack:
                offsets[stack[-1]] = [float(tokens[1]), float(tokens[2]), float(tokens[3])]

        elif tok == "CHANNELS":
            if stack:
                n_chan = int(tokens[1])
                chan_list = tokens[2:2 + n_chan]
                channels[stack[-1]] = chan_list

        elif tok == "}":
            if stack:
                stack.pop()

        i += 1

    if in_motion:
        while i < len(lines):
            line = lines[i]
            if line.upper().startswith("FRAMES:"):
                frames = int(line.split(":")[1].strip())
            elif line.upper().startswith("FRAME TIME:"):
                frame_time = float(line.split(":")[1].strip())
                break
            i += 1

    fps = round(1.0 / frame_time, 2) if frame_time > 0 else 30.0
    duration = round(frames * frame_time, 2)

    return {
        "format": "BVH",
        "root": root,
        "joints": joints,
        "hierarchy": hierarchy,
        "offsets": offsets,
        "channels": channels,
        "frames": frames,
        "frame_time": frame_time,
        "fps": fps,
        "duration": duration,
    }


def inspect_mocap_file(filepath):
    """Inspects a mocap or animation clip file (BVH or FBX).
    Returns dict with file metadata, joints, frame count, fps, convention."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Mocap file not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".bvh":
        meta = parse_bvh_header(filepath)
        conv, conf = skeletons.detect_convention(meta["joints"])
        meta["convention"] = conv
        meta["convention_confidence"] = conf
        meta["file"] = os.path.basename(filepath)
        meta["path"] = os.path.abspath(filepath)
        return meta

    elif ext == ".fbx":
        # Extract bone names and action lengths via fast Blender probe
        cmd = [
            blender.find(),
            "-b",
            "--python-expr",
            f"""
import bpy, json
bpy.ops.import_scene.fbx(filepath="{os.path.abspath(filepath)}", use_anim=True)
arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bones = [b.name for b in arm.data.bones] if arm else []
frames = 0
fps = bpy.context.scene.render.fps
if arm and arm.animation_data and arm.animation_data.action:
    act = arm.animation_data.action
    frames = int(act.frame_range[1] - act.frame_range[0] + 1)
print("__FBX_META__" + json.dumps({{"format": "FBX", "joints": bones, "frames": frames, "fps": fps}}))
"""
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        meta = {"format": "FBX", "joints": [], "frames": 0, "fps": 30.0}
        for line in r.stdout.splitlines():
            if line.startswith("__FBX_META__"):
                try:
                    meta = json.loads(line[len("__FBX_META__"):])
                except Exception:
                    pass
                break

        conv, conf = skeletons.detect_convention(meta.get("joints", []))
        meta["convention"] = conv
        meta["convention_confidence"] = conf
        meta["file"] = os.path.basename(filepath)
        meta["path"] = os.path.abspath(filepath)
        meta["duration"] = round(meta["frames"] / (meta["fps"] or 30.0), 2)
        return meta

    else:
        raise ValueError(f"Unsupported mocap format '{ext}'. Must be .bvh or .fbx")


def build_retarget_mapping(source_bones, target_bones, overrides=None):
    """Constructs a semantic retargeting mapping from source mocap bones to target character bones.
    Returns: dict with mapping {source_bone: target_bone}, canonical roles, stats, and confidence."""
    mapping = {}
    roles_map = {}
    unmapped_source = []
    unmapped_target = list(target_bones)

    # 1. Map target bones to canonical roles
    tgt_role_to_bone = {}
    for tb in target_bones:
        role = skeletons.map_bone_to_canonical(tb)
        if role:
            tgt_role_to_bone[role] = tb

    # 2. Check source bones
    for sb in source_bones:
        # Check explicit overrides first
        if overrides and sb in overrides:
            tb = overrides[sb]
            if tb in target_bones:
                mapping[sb] = tb
                roles_map[sb] = (tb, "override")
                if tb in unmapped_target:
                    unmapped_target.remove(tb)
                continue

        # Exact name match (case-insensitive)
        exact_match = next((tb for tb in target_bones if tb.lower() == sb.lower()), None)
        if exact_match:
            mapping[sb] = exact_match
            role = skeletons.map_bone_to_canonical(sb) or exact_match
            roles_map[sb] = (exact_match, role)
            if exact_match in unmapped_target:
                unmapped_target.remove(exact_match)
            continue

        # Semantic canonical role match
        src_role = skeletons.map_bone_to_canonical(sb)
        if src_role and src_role in tgt_role_to_bone:
            matched_tb = tgt_role_to_bone[src_role]
            mapping[sb] = matched_tb
            roles_map[sb] = (matched_tb, src_role)
            if matched_tb in unmapped_target:
                unmapped_target.remove(matched_tb)
        else:
            unmapped_source.append(sb)

    # Core anatomical roles to verify retarget quality
    core_roles = {"hips", "spine", "arm.L", "arm.R", "thigh.L", "thigh.R"}
    mapped_roles = {r for _, (_, r) in roles_map.items()}
    core_matched = sum(1 for r in core_roles if r in mapped_roles)
    confidence = round(core_matched / len(core_roles), 2)

    # Identify root bones for translation transfer
    root_pair = None
    for sb, tb in mapping.items():
        if skeletons.map_bone_to_canonical(sb) == "hips" or skeletons.map_bone_to_canonical(tb) == "hips":
            root_pair = (sb, tb)
            break
    if not root_pair and "root" in target_bones:
        # If target has root bone, pair with source hips or first mapped bone
        for sb, tb in mapping.items():
            if "hip" in sb.lower() or "pelvis" in sb.lower():
                root_pair = (sb, tb)
                break

    return {
        "mapping": mapping,
        "roles": roles_map,
        "root_pair": root_pair,
        "unmapped_source": unmapped_source,
        "unmapped_target": unmapped_target,
        "mapped_count": len(mapping),
        "total_source": len(source_bones),
        "total_target": len(target_bones),
        "confidence": confidence,
    }


def plan_retarget(model_name, mocap_file, clip_name=None, root_motion=True, overrides=None):
    """Pre-flight planning and validation for retargeting a mocap clip onto a rigged model."""
    mocap_meta = inspect_mocap_file(mocap_file)

    # Verify model blend exists
    rd = layout.rigged_dir(model_name)
    blend_path = os.path.join(rd, f"{model_name}.blend")
    if not os.path.isfile(blend_path):
        raise FileNotFoundError(f"Rigged blend file not found for model '{model_name}': {blend_path}")

    # Inspect target bones from blend
    cmd = [
        blender.find(),
        "-b",
        blend_path,
        "--python-expr",
        """
import bpy, json
arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bones = [b.name for b in arm.data.bones] if arm else []
print("__TGT_BONES__" + json.dumps(bones))
"""
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    tgt_bones = []
    for line in r.stdout.splitlines():
        if line.startswith("__TGT_BONES__"):
            try:
                tgt_bones = json.loads(line[len("__TGT_BONES__"):])
            except Exception:
                pass
            break

    if not tgt_bones:
        raise ValueError(f"No armature bones found in '{blend_path}'")

    map_result = build_retarget_mapping(mocap_meta["joints"], tgt_bones, overrides=overrides)
    stem = os.path.splitext(os.path.basename(mocap_file))[0]
    final_clip = clip_name or re.sub(r"[^a-zA-Z0-9_\-]+", "_", stem).lower()

    return {
        "model": model_name,
        "blend_path": blend_path,
        "clip_name": final_clip,
        "mocap": mocap_meta,
        "mapping_result": map_result,
        "root_motion": root_motion and (map_result["root_pair"] is not None),
        "root_pair": map_result["root_pair"],
    }


def format_retarget_summary(plan):
    """Formats an ASCII summary table of the retargeting plan."""
    m = plan["mocap"]
    res = plan["mapping_result"]
    lines = [
        f"RETARGET PLAN: {m['file']} -> {plan['model']} (Clip: '{plan['clip_name']}')",
        "-" * 68,
        f"Mocap Format:     {m['format']} ({m.get('convention', 'unknown').upper()}, {m['frames']} frames @ {m['fps']} fps, {m['duration']}s)",
        f"Target Rig:       {plan['blend_path']}",
        f"Bone Mapping:     {res['mapped_count']} mapped / {res['total_target']} target bones ({int(res['confidence'] * 100)}% confidence)",
        f"Root Motion:      {'ENABLED' if plan['root_motion'] else 'DISABLED'} (Root Pair: {plan['root_pair']})",
        "-" * 68,
        f"{'Source Joint':<24} {'Target Bone':<24} {'Semantic Role':<16}",
        "-" * 68,
    ]
    for sb, (tb, role) in sorted(res["roles"].items()):
        lines.append(f"{sb:<24} {tb:<24} {role:<16}")
    lines.append("-" * 68)
    if res["unmapped_target"]:
        lines.append(f"Unmapped Target: {', '.join(res['unmapped_target'][:10])}")
    return "\n".join(lines)


def retarget_clip(model_name, mocap_file, clip_name=None, root_motion=True,
                  scale_proportions=True, overrides=None, export_glb=False):
    """Executes full retargeting in Blender and returns results dictionary."""
    plan = plan_retarget(model_name, mocap_file, clip_name=clip_name,
                         root_motion=root_motion, overrides=overrides)

    worker_script = os.path.join(PKG, "steps", "retarget_worker.py")
    out_json = os.path.join(layout.WORK, "qa", f"{model_name}_{plan['clip_name']}_retarget.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)

    config = {
        "target_blend": plan["blend_path"],
        "source_file": os.path.abspath(mocap_file),
        "clip_name": plan["clip_name"],
        "fps": int(plan["mocap"]["fps"]),
        "root_motion": plan["root_motion"],
        "root_pair": plan["root_pair"],
        "scale_proportions": scale_proportions,
        "mapping": plan["mapping_result"]["mapping"],
        "export_glb": export_glb,
        "out_json": out_json,
    }

    config_str = json.dumps(config)
    r = blender.run("retarget_worker.py", config_str)
    if r.returncode != 0:
        raise RuntimeError(f"Blender retarget worker failed (code {r.returncode}):\n{r.stderr or r.stdout}")

    if not os.path.isfile(out_json):
        raise RuntimeError(f"Blender retarget worker failed to generate output:\n{r.stderr or r.stdout}")

    with open(out_json, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    result["plan"] = plan
    return result

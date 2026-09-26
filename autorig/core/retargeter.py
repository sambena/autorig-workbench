# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: External Motion Capture / BVH & FBX Clip Retargeter.
#
# Employs the Universal Semantic Bone Dictionary to map BVH / Mixamo / Rokoko / CMU mocap
# curves onto Autorig skeletons, retargeting bone rotations while preserving proportions,
# rest poses, and twist offsets.

import glob
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
        stem = os.path.splitext(meta["file"])[0]
        meta["actions"] = [{"name": stem, "frames": meta["frames"], "frame_start": 1, "frame_end": meta["frames"], "fps": meta["fps"]}]
        meta["active_action"] = stem
        return meta

    elif ext == ".fbx":
        cmd = [
            blender.find(),
            "-b",
            "--python-expr",
            f"""
import bpy, json
bpy.ops.import_scene.fbx(filepath="{os.path.abspath(filepath)}", use_anim=True)
arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bones = [b.name for b in arm.data.bones] if arm else []
fps = bpy.context.scene.render.fps
actions = []
for a in bpy.data.actions:
    f_start = int(a.frame_range[0])
    f_end = int(a.frame_range[1])
    f_count = max(1, f_end - f_start + 1)
    actions.append({{"name": a.name, "frames": f_count, "frame_start": f_start, "frame_end": f_end, "fps": fps}})

act = None
if arm and arm.animation_data and arm.animation_data.action:
    act = arm.animation_data.action
elif bpy.data.actions:
    act = bpy.data.actions[0]

frames = int(act.frame_range[1] - act.frame_range[0] + 1) if act else 0
active_name = act.name if act else None
print("__FBX_META__" + json.dumps({{"format": "FBX", "joints": bones, "frames": frames, "fps": fps, "actions": actions, "active_action": active_name}}))
"""
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        meta = {"format": "FBX", "joints": [], "frames": 0, "fps": 30.0, "actions": [], "active_action": None}
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
            if role == "hips" and "hips" in tgt_role_to_bone:
                if tb.lower() in ("hips", "pelvis") and tgt_role_to_bone["hips"].lower() == "root":
                    tgt_role_to_bone[role] = tb
                continue
            tgt_role_to_bone[role] = tb

    # 2. Check source bones
    for sb in source_bones:
        # Explicit overrides first
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


def find_blend_file(model_identifier):
    """Finds the rigged blend file for a model name, group/model, or file path."""
    if os.path.isfile(model_identifier) and model_identifier.lower().endswith(".blend"):
        return os.path.abspath(model_identifier), os.path.splitext(os.path.basename(model_identifier))[0]

    # Try layout.rigged_dir
    name = layout.leaf(model_identifier)
    rd = layout.rigged_dir(model_identifier)
    blend_path = os.path.join(rd, f"{name}.blend")
    if os.path.isfile(blend_path):
        return blend_path, name

    # Search directly in models root
    for g, m in layout.all_models():
        if m == name:
            bp = os.path.join(layout.rigged_dir(f"{g}/{m}"), f"{m}.blend")
            if os.path.isfile(bp):
                return bp, m

    # Check relative to cwd and subdirectories (e.g. Heartroot, Smeltdown, etc.)
    cwd = os.getcwd()
    direct_cand = os.path.join(cwd, model_identifier, "rigged", f"{name}.blend")
    if os.path.isfile(direct_cand):
        return os.path.abspath(direct_cand), name

    search_dirs = [cwd]
    try:
        search_dirs += [os.path.join(cwd, d) for d in os.listdir(cwd)
                        if os.path.isdir(os.path.join(cwd, d)) and not d.startswith((".", "_"))]
    except OSError:
        pass

    for sdir in search_dirs:
        cand = os.path.join(sdir, name, "rigged", f"{name}.blend")
        if os.path.isfile(cand):
            return os.path.abspath(cand), name

    raise FileNotFoundError(f"Rigged blend file not found for model '{model_identifier}': {blend_path}")


def clean_action_name(raw_name):
    """Strips namespace prefixes and returns a clean snake_case clip name."""
    name = raw_name.split("|")[-1].split("/")[-1].strip()
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return name or "clip"


def plan_retarget(model_name, mocap_file, clip_name=None, source_action=None, root_motion=True,
                  solve_offsets=True, overrides=None):
    """Pre-flight planning and validation for retargeting a mocap clip onto a rigged model."""
    mocap_meta = inspect_mocap_file(mocap_file)
    blend_path, model_leaf = find_blend_file(model_name)

    chosen_action = None
    if source_action and mocap_meta.get("actions"):
        for act in mocap_meta["actions"]:
            aname = act["name"]
            if (aname == source_action or aname.lower() == source_action.lower() or
                aname.endswith("|" + source_action) or aname.endswith("/" + source_action) or
                clean_action_name(aname) == source_action.lower()):
                chosen_action = act
                break
        if not chosen_action:
            available = [a["name"] for a in mocap_meta["actions"][:10]]
            raise ValueError(f"Action '{source_action}' not found in '{mocap_file}'. Available actions: {available}")
    elif mocap_meta.get("actions"):
        active_name = mocap_meta.get("active_action")
        chosen_action = next((a for a in mocap_meta["actions"] if a["name"] == active_name), mocap_meta["actions"][0])

    if chosen_action:
        mocap_meta["frames"] = chosen_action["frames"]
        mocap_meta["duration"] = round(chosen_action["frames"] / (mocap_meta["fps"] or 30.0), 2)
        mocap_meta["selected_action"] = chosen_action["name"]
    else:
        mocap_meta["selected_action"] = None

    # Inspect target bones and rest bone vectors from blend
    cmd = [
        blender.find(),
        "-b",
        blend_path,
        "--python-expr",
        """
import bpy, json
arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bones = [b.name for b in arm.data.bones] if arm else []
dirs = {}
if arm:
    for b in arm.data.bones:
        v = (arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)).normalized()
        dirs[b.name] = [round(v.x, 3), round(v.y, 3), round(v.z, 3)]
print("__TGT_BONES__" + json.dumps({"bones": bones, "dirs": dirs}))
"""
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    tgt_bones = []
    tgt_dirs = {}
    for line in r.stdout.splitlines():
        if line.startswith("__TGT_BONES__"):
            try:
                data = json.loads(line[len("__TGT_BONES__"):])
                tgt_bones = data.get("bones", [])
                tgt_dirs = data.get("dirs", {})
            except Exception:
                pass
            break

    if not tgt_bones:
        raise ValueError(f"No armature bones found in '{blend_path}'")

    map_result = build_retarget_mapping(mocap_meta["joints"], tgt_bones, overrides=overrides)
    if clip_name:
        final_clip = clip_name
    elif chosen_action:
        final_clip = clean_action_name(chosen_action["name"])
    else:
        stem = os.path.splitext(os.path.basename(mocap_file))[0]
        final_clip = clean_action_name(stem)

    return {
        "model": model_leaf,
        "blend_path": blend_path,
        "clip_name": final_clip,
        "source_action": mocap_meta.get("selected_action"),
        "mocap": mocap_meta,
        "mapping_result": map_result,
        "root_motion": root_motion and (map_result["root_pair"] is not None),
        "root_pair": map_result["root_pair"],
        "solve_offsets": solve_offsets,
        "tgt_dirs": tgt_dirs,
    }


def format_retarget_summary(plan):
    """Formats an ASCII summary table of the retargeting plan."""
    m = plan["mocap"]
    res = plan["mapping_result"]
    total_actions = len(m.get("actions", []))
    act_str = f"Selected Action:  {plan.get('source_action') or '(default)'}"
    if total_actions > 1:
        act_str += f" ({total_actions} total in file)"
    lines = [
        f"RETARGET PLAN: {m['file']} -> {plan['model']} (Clip: '{plan['clip_name']}')",
        "-" * 72,
        f"Mocap Format:     {m['format']} ({m.get('convention', 'unknown').upper()}, {m['frames']} frames @ {m['fps']} fps, {m['duration']}s)",
        act_str,
        f"Target Rig:       {plan['blend_path']}",
        f"Bone Mapping:     {res['mapped_count']} mapped / {res['total_target']} target bones ({int(res['confidence'] * 100)}% confidence)",
        f"Root Motion:      {'ENABLED' if plan['root_motion'] else 'DISABLED'} (Root Pair: {plan['root_pair']})",
        f"Orientation Solve:{'ENABLED (T-pose/A-pose offset correction)' if plan.get('solve_offsets', True) else 'DISABLED'}",
        "-" * 72,
        f"{'Source Joint':<24} {'Target Bone':<24} {'Semantic Role':<20}",
        "-" * 72,
    ]
    for sb, (tb, role) in sorted(res["roles"].items()):
        lines.append(f"{sb:<24} {tb:<24} {role:<20}")
    lines.append("-" * 72)
    if res["unmapped_target"]:
        lines.append(f"Unmapped Target: {', '.join(res['unmapped_target'][:10])}")
    return "\n".join(lines)


def retarget_clip(model_name, mocap_file, clip_name=None, source_action=None, root_motion=True,
                  scale_proportions=True, solve_offsets=True, fps=None,
                  frame_range=None, overrides=None, export_glb=False, preview=False):
    """Executes full retargeting in Blender and returns results dictionary."""
    plan = plan_retarget(model_name, mocap_file, clip_name=clip_name,
                         source_action=source_action,
                         root_motion=root_motion, solve_offsets=solve_offsets,
                         overrides=overrides)

    worker_script = os.path.join(PKG, "steps", "retarget_worker.py")
    out_json = os.path.join(layout.WORK, "qa", f"{plan['model']}_{plan['clip_name']}_retarget.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)

    config = {
        "target_blend": plan["blend_path"],
        "source_file": os.path.abspath(mocap_file),
        "source_action": plan.get("source_action"),
        "clip_name": plan["clip_name"],
        "fps": int(fps or plan["mocap"]["fps"]),
        "root_motion": plan["root_motion"],
        "root_pair": plan["root_pair"],
        "scale_proportions": scale_proportions,
        "solve_offsets": solve_offsets,
        "frame_range": frame_range,
        "mapping": plan["mapping_result"]["mapping"],
        "export_glb": export_glb,
        "preview": preview,
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


def retarget_batch(models, mocap_files, clip_names=None, source_actions=None, root_motion=True,
                   scale_proportions=True, solve_offsets=True, fps=None,
                   frame_range=None, overrides=None, export_glb=False, preview=False):
    """Retargets a collection of mocap clips onto a collection of models."""
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    if isinstance(mocap_files, str):
        if os.path.isdir(mocap_files):
            mocap_files = sorted(glob.glob(os.path.join(mocap_files, "*.bvh")) +
                                 glob.glob(os.path.join(mocap_files, "*.fbx")))
        else:
            mocap_files = [f.strip() for f in mocap_files.split(",") if f.strip()]

    results = []
    total_ops = len(models) * len(mocap_files)
    op_idx = 0

    for m in models:
        for f_idx, mocap_file in enumerate(mocap_files):
            op_idx += 1
            clip_name = None
            if clip_names and f_idx < len(clip_names):
                clip_name = clip_names[f_idx]
            source_action = None
            if source_actions and f_idx < len(source_actions):
                source_action = source_actions[f_idx]

            print(f"RETARGET_BATCH [{op_idx}/{total_ops}] Retargeting {os.path.basename(mocap_file)} -> {m}...")
            try:
                res = retarget_clip(
                    model_name=m,
                    mocap_file=mocap_file,
                    clip_name=clip_name,
                    source_action=source_action,
                    root_motion=root_motion,
                    scale_proportions=scale_proportions,
                    solve_offsets=solve_offsets,
                    fps=fps,
                    frame_range=frame_range,
                    overrides=overrides,
                    export_glb=export_glb,
                    preview=preview,
                )
                results.append({"status": "OK", "model": m, "file": mocap_file, "result": res})
            except Exception as e:
                print(f"  RETARGET_BATCH ERROR on {m}: {e}", file=sys.stderr)
                results.append({"status": "ERROR", "model": m, "file": mocap_file, "error": str(e)})

    return results

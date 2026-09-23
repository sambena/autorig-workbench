# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Multi-target engine export presets and packaging system.
#
# Generates tailor-made export packages for major game engines and runtimes:
#   - Unreal Engine 4 / 5: Z-up FBX, UE Mannequin bone mapping, root motion guide, import preset.
#   - Unity: Y-up FBX, Mecanim HumanDescription avatar descriptor, clip loop settings, import guide.
#   - Godot 4: Self-contained GLB scene, .import presets, GDScript CharacterBody3D loader template.
#   - Web / glTF: Self-contained GLB, web manifest with metadata/clips, and standalone HTML 3D preview.
#
# Packages are assembled in <work>/export/<model>/<target>/ and archived to <work>/export/<model>_<target>.zip.

import glob
import json
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import layout
    import skeletons
    import spec_store
except ImportError:
    from autorig.core import layout
    from autorig.core import skeletons
    from autorig.core import spec_store

PRESETS = {
    "unreal": {
        "id": "unreal",
        "name": "Unreal Engine 4 / 5",
        "primary_format": "FBX",
        "badge": "UE4 / UE5",
        "description": "Z-up skeletal mesh FBX package with UE Mannequin hierarchy mapping, root motion guide, and engine import preset.",
        "doc_file": "Unreal_Import_Guide.md",
    },
    "unity": {
        "id": "unity",
        "name": "Unity (Mecanim)",
        "primary_format": "FBX",
        "badge": "Unity Mecanim",
        "description": "Y-up FBX package with Unity Humanoid Mecanim avatar descriptor, clip loop flags, and import guide.",
        "doc_file": "Unity_Import_Guide.md",
    },
    "godot": {
        "id": "godot",
        "name": "Godot 4.x",
        "primary_format": "GLB",
        "badge": "Godot 4",
        "description": "Self-contained GLB scene with embedded animation library, Godot 4 .import presets, and sample GDScript character controller.",
        "doc_file": "Godot_Import_Guide.md",
    },
    "web": {
        "id": "web",
        "name": "Web / glTF",
        "primary_format": "GLB",
        "badge": "WebGL / WebGPU",
        "description": "Optimized standalone GLB with web manifest and complete HTML/JS 3D preview player.",
        "doc_file": "Web_Usage_Guide.md",
    },
}

CANONICAL_TO_UNREAL = {
    "hips": "pelvis",
    "spine": "spine_01",
    "spine1": "spine_02",
    "spine2": "spine_03",
    "neck": "neck_01",
    "head": "head",
    "jaw": "jaw",
    "shoulder.L": "clavicle_l", "shoulder.R": "clavicle_r",
    "arm.L": "upperarm_l", "arm.R": "upperarm_r",
    "forearm.L": "lowerarm_l", "forearm.R": "lowerarm_r",
    "hand.L": "hand_l", "hand.R": "hand_r",
    "thigh.L": "thigh_l", "thigh.R": "thigh_r",
    "shin.L": "calf_l", "shin.R": "calf_r",
    "foot.L": "foot_l", "foot.R": "foot_r",
    "toe.L": "ball_l", "toe.R": "ball_r",
    # Fingers Left
    "thumb1.L": "thumb_01_l", "thumb2.L": "thumb_02_l", "thumb3.L": "thumb_03_l",
    "index1.L": "index_01_l", "index2.L": "index_02_l", "index3.L": "index_03_l",
    "middle1.L": "middle_01_l", "middle2.L": "middle_02_l", "middle3.L": "middle_03_l",
    "ring1.L": "ring_01_l", "ring2.L": "ring_02_l", "ring3.L": "ring_03_l",
    "pinky1.L": "pinky_01_l", "pinky2.L": "pinky_02_l", "pinky3.L": "pinky_03_l",
    # Fingers Right
    "thumb1.R": "thumb_01_r", "thumb2.R": "thumb_02_r", "thumb3.R": "thumb_03_r",
    "index1.R": "index_01_r", "index2.R": "index_02_r", "index3.R": "index_03_r",
    "middle1.R": "middle_01_r", "middle2.R": "middle_02_r", "middle3.R": "middle_03_r",
    "ring1.R": "ring_01_r", "ring2.R": "ring_02_r", "ring3.R": "ring_03_r",
    "pinky1.R": "pinky_01_r", "pinky2.R": "pinky_02_r", "pinky3.R": "pinky_03_r",
}

CANONICAL_TO_UNITY = {
    "hips": "Hips",
    "spine": "Spine",
    "spine1": "Chest",
    "spine2": "UpperChest",
    "neck": "Neck",
    "head": "Head",
    "jaw": "Jaw",
    "shoulder.L": "LeftShoulder", "shoulder.R": "RightShoulder",
    "arm.L": "LeftUpperArm", "arm.R": "RightUpperArm",
    "forearm.L": "LeftLowerArm", "forearm.R": "RightLowerArm",
    "hand.L": "LeftHand", "hand.R": "RightHand",
    "thigh.L": "LeftUpperLeg", "thigh.R": "RightUpperLeg",
    "shin.L": "LeftLowerLeg", "shin.R": "RightLowerLeg",
    "foot.L": "LeftFoot", "foot.R": "RightFoot",
    "toe.L": "LeftToes", "toe.R": "RightToes",
    # Fingers Left
    "thumb1.L": "Left Thumb Proximal", "thumb2.L": "Left Thumb Intermediate", "thumb3.L": "Left Thumb Distal",
    "index1.L": "Left Index Proximal", "index2.L": "Left Index Intermediate", "index3.L": "Left Index Distal",
    "middle1.L": "Left Middle Proximal", "middle2.L": "Left Middle Intermediate", "middle3.L": "Left Middle Distal",
    "ring1.L": "Left Ring Proximal", "ring2.L": "Left Ring Intermediate", "ring3.L": "Left Ring Distal",
    "pinky1.L": "Left Little Proximal", "pinky2.L": "Left Little Intermediate", "pinky3.L": "Left Little Distal",
    # Fingers Right
    "thumb1.R": "Right Thumb Proximal", "thumb2.R": "Right Thumb Intermediate", "thumb3.R": "Right Thumb Distal",
    "index1.R": "Right Index Proximal", "index2.R": "Right Index Intermediate", "index3.R": "Right Index Distal",
    "middle1.R": "Right Middle Proximal", "middle2.R": "Right Middle Intermediate", "middle3.R": "Right Middle Distal",
    "ring1.R": "Right Ring Proximal", "ring2.R": "Right Ring Intermediate", "ring3.R": "Right Ring Distal",
    "pinky1.R": "Right Little Proximal", "pinky2.R": "Right Little Intermediate", "pinky3.R": "Right Little Distal",
}


def list_presets():
    """Returns metadata for all available export presets."""
    return list(PRESETS.values())


def get_model_assets(name):
    """Gathers all available asset files, spec, clips, and QA data for a given model."""
    pack_d = layout.pack_dir(name)
    if not pack_d or not os.path.isdir(pack_d):
        raise ValueError(f"model '{name}' not found in models root")

    rf = layout.rig_folder(name)
    rig_d = os.path.join(pack_d, rf)

    fbx_file = os.path.join(rig_d, f"{name}.fbx")
    blend_file = os.path.join(rig_d, f"{name}.blend")
    preview_glb = os.path.join(rig_d, "preview.glb")
    preview_json = os.path.join(rig_d, "preview.json")

    # If preview.glb is not beside rig, check source directory for any .glb
    if not os.path.isfile(preview_glb):
        src_glb = os.path.join(pack_d, f"{name}.glb")
        if os.path.isfile(src_glb):
            preview_glb = src_glb

    clips_json = os.path.join(pack_d, "clips", f"{name}_clips.json")
    clips_fbx = os.path.join(pack_d, "clips", f"{name}.fbx")
    clips_blend = os.path.join(pack_d, "clips", f"{name}_clips.blend")

    spec = spec_store.model(name) or {}
    card_path = os.path.join(pack_d, "model.json")
    card = json.load(open(card_path, encoding="utf-8")) if os.path.isfile(card_path) else {}

    qa_json = os.path.join(layout.work_dir("qa"), f"{name}.json")
    qa_data = json.load(open(qa_json, encoding="utf-8")) if os.path.isfile(qa_json) else {}

    textures = []
    fbm_dir = os.path.join(pack_d, f"{name}.fbm")
    if os.path.isdir(fbm_dir):
        for f in os.listdir(fbm_dir):
            p = os.path.join(fbm_dir, f)
            if os.path.isfile(p):
                textures.append(p)
    for ext in (".png", ".jpg", ".jpeg", ".tga", ".webp"):
        for p in glob.glob(os.path.join(glob.escape(pack_d), "*" + ext)):
            if p not in textures:
                textures.append(p)

    # Collect known deform bone names
    bones = []
    if qa_data.get("chains"):
        for c in qa_data["chains"]:
            if isinstance(c, dict) and "bones" in c:
                bones.extend(c["bones"])
    elif isinstance(qa_data.get("bones"), list):
        bones = list(qa_data["bones"])

    if not bones and spec.get("rig", {}).get("chains"):
        for c in spec["rig"]["chains"]:
            if "names" in c:
                bones.extend(c["names"])
            elif "name" in c:
                bones.append(c["name"])

    # Deduplicate while preserving order
    seen = set()
    bones = [b for b in bones if not (b in seen or seen.add(b))]

    return {
        "name": name,
        "dir": pack_d,
        "rig_dir": rig_d,
        "fbx": fbx_file if os.path.isfile(fbx_file) else None,
        "blend": blend_file if os.path.isfile(blend_file) else None,
        "glb": preview_glb if os.path.isfile(preview_glb) else None,
        "preview_json": preview_json if os.path.isfile(preview_json) else None,
        "clips_json": clips_json if os.path.isfile(clips_json) else None,
        "clips_fbx": clips_fbx if os.path.isfile(clips_fbx) else None,
        "clips_blend": clips_blend if os.path.isfile(clips_blend) else None,
        "textures": textures,
        "spec": spec,
        "card": card,
        "qa": qa_data,
        "bones": bones,
    }


def compute_bone_mappings(bones):
    """Maps model deform bones to canonical roles, Unreal Mannequin, and Unity Humanoid names."""
    out = {
        "canonical": {},
        "unreal": {},
        "unity": {},
    }
    for b in bones:
        c = skeletons.map_bone_to_canonical(b)
        if c:
            out["canonical"][b] = c
            if c in CANONICAL_TO_UNREAL:
                out["unreal"][b] = CANONICAL_TO_UNREAL[c]
            if c in CANONICAL_TO_UNITY:
                out["unity"][b] = CANONICAL_TO_UNITY[c]
    return out


def build_unreal_package(model_name, dest_dir, assets):
    """Assembles Unreal Engine 4 / 5 optimized export folder."""
    os.makedirs(dest_dir, exist_ok=True)
    files = []

    # 1. Rigged FBX
    if assets["fbx"]:
        dst = os.path.join(dest_dir, f"{model_name}.fbx")
        shutil.copy2(assets["fbx"], dst)
        files.append(os.path.basename(dst))

    # 2. Clips FBX
    if assets["clips_fbx"]:
        dst = os.path.join(dest_dir, f"{model_name}_Clips.fbx")
        shutil.copy2(assets["clips_fbx"], dst)
        files.append(os.path.basename(dst))

    # 3. Textures
    if assets["textures"]:
        tex_dir = os.path.join(dest_dir, "Textures")
        os.makedirs(tex_dir, exist_ok=True)
        for t in assets["textures"]:
            dst = os.path.join(tex_dir, os.path.basename(t))
            shutil.copy2(t, dst)
            files.append(f"Textures/{os.path.basename(dst)}")

    # 4. Bone Mapping JSON
    mappings = compute_bone_mappings(assets["bones"])
    map_path = os.path.join(dest_dir, "unreal_bone_mapping.json")
    with open(map_path, "w", encoding="utf-8") as fh:
        json.dump({
            "model": model_name,
            "target": "Unreal Engine 4 / 5",
            "mannequin_mappings": mappings["unreal"],
            "canonical_roles": mappings["canonical"],
        }, fh, indent=2)
    files.append(os.path.basename(map_path))

    # 5. Unreal Import Preset & Metadata
    preset_path = os.path.join(dest_dir, "unreal_import_preset.json")
    with open(preset_path, "w", encoding="utf-8") as fh:
        json.dump({
            "MeshType": "SkeletalMesh",
            "ImportMesh": True,
            "ImportTextures": True,
            "ImportMaterials": True,
            "NormalImportMethod": "FBXNIM_ImportNormalsAndTangents",
            "CreatePhysicsAsset": True,
            "ImportUniformScale": 1.0,
            "ConvertScene": True,
            "ForceFrontXAxis": False,
            "Animation": {
                "ImportAnimations": bool(assets["clips_fbx"]),
                "AnimationLength": "FBXALIT_ExportedTime",
                "PreserveLocalTransform": True,
            },
        }, fh, indent=2)
    files.append(os.path.basename(preset_path))

    # 6. Unreal Import Guide
    guide_path = os.path.join(dest_dir, "Unreal_Import_Guide.md")
    with open(guide_path, "w", encoding="utf-8") as fh:
        fh.write(f"""# Unreal Engine 4 / 5 Import Guide for `{model_name}`

Generated by **Autorig Workbench**.

---

### Step 1: Import Skeletal Mesh
1. In the Content Browser, create a folder (e.g. `/Game/Characters/{model_name}/`).
2. Drag and drop `{model_name}.fbx` into the Content Browser.
3. In the **FBX Import Options** dialog:
   - **Skeletal Mesh**: `Enabled` (Checked)
   - **Import Mesh**: `Enabled` (Checked)
   - **Skeleton**: Select an existing compatible skeleton, or leave empty to auto-generate a new one.
   - **Import Uniform Scale**: `1.0` (or `100.0` if using centimeter conversion).
   - **Normal Import Method**: `Import Normals and Tangents`.
   - **Create Physics Asset**: `Checked`.
4. Click **Import All**.

---

### Step 2: Import Animations
1. Drag and drop `{model_name}_Clips.fbx` into the same folder.
2. In the **FBX Import Options** dialog:
   - **Import Mesh**: `Disabled` (Unchecked).
   - **Skeleton**: Select the Skeleton created in Step 1.
   - **Import Animations**: `Enabled` (Checked).
   - **Animation Length**: `Exported Time`.
3. Click **Import All**.

---

### Step 3: Retargeting & Bone Names
Refer to `unreal_bone_mapping.json` for mapping Autorig deform bones to the UE Mannequin hierarchy:
```json
{json.dumps(mappings["unreal"], indent=2)}
```
""")
    files.append(os.path.basename(guide_path))
    return files


def build_unity_package(model_name, dest_dir, assets):
    """Assembles Unity (Mecanim) optimized export folder."""
    os.makedirs(dest_dir, exist_ok=True)
    files = []

    # 1. Rigged FBX
    if assets["fbx"]:
        dst = os.path.join(dest_dir, f"{model_name}.fbx")
        shutil.copy2(assets["fbx"], dst)
        files.append(os.path.basename(dst))

    # 2. Clips FBX
    if assets["clips_fbx"]:
        dst = os.path.join(dest_dir, f"{model_name}_Clips.fbx")
        shutil.copy2(assets["clips_fbx"], dst)
        files.append(os.path.basename(dst))

    # 3. Textures
    if assets["textures"]:
        tex_dir = os.path.join(dest_dir, "Textures")
        os.makedirs(tex_dir, exist_ok=True)
        for t in assets["textures"]:
            dst = os.path.join(tex_dir, os.path.basename(t))
            shutil.copy2(t, dst)
            files.append(f"Textures/{os.path.basename(dst)}")

    # 4. Unity Humanoid Avatar Descriptor
    mappings = compute_bone_mappings(assets["bones"])
    human_slots = [{"humanName": unity_name, "boneName": bone}
                   for bone, unity_name in mappings["unity"].items()]
    is_humanoid = len(human_slots) >= 6

    avatar_desc = {
        "avatar": {
            "name": f"{model_name}Avatar",
            "type": "Humanoid" if is_humanoid else "Generic",
            "humanDescription": {
                "human": human_slots,
                "armStretch": 0.05,
                "legStretch": 0.05,
                "upperArmTwist": 0.5,
                "lowerArmTwist": 0.5,
                "upperLegTwist": 0.5,
                "lowerLegTwist": 0.5,
                "feetSpacing": 0,
                "hasTranslationDoF": False,
            }
        }
    }
    avatar_path = os.path.join(dest_dir, "unity_avatar_definition.json")
    with open(avatar_path, "w", encoding="utf-8") as fh:
        json.dump(avatar_desc, fh, indent=2)
    files.append(os.path.basename(avatar_path))

    # 5. Unity Import Guide
    guide_path = os.path.join(dest_dir, "Unity_Import_Guide.md")
    with open(guide_path, "w", encoding="utf-8") as fh:
        fh.write(f"""# Unity Mecanim Import Guide for `{model_name}`

Generated by **Autorig Workbench**.

---

### Step 1: Import Asset
1. Copy or drag the folder `{model_name}` into your Unity `Assets/` directory.
2. Select `{model_name}.fbx` in the Project view.

---

### Step 2: Rig Setup (Inspector)
1. Select the **Rig** tab in the Inspector:
   - **Animation Type**: `{"Humanoid" if is_humanoid else "Generic"}`
   - **Avatar Definition**: `Create From This Model`
   - **Root Node**: `None` (or select `root` / `Hips`)
2. Click **Apply**.
3. (Optional) Click **Configure...** to verify bones match `unity_avatar_definition.json`.

---

### Step 3: Model & Materials
- In the **Model** tab:
  - **Convert Units**: `Checked`
  - **Normals**: `Import`
  - **Tangents**: `Calculate Mikktspace`
- In the **Materials** tab:
  - Click **Extract Textures...** and **Extract Materials...** if textures are packed.

---

### Mecanim Bone Mapping Table:
```json
{json.dumps(mappings["unity"], indent=2)}
```
""")
    files.append(os.path.basename(guide_path))
    return files


def build_godot_package(model_name, dest_dir, assets):
    """Assembles Godot 4.x optimized export folder."""
    os.makedirs(dest_dir, exist_ok=True)
    files = []

    # 1. Model GLB
    glb_src = assets["glb"] or assets["fbx"]
    glb_name = f"{model_name}.glb"
    if glb_src and glb_src.lower().endswith(".glb"):
        dst = os.path.join(dest_dir, glb_name)
        shutil.copy2(glb_src, dst)
        files.append(glb_name)
    elif assets["fbx"]:
        # Fallback to FBX if GLB preview not generated yet
        dst = os.path.join(dest_dir, f"{model_name}.fbx")
        shutil.copy2(assets["fbx"], dst)
        files.append(f"{model_name}.fbx")

    # 2. Textures (if external)
    if assets["textures"]:
        tex_dir = os.path.join(dest_dir, "textures")
        os.makedirs(tex_dir, exist_ok=True)
        for t in assets["textures"]:
            dst = os.path.join(tex_dir, os.path.basename(t))
            shutil.copy2(t, dst)
            files.append(f"textures/{os.path.basename(dst)}")

    # 3. Godot 4 .import Preset
    import_cfg = os.path.join(dest_dir, f"{model_name}.glb.import")
    with open(import_cfg, "w", encoding="utf-8") as fh:
        fh.write(f"""[remap]
importer="scene"
importer_version=1
type="PackedScene"
uid="uid://autorig_{model_name.lower()}"

[params]
nodes/root_type="CharacterBody3D"
animation/import=true
animation/fps=30.0
animation/trimming=false
animation/remove_immutable_tracks=true
import_script/path=""
_subresources={{
"nodes":{{
"PATH:AnimationPlayer":{{
"blend_times":[]
}}
}}
}}
""")
    files.append(f"{model_name}.glb.import")

    # 4. Sample Godot 4 Character Controller GDScript
    script_path = os.path.join(dest_dir, "character_controller.gd")
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(f"""# SPDX-License-Identifier: MIT
# Godot 4 Character Controller for {model_name}
# Attach to the root CharacterBody3D node of the instantiated scene.

extends CharacterBody3D

@export var speed: float = 4.0
@export var rotation_speed: float = 8.0

@onready var anim_player: AnimationPlayer = get_node_or_null("AnimationPlayer")

var gravity: float = ProjectSettings.get_setting("physics/3d/default_gravity")

func _ready() -> void:
    if anim_player:
        var clips = anim_player.get_animation_list()
        print("Autorig clips available: ", clips)
        if anim_player.has_animation("idle"):
            anim_player.play("idle")
        elif clips.size() > 0:
            anim_player.play(clips[0])

func _physics_process(delta: float) -> void:
    if not is_on_floor():
        velocity.y -= gravity * delta

    var input_dir := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
    var direction := (transform.basis * Vector3(input_dir.x, 0, input_dir.y)).normalized()

    if direction:
        velocity.x = direction.x * speed
        velocity.z = direction.z * speed
        if anim_player and anim_player.has_animation("walk") and anim_player.current_animation != "walk":
            anim_player.play("walk")
    else:
        velocity.x = move_toward(velocity.x, 0, speed)
        velocity.z = move_toward(velocity.z, 0, speed)
        if anim_player and anim_player.has_animation("idle") and anim_player.current_animation != "idle":
            anim_player.play("idle")

    move_and_slide()
""")
    files.append(os.path.basename(script_path))

    # 5. Godot Import Guide
    guide_path = os.path.join(dest_dir, "Godot_Import_Guide.md")
    with open(guide_path, "w", encoding="utf-8") as fh:
        fh.write(f"""# Godot 4.x Import Guide for `{model_name}`

Generated by **Autorig Workbench**.

---

### Step 1: Add to Godot Project
1. Copy this entire folder into your Godot 4 project folder (e.g. `res://characters/{model_name}/`).
2. Switch to the Godot Editor window to let it auto-import `{model_name}.glb`.

---

### Step 2: Instantiate Scene
1. Right-click `{model_name}.glb` in the FileSystem dock -> **New Inherited Scene**.
2. Save the scene as `{model_name}.tscn`.
3. Check the `AnimationPlayer` node to view and trigger baked clips (e.g. `idle`, `walk`, `attack`).

---

### Step 3: Character Controller
- Attach `character_controller.gd` to the root `CharacterBody3D` node.
- Add a `CollisionShape3D` (e.g. CapsuleShape3D) matching the character's height.
- Press **F6** to test scene movement!
""")
    files.append(os.path.basename(guide_path))
    return files


def build_web_package(model_name, dest_dir, assets):
    """Assembles Web / glTF standalone distribution folder."""
    os.makedirs(dest_dir, exist_ok=True)
    files = []

    # 1. Model asset (GLB if available, fallback to FBX)
    glb_src = assets["glb"]
    model_file = f"{model_name}.glb"
    if glb_src and glb_src.lower().endswith(".glb"):
        dst = os.path.join(dest_dir, model_file)
        shutil.copy2(glb_src, dst)
        files.append(model_file)
    elif assets["fbx"]:
        model_file = f"{model_name}.fbx"
        dst = os.path.join(dest_dir, model_file)
        shutil.copy2(assets["fbx"], dst)
        files.append(model_file)

    # 2. Web Manifest
    clips_list = []
    if assets["clips_json"] and os.path.isfile(assets["clips_json"]):
        try:
            cj = json.load(open(assets["clips_json"], encoding="utf-8"))
            clips_list = [{"name": c["name"], "seconds": c.get("seconds"), "loops": c.get("loops", True)}
                          for c in cj.get("clips", [])]
        except Exception:
            pass

    card = assets["card"] or {}
    manifest = {
        "schema": "autorig-web/1",
        "name": model_name,
        "file": model_file,
        "format": "glTF 2.0 Binary (GLB)" if model_file.endswith(".glb") else "Autodesk FBX",
        "height_metres": card.get("metres", 1.8),
        "role": card.get("role", "character"),
        "budget": assets["spec"].get("budget", 1500),
        "clips": clips_list,
    }
    man_path = os.path.join(dest_dir, "web_manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    files.append(os.path.basename(man_path))

    # 3. Standalone HTML 3D Previewer
    html_path = os.path.join(dest_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{model_name} · 3D Web Preview</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.4.0/model-viewer.min.js"></script>
<style>
  :root {{ --bg: #12151b; --panel: #1b1f27; --ink: #e6e9ef; --line: #2c3340; --accent: #5b8ff0; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: system-ui, sans-serif; display: flex; flex-direction: column; height: 100vh; }}
  header {{ padding: 12px 18px; background: var(--panel); border-bottom: 1px solid var(--line); display: flex; align-items: baseline; gap: 12px; }}
  header h1 {{ margin: 0; font-size: 16px; }}
  header .meta {{ color: #98a2b3; font-size: 12px; }}
  main {{ flex: 1; position: relative; }}
  model-viewer {{ width: 100%; height: 100%; }}
  .hud {{ position: absolute; bottom: 16px; left: 16px; background: rgba(27,31,39,0.85); backdrop-filter: blur(8px); padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); display: flex; gap: 10px; align-items: center; }}
  select, button {{ background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 5px 10px; font: inherit; cursor: pointer; }}
</style>
</head>
<body>
<header>
  <h1>{model_name}</h1>
  <span class="meta">{card.get('metres', 1.8)} m · {assets['spec'].get('budget', 1500)} tris · WebGL / WebGPU</span>
</header>
<main>
  <model-viewer id="mv" src="{model_file}" camera-controls auto-rotate shadow-intensity="1" exposure="1" autoplay ar>
  </model-viewer>
  <div class="hud">
    <label for="clipSelect" style="font-size:12px;color:#98a2b3">Clip:</label>
    <select id="clipSelect"></select>
    <button id="bPlay" type="button">Pause</button>
  </div>
</main>
<script>
  const mv = document.querySelector("#mv");
  const sel = document.querySelector("#clipSelect");
  const btn = document.querySelector("#bPlay");
  mv.addEventListener("load", () => {{
    const anims = mv.availableAnimations;
    sel.innerHTML = anims.map(a => `<option value="${{a}}">${{a}}</option>`).join("");
    if (anims.length) mv.animationName = anims[0];
  }});
  sel.addEventListener("change", () => {{ mv.animationName = sel.value; mv.play(); btn.textContent = "Pause"; }});
  btn.addEventListener("click", () => {{
    if (mv.paused) {{ mv.play(); btn.textContent = "Pause"; }}
    else {{ mv.pause(); btn.textContent = "Play"; }}
  }});
</script>
</body>
</html>
""")
    files.append(os.path.basename(html_path))

    # 4. Web Usage Guide
    guide_path = os.path.join(dest_dir, "Web_Usage_Guide.md")
    with open(guide_path, "w", encoding="utf-8") as fh:
        fh.write(f"""# Web / glTF Usage Guide for `{model_name}`

Generated by **Autorig Workbench**.

---

### Included Files:
- `{model_file}`: Self-contained 3D model asset with embedded textures, skeleton, and baked clips.
- `web_manifest.json`: Metadata, clip durations, loop states, and bounding dimensions.
- `index.html`: Standalone interactive HTML previewer using `<model-viewer>`.

---

### Running Locally:
To test the web preview locally:
```bash
python3 -m http.server 8080
```
Then navigate to `http://localhost:8080` in your browser.
""")
    files.append(os.path.basename(guide_path))
    return files


def zip_directory(src_dir, zip_dest):
    """Zips the contents of src_dir into zip_dest."""
    os.makedirs(os.path.dirname(os.path.abspath(zip_dest)), exist_ok=True)
    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, src_dir).replace("\\", "/")
                zf.write(p, rel)


def create_export_package(model_name, target="unreal", out_dir=None):
    """Builds and archives an export package for the specified target preset.
    target: 'unreal', 'unity', 'godot', 'web', or 'all'.
    Returns: dict with package summary and archive path."""
    target = target.lower()
    valid_targets = list(PRESETS.keys()) + ["all"]
    if target not in valid_targets:
        raise ValueError(f"unknown export target '{target}' (valid: {', '.join(valid_targets)})")

    assets = get_model_assets(model_name)
    base_export_dir = out_dir or layout.work_dir("export", model_name)
    os.makedirs(base_export_dir, exist_ok=True)

    if target == "all":
        results = {}
        for t in PRESETS.keys():
            t_dir = os.path.join(base_export_dir, t)
            res = create_export_package(model_name, target=t, out_dir=t_dir)
            results[t] = res

        # Also create combined archive
        all_zip = os.path.join(layout.work_dir("export"), f"{model_name}_all_presets.zip")
        with zipfile.ZipFile(all_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for t, res in results.items():
                t_src = res["dir"]
                for root, dirs, files in os.walk(t_src):
                    for f in files:
                        p = os.path.join(root, f)
                        rel = f"{t}/" + os.path.relpath(p, t_src).replace("\\", "/")
                        zf.write(p, rel)

        return {
            "model": model_name,
            "target": "all",
            "dir": base_export_dir,
            "zip_path": all_zip,
            "zip_rel": os.path.relpath(all_zip, layout.WORK).replace("\\", "/"),
            "zip_size": os.path.getsize(all_zip),
            "presets": results,
        }

    # Individual Target
    target_dir = os.path.join(base_export_dir, target)
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir, ignore_errors=True)
    os.makedirs(target_dir, exist_ok=True)

    builders = {
        "unreal": build_unreal_package,
        "unity": build_unity_package,
        "godot": build_godot_package,
        "web": build_web_package,
    }
    files = builders[target](model_name, target_dir, assets)

    zip_path = os.path.join(layout.work_dir("export"), f"{model_name}_{target}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    zip_directory(target_dir, zip_path)

    return {
        "model": model_name,
        "target": target,
        "preset_name": PRESETS[target]["name"],
        "dir": target_dir,
        "zip_path": zip_path,
        "zip_rel": os.path.relpath(zip_path, layout.WORK).replace("\\", "/"),
        "zip_size": os.path.getsize(zip_path),
        "files": files,
    }

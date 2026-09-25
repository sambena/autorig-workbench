# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Procedural Facial Blendshape & Morph Generator.
#
# Generates ARKit-compatible standard facial shape keys:
# - eyeBlink_L, eyeBlink_R
# - jawOpen
# - mouthSmile
# - viseme_aa
#
# Integrates with Blender mesh shape keys and exports to GLTF/FBX morph targets.

import math


def identify_facial_landmarks(vertices, head_pos, head_length=0.25):
    """Categorizes vertex indices into facial feature zones:
    - eyes_L, eyes_R, mouth_center, mouth_corners_L, mouth_corners_R, jaw.
    vertices: list of 3D coordinates (x, y, z).
    head_pos: tuple (hx, hy, hz) representing the head joint pivot.
    head_length: approximate vertical length of the head in meters.
    """
    hx, hy, hz = head_pos
    H = max(0.05, float(head_length))

    eyes_L = []
    eyes_R = []
    mouth_center = []
    mouth_corner_L = []
    mouth_corner_R = []
    jaw = []

    # Find the anterior front of the head
    # In Blender default, front is -Y
    head_verts = [
        (i, v) for i, v in enumerate(vertices)
        if abs(v[0] - hx) < H * 1.2 and abs(v[2] - (hz + H * 0.5)) < H * 1.2
    ]
    if not head_verts:
        return {}

    min_y = min(v[1] for _, v in head_verts)
    face_front_y = min_y + H * 0.55

    for i, (vx, vy, vz) in head_verts:
        # Only consider vertices on the front of the face
        if vy > face_front_y:
            continue

        rel_x = vx - hx
        rel_z = vz - hz

        # 1. Eyes: z in [0.35 H, 0.70 H]
        if 0.30 * H <= rel_z <= 0.65 * H:
            if rel_x > 0.08 * H:
                eyes_L.append((i, vx, vy, vz))
            elif rel_x < -0.08 * H:
                eyes_R.append((i, vx, vy, vz))

        # 2. Mouth: z in [0.05 H, 0.28 H]
        if 0.04 * H <= rel_z <= 0.28 * H:
            if abs(rel_x) < 0.12 * H:
                mouth_center.append((i, vx, vy, vz))
            elif rel_x >= 0.12 * H:
                mouth_corner_L.append((i, vx, vy, vz))
            elif rel_x <= -0.12 * H:
                mouth_corner_R.append((i, vx, vy, vz))

        # 3. Jaw / Chin: z in [-0.20 H, 0.08 H]
        if -0.25 * H <= rel_z <= 0.08 * H:
            if abs(rel_x) < 0.25 * H:
                jaw.append((i, vx, vy, vz))

    return {
        "eyes_L": eyes_L,
        "eyes_R": eyes_R,
        "mouth_center": mouth_center,
        "mouth_corner_L": mouth_corner_L,
        "mouth_corner_R": mouth_corner_R,
        "jaw": jaw,
        "head_length": H,
    }


def compute_shape_key_deltas(landmarks, num_vertices):
    """Calculates vertex offset deltas { shape_key_name: { vert_index: (dx, dy, dz) } }."""
    H = landmarks.get("head_length", 0.20)
    deltas = {
        "eyeBlink_L": {},
        "eyeBlink_R": {},
        "jawOpen": {},
        "mouthSmile": {},
        "viseme_aa": {},
    }

    blink_drop = -0.045 * H
    # Left eye blink
    for i, vx, vy, vz in landmarks.get("eyes_L", []):
        deltas["eyeBlink_L"][i] = (0.0, 0.005 * H, blink_drop)

    # Right eye blink
    for i, vx, vy, vz in landmarks.get("eyes_R", []):
        deltas["eyeBlink_R"][i] = (0.0, 0.005 * H, blink_drop)

    # Jaw open
    jaw_drop = -0.09 * H
    jaw_back = 0.035 * H
    for i, vx, vy, vz in landmarks.get("jaw", []):
        deltas["jawOpen"][i] = (0.0, jaw_back, jaw_drop)
    for i, vx, vy, vz in landmarks.get("mouth_center", []):
        deltas["jawOpen"][i] = (0.0, jaw_back * 0.5, jaw_drop * 0.5)

    # Mouth smile
    smile_up = 0.03 * H
    for i, vx, vy, vz in landmarks.get("mouth_corner_L", []):
        deltas["mouthSmile"][i] = (0.015 * H, -0.005 * H, smile_up)
    for i, vx, vy, vz in landmarks.get("mouth_corner_R", []):
        deltas["mouthSmile"][i] = (-0.015 * H, -0.005 * H, smile_up)

    # Viseme aa (open oval mouth)
    for i, vx, vy, vz in landmarks.get("mouth_center", []):
        deltas["viseme_aa"][i] = (0.0, 0.01 * H, -0.04 * H)
    for i, vx, vy, vz in landmarks.get("jaw", []):
        deltas["viseme_aa"][i] = (0.0, 0.015 * H, -0.05 * H)

    return deltas


def generate_blender_shape_keys(mesh, arm):
    """Creates Blender Shape Keys on the mesh object from detected landmarks."""
    try:
        import bpy
    except ImportError:
        return []

    # Find head bone position
    head_bone = None
    for cand in ("Head", "head", "Neck", "neck", "mixamorig:Head"):
        if cand in arm.data.bones:
            head_bone = arm.data.bones[cand]
            break
    if not head_bone:
        return []

    mw = arm.matrix_world
    mesh_mw = mesh.matrix_world
    mw_inv = mesh_mw.inverted()

    # Head pos in mesh local coordinates
    head_world = mw @ head_bone.head_local
    head_local = mw_inv @ head_world
    head_len = head_bone.length if head_bone.length > 0.05 else 0.20

    verts_local = [v.co[:] for v in mesh.data.vertices]
    landmarks = identify_facial_landmarks(verts_local, head_local, head_len)
    if not landmarks or (not landmarks.get("eyes_L") and not landmarks.get("jaw")):
        return []

    deltas = compute_shape_key_deltas(landmarks, len(verts_local))

    # Add shape keys in Blender
    if not mesh.data.shape_keys:
        mesh.shape_key_add(name="Basis")

    created = []
    for key_name, vert_deltas in deltas.items():
        if not vert_deltas:
            continue
        sk = mesh.shape_key_add(name=key_name)
        for idx, (dx, dy, dz) in vert_deltas.items():
            orig = mesh.data.shape_keys.key_blocks["Basis"].data[idx].co
            sk.data[idx].co = (orig[0] + dx, orig[1] + dy, orig[2] + dz)
        sk.value = 0.0
        created.append(key_name)

    return created

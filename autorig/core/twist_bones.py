# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Dual-Segment Twist Bones and Volume Preservation.
#
# Eliminates linear blend skinning (LBS) "candy-wrapper" joint pinch and volume loss
# when wrists, shoulders, and hips rotate axially (pronation / supination).
#
# Industry standard parallel subordinate hierarchy:
# 1. Main skeletal hierarchy remains unchanged (preserves IK chains & game retargeting).
# 2. Subordinate twist bones (e.g. LeftForeArm_Twist) absorb 50% of the child joint's axial roll.
# 3. Smooth longitudinal weight distribution along the limb segment preserves cross-sectional volume.

import math

def smoothstep(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / max(1e-6, edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def identify_twist_pairs(bone_names):
    """Finds candidate limb bones and their respective twist reference drivers.
    Returns list of dicts:
      [
        {"base": "LeftForeArm", "driver": "LeftHand", "type": "forearm", "twist": "LeftForeArm_Twist"},
        ...
      ]
    """
    names_set = set(bone_names)
    pairs = []

    # Patterns for forearms (driven by wrist/hand roll)
    forearm_patterns = [
        ("LeftForeArm", "LeftHand"),
        ("RightForeArm", "RightHand"),
        ("forearm.L", "hand.L"),
        ("forearm.R", "hand.R"),
        ("mixamorig:LeftForeArm", "mixamorig:LeftHand"),
        ("mixamorig:RightForeArm", "mixamorig:RightHand"),
    ]
    for b_base, b_driver in forearm_patterns:
        if b_base in names_set and b_driver in names_set:
            twist_name = b_base + "_Twist"
            pairs.append({"base": b_base, "driver": b_driver, "type": "forearm", "twist": twist_name})

    # Patterns for upper arms (driven by forearm or shoulder roll)
    arm_patterns = [
        ("LeftArm", "LeftForeArm"),
        ("RightArm", "RightForeArm"),
        ("arm.L", "forearm.L"),
        ("arm.R", "forearm.R"),
        ("mixamorig:LeftArm", "mixamorig:LeftForeArm"),
        ("mixamorig:RightArm", "mixamorig:RightForeArm"),
    ]
    for b_base, b_driver in arm_patterns:
        if b_base in names_set and b_driver in names_set:
            twist_name = b_base + "_Twist"
            pairs.append({"base": b_base, "driver": b_driver, "type": "arm", "twist": twist_name})

    # Patterns for thighs (driven by lower leg roll)
    thigh_patterns = [
        ("LeftUpLeg", "LeftLeg"),
        ("RightUpLeg", "RightLeg"),
        ("thigh.L", "shin.L"),
        ("thigh.R", "shin.R"),
        ("mixamorig:LeftUpLeg", "mixamorig:LeftLeg"),
        ("mixamorig:RightUpLeg", "mixamorig:RightLeg"),
    ]
    for b_base, b_driver in thigh_patterns:
        if b_base in names_set and b_driver in names_set:
            twist_name = b_base + "_Twist"
            pairs.append({"base": b_base, "driver": b_driver, "type": "thigh", "twist": twist_name})

    return pairs


def calculate_weight_split(along_ratio, limb_type="forearm"):
    """Calculates the weight fraction allocated to the twist bone vs the base bone.
    along_ratio: 0.0 at parent joint (e.g. elbow), 1.0 at child joint (e.g. wrist).

    Returns: (base_fraction, twist_fraction) where base_fraction + twist_fraction == 1.0.
    """
    t = max(0.0, min(1.0, float(along_ratio)))
    if limb_type == "forearm":
        # Elbow retains 100% base bone; twist bone smoothly takes over up to 50% near the wrist
        twist_f = smoothstep(0.25, 0.95, t) * 0.50
    elif limb_type == "arm":
        # Upper arm: twist bone near the shoulder absorbs up to 50% roll
        twist_f = (1.0 - smoothstep(0.05, 0.75, t)) * 0.50
    else:  # thigh
        twist_f = (1.0 - smoothstep(0.05, 0.75, t)) * 0.50

    return (1.0 - twist_f, twist_f)


def add_twist_bones_to_armature(arm, mesh=None, spec=None):
    """Inserts dual-segment twist bones into a Blender armature object and sets up
    subordinate Copy Rotation constraints on the local longitudinal axis (Y)."""
    try:
        import bpy
        from mathutils import Vector
    except ImportError:
        return []

    bone_names = [b.name for b in arm.data.bones]
    pairs = identify_twist_pairs(bone_names)
    if not pairs:
        return []

    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones

    created = []
    for p in pairs:
        b_base = p["base"]
        t_name = p["twist"]
        if b_base not in eb or t_name in eb:
            continue

        base_b = eb[b_base]
        twist_b = eb.new(t_name)

        # Position twist bone at the distal half of the bone
        head_pos = base_b.head.lerp(base_b.tail, 0.5)
        tail_pos = base_b.tail.copy()

        twist_b.head = head_pos
        twist_b.tail = tail_pos
        twist_b.roll = base_b.roll
        twist_b.parent = base_b
        twist_b.use_connect = False
        twist_b.use_deform = True

        created.append(p)

    bpy.ops.object.mode_set(mode='POSE')
    for p in created:
        t_name = p["twist"]
        driver_name = p["driver"]
        if t_name not in arm.pose.bones or driver_name not in arm.pose.bones:
            continue

        t_pb = arm.pose.bones[t_name]
        # Remove any existing constraints
        for c in list(t_pb.constraints):
            t_pb.constraints.remove(c)

        cr = t_pb.constraints.new('COPY_ROTATION')
        cr.name = "Twist_Axial_Roll"
        cr.target = arm
        cr.subtarget = driver_name
        cr.target_space = 'LOCAL'
        cr.owner_space = 'LOCAL'
        cr.use_x = False
        cr.use_y = True   # Blender edit bones longitudinal roll axis is Y
        cr.use_z = False
        cr.influence = 0.50

    bpy.ops.object.mode_set(mode='OBJECT')

    # If mesh is provided, distribute the skin weights
    if mesh is not None and created:
        apply_twist_weights_to_mesh(mesh, arm, created)

    return created


def apply_twist_weights_to_mesh(mesh, arm, twist_pairs):
    """Redistributes skin weights from base limb bones to their subordinate twist bones."""
    try:
        import bpy
        from mathutils import Vector
    except ImportError:
        return

    mw = arm.matrix_world
    mw_inv = mw.inverted()
    mesh_mw = mesh.matrix_world

    for p in twist_pairs:
        base_name = p["base"]
        twist_name = p["twist"]
        limb_type = p["type"]

        if base_name not in mesh.vertex_groups:
            continue

        base_vg = mesh.vertex_groups[base_name]
        base_idx = base_vg.index

        if twist_name not in mesh.vertex_groups:
            twist_vg = mesh.vertex_groups.new(name=twist_name)
        else:
            twist_vg = mesh.vertex_groups[twist_name]

        twist_idx = twist_vg.index

        # Bone rest coordinates in armature space
        base_b = arm.data.bones[base_name]
        b_head = base_b.head_local
        b_tail = base_b.tail_local
        b_vec = b_tail - b_head
        b_len_sq = max(1e-8, b_vec.length_squared)

        # Inspect all vertices having non-zero weight for base bone
        for v in mesh.data.vertices:
            # Check weight
            w = 0.0
            for g in v.groups:
                if g.group == base_idx:
                    w = g.weight
                    break
            if w <= 1e-4:
                continue

            # Vertex in armature space
            v_world = mesh_mw @ v.co
            v_arm = mw_inv @ v_world

            # Projection along bone axis
            along = (v_arm - b_head).dot(b_vec) / b_len_sq
            base_frac, twist_frac = calculate_weight_split(along, limb_type)

            if twist_frac > 1e-4:
                new_base_w = w * base_frac
                new_twist_w = w * twist_frac
                base_vg.add([v.index], new_base_w, 'REPLACE')
                twist_vg.add([v.index], new_twist_w, 'REPLACE')

    # Enforce standard game engine limit of 4 influences per vertex
    try:
        prev_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
        bpy.ops.object.vertex_group_clean(group_select_mode='ALL', limit=1e-4)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL')
        bpy.context.view_layer.objects.active = prev_active
    except Exception:
        pass

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Dual-Segment Twist Bones and Volume Preservation.
#
# Eliminates linear blend skinning (LBS) "candy-wrapper" joint pinch and volume loss
# when wrists, shoulders, and hips rotate axially (pronation / supination).
#
# Industry standard parallel subordinate hierarchy:
# 1. Main skeletal hierarchy remains unchanged (preserves IK chains & game retargeting).
# 2. Subordinate twist bones: a forearm's (e.g. LeftForeArm_Twist, by the wrist) takes half the hand's twist; an
#    upper arm's or thigh's (by the shoulder or hip) holds back half of its own bone's twist. Drivers read the twist
#    alone (swing-twist about Y), never the swing of a bending wrist.
# 3. Smooth longitudinal weight distribution along the limb segment preserves cross-sectional volume.

import math, re

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

    # Rigify: upper_arm.L -> forearm.L -> hand.L, thigh.L -> shin.L (thigh/shin are above)
    for s in (".L", ".R"):
        if "upper_arm" + s in names_set and "forearm" + s in names_set:
            pairs.append({"base": "upper_arm" + s, "driver": "forearm" + s, "type": "arm", "twist": "upper_arm_twist" + s})

    # This tool's own chains (rerig.name_chains): arm_1.L upper arm, arm_2.L forearm, arm_3.L hand (arm_0.L the
    # girdle); leg_1.L / leg_front_1.L / leg_hind_1.L / leg2_1.L the thigh, _2 the shin
    for n in bone_names:
        m = OWN_RE.match(n)
        if not m:
            continue
        base, link, side = m.group(1), int(m.group(2)), m.group(3)
        nxt = "%s_%d%s" % (base, link + 1, side)
        if nxt not in names_set or link not in (1, 2):
            continue
        is_arm = base.startswith("arm")
        if link == 1:
            pairs.append({"base": n, "driver": nxt, "type": "arm" if is_arm else "thigh",
                          "twist": "%s_%d_twist%s" % (base, link, side)})
        elif is_arm:                                    # forearm, driven by the hand
            pairs.append({"base": n, "driver": nxt, "type": "forearm", "twist": "%s_%d_twist%s" % (base, link, side)})

    seen, out = set(), []
    for p in pairs:
        if p["base"] not in seen:
            seen.add(p["base"])
            out.append(p)
    return out


OWN_RE = re.compile(r"^(arm|leg|leg_front|leg_hind|leg\d+)_(\d+)(\.[LR])$")


def twist_segment(limb_type):
    """(start, end) of the twist bone along its base bone, 0 at the base's head: the half its weights cover. A
    forearm's twist bone is the half by the wrist (it takes the hand's twist); an upper arm's or thigh's is the half
    by the shoulder or hip (it holds back the bone's own twist there)."""
    return (0.5, 1.0) if limb_type == "forearm" else (0.0, 0.5)


def twist_driver(limb_type):
    """(which bone drives it: "driver" or "base", factor) for the twist bone's local Y twist. The forearm's twist
    bone takes half the hand's twist; an upper arm's or thigh's takes back half of its own base's twist, so the
    shoulder or hip end turns half as far as the elbow or knee end."""
    return ("driver", 0.5) if limb_type == "forearm" else ("base", -0.5)


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

    # Edit mode works on the active object: the armature must be it (after skinning the mesh usually is), and the
    # scene is always handed back in object mode with the previous active object, whatever happens in between.
    vl = bpy.context.view_layer
    prev_active = vl.objects.active
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    arm.hide_set(False)
    vl.objects.active = arm
    arm.select_set(True)

    created = []
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        eb = arm.data.edit_bones
        for p in pairs:
            b_base = p["base"]
            t_name = p["twist"]
            if b_base not in eb or t_name in eb:
                continue

            base_b = eb[b_base]
            twist_b = eb.new(t_name)

            # on the half of the bone its weights cover (twist_segment)
            a, b = twist_segment(p["type"])
            twist_b.head = base_b.head.lerp(base_b.tail, a)
            twist_b.tail = base_b.head.lerp(base_b.tail, b)
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
            for c in list(t_pb.constraints):
                t_pb.constraints.remove(c)
            # The twist alone, never the swing: a driver reading the source bone's local rotation as swing-twist
            # about Y (a Copy Rotation of local Y also picked up the swing of a bending wrist)
            which, factor = twist_driver(p["type"])
            source = driver_name if which == "driver" else p["base"]
            t_pb.rotation_mode = 'YXZ'
            try:
                t_pb.driver_remove("rotation_euler", 1)
            except Exception:
                pass
            fc = t_pb.driver_add("rotation_euler", 1)
            drv = fc.driver
            drv.type = 'SCRIPTED'
            drv.expression = "twist * %g" % factor          # a simple expression: runs without Python scripts
            var = drv.variables.new()
            var.name = "twist"
            var.type = 'TRANSFORMS'
            tgt = var.targets[0]
            tgt.id = arm
            tgt.bone_target = source
            tgt.transform_type = 'ROT_Y'
            tgt.rotation_mode = 'SWING_TWIST_Y'
            tgt.transform_space = 'LOCAL_SPACE'
    finally:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        vl.objects.active = prev_active

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

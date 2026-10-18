# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Blender half of m2m_clips.py. Mesh2Motion human clips baked onto a Mixamo-rigged person.
#
#   blender -b --python autorig/steps/m2m_clips_worker.py -- '<config json>'
#
# config: {"target_fbx": the person's FBX (fbx_maps.py's, textures beside it), "out_fbx": the FBX to write with one
# take per clip, "preview_glb": a GLB of the same for the viewer and clip-check (or null), "fps": 30,
# "clips": [{"glb": a Mesh2Motion animation library, "action": its animation, "take": the take's name,
# "strip_travel": true to play an *_RM clip in place}]}.
#
# Each library is imported once; its skeleton (Mesh2Motion's Human, rigs/rig-human.glb with longer arms) drives the
# person's through retarget_worker.bake_clip, the retargeter's own bake: the world-space change of every mapped bone
# from its rest, rests aligned limb by limb (Mesh2Motion's library rests in a T-pose, a Mixamo upload in whatever pose
# it was sent in), and the pelvis's translation onto Hips. Before baking, the library's armature is turned and scaled
# about the world origin so its hips face the person's way and stand at the person's leg height: then a walk's stride
# and bob fit the legs that take it, and the feet land where the library's did. Mesh2Motion (MIT code, CC0 clips,
# (c) 2025 Scott Petrovic): https://github.com/Mesh2Motion/mesh2motion-app
# Prints M2M_CLIPS_WORKER {json}.
import json, math, os, sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "core"), HERE]
import skeletons  # noqa: E402
from retarget_worker import action_fcurves, bake_clip  # noqa: E402


def config():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[-1:]
    return json.loads(argv[0])


def mapping_for(src_bones, tgt_bones):
    """{source bone: target bone} by canonical role: the skeletons.py conventions on both sides (mesh2motion,
    mixamo). Imported here rather than through retargeter.py, which wants the workbench's folders set up."""
    src, tgt = skeletons.canonical_roles(src_bones), skeletons.canonical_roles(tgt_bones)
    by_role = {}
    for b in tgt_bones:
        if tgt[b] and tgt[b] not in by_role: by_role[tgt[b]] = b
    return {s: by_role[r] for s, r in src.items() if r and r in by_role}


def world_head(arm, name):
    return arm.matrix_world @ arm.data.bones[name].head_local


def placement(src_arm, tgt_arm, mapping, src_roles):
    """The world matrix that turns and scales the library's armature (about the origin) onto the person: hips across
    the same way (left thigh minus right thigh, seen from above), hips at the same height above the toes."""
    inv = {r: s for s, r in src_roles.items() if r and s in mapping}
    need = ("hips", "thigh.L", "thigh.R", "toe.L", "toe.R")
    if not all(r in inv for r in need):
        raise SystemExit("m2m_clips_worker: the library skeleton is missing " +
                         ", ".join(r for r in need if r not in inv))
    s = {r: inv[r] for r in need}
    t = {r: mapping[s[r]] for r in need}

    def across(arm, names):
        v = world_head(arm, names["thigh.L"]) - world_head(arm, names["thigh.R"])
        return math.atan2(v.y, v.x)

    def leg(arm, names):
        toes = (world_head(arm, names["toe.L"]).z + world_head(arm, names["toe.R"]).z) / 2
        return world_head(arm, names["hips"]).z - toes

    yaw = across(tgt_arm, t) - across(src_arm, s)
    yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    scale = leg(tgt_arm, t) / max(leg(src_arm, s), 1e-6)
    return Matrix.Rotation(yaw, 4, "Z") @ Matrix.Scale(scale, 4), math.degrees(yaw), scale


def find_action(name, actions):
    """The imported action for a glTF animation: its own name, or the importer's "<name>_<object>" form."""
    exact = [a for a in actions if a.name == name]
    if exact: return exact[0]
    loose = [a for a in actions if a.name.startswith(name + "_") or a.name.split("|")[-1] == name]
    return loose[0] if len(loose) == 1 else None


def feet_of(roles):
    return [b for b, r in roles.items() if r in ("foot.L", "foot.R", "toe.L", "toe.R", "toetip.L", "toetip.R")]


def lowest(arm, bones, rest=False):
    if rest:
        return min((arm.matrix_world @ arm.data.bones[b].head_local).z for b in bones)
    return min((arm.matrix_world @ arm.pose.bones[b].head).z for b in bones)


def keep_feet_down(src_arm, tgt_arm, action, hips, start, n_out, src_feet, tgt_feet):
    """Raises or lowers Hips, frame by frame, so the person's lowest foot joint is as far above its floor as the
    library's is above its own. The rotations carry over exactly, but a person's thigh, shin and foot are not in the
    library's proportions: a jog that bends the knees deep put Ice's feet 6 cm into the floor. Returns the largest
    correction (metres)."""
    src_floor, tgt_floor = lowest(src_arm, src_feet, True), lowest(tgt_arm, tgt_feet, True)
    scene, fixes = bpy.context.scene, []
    tgt_arm.animation_data.action = action
    for k in range(n_out):
        scene.frame_set(start + k)
        fixes.append((lowest(src_arm, src_feet) - src_floor) - (lowest(tgt_arm, tgt_feet) - tgt_floor))
    db = tgt_arm.data.bones[hips]
    to_local = db.matrix_local.to_3x3().inverted() @ tgt_arm.matrix_world.to_3x3().inverted()
    path = 'pose.bones["%s"].location' % hips
    curves = {fc.array_index: fc for fc in action_fcurves(action) if fc.data_path == path}
    for k, dz in enumerate(fixes):
        d = to_local @ Vector((0.0, 0.0, dz))
        for i, fc in curves.items():
            for kp in fc.keyframe_points:
                if abs(kp.co.x - (start + k)) < 1e-3:
                    kp.co.y += d[i]; kp.handle_left.y += d[i]; kp.handle_right.y += d[i]
    for fc in curves.values(): fc.update()
    return max((abs(f) for f in fixes), default=0.0)


def legs_of(src_roles, tgt_roles):
    """[(source thigh, knee, ankle), (target thigh, knee, ankle)] for each side both skeletons have."""
    s = {r: b for b, r in src_roles.items() if r}
    t = {r: b for b, r in tgt_roles.items() if r}
    out = []
    for side in ("L", "R"):
        roles = ("thigh." + side, "shin." + side, "foot." + side)
        if all(r in s and r in t for r in roles):
            out.append((tuple(s[r] for r in roles), tuple(t[r] for r in roles)))
    return out


def _swing(pb, pivot, old_dir, new_dir):
    """Turns a pose bone (armature space) about `pivot` so old_dir points along new_dir."""
    q = old_dir.rotation_difference(new_dir)
    m = pb.matrix.copy()
    pb.matrix = Matrix.Translation(pivot) @ (q.to_matrix() @ m.to_3x3()).to_4x4()


def plant_legs(src_arm, tgt_arm, action, start, n_out, legs):
    """Each of the person's legs re-solved, frame by frame, so its ankle is where the library's ankle is (offset by
    the two rests' difference): two-bone IK in the plane the library's own knee bends in, the foot keeping its baked
    orientation. The rotations alone put a planted foot where a leg of the library's proportions would put it, so a
    person with a longer shin or shorter thigh skated (Ice's jog: 2.7 times the library's own skate). Returns the
    largest distance an ankle was moved (metres)."""
    if not legs:
        return 0.0
    scene = bpy.context.scene
    sw, tw = src_arm.matrix_world, tgt_arm.matrix_world
    inv = tw.inverted()
    offset = {t[2]: (tw @ tgt_arm.data.bones[t[2]].head_local) - (sw @ src_arm.data.bones[s[2]].head_local)
              for s, t in legs}
    tgt_arm.animation_data.action = action
    moved = 0.0
    for k in range(n_out):
        scene.frame_set(start + k)
        for s, t in legs:
            sp = [sw @ src_arm.pose.bones[b].head for b in s]
            hip, knee, foot = (tgt_arm.pose.bones[b] for b in t)
            keep = foot.matrix.to_3x3().copy()
            goal = inv @ (sp[2] + offset[t[2]])
            H, K, A = hip.head.copy(), knee.head.copy(), foot.head.copy()
            a, b = (K - H).length, (A - K).length
            reach = goal - H
            if reach.length < 1e-6 or a < 1e-6 or b < 1e-6:
                continue
            d = max(abs(a - b) + 1e-4, min(reach.length, (a + b) * 0.9999))
            u = reach.normalized()
            # the bend: the library's knee, off its own hip-to-ankle line, in the person's armature space
            pole = inv.to_3x3() @ ((sp[1] - sp[0]) - (sp[2] - sp[0]).normalized() * (sp[1] - sp[0]).dot(
                (sp[2] - sp[0]).normalized()))
            pole = pole - u * pole.dot(u)
            if pole.length < 1e-6:
                pole = (K - H) - u * (K - H).dot(u)
            if pole.length < 1e-6:
                continue
            x = (a * a - b * b + d * d) / (2 * d)
            K2 = H + u * x + pole.normalized() * math.sqrt(max(a * a - x * x, 0.0))
            A2 = H + u * d
            moved = max(moved, (tw.to_3x3() @ (A2 - A)).length)
            _swing(hip, H, K - H, K2 - H)
            bpy.context.view_layer.update()
            K1, A1 = knee.head.copy(), foot.head.copy()
            _swing(knee, K1, A1 - K1, A2 - K1)
            bpy.context.view_layer.update()
            foot.matrix = Matrix.Translation(foot.head) @ keep.to_4x4()
            bpy.context.view_layer.update()
            for pb in (hip, knee, foot):
                pb.keyframe_insert("rotation_quaternion", frame=start + k)
    return moved


def shift_to_zero(action, start):
    if start:
        for fc in action_fcurves(action):
            for kp in fc.keyframe_points:
                kp.co.x -= start; kp.handle_left.x -= start; kp.handle_right.x -= start
            fc.update()


def main():
    cfg = config()
    fps = float(cfg.get("fps") or 30)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = max(1, int(round(fps))), max(1, int(round(fps))) / fps
    bpy.ops.import_scene.fbx(filepath=os.path.abspath(cfg["target_fbx"]), ignore_leaf_bones=False)
    person = list(bpy.data.objects)
    tgt_arm = next((o for o in person if o.type == "ARMATURE"), None)
    if tgt_arm is None:
        raise SystemExit("m2m_clips_worker: no armature in " + cfg["target_fbx"])
    tgt_bones = [b.name for b in tgt_arm.data.bones]
    conv, _ = skeletons.detect_convention(tgt_bones)
    tgt_roles = skeletons.canonical_roles(tgt_bones)
    report = {"target": cfg["target_fbx"], "convention": conv, "takes": []}
    kept = set(bpy.data.actions)
    made = []

    by_glb = {}
    for c in cfg["clips"]:
        by_glb.setdefault(os.path.abspath(c["glb"]), []).append(c)
    for glb, clips in by_glb.items():
        before_objs, before_actions = set(bpy.data.objects), set(bpy.data.actions)
        bpy.ops.import_scene.gltf(filepath=glb)
        new = [o for o in bpy.data.objects if o not in before_objs]
        src_arm = next((o for o in new if o.type == "ARMATURE"), None)
        if src_arm is None:
            raise SystemExit("m2m_clips_worker: no armature in " + glb)
        for o in new:                                  # its mesh would only be deleted later
            if o.type == "MESH": bpy.data.objects.remove(o, do_unlink=True)
        imported = [a for a in bpy.data.actions if a not in before_actions]
        ad = src_arm.animation_data or src_arm.animation_data_create()
        for t in ad.nla_tracks: t.mute = True
        ad.use_nla = False
        src_bones = [b.name for b in src_arm.data.bones]
        mapping = mapping_for(src_bones, tgt_bones)
        src_roles = skeletons.canonical_roles(src_bones)
        hips = next(s for s, r in src_roles.items() if r == "hips")
        move, yaw, scale = placement(src_arm, tgt_arm, mapping, src_roles)
        src_arm.matrix_world = move @ src_arm.matrix_world
        bpy.context.view_layer.update()
        print("M2M_CLIPS_WORKER placed %s: yaw %.1f deg, scale %.3f, %d bones mapped"
              % (os.path.basename(glb), yaw, scale, len(mapping)))
        for c in clips:
            act = find_action(c["action"], imported)
            if act is None:
                raise SystemExit("m2m_clips_worker: no animation '%s' in %s" % (c["action"], glb))
            ad.action = act
            if getattr(act, "slots", None) and len(act.slots) and ad.action_slot is None:
                ad.action_slot = act.slots[0]
            start, end = act.frame_range
            start, end = int(math.floor(start)), int(math.ceil(end))
            action, n_out, pairs = bake_clip(
                src_arm, tgt_arm, mapping, c["take"], start, end, fps, fps, root_motion=True,
                root_pair=(hips, mapping[hips]), scale_factor=1.0, solve_offsets=True, keep_actions=kept,
                travel_bone="root" if c.get("strip_travel") and "root" in src_arm.pose.bones else None)
            lifted = keep_feet_down(src_arm, tgt_arm, action, mapping[hips], start, n_out,
                                    feet_of(src_roles), feet_of(tgt_roles))
            planted = plant_legs(src_arm, tgt_arm, action, start, n_out, legs_of(src_roles, tgt_roles))                 if cfg.get("plant", True) else 0.0
            shift_to_zero(action, start)
            action["autorig_take"] = c["take"]
            action["autorig_retarget_source"] = "mesh2motion:%s:%s" % (os.path.basename(glb), c["action"])
            action.use_frame_range = True
            action.frame_start, action.frame_end = 0, n_out - 1
            tgt_arm.animation_data.action = None
            made.append(action)
            report["takes"].append({"take": c["take"], "from": c["action"], "library": os.path.basename(glb),
                                    "frames": n_out, "fps": fps, "mapped_bones": len(pairs),
                                    "travel_removed": bool(c.get("strip_travel")), "yaw": round(yaw, 2),
                                    "scale": round(scale, 4), "feet_fix_m": round(lifted, 4),
                                    "ankle_fix_m": round(planted, 4)})
            print("M2M_CLIPS_WORKER baked %s <- %s (%d frames)" % (c["take"], c["action"], n_out))
        for o in [o for o in bpy.data.objects if o not in before_objs]:
            bpy.data.objects.remove(o, do_unlink=True)
        for a in imported:
            if a not in made: bpy.data.actions.remove(a)
        for action in made:                            # "Walk.001" while the library's own Walk was loaded
            action.name = action["autorig_take"]

    ad = tgt_arm.animation_data or tgt_arm.animation_data_create()
    ad.action = None
    for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)
    for action in made:
        tr = ad.nla_tracks.new(); tr.name = action.name
        st = tr.strips.new(action.name, 0, action); st.name = action.name
    for pb in tgt_arm.pose.bones: pb.matrix_basis.identity()
    scene.frame_set(0)

    bpy.ops.object.select_all(action="DESELECT")
    for o in person:
        o.select_set(o.type in ("MESH", "ARMATURE"))
    bpy.context.view_layer.objects.active = tgt_arm
    out = os.path.abspath(cfg["out_fbx"])
    # fbx_maps.py's settings, with the takes laid out as make_clips.py lays them out
    bpy.ops.export_scene.fbx(filepath=out, use_selection=True, object_types={"ARMATURE", "MESH"},
                             apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True,
                             axis_forward="-Z", axis_up="Y", add_leaf_bones=False, use_armature_deform_only=True,
                             bake_anim=True, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=True,
                             bake_anim_use_all_bones=True, bake_anim_force_startend_keying=True,
                             bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
                             mesh_smooth_type="OFF", path_mode="RELATIVE", embed_textures=False)
    report["out_fbx"] = out
    if cfg.get("preview_glb"):
        glb = os.path.abspath(cfg["preview_glb"])
        os.makedirs(os.path.dirname(glb), exist_ok=True)
        bpy.ops.export_scene.gltf(
            filepath=glb, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
            export_texcoords=True, export_materials="EXPORT", export_image_format="AUTO", export_skins=True,
            export_animations=True, export_animation_mode="NLA_TRACKS", export_force_sampling=True,
            export_frame_range=False, export_frame_step=1, export_reset_pose_bones=True,
            export_optimize_animation_size=False, export_bake_animation=False, export_anim_slide_to_zero=True)
        report["preview_glb"] = glb
    print("M2M_CLIPS_WORKER " + json.dumps(report))


if __name__ == "__main__":
    main()

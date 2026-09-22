# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a viewer copy of a rigged model, as one GLB the GUI's 3D viewer (gui/viewer.html) loads.
#
# The engine files stay FBX (rigged/<model>.fbx, clips/<model>.fbx). three.js reads FBX from Blender unreliably
# (axes, scale, takes), so this writes a glTF binary beside the rig instead, for looking at and nothing else:
#
#   <rig folder>/preview.glb    the full-resolution mesh with its materials and textures, the armature, and every
#                               clip as a named glTF animation (sampled every frame, constraints baked in)
#   <rig folder>/preview.json   what went in: the file it came from, the clips with their frames, bone counts, the
#                               card's real size, the facing
#
# It reads the rig's .blend (full resolution, textured) and takes the clips from clips/<model>_clips.blend when
# that exists (make_clips.py), so a re-rig after the clips still previews with them; with no .blend it imports the
# rigged FBX and uses its takes. The model's own files are never written to.
#
#   blender -b --python autorig/steps/preview_glb.py -- -only wolf,moth
#   blender -b --python autorig/steps/preview_glb.py -- [<group>]            every rigged model in a group (or root)
#   blender -b --python autorig/steps/preview_glb.py -- -file <rig.blend|rig.fbx> [-out <preview.glb>]
#
# Prints one PREVIEW line of JSON per model, then PREVIEW_DONE.
import bpy, sys, os, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import layout

FORMAT = "autorig-preview/1"


def rig_files(key):
    """(blend, fbx, clips blend) for a model: each path, or None where it does not exist."""
    rd = layout.rigged_dir(key)
    name = layout.leaf(key)
    have = lambda p: p if os.path.exists(p) else None
    return (have(os.path.join(rd, name + ".blend")), have(os.path.join(rd, name + ".fbx")),
            have(os.path.join(layout.pack_dir(key), "clips", name + "_clips.blend")))


def load(path):
    if path.lower().endswith(".blend"):
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=path, ignore_leaf_bones=False, automatic_bone_orientation=False)
        # the importer names each take "<armature>|<take>": the clip is the take
        for a in list(bpy.data.actions):
            if "|" in a.name:
                a.name = a.name.split("|", 1)[1]
    # the rig step's QA props (markers, cameras) are not the model
    for o in [o for o in bpy.data.objects if o.name.startswith("qa_")]:
        bpy.data.objects.remove(o, do_unlink=True)


def the_rig():
    """The armature that deforms the most mesh, and every mesh it moves (skinned to it or parented to it)."""
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    def moved(a):
        return [o for o in bpy.data.objects if o.type == 'MESH' and (o.find_armature() == a or o.parent == a)]
    if not arms:
        return None, [o for o in bpy.data.objects if o.type == 'MESH']
    arm = max(arms, key=lambda a: (sum(len(m.data.vertices) for m in moved(a)), len(a.data.bones)))
    return arm, moved(arm)


def bring_clips(path):
    """Appends the clips' actions from make_clips.py's .blend. It is the newer authority: an action of the same name
    already in the rig's file is replaced."""
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        names = list(src.actions)
    for n in names:
        if n in bpy.data.actions:
            bpy.data.actions.remove(bpy.data.actions[n])
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.actions = names
    return [a.name for a in dst.actions if a is not None]


def clip_actions(arm):
    """The actions that animate this armature's bones: the clips."""
    bones = {b.name for b in arm.data.bones} if arm else set()
    out = []
    for a in bpy.data.actions:
        paths = [fc.data_path for fc in action_fcurves(a)]
        if any(p.startswith('pose.bones["') and p.split('"')[1] in bones for p in paths):
            out.append(a)
    return out


def action_fcurves(action):
    """Every F-curve of an action: the action's own list, or (slotted actions, Blender 4.4+) its layers' channelbags."""
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


def frames_of(action):
    lo, hi = action.frame_range
    return int(round(lo)), int(round(hi))


def card_metres(key):
    """The model's real length along its longest axis, from its card or clip manifest, if either says."""
    pack = layout.pack_dir(key)
    for p in (os.path.join(pack, "model.json"), os.path.join(pack, "clips", layout.leaf(key) + "_clips.json")):
        try:
            m = json.load(open(p, encoding="utf-8")).get("metres")
            if m: return float(m)
        except Exception:
            pass
    return None


def export(arm, meshes, out):
    bpy.ops.object.select_all(action='DESELECT')
    for o in ([arm] if arm else []) + meshes:
        o.hide_set(False); o.hide_viewport = False; o.select_set(True)
    bpy.context.view_layer.objects.active = arm or meshes[0]
    if arm:
        # the rest pose is the bind pose: nothing left playing in the file, frame 0
        arm.animation_data_create()
        arm.animation_data.action = None
        bpy.context.scene.frame_set(0)
    bpy.ops.export_scene.gltf(
        filepath=out, export_format='GLB', use_selection=True, export_yup=True, export_apply=False,
        export_texcoords=True, export_materials='EXPORT', export_image_format='AUTO',
        export_skins=True, export_def_bones=False, export_leaf_bone=False,
        export_animations=bool(arm), export_animation_mode='ACTIONS', export_force_sampling=True,
        export_frame_range=False, export_frame_step=1, export_anim_single_armature=True,
        export_reset_pose_bones=True, export_optimize_animation_size=False, export_bake_animation=False,
        export_negative_frame='SLIDE', export_anim_slide_to_zero=True, export_morph=True)


def run(src, out, key=None, clips_blend=None):
    t0 = time.time()
    load(src)
    appended = bring_clips(clips_blend) if clips_blend else []
    arm, meshes = the_rig()
    if not meshes:
        return {"model": key, "error": "no mesh in " + os.path.basename(src)}
    acts = clip_actions(arm)
    for a in acts:
        a.use_fake_user = True
    fps = bpy.context.scene.render.fps / (bpy.context.scene.render.fps_base or 1.0)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    export(arm, meshes, out)

    clips = []
    for a in sorted(acts, key=lambda a: a.name):
        lo, hi = frames_of(a)
        clips.append({"name": a.name, "frames": hi - lo + 1, "seconds": round((hi - lo) / fps, 4)})
    bones = list(arm.data.bones) if arm else []
    info = {
        "format": FORMAT,
        "model": key or os.path.splitext(os.path.basename(src))[0],
        "from": os.path.basename(src),
        "clips_from": os.path.basename(clips_blend) if clips_blend else None,
        "glb": os.path.basename(out),
        "fps": fps,
        "bones": len(bones),
        "deform_bones": sum(1 for b in bones if b.use_deform),
        "meshes": len(meshes),
        "vertices": sum(len(m.data.vertices) for m in meshes),
        "clips": clips,
        # glTF keeps a joint's head and its axes (Blender's +Y runs along the bone) but not its length
        "bone_lengths": {b.name: round(b.length, 6) for b in bones},
        "deform": sorted(b.name for b in bones if b.use_deform),
        "metres": card_metres(key) if key else None,
        "facing": "+Z in the GLB (glTF is Y up): the rig faces Blender's -Y",
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(info, fh, indent=1)
    info["seconds_taken"] = round(time.time() - t0, 1)
    info["appended_clips"] = len(appended)
    return info


def run_model(key):
    blend, fbx, clips_blend = rig_files(key)
    src = blend or fbx
    if not src:
        return {"model": key, "error": "no rig yet (%s/%s.blend or .fbx): run Rig" % (layout.rig_folder(key), layout.leaf(key))}
    # clips authored into the rig's own .blend (the winged archetype) are already there
    return run(src, os.path.join(layout.rigged_dir(key), "preview.glb"), key, clips_blend)


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    val = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    if val("-file"):
        src = os.path.abspath(val("-file"))
        out = os.path.abspath(val("-out") or os.path.join(os.path.dirname(src), "preview.glb"))
        try:
            r = run(src, out)
        except Exception as e:
            r = {"file": src, "error": repr(e)}
        print("PREVIEW " + json.dumps(r))
        print("PREVIEW_DONE 1")
        sys.stdout.flush()
        if "error" in r:
            sys.exit(1)
        return
    if val("-only"):
        keys = val("-only").split(",")
    else:
        keys = [k for k in layout.models_in(a[0] if a and not a[0].startswith("-") else None) if any(rig_files(k)[:2])]
    failed = 0
    for k in keys:
        try:
            r = run_model(k)
        except Exception as e:
            r = {"model": k, "error": repr(e)}
        failed += "error" in r
        print("PREVIEW " + json.dumps(r))
    print("PREVIEW_DONE %d" % len(keys))
    sys.stdout.flush()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

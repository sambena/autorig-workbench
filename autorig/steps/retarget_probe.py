# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: read-only probes for the retargeter (core/retargeter.py), run under headless Blender.
#
#   blender -b --python retarget_probe.py -- fbx <mocap.fbx>      the FBX's armature bones and actions
#   blender -b --python retarget_probe.py -- blend <rig.blend>    the rig's bones and their rest directions
#
# The path arrives as an argument, never pasted into Python source, so any character in it is safe. Prints one line
# tagged __FBX_META__ or __TGT_BONES__ with the JSON; nothing is saved.
import json, sys

import bpy


def fbx(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True)
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    bones = [b.name for b in arm.data.bones] if arm else []
    fps = bpy.context.scene.render.fps
    actions = []
    for a in bpy.data.actions:
        f_start, f_end = int(a.frame_range[0]), int(a.frame_range[1])
        actions.append({"name": a.name, "frames": max(1, f_end - f_start + 1), "frame_start": f_start,
                        "frame_end": f_end, "fps": fps})
    act = None
    if arm and arm.animation_data and arm.animation_data.action:
        act = arm.animation_data.action
    elif bpy.data.actions:
        act = bpy.data.actions[0]
    frames = int(act.frame_range[1] - act.frame_range[0] + 1) if act else 0
    print("__FBX_META__" + json.dumps({"format": "FBX", "joints": bones, "frames": frames, "fps": fps,
                                       "actions": actions, "active_action": act.name if act else None}))


def blend(path):
    bpy.ops.wm.open_mainfile(filepath=path)
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    bones = [b.name for b in arm.data.bones] if arm else []
    dirs = {}
    if arm:
        for b in arm.data.bones:
            v = (arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)).normalized()
            dirs[b.name] = [round(v.x, 3), round(v.y, 3), round(v.z, 3)]
    print("__TGT_BONES__" + json.dumps({"bones": bones, "dirs": dirs}))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 2 or argv[0] not in ("fbx", "blend"):
        print("usage: retarget_probe.py -- fbx|blend <path>", file=sys.stderr)
        sys.exit(2)
    (fbx if argv[0] == "fbx" else blend)(argv[1])


if __name__ == "__main__":
    main()

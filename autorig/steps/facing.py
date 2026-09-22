# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: renders each model from four sides, so `forward` in a rig spec is read off a picture
# rather than guessed.
#
# A kind="build" spec needs the way the model faces in its source file, and every chain
# point after that is given in the turned model's coordinates. Get `forward` wrong and the
# tips land on the wrong limbs, the rig is built inside out, and nothing about the failure
# says which of the two mistakes it was.
#
#   blender -b --python autorig/steps/facing.py -- [<group>] [-out <dir>]
#   blender -b --python autorig/steps/facing.py -- -only wolf,moth -out <dir>

import bpy, sys, os
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from layout import source_fbx, models_in, work_dir

# Blender's front view looks along +Y, so a model facing -Y shows its face there.
VIEWS = [("-Y", (0, -1, 0)), ("+X", (1, 0, 0)), ("+Y", (0, 1, 0)), ("-X", (-1, 0, 0))]
SIZE = 320


def scene(size_px):
    s = bpy.context.scene
    s.render.engine = 'BLENDER_WORKBENCH'
    s.render.resolution_x = s.render.resolution_y = size_px
    s.render.film_transparent = False
    s.display.shading.light = 'STUDIO'
    s.display.shading.show_shadows = False


def shoot(mesh, out_dir, key):
    lo = Vector((min((mesh.matrix_world @ v.co)[i] for v in mesh.data.vertices) for i in range(3)))
    hi = Vector((max((mesh.matrix_world @ v.co)[i] for v in mesh.data.vertices) for i in range(3)))
    mid = (lo + hi) / 2
    span = max(hi - lo) * 1.25

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = span
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    paths = []
    for label, d in VIEWS:
        dv = Vector(d)
        cam.location = mid - dv * span * 3
        # Point the camera down its -Z at the model, keeping +Z up.
        cam.rotation_euler = dv.to_track_quat('-Z', 'Y').to_euler()
        p = os.path.join(out_dir, "%s__%s.png" % (key, label.replace("+", "p").replace("-", "m")))
        bpy.context.scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        paths.append(p)
    return paths


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = a[a.index("-out") + 1] if "-out" in a else work_dir("facing")
    if "-only" in a:
        keys = a[a.index("-only") + 1].split(",")
    else:
        keys = models_in(a[0] if a and not a[0].startswith("-") else None)

    os.makedirs(out_dir, exist_ok=True)
    import rerig

    for key in keys:
        path = source_fbx(key)
        if not path:
            print("FACING %s no fbx" % key)
            continue
        try:
            mesh, _ = rerig.load(path)
            scene(SIZE)
            shoot(mesh, out_dir, key)
            print("FACING %s ok" % key)
        except Exception as e:
            print("FACING %s error %r" % (key, e))
    print("FACING_DONE %d" % len(keys))


main()

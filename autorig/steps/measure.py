# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: three orthographic views of a model, turned as its spec turns it, with the 0..1 grid a spec's points are given in
# drawn over them, and the rig the spec currently builds drawn as sticks. This is how joints are measured.
#
#   blender -b --python autorig/steps/measure.py -- wolf [-out dir] [-spec <rig.json>] [-nobones]
#
# Why a picture and not a finder: the limb's root is the one joint no automatic rule got right on generated sculpts.
# Surface-distance rings leak across the loose pieces a limb is pushed into; the medial radius walks along a
# centreline that itself wanders over the shell; a straight line from the spine to a bent leg's foot runs through the
# leg. A base_f fraction was the old answer and it is what put an insect's legs up through its carapace (docs/PIPELINE.md,
# rule C). A measured point takes a minute per limb and is right.
#
# Grid: thin lines every 0.1, heavy every 0.5, in the spec's coordinates (x 0 = its right .. 1 = its left; y 0 =
# nose .. 1 = tail; z 0 = floor .. 1 = top). Views: side from its left (+X; nose to the left of the picture), front
# (from -Y, its left on the picture's right), top (nose at the top).
import bpy, sys, os, math
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import rerig

RES = int(os.environ.get("MEASURE_RES", 900))


def args():
    a = sys.argv[sys.argv.index("--") + 1:]
    val = lambda n, d=None: a[a.index(n) + 1] if n in a else d
    return a[0], val("-out") or rerig.work_dir("measure"), val("-spec"), "-nobones" not in a


def main():
    key, out, spec_mod, bones = args()
    import spec_store
    spec = dict(spec_store.load_file(spec_mod)["rig"] if spec_mod else spec_store.SPECS[key])
    mesh, joints = rerig.load(rerig.find_fbx(key, spec))
    rerig.normalise(mesh, joints, spec)
    lo, hi = rerig.bounds(mesh); size = hi - lo
    sticks = []
    if bones:
        try:
            if spec["kind"] == "tripo":
                from mathutils.bvhtree import BVHTree
                me = mesh.data
                bvh = BVHTree.FromPolygons([v.co.copy() for v in me.vertices], [tuple(p.vertices) for p in me.polygons])
                rerig.repair(joints, spec, size)
                chains = rerig.tripo_chains(joints, spec, bvh, size, mesh)
            else:
                chains = rerig.build_chains(mesh, spec, size)
            sticks = rerig.sticks(chains, None, max(size) * 0.008)
        except Exception as e:
            print("MEASURE no bones:", repr(e))
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.film_transparent = True
    sh = scene.display.shading
    sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'; sh.show_cavity = True; sh.show_object_outline = True
    for s in mesh.material_slots:
        if s.material: s.material.diffuse_color = (0.72, 0.74, 0.8, 0.55 if sticks else 1.0)
    cd = bpy.data.cameras.new("m"); cd.type = 'ORTHO'
    cam = bpy.data.objects.new("m", cd); scene.collection.objects.link(cam); scene.camera = cam
    centre = (lo + hi) * 0.5; ext = max(size) * 1.12; dist = ext * 4 + 1
    cd.ortho_scale = ext; cd.clip_start = 0.001; cd.clip_end = dist * 3
    P = lambda u, v, w: Vector((lo.x + size.x * u, lo.y + size.y * v, lo.z + size.z * w))
    views = [("side", (1, 0, 0), ("y", "z")), ("front", (0, -1, 0), ("x", "z")), ("top", (0, 0, 1), ("x", "y"))]
    tiles = []
    for name, d, axes in views:
        d = Vector(d)
        cam.location = centre + d * dist
        cam.rotation_euler = (-d).to_track_quat('-Z', 'Y' if name != "top" else 'Y').to_euler()
        if name == "top": cam.rotation_euler = (0, 0, math.pi)  # nose (y=0, -Y) at the top of the picture
        bpy.context.view_layer.update()
        fp = os.path.join(out, "_tmp.png"); os.makedirs(out, exist_ok=True); scene.render.filepath = fp
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(fp); w, h = img.size
        px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4); bpy.data.images.remove(img); os.remove(fp)
        tile = np.ones((h, w, 3), np.float32) * 0.97
        a = px[..., 3:4]; tile = tile * (1 - a) + px[..., :3] * a

        def pix(p):
            c = world_to_camera_view(scene, cam, p); return c.x * (w - 1), c.y * (h - 1)

        def line(p, q, col, width):
            (x0, y0), (x1, y1) = pix(p), pix(q)
            n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
            for t in np.linspace(0, 1, n):
                x, y = int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))
                tile[max(0, y - width):y + width + 1, max(0, x - width):x + width + 1] = col
        ia, ib = "xyz".index(axes[0]), "xyz".index(axes[1])
        for k in range(11):
            f = k / 10.0
            heavy = k in (0, 5, 10)
            col = (0.85, 0.15, 0.1) if heavy else (0.35, 0.55, 0.95)
            for along in (ia, ib):
                other = ib if along == ia else ia
                a0 = [0.5, 0.5, 0.5]; a1 = [0.5, 0.5, 0.5]
                a0[along] = f; a1[along] = f; a0[other] = 0.0; a1[other] = 1.0
                line(P(*a0), P(*a1), col, 1 if heavy else 0)
        # tick labels as dots: one dot per tenth along the bottom and left edges, so tenths can be counted
        for k in range(1, 10):
            for along in (ia, ib):
                other = ib if along == ia else ia
                q = [0.5, 0.5, 0.5]; q[along] = k / 10.0; q[other] = 0.0
                x, y = pix(P(*q)); x, y = int(x), int(y)
                for j in range(k):
                    ox, oy = (j * 5, -8) if along == ia else (-8, j * 5)
                    tile[max(0, y + oy - 1):y + oy + 2, max(0, x + ox - 1):x + ox + 2] = (0.1, 0.1, 0.1)
        # sticks, drawn flat on top
        for ob in sticks:
            col = ob.data.materials[0].diffuse_color[:3]
            vs = [ob.matrix_world @ v.co for v in ob.data.vertices]
            for i in range(0, len(vs), 6):
                line(vs[i], vs[i + 5], col, 2)
                x, y = pix(vs[i]); tile[int(y) - 4:int(y) + 5, int(x) - 4:int(x) + 5] = (0, 0, 0)
        tile[:, :2] = 0.4
        tiles.append(tile)
    sheet = np.concatenate(tiles, axis=1)
    hh, ww = sheet.shape[:2]
    rgba = np.ones((hh, ww, 4), np.float32); rgba[..., :3] = sheet
    im = bpy.data.images.new("m", width=ww, height=hh, alpha=True); im.pixels = rgba.ravel().tolist()
    im.filepath_raw = os.path.join(out, key + ".png"); im.file_format = 'PNG'; im.save()
    print("MEASURE", key, "->", im.filepath_raw, "size", tuple(round(x, 3) for x in size))


if __name__ == "__main__":
    main()

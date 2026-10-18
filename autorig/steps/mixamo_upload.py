# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a generated character as an upload for Mixamo's auto-rigger.
#
#   blender -b --python autorig/steps/mixamo_upload.py -- --in Ice.glb --out <folder>/IceAdult.fbx --height 1.8
#           [--triangles 30000] [--texture 2048]
#   python autorig/cli/run.py mixamo-upload <the same arguments>
#
# Mixamo rigs a clothed person far better at the shoulders than bone heat does (Tornado Titans' cast, 2026-10-17:
# autorig's rigs folded 8-32 per 10k triangles stood with the arms down, Mixamo's 1-13; docs/PIPELINE.md). The upload
# is the one manual step: a person drags it into mixamo.com on their own account, places the markers and downloads
# the rig (FBX Binary, Original Pose). mixamo_import.py makes the game file from that download. This writes the upload:
#   1. every mesh joined into one, Tripo's mix-node material replaced by a plain one reading the base colour;
#   2. duplicate vertices welded and loose islands under 1% of the vertices dropped: Mixamo silently bounces a mesh
#      it cannot rig back to the marker screen, and a raw Tripo person (Laree) did that until it was cleaned;
#   3. decimated to --triangles, scaled to --height metres, feet on the ground, centred, facing -Y (Mixamo's front);
#   4. the base colour shrunk to --texture pixels and embedded as a JPEG, so the upload stays well under Mixamo's limit.
# Prints MIXAMO_UPLOAD {json}.
import argparse, json, os, sys

import bmesh
import bpy
from mathutils import Matrix, Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True, help="the generated character (GLB, glTF, FBX or OBJ)")
    p.add_argument("--out", required=True, help="the upload to write, <Name>.fbx")
    p.add_argument("--height", type=float, required=True, help="how tall the character stands, in metres")
    p.add_argument("--triangles", type=int, default=30000)
    p.add_argument("--texture", type=int, default=2048, help="the embedded base colour's size, in pixels")
    return p.parse_args(argv)


def import_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj": bpy.ops.wm.obj_import(filepath=path)
    else: raise SystemExit("MIXAMO_UPLOAD_ERROR unsupported source " + path)


def joined_mesh():
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    if not meshes: raise SystemExit("MIXAMO_UPLOAD_ERROR no mesh in the source")
    for o in meshes:
        mw = o.matrix_world.copy(); o.parent = None; o.matrix_world = mw
    for o in list(bpy.data.objects):
        if o.type != 'MESH': bpy.data.objects.remove(o)
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes: o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1: bpy.ops.object.join()
    m = bpy.context.view_layer.objects.active
    for mod in list(m.modifiers): m.modifiers.remove(mod)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return m


def clean(m):
    """Welds duplicates and drops islands under 1% of the vertices; returns (islands, vertices removed)."""
    bm = bmesh.new(); bm.from_mesh(m.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005 * max(m.dimensions))
    bm.verts.ensure_lookup_table()
    seen, islands = set(), []
    for v in bm.verts:
        if v.index in seen: continue
        stack, comp = [v], []
        seen.add(v.index)
        while stack:
            x = stack.pop(); comp.append(x)
            for e in x.link_edges:
                y = e.other_vert(x)
                if y.index not in seen: seen.add(y.index); stack.append(y)
        islands.append(comp)
    n = len(bm.verts)
    kill = [v for c in islands if len(c) < 0.01 * n for v in c]
    bmesh.ops.delete(bm, geom=kill, context='VERTS')
    bm.to_mesh(m.data); bm.free()
    return len(islands), len(kill)


def triangles(m):
    return sum(len(p.vertices) - 2 for p in m.data.polygons)


def main():
    a = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_any(a.src)
    images = list(bpy.data.images)
    m = joined_mesh()
    islands, dropped = clean(m)
    if triangles(m) > a.triangles:
        d = m.modifiers.new("decimate", 'DECIMATE'); d.ratio = a.triangles / triangles(m)
        bpy.ops.object.modifier_apply(modifier=d.name)
    co = [v.co for v in m.data.vertices]
    lo = Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co)))
    hi = Vector((max(c.x for c in co), max(c.y for c in co), max(c.z for c in co)))
    m.data.transform(Matrix.Translation(Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))))
    m.data.transform(Matrix.Scale(a.height / max(hi.z - lo.z, 1e-9), 4))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.out))[0]
    base = next((i for i in images if "basecolor" in i.name.lower() or "base_color" in i.name.lower()), None)
    base = base or next((i for i in images if "normal" not in i.name.lower() and "rough" not in i.name.lower()
                         and "metal" not in i.name.lower() and not i.name.lower().endswith("_rm.png")), None)
    texture = None
    if base is not None:
        if max(base.size) > a.texture: base.scale(a.texture, a.texture)
        texture = os.path.join(os.path.dirname(os.path.abspath(a.out)), stem + "_color.jpg")
        settings = bpy.context.scene.render.image_settings
        settings.file_format = 'JPEG'; settings.quality = 88
        base.save_render(texture)
        mat = bpy.data.materials.new(stem); mat.use_nodes = True
        node = mat.node_tree.nodes.new("ShaderNodeTexImage"); node.image = bpy.data.images.load(texture)
        mat.node_tree.links.new(node.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        m.data.materials.clear(); m.data.materials.append(mat)
    bpy.ops.export_scene.fbx(filepath=a.out, use_selection=False, apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL',
                             axis_forward='-Z', axis_up='Y', path_mode='COPY', embed_textures=True, mesh_smooth_type='FACE')
    print("MIXAMO_UPLOAD " + json.dumps({"out": a.out, "triangles": triangles(m), "height_m": a.height,
                                         "islands": islands, "dropped_vertices": dropped, "texture": texture,
                                         "bytes": os.path.getsize(a.out)}))


main()

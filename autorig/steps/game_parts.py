# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the moving parts of a game prop cut out of its one-piece mesh, so the game can move them (Tornado
# Titans' pumpjacks nod and its windpumps' fans turn, 2026-10-17). It takes an FBX as game_prop.py writes it (a root
# <Name> over <Name>_LOD0, _LOD1, _LOD2) and a JSON spec of named parts, and writes the FBX again with LOD0 minus the
# parts and each part its own mesh object beside the LODs, named <Name>_<Part> (Pumpjack_Beam, Windpump_Fan), its
# origin at its pivot. LOD1 and LOD2 stay whole: far off, nothing moves. Run it again on its own output and the parts
# are joined back first, so a spec can be tuned and rerun.
#
#   blender -b --python autorig/steps/game_parts.py -- --in Pumpjack.fbx --spec parts.json --out <folder>
#           [--name Pumpjack] [--renders <folder>] [--survey]
#   python autorig/cli/run.py game-parts <the same arguments>
#
# A part is selected by region, a box or a disc (core/game_parts_spec.py has the format), in Blender's frame: metres,
# Z up, front towards -Y. Rodin's and Tripo's models are thousands of loose fragments, so a part takes whole fragments
# (connected components) by where each one's centre lies; a face is never split. --survey writes every fragment's
# centre, bounds and size to <renders>/<Name>_fragments.json for tuning a spec; --renders draws the model and each part
# from the side and the front over a metre grid (the axes drawn thicker), before and after the cut.
#
# The FBX is exported as game_prop.py exports (-Z forward, Y up, unit scale applied, no leaf bones), with the textures
# referenced where they lie, in <Name>.fbm beside the FBX (copied there when --out is another folder).
import argparse, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import game_parts_spec as spec
import game_prop

import bpy
from mathutils import Matrix, Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True, help="the game FBX (game_prop.py's: <Name> over <Name>_LOD0..)")
    p.add_argument("--spec", required=True, help="JSON of parts per model name (core/game_parts_spec.py)")
    p.add_argument("--name", help="the model's name in the spec and the FBX (default: the file's name)")
    p.add_argument("--out", required=True, help="folder for the FBX (and its <Name>.fbm maps)")
    p.add_argument("--renders", help="folder for check renders: before and after, side and front, over a metre grid")
    p.add_argument("--survey", action="store_true", help="write every fragment to <renders>/<Name>_fragments.json")
    return p.parse_args(argv)


def flatten(name):
    """The imported FBX as game_prop.py had it before export: every mesh's transform baked into its vertices (the FBX
    importer puts the axis turn on the root), the root and other empties gone, a part from an earlier run joined back
    into LOD0. Returns [LOD0, LOD1, ...]."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for o in meshes:
        world = o.matrix_world.copy()
        o.parent = None
        o.data.transform(world)
        o.matrix_world = Matrix.Identity(4)
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    lods = sorted((o for o in meshes if o.name.startswith(name + "_LOD")), key=lambda o: o.name)
    if not lods or lods[0].name != name + "_LOD0":
        raise SystemExit("game_parts.py: no %s_LOD0 in the file (objects: %s)" % (name, sorted(o.name for o in meshes)))
    old = [o for o in meshes if o not in lods]
    if old:
        print("game_parts.py: joining %s back into %s_LOD0 first" % (sorted(o.name for o in old), name))
        game_prop.join([lods[0]] + old)
        lods[0].name = lods[0].data.name = name + "_LOD0"
    return lods


def fragments(obj):
    """Per connected component of obj: (face indices, centre (mean of its vertices), lo, hi)."""
    mesh = obj.data
    labels = spec.components(len(mesh.vertices), [p.vertices for p in mesh.polygons])
    co = [v.co for v in mesh.vertices]
    n = max(labels) + 1 if labels else 0
    sums = [[0.0, 0.0, 0.0, 0] for _ in range(n)]
    lo = [[1e9] * 3 for _ in range(n)]
    hi = [[-1e9] * 3 for _ in range(n)]
    for i, c in enumerate(co):
        k = labels[i]
        s = sums[k]
        s[0] += c.x; s[1] += c.y; s[2] += c.z; s[3] += 1
        for a in range(3):
            if c[a] < lo[k][a]: lo[k][a] = c[a]
            if c[a] > hi[k][a]: hi[k][a] = c[a]
    faces = [[] for _ in range(n)]
    for p in mesh.polygons:
        faces[labels[p.vertices[0]]].append(p.index)
    out = []
    for k in range(n):
        s = sums[k]
        if s[3] == 0 or not faces[k]:
            continue
        out.append((faces[k], (s[0] / s[3], s[1] / s[3], s[2] / s[3]), tuple(lo[k]), tuple(hi[k])))
    return out


TAG = "game_part"


def separate(obj, tag):
    """Moves the faces of obj whose TAG attribute is `tag` into a new object and returns it. (A tag, not face
    indices: separating one part renumbers the faces left behind.)"""
    mesh = obj.data
    values = [0] * len(mesh.polygons)
    mesh.attributes[TAG].data.foreach_get("value", values)
    want = {i for i, v in enumerate(values) if v == tag}
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for v in mesh.vertices:
        v.select = False
    for p in mesh.polygons:
        p.select = p.index in want
        if p.select:
            for i in p.vertices:
                mesh.vertices[i].select = True
    before = set(bpy.context.scene.objects)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    return [o for o in bpy.context.scene.objects if o not in before][0]


def cut(lod0, name, parts, survey_path=None):
    """Cuts the spec's parts out of lod0. Returns [(part dict, object)] for the parts that took anything."""
    frags = fragments(lod0)
    if survey_path:
        with open(survey_path, "w", encoding="utf-8") as f:
            json.dump([{"faces": len(fs), "centre": [round(v, 3) for v in c], "min": [round(v, 3) for v in lo],
                        "max": [round(v, 3) for v in hi]} for fs, c, lo, hi in frags], f, indent=0)
        print("game_parts.py: %d fragments surveyed to %s" % (len(frags), survey_path))
    owner = spec.assign(parts, [c for _, c, _, _ in frags], [[hi[a] - lo[a] for a in range(3)] for _, _, lo, hi in frags])
    total = len(lod0.data.polygons)
    tags = [0] * total
    for k, (fs, _, _, _) in enumerate(frags):
        for f in fs:
            tags[f] = owner[k] + 1
    attr = lod0.data.attributes.new(TAG, "INT", "FACE")
    attr.data.foreach_set("value", tags)
    taken = []
    for i, prt in enumerate(parts):
        mine = [frags[k] for k in range(len(frags)) if owner[k] == i]
        face_count = sum(len(fs) for fs, _, _, _ in mine)
        if not face_count:
            print("game_parts.py: %s_%s took nothing; check its region" % (name, prt["name"]))
            continue
        lo = tuple(min(m[2][a] for m in mine) for a in range(3))
        hi = tuple(max(m[3][a] for m in mine) for a in range(3))
        piv = Vector(spec.pivot(prt, lo, hi))
        obj = separate(lod0, i + 1)
        obj.data.attributes.remove(obj.data.attributes[TAG])
        obj.name = obj.data.name = "%s_%s" % (name, prt["name"])
        obj.data.transform(Matrix.Translation(-piv))
        obj.location = piv
        print("game_parts.py: %s: %d fragments, %d faces of %d, x %.2f..%.2f y %.2f..%.2f z %.2f..%.2f, pivot (%.3f, %.3f, %.3f)"
              % (obj.name, len(mine), len(obj.data.polygons), total, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2], piv.x, piv.y, piv.z))
        if len(obj.data.polygons) != face_count:
            raise SystemExit("game_parts.py: %s took %d faces, not the %d its fragments have" % (obj.name, len(obj.data.polygons), face_count))
        taken.append((prt, obj))
    lod0.data.attributes.remove(lod0.data.attributes[TAG])
    return taken


def maps_beside(name, src_dir, out_dir):
    """Every image the materials use, referenced from <out>/<Name>.fbm (copied there from beside the source when
    --out is another folder), so the FBX names them "<Name>.fbm/<file>" as game_prop.py's did."""
    dst = os.path.join(out_dir, name + ".fbm")
    src = os.path.join(src_dir, name + ".fbm")
    if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)) and os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            if not f.endswith(".meta"):
                shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
    for im in bpy.data.images:
        if not im.filepath:
            continue
        there = os.path.join(dst, os.path.basename(bpy.path.abspath(im.filepath)))
        if os.path.exists(there):
            im.filepath = there
        else:
            print("game_parts.py: image %s (%s) is not in %s" % (im.name, im.filepath, dst))


# ------------------------------------------------------------------ check renders

COLOURS = [(0.95, 0.25, 0.2), (0.2, 0.55, 0.95), (0.95, 0.8, 0.15), (0.6, 0.3, 0.9), (0.2, 0.85, 0.5), (0.95, 0.5, 0.1)]


def grid(lo, hi, view):
    """A metre grid behind the model for `view` "side" (looking along -X at the Y-Z plane) or "front" (looking along +Y
    at the X-Z plane): thin bars every metre, thicker on the axes and every fifth metre."""
    import bmesh
    bm = bmesh.new()
    a, depth = (1, 0) if view == "side" else (0, 1)
    u0, u1 = int(lo[a]) - 2, int(hi[a]) + 2
    z0, z1 = int(lo[2]) - 2, int(hi[2]) + 2
    back = (lo[depth] - 3.0) if view == "side" else (hi[depth] + 3.0)

    def bar(p0, p1, w):
        # a quad from p0 to p1 (in the view plane, as (u, z)) w wide
        du, dz = p1[0] - p0[0], p1[1] - p0[1]
        n = (du * du + dz * dz) ** 0.5
        ou, oz = -dz / n * w * 0.5, du / n * w * 0.5
        vs = []
        for (u, z) in ((p0[0] + ou, p0[1] + oz), (p1[0] + ou, p1[1] + oz), (p1[0] - ou, p1[1] - oz), (p0[0] - ou, p0[1] - oz)):
            c = [0.0, 0.0, 0.0]
            c[a], c[2], c[depth] = u, z, back
            vs.append(bm.verts.new(c))
        bm.faces.new(vs)
    for u in range(u0, u1 + 1):
        bar((u, z0), (u, z1), 0.08 if u == 0 else 0.04 if u % 5 else 0.06)
    for z in range(z0, z1 + 1):
        bar((u0, z), (u1, z), 0.08 if z == 0 else 0.04 if z % 5 else 0.06)
    mesh = bpy.data.meshes.new("Grid")
    bm.to_mesh(mesh)
    obj = bpy.data.objects.new("Grid", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.color = (0.15, 0.15, 0.15, 1.0)
    return obj


def render(path, objs, view, lo, hi, textured=False):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 1100
    scene.render.film_transparent = False
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "TEXTURE" if textured else "OBJECT"
    shading.background_type = "VIEWPORT"
    shading.background_color = (0.85, 0.85, 0.85)
    shading.show_backface_culling = False
    g = grid(lo, hi, view)
    shown = set(objs) | {g}
    for o in scene.objects:
        o.hide_render = o not in shown
    cam_data = bpy.data.cameras.new("Check")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) + 3.0
    cam = bpy.data.objects.new("Check", cam_data)
    scene.collection.objects.link(cam)
    mid = Vector(((lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5, (lo[2] + hi[2]) * 0.5))
    if view == "side":       # from +X, looking along -X: +Y to the right, the front (-Y) on the left
        cam.location = mid + Vector((40.0, 0.0, 0.0))
        cam.rotation_euler = (1.5708, 0.0, 1.5708)
    else:                    # from -Y (the front), looking along +Y: +X to the right
        cam.location = mid + Vector((0.0, -40.0, 0.0))
        cam.rotation_euler = (1.5708, 0.0, 0.0)
    cam_data.clip_end = 200.0
    scene.camera = cam
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    bpy.data.objects.remove(g, do_unlink=True)
    print("game_parts.py: rendered " + path)


def bounds_of(objs):
    pts = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    return tuple(min(p[a] for p in pts) for a in range(3)), tuple(max(p[a] for p in pts) for a in range(3))


def main():
    a = args()
    src = os.path.abspath(a.src)
    name = a.name or os.path.splitext(os.path.basename(src))[0]
    specs = spec.load(a.spec)
    if name not in specs:
        raise SystemExit("game_parts.py: %s has no parts in %s (it has %s)" % (name, a.spec, sorted(specs)))
    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.ops.import_scene.fbx(filepath=src)
    lods = flatten(name)
    lod0 = lods[0]
    renders = os.path.abspath(a.renders) if a.renders else None
    if renders:
        os.makedirs(renders, exist_ok=True)
        lo, hi = bounds_of([lod0])
        for view in ("side", "front"):
            render(os.path.join(renders, "%s_before_%s.png" % (name, view)), [lod0], view, lo, hi, textured=True)
    survey = os.path.join(renders or out_dir, name + "_fragments.json") if a.survey else None
    taken = cut(lod0, name, specs[name], survey)
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for o in lods:
        o.parent = root
    for _, o in taken:
        o.parent = root   # beside the LODs, as game_prop.py's wheels: an engine adds them to LOD0's renderers itself
    if renders:
        lod0.color = (0.7, 0.7, 0.7, 1.0)
        for i, (_, o) in enumerate(taken):
            o.color = COLOURS[i % len(COLOURS)] + (1.0,)
        bpy.context.view_layer.update()   # the parts' world matrices, now they stand at their pivots
        lo, hi = bounds_of([lod0] + [o for _, o in taken])
        for view in ("side", "front"):
            render(os.path.join(renders, "%s_after_%s.png" % (name, view)), [lod0] + [o for _, o in taken], view, lo, hi)
            render(os.path.join(renders, "%s_rest_%s.png" % (name, view)), [lod0], view, lo, hi)
            for prt, o in taken:
                render(os.path.join(renders, "%s_%s_%s.png" % (name, prt["name"], view)), [o], view, lo, hi)
    maps_beside(name, os.path.dirname(src), out_dir)
    out = os.path.join(out_dir, name + ".fbx")
    game_prop.export(root, lods, out, [o for _, o in taken], path_mode="RELATIVE")
    print("wrote %s: parts %s, LOD0 %d faces left" % (out, [o.name for _, o in taken], len(lod0.data.polygons)))


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a generated prop (Rodin, Tripo, Meshy: GLB, FBX or OBJ) made into a game prop: one FBX with three
# LODs, standing on the ground at the origin, facing -Y (an engine's +Z), at the size the game wants. Moved here from
# storm-chaser's Tools/blender/cleanup.py (Sam, 2026-10-17: the tools live in Autorig Workbench), with the fixes made
# importing the Tripo batch: --front turns a GLB (glTF imports are quaternion, so it was ignored) the right way;
# parents' transforms are kept; a GLB's colour and normal maps are saved as <Name>.fbm/<Name>_basecolor/_normal.png and
# wired straight into the material (FBX's exporter dropped the colour map); and --carve-wheels cuts a one-piece
# truck's tyres out as Wheel_FL/FR/RL/RR (a tandem axle as RL2/RR2), or marks Wheels_Baked when it cannot.
#
#   blender -b --python autorig/steps/game_prop.py -- --in barn.glb --name Barn --length 18 --out <folder>
#           [--height 9] [--front -y|+y|+x|-x] [--triangles 30000] [--lods 0.4,0.15] [--carve-wheels | --wheels]
#   python autorig/cli/run.py game-prop <the same arguments>
#
# What it does, in order:
#   1. imports the file and joins everything into one mesh (materials and UVs kept);
#   2. turns it so its front faces -Y (--front says which way it faces now) and sets the pivot: centred in x and y,
#      lowest point at z = 0;
#   3. scales it so its longest footprint side is --length metres (or --height for its height);
#   4. with --carve-wheels, cuts the tyres out of a truck in one piece; with --wheels, takes a truck already split into
#      parts (hyper3d bang) and names the four corner parts;
#   5. makes LOD1 and LOD2 by decimation, named <Name>_LOD0, _LOD1, _LOD2 (an engine's LODGroup);
#   6. exports the FBX: -Z forward, Y up, unit scale applied, no leaf bones.
# It never repairs topology: a model that is wrong gets regenerated, not fixed.
import argparse, math, os, sys

import bpy
from mathutils import Matrix, Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True, help="the generated model: .glb, .gltf, .fbx or .obj")
    p.add_argument("--name", required=True, help="the TITAN name: a PropType (Barn, HayBale...) or Truck_<Kind>")
    p.add_argument("--length", type=float, help="longest footprint side in metres")
    p.add_argument("--height", type=float, help="height in metres (instead of --length; with it, the height is fitted separately)")
    p.add_argument("--front", default="-y", choices=["-y", "+y", "+x", "-x"], help="which way the model faces as imported")
    p.add_argument("--triangles", type=int, help="decimate the base mesh to this many triangles before making LODs")
    p.add_argument("--lods", default="0.4,0.15", help="decimation ratios for LOD1 and LOD2")
    p.add_argument("--out", required=True, help="folder for the FBX and its <Name>.fbm maps")
    p.add_argument("--wind", action="store_true", help="a plant: height-based vertex alpha for a wind shader to bend it by")
    p.add_argument("--carve-wheels", action="store_true", help="a truck in one piece (Tripo): cut the four tyres out as Wheel_FL/FR/RL/RR")
    p.add_argument("--wheels", action="store_true", help="a truck split into parts (hyper3d bang): the four corner parts become Wheel_FL/FR/RL/RR with hub pivots")
    return p.parse_args(argv)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise SystemExit("game_prop.py: cannot import " + ext)


def image_for(socket, seen=None):
    """The image feeding a material socket, through whatever glTF nodes sit between."""
    seen = set() if seen is None else seen
    for link in socket.links:
        node = link.from_node
        if node in seen:
            continue
        seen.add(node)
        if node.type == "TEX_IMAGE":
            return node.image
        for child in node.inputs:
            found = image_for(child, seen)
            if found:
                return found
    return None


def save_maps(name, out_dir):
    """A GLB's textures, saved beside the FBX as <Name>.fbm/<Name>_basecolor.png (and _normal)
    and wired straight into a plain Principled BSDF. FBX's exporter does not follow glTF's node graph, so without
    this a Tripo model reaches Unity with its normal map only (package_autorig_lods.py does the same)."""
    folder = os.path.join(out_dir, name + ".fbm")
    os.makedirs(folder, exist_ok=True)
    materials = [m for m in bpy.data.materials if m.node_tree and any(n.type == "BSDF_PRINCIPLED" for n in m.node_tree.nodes)]
    saved = {}
    for index, material in enumerate(materials):
        bsdf = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        maps = {}
        # base colour and normal only: PixelLit reads no roughness, and everything in Resources ships
        for suffix, socket in (("basecolor", "Base Color"), ("normal", "Normal")):
            image = image_for(bsdf.inputs[socket])
            if image is None:
                continue
            maps[suffix] = image
            if image.name not in saved:
                tail = "" if len(materials) == 1 else "_%d" % index
                image.filepath_raw = os.path.join(folder, "%s%s_%s.png" % (name, tail, suffix))
                image.file_format = "PNG"
                image.save()
                saved[image.name] = image
        if "basecolor" not in maps:
            continue
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        b = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(b.outputs["BSDF"], out.inputs["Surface"])
        base = nodes.new("ShaderNodeTexImage")
        base.image = maps["basecolor"]
        links.new(base.outputs["Color"], b.inputs["Base Color"])
        b.inputs["Roughness"].default_value = 0.75
        if "normal" in maps:
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = maps["normal"]
            nm = nodes.new("ShaderNodeNormalMap")
            links.new(tex.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs["Normal"], b.inputs["Normal"])
    return len(saved)


def all_meshes():
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("game_prop.py: no meshes in the file")
    # drop empties and armatures: a prop has none worth keeping; bake every transform into the vertices.
    # Unparent first, keeping each mesh where it stands: a Tripo GLB turns its mesh with a parent node, and
    # deleting the parent alone would drop that turn.
    for o in meshes:
        world = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = world
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return meshes


def join(meshes):
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def join_all():
    return join(all_meshes())


def centre(obj):
    (x0, x1), (y0, y1), (z0, z1) = bounds(obj)
    return Vector(((x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5)), (x1 - x0, y1 - y0, z1 - z0)


def split_wheels(meshes):
    """The four parts that sit low at the corners are the wheels; everything else is the body. Returns (body, wheels)
    with wheels as a dict corner -> object, corners FL, FR, RL, RR (front is -Y, left is +X, as Unity sees it)."""
    xs = [v for o in meshes for v in (bounds(o)[0])]
    ys = [v for o in meshes for v in (bounds(o)[1])]
    zs = [v for o in meshes for v in (bounds(o)[2])]
    hw, hl, h = (max(xs) - min(xs)) * 0.5, (max(ys) - min(ys)) * 0.5, max(zs) - min(zs)
    mid = Vector(((max(xs) + min(xs)) * 0.5, (max(ys) + min(ys)) * 0.5, min(zs)))
    candidates = []
    for o in meshes:
        c, size = centre(o)
        if size[2] > 0.5 * h:
            continue   # too tall for a wheel
        if abs(c.x - mid.x) < 0.3 * hw or abs(c.y - mid.y) < 0.15 * hl:
            continue   # not at a corner
        if c.z - mid.z > 0.45 * h:
            continue   # too high
        candidates.append((c.z, o, c))
    candidates.sort(key=lambda t: t[0])
    wheels = {}
    for _, o, c in candidates:
        if not wheel_like(o):
            continue   # a mud flap, a bit of arch: Rodin's split leaves such things loose
        corner = ("F" if c.y < mid.y else "R") + ("L" if c.x > mid.x else "R")
        if corner not in wheels:
            wheels[corner] = o
    body = join([o for o in meshes if o not in wheels.values()])
    # a corner whose wheel the split left in the body gets it carved out, in the image of the wheel across from it
    for corner in ("FL", "FR", "RL", "RR"):
        if corner in wheels:
            continue
        other = corner[0] + ("R" if corner[1] == "L" else "L")
        if other not in wheels:
            continue
        carved = carve_wheel(body, wheels[other], mid.x)
        if carved is not None:
            wheels[corner] = carved
            print("game_prop.py: %s wheel carved out of the body, in the image of %s" % (corner, other))
    if len(wheels) != 4:
        print("game_prop.py: found %d wheel parts, not 4 (%s); keeping everything as the body" % (len(wheels), sorted(wheels)))
        return join([body] + list(wheels.values())), {}
    return body, wheels


def wheel_like(o):
    """Round across Y/Z and no wider than that across X: a wheel, not a flap or a bit of arch."""
    _, size = centre(o)
    d = max(size[1], size[2])
    return d > 0 and min(size[1], size[2]) > 0.75 * d and size[0] < 0.75 * d


def carve_wheel(body, like, mid_x):
    """Separates from the body the geometry inside the cylinder where a wheel would stand mirrored across the
    body's centre line from `like`, and returns it as its own object (None if there is nothing there)."""
    import bmesh
    c, size = centre(like)
    hub = Vector((2 * mid_x - c.x, c.y, c.z))
    hw, r = size[0] * 0.55, max(size[1], size[2]) * 0.52
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(body.data)
    for v in bm.verts:
        v.select = abs(v.co.x - hub.x) < hw and ((v.co.y - hub.y) ** 2 + (v.co.z - hub.z) ** 2) ** 0.5 < r
    picked = 0
    for f in bm.faces:
        f.select = all(v.select for v in f.verts)
        picked += f.select
    for e in bm.edges:
        e.select = all(v.select for v in e.verts)
    bmesh.update_edit_mesh(body.data)
    if picked < 50:
        bpy.ops.object.mode_set(mode="OBJECT")
        return None
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    new = [o for o in bpy.context.selected_objects if o is not body and o.type == "MESH"]
    return new[0] if new else None


def face_minus_y(objs, front):
    turns = {"-y": 0.0, "+y": math.pi, "+x": -math.pi / 2, "-x": math.pi / 2}   # a turn of +90 degrees takes +x to +y
    if turns[front] == 0.0:
        return
    # turn the vertices themselves: a glTF import leaves objects in quaternion mode, where setting
    # rotation_euler does nothing, so --front was silently ignored for every GLB
    turn = Matrix.Rotation(turns[front], 4, "Z")
    for obj in objs:
        obj.data.transform(turn)


def bounds(obj):
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def group_bounds(objs):
    xs = [v for o in objs for v in bounds(o)[0]]
    ys = [v for o in objs for v in bounds(o)[1]]
    zs = [v for o in objs for v in bounds(o)[2]]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def stand_on_ground(objs):
    (x0, x1), (y0, y1), (z0, z1) = group_bounds(objs)
    shift = Vector((-(x0 + x1) * 0.5, -(y0 + y1) * 0.5, -z0))
    for obj in objs:
        for v in obj.data.vertices:
            v.co += shift
        obj.location = (0.0, 0.0, 0.0)


def mark_tree_wind(obj):
    """Write the same height weights as NatureModels.MarkHeights for PixelLit's wind sway."""
    mesh = obj.data
    colors = mesh.color_attributes
    wind = colors.get("TreeWind")
    if wind is not None and wind.domain != "CORNER":
        colors.remove(wind)
        wind = None
    if wind is None:
        wind = colors.new(name="TreeWind", type="FLOAT_COLOR", domain="CORNER")
    source = colors.active_color
    for loop in mesh.loops:
        vertex = mesh.vertices[loop.vertex_index]
        up = max(0.0, min(1.0, vertex.co.z / 9.0))
        alpha = (228.0 + (166.0 - 228.0) * up) / 255.0
        if source is not None:
            rgb = source.data[loop.vertex_index if source.domain == "POINT" else loop.index].color
            wind.data[loop.index].color = (rgb[0], rgb[1], rgb[2], alpha)
        else:
            wind.data[loop.index].color = (1.0, 1.0, 1.0, alpha)
    colors.active_color = wind


def size_to(objs, length, height):
    """Scales the model so its longest footprint side is `length` metres, or its height is `height`; given both, the
    footprint takes the length and the height is squashed or stretched to fit separately (a fence section from Rodin
    came squat: 8 m long had it 5 m tall)."""
    (x0, x1), (y0, y1), (z0, z1) = group_bounds(objs)
    if length and height:
        flat = length / max(x1 - x0, y1 - y0, 1e-6)
        tall = height / max(z1 - z0, 1e-6)
        for obj in objs:
            for v in obj.data.vertices:
                v.co = Vector((v.co.x * flat, v.co.y * flat, v.co.z * tall))
        print("game_prop.py: scaled %.3f across and %.3f up" % (flat, tall))
        return
    if height:
        scale = height / max(z1 - z0, 1e-6)
    elif length:
        scale = length / max(x1 - x0, y1 - y0, 1e-6)
    else:
        return
    for obj in objs:
        for v in obj.data.vertices:
            v.co *= scale


def hub_centre(obj):
    """Where a wheel part turns: the centre of the circle its tread lies on, fitted to the vertices furthest from the
    axle. The middle of the part's box is off the hub when the part is lopsided (Rodin's wheel splits bring a bit of
    arch or axle along, and the tyre sits flat where it meets the ground), and a wheel spun about that wobbles.
    The axle runs along X (the part's thinnest side); the circle is fitted in the Y/Z plane."""
    c, size = centre(obj)
    pts = [v.co.copy() for v in obj.data.vertices]
    cy, cz = c.y, c.z
    for _ in range(4):
        rs = [((p.y - cy) ** 2 + (p.z - cz) ** 2) ** 0.5 for p in pts]
        rmax = max(rs)
        rim = [p for p, r in zip(pts, rs) if r > 0.8 * rmax]
        if len(rim) < 8:
            break
        # Kasa fit: minimise sum (y^2 + z^2 + D y + E z + F)^2 over the rim, a 3x3 least-squares problem
        n = float(len(rim))
        sy = sum(p.y for p in rim); sz = sum(p.z for p in rim)
        syy = sum(p.y * p.y for p in rim); szz = sum(p.z * p.z for p in rim); syz = sum(p.y * p.z for p in rim)
        w = [p.y * p.y + p.z * p.z for p in rim]
        sw = sum(w); swy = sum(wi * p.y for wi, p in zip(w, rim)); swz = sum(wi * p.z for wi, p in zip(w, rim))
        m = [[syy, syz, sy], [syz, szz, sz], [sy, sz, n]]
        rhs = [-swy, -swz, -sw]
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        if abs(det) < 1e-12:
            break
        def solve(col):
            mm = [row[:] for row in m]
            for i in range(3):
                mm[i][col] = rhs[i]
            return (mm[0][0] * (mm[1][1] * mm[2][2] - mm[1][2] * mm[2][1]) - mm[0][1] * (mm[1][0] * mm[2][2] - mm[1][2] * mm[2][0])
                    + mm[0][2] * (mm[1][0] * mm[2][1] - mm[1][1] * mm[2][0])) / det
        d, e = solve(0), solve(1)
        cy, cz = -d * 0.5, -e * 0.5
    rs = [((p.y - cy) ** 2 + (p.z - cz) ** 2) ** 0.5 for p in pts]
    rmax = max(rs)
    rim = [r for r in rs if r > 0.8 * rmax]
    mean = sum(rim) / len(rim)
    spread = (sum((r - mean) ** 2 for r in rim) / len(rim)) ** 0.5
    hub = Vector((c.x, cy, cz))
    print("game_prop.py: %s hub %.3f m off the box middle, radius %.3f (rim spread %.3f)" % (obj.name, (hub - c).length, mean, spread))
    return hub


def straighten_axle(obj):
    """Turns the wheel's vertices (about the origin, the middle of the part) so the axle it was modelled on lies
    exactly along X. Rodin models a steered or cambered wheel as it stands (the RANGER's fronts were turned 20
    degrees), and a wheel like that spun about X sweeps a cone: a wobble. The axle is the direction most of the
    part's surface faces, by area: the two sidewalls and the rim face along it, while the tread's normals spread
    evenly round the plane across it (so the axle is the top eigenvector of the area-weighted normal tensor)."""
    import numpy as np
    m = np.zeros((3, 3))
    for poly in obj.data.polygons:
        n = np.array(poly.normal[:])
        m += poly.area * np.outer(n, n)
    w, vecs = np.linalg.eigh(m)
    axis = vecs[:, 2]
    if axis[0] < 0:
        axis = -axis
    axis = Vector(axis.tolist())
    angle = axis.angle(Vector((1.0, 0.0, 0.0)))
    if angle < 0.002:
        return 0.0
    # a wheel squared to its axle is at its narrowest across X; if the turn widens it, the fit was fooled (a
    # wheel with a bulging cap, or a part that is not a wheel) and the part is left as modelled
    before = centre(obj)[1][0]
    q = axis.rotation_difference(Vector((1.0, 0.0, 0.0)))
    turned = [q @ v.co for v in obj.data.vertices]
    after = max(p.x for p in turned) - min(p.x for p in turned)
    if after > before - 0.002:
        print("game_prop.py: %s left as modelled (squaring it %.1f degrees would widen it %.3f -> %.3f)" % (obj.name, angle * 57.2958, before, after))
        return 0.0
    for v, p in zip(obj.data.vertices, turned):
        v.co = p
    return angle


def pivot_at_centre(obj):
    """Moves the object's origin to its hub (hub_centre) and squares its axle to X, so a wheel turns true."""
    c0, _ = centre(obj)
    for v in obj.data.vertices:
        v.co -= c0
    wide_before = centre(obj)[1][0]
    tilt = straighten_axle(obj)
    print("game_prop.py: %s axle was %.2f degrees off X; %.3f m across before, %.3f after" % (obj.name, tilt * 57.2958, wide_before, centre(obj)[1][0]))
    c = hub_centre(obj)
    for v in obj.data.vertices:
        v.co -= c
    obj.location = c0 + c


def clusters(values, gap):
    """Sorted values split where they jump by more than gap: [(from, to, count)]."""
    out = []
    for v in sorted(values):
        if out and v - out[-1][1] <= gap:
            out[-1] = (out[-1][0], v, out[-1][2] + 1)
        else:
            out.append((v, v, 1))
    return out


def slice_points(segments, h):
    """Where the mesh's edges cross the height h: points (x, y). Edges, not vertices, so a low-poly tyre, whose few
    vertices miss the heights measured, still shows its full width."""
    out = []
    for (ax, ay, az), (bx, by, bz) in segments:
        if (az - h) * (bz - h) <= 0.0 and az != bz:
            t = (h - az) / (bz - az)
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


def find_hubs(segments, side, end, width, length, height):
    """A corner's tyres on a truck in one piece (Tripo): each place it meets the ground is a tyre, and the chord of
    its circle a little above the ground gives its centre and radius (low, so two tyres of a tandem axle are still
    apart, and a door or step hanging low is not measured: only the run around the ground contact). side +1 left (+x),
    end -1 front (-y). Returns [(hub y, radius, outer x)], the one nearest the middle first."""
    def corner(pts):
        return [p for p in pts if side * p[0] > 0.15 * width and end * p[1] > 0.02 * length]
    ground = [y for _, y in corner(slice_points(segments, 0.008 * height))]
    hubs = []
    for g0, g1, n in clusters(ground, 0.04 * length):
        y0 = (g0 + g1) * 0.5
        rs, mids = [], []
        for frac in (0.03, 0.05, 0.07):
            h = frac * height
            ys = [y for _, y in corner(slice_points(segments, h)) if abs(y - y0) < 0.3 * length]
            runs = [c for c in clusters(ys, 0.035 * length) if c[0] - 0.02 <= y0 <= c[1] + 0.02]
            if not runs:
                continue
            c0, c1, _ = runs[0]
            half = (c1 - c0) * 0.5
            rs.append((half * half + h * h) / (2.0 * h))
            mids.append((c0 + c1) * 0.5)
        if not rs:
            continue
        rs.sort()
        r = rs[len(rs) // 2]
        yc = y0   # the ground contact is right under the hub; the chords can run into a tandem neighbour
        if not (0.04 * length < r < 0.22 * length):
            continue
        xs = [side * x for x, y in corner(slice_points(segments, r)) if abs(y - yc) < r]
        if not xs:
            continue
        hubs.append((yc, r, side * max(xs)))
    hubs.sort(key=lambda t: abs(t[0]))
    return hubs


def carve_wheels(obj):
    """A truck generated in one piece (Tripo's are thousands of fused fragments): cut each tyre out as its own
    object, a cylinder across x at the hub find_hubs measures, so TruckModelTitan can spin and steer it: Wheel_FL,
    FR, RL, RR, and a second rear axle as RL2, RR2. Returns name -> object, or {} when a corner has no tyre that can
    be told from the body (the code wheels stay then)."""
    (x0, x1), (y0, y1), (z0, z1) = bounds(obj)
    width, length, height = x1 - x0, y1 - y0, z1 - z0
    co = [v.co.copy() for v in obj.data.vertices]
    segments = [(tuple(co[e.vertices[0]]), tuple(co[e.vertices[1]])) for e in obj.data.edges]
    found = {}
    for corner, side, end in (("FL", 1, -1), ("FR", -1, -1), ("RL", 1, 1), ("RR", -1, 1)):
        found[corner] = (side, end, find_hubs(segments, side, end, width, length, height))
    cuts = {}
    mirror = {"FL": "FR", "FR": "FL", "RL": "RR", "RR": "RL"}
    for corner, (side, end, hubs) in found.items():
        if not hubs:
            # something hangs low at this corner (an open door, a step): the truck is symmetric, so take the other
            # side's tyre across
            other = found[mirror[corner]][2]
            if not other:
                print("game_prop.py: no tyre found at %s or %s; keeping the code wheels" % (corner, mirror[corner]))
                return {}
            hubs = [(y, r, -x) for y, r, x in other]
            print("game_prop.py: no tyre found at %s; mirrored from %s" % (corner, mirror[corner]))
        cuts[corner] = (side,) + hubs[0]
        if end > 0 and len(hubs) > 1:
            cuts[corner + "2"] = (side,) + hubs[1]   # a tandem rear axle
    # a truck is the same on both sides: each axle's two tyres share their place and size
    for left in [k for k in cuts if k.startswith(("FL", "RL"))]:
        right = left.replace("L", "R", 1) if left[0] == "F" else "RR" + left[2:]
        if right in cuts:
            (sl, yl, rl, ol), (sr, yr, rr, orr) = cuts[left], cuts[right]
            y, r, o = (yl + yr) * 0.5, (rl + rr) * 0.5, (abs(ol) + abs(orr)) * 0.5
            cuts[left], cuts[right] = (sl, y, r, sl * o), (sr, y, r, sr * o)
    wheels = {}
    for corner, (side, yc, r, outer) in cuts.items():
        inner = outer - side * 0.8 * r
        lo, hi = min(inner, outer), max(inner, outer)
        n = 0
        for poly in obj.data.polygons:
            c = poly.center
            n += lo - 0.02 <= c.x <= hi + 0.02 and (c.y - yc) ** 2 + (c.z - r) ** 2 <= (r * 1.02) ** 2
        if n < 20:
            print("game_prop.py: only %d faces at %s; keeping the code wheels" % (n, corner))
            return {}
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        before = set(bpy.context.scene.objects)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.select_all(action="DESELECT")   # an import leaves every vertex selected: start from none
        bpy.ops.object.mode_set(mode="OBJECT")
        for poly in obj.data.polygons:
            c = poly.center
            poly.select = lo - 0.02 <= c.x <= hi + 0.02 and (c.y - yc) ** 2 + (c.z - r) ** 2 <= (r * 1.02) ** 2
            if poly.select:
                for i in poly.vertices:
                    obj.data.vertices[i].select = True
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
        w = [o for o in bpy.context.scene.objects if o not in before][0]
        wheels[corner] = w
        print("game_prop.py: %s tyre r %.2f m at y %.2f, x %.2f..%.2f, %d faces" % (corner, r, yc, lo, hi, n))
    return wheels


def make_lods(obj, name, ratios):
    obj.name = name + "_LOD0"
    obj.data.name = name + "_LOD0"
    lods = [obj]
    for i, ratio in enumerate(ratios, start=1):
        copy = obj.copy()
        copy.data = obj.data.copy()
        copy.name = name + "_LOD%d" % i
        copy.data.name = copy.name
        bpy.context.scene.collection.objects.link(copy)
        mod = copy.modifiers.new("Decimate", "DECIMATE")
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = copy
        bpy.ops.object.modifier_apply(modifier=mod.name)
        lods.append(copy)
    # Unity wants the LODs as siblings under one root named after the model
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for lod in lods:
        lod.parent = root
    return root, lods


def decimate_to_triangles(obj, target):
    """Reduce an over-dense generated mesh to a practical base LOD while retaining its UVs and materials."""
    if not target or target <= 0:
        return
    obj.data.calc_loop_triangles()
    current = len(obj.data.loop_triangles)
    if current <= target:
        print("game_prop.py: %s already has %d triangles (target %d); keeping it" % (obj.name, current, target))
        return
    mod = obj.modifiers.new("Base mesh budget", "DECIMATE")
    mod.ratio = target / float(current)
    mod.use_collapse_triangulate = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.data.calc_loop_triangles()
    print("game_prop.py: reduced base mesh from %d to %d triangles" % (current, len(obj.data.loop_triangles)))


def export(root, lods, out_path, extras=(), path_mode="COPY"):
    """The game FBX: -Z forward, Y up, unit scale applied, no leaf bones. path_mode "COPY" puts the maps beside it;
    game_parts.py passes "RELATIVE" to keep naming them in <Name>.fbm where they already lie."""
    bpy.ops.object.select_all(action="DESELECT")
    root.select_set(True)
    for lod in lods:
        lod.select_set(True)
    for e in extras:
        e.select_set(True)
    bpy.context.view_layer.objects.active = root
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    bpy.ops.export_scene.fbx(filepath=out_path, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                             axis_forward="-Z", axis_up="Y", bake_space_transform=True, use_mesh_modifiers=True, mesh_smooth_type="FACE",
                             add_leaf_bones=False, bake_anim=False, path_mode=path_mode, embed_textures=False)


def main():
    a = args()
    src = os.path.abspath(a.src)
    out_dir = a.out
    out = os.path.join(os.path.abspath(out_dir), a.name + ".fbx")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0
    import_model(src)
    # a GLB carries its textures inside; write them out beside the FBX so Unity finds them
    if os.path.splitext(src)[1].lower() in (".glb", ".gltf"):
        print("game_prop.py: %d maps saved to %s.fbm" % (save_maps(a.name, os.path.abspath(out_dir)), a.name))
    else:
        try:
            bpy.ops.file.unpack_all(method="WRITE_LOCAL")
        except RuntimeError:
            pass
    wheels = {}
    if a.wheels:
        obj, wheels = split_wheels(all_meshes())
    else:
        obj = join_all()
    parts = [obj] + list(wheels.values())
    face_minus_y(parts, a.front)
    size_to(parts, a.length, a.height)
    stand_on_ground(parts)
    if a.carve_wheels:
        wheels = carve_wheels(obj)
        for w in wheels.values():
            decimate_to_triangles(w, 2500)
        parts = [obj] + list(wheels.values())
    decimate_to_triangles(obj, a.triangles)
    if a.wind or a.name in {"TreeOak", "TreeRound", "TreePine", "TreeDead", "Bush"}:
        mark_tree_wind(obj)
    ratios = [float(r) for r in a.lods.split(",") if r.strip()]
    root, lods = make_lods(obj, a.name, ratios)
    (x0, x1), (y0, y1), (z0, z1) = group_bounds(parts)
    for corner, w in wheels.items():
        pivot_at_centre(w)
        w.name = "Wheel_" + corner
        w.data.name = w.name
        w.parent = root
    extras = list(wheels.values())
    if a.carve_wheels and not wheels:
        # its tyres are part of the body: an empty marker tells TruckModelTitan to hide the code wheels anyway
        baked = bpy.data.objects.new("Wheels_Baked", None)
        bpy.context.scene.collection.objects.link(baked)
        baked.parent = root
        extras.append(baked)
        print("game_prop.py: tyres stay in the body (Wheels_Baked)")
    export(root, lods, out, extras)
    tris = [sum(len(p.vertices) - 2 for p in lod.data.polygons) for lod in lods]
    print("wrote %s: %.1f x %.1f x %.1f m, triangles %s, wheels %s" % (out, x1 - x0, y1 - y0, z1 - z0, tris, sorted(wheels)))


if __name__ == "__main__":
    main()

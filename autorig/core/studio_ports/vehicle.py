# SPDX-License-Identifier: GPL-3.0-or-later


"""Vehicle geometry helpers adapted from storm-chaser/Tools/blender/cleanup.py.

See docs/MODEL-STUDIO.md for provenance. Studio owns import, export and settings.
"""


import math


import bpy


from mathutils import Vector


def all_meshes():
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("cleanup.py: no meshes in the file")
    # drop empties and armatures: a prop has none worth keeping; bake every transform into the vertices
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
        if size[2] > (0.5 + 1e-5) * h:
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
            print("cleanup.py: %s wheel carved out of the body, in the image of %s" % (corner, other))
    if len(wheels) != 4:
        print("cleanup.py: found %d wheel parts, not 4 (%s); keeping everything as the body" % (len(wheels), sorted(wheels)))
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
    turns = {"-y": 0.0, "+y": math.pi, "+x": math.pi / 2, "-x": -math.pi / 2}
    if turns[front] == 0.0:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.rotation_euler = (0.0, 0.0, turns[front])
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


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
        print("cleanup.py: scaled %.3f across and %.3f up" % (flat, tall))
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
    print("cleanup.py: %s hub %.3f m off the box middle, radius %.3f (rim spread %.3f)" % (obj.name, (hub - c).length, mean, spread))
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
        print("cleanup.py: %s left as modelled (squaring it %.1f degrees would widen it %.3f -> %.3f)" % (obj.name, angle * 57.2958, before, after))
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
    print("cleanup.py: %s axle was %.2f degrees off X; %.3f m across before, %.3f after" % (obj.name, tilt * 57.2958, wide_before, centre(obj)[1][0]))
    c = hub_centre(obj)
    for v in obj.data.vertices:
        v.co -= c
    obj.location = c0 + c

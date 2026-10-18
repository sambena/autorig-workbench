# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a four-legged animal on Mesh2Motion's skeleton, playing Mesh2Motion's clips.
#
#   blender -b --python autorig/steps/m2m_quadruped.py -- --in Cow.glb --rig horse-animations.glb --name CowHolstein
#           --height 1.6 --out <folder> [--triangles 15000] [--clips Idle=idle,Walk=walk,Trot=trot,Run=gallop]
#   python autorig/cli/run.py m2m-quadruped <the same arguments>
#
# Mesh2Motion (https://mesh2motion.org, rigs and animations CC0, app MIT) is an open-source answer to Mixamo with a
# horse, fox and bird skeleton and a library of clips made for each. Clips made for the skeleton they play on need no
# retargeting, which is what bent Tornado Titans' people out of shape. Its animations GLB (the app's
# static/animations/<kind>-animations.glb: the armature and every action) is the --rig. For each animal:
#   1. the source mesh alone (any armature it came with is dropped: Tripo's Holstein had a front leg of one bone),
#      joined, decimated, stood on the ground in metres at --height, facing -Y as the rig does;
#   2. landmarks read off the mesh (the four feet, the belly, the top line, the nose, the tail) and off the rig's own
#      rest pose; every joint of the rig is moved by a piecewise map between the two, so the legs land in the legs and
#      the spine in the body, and the bones keep their rolls (the clips are rotations about them);
#   3. the clips' root and hip travel scaled by the size change;
#   4. skinned through a watertight voxel copy (bone heat), the weights carried back to the real mesh by nearest
#      face, because a generated mesh is thousands of loose fragments that bone heat on its own leaves unweighted;
#   5. the clips renamed for the engine (--clips) and the rest dropped, exported as FBX takes (-Z forward, Y up,
#      deform bones only); run fbx-maps and unity_fbx on it after for the maps and Unity's axes.
# Prints M2M_QUADRUPED {json}.
import argparse, json, math, os, sys

import bmesh
import bpy
from mathutils import Matrix, Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--rig", required=True, help="Mesh2Motion's <kind>-animations.glb")
    p.add_argument("--name", required=True)
    p.add_argument("--height", type=float, required=True, help="height at the top of the back or head, metres")
    p.add_argument("--out", required=True)
    p.add_argument("--triangles", type=int, default=15000)
    p.add_argument("--clips", default="Idle=idle,Walk=walk,Trot=trot,Run=gallop")
    p.add_argument("--facing", default="auto", help="which way the source's head points: +x, -x, +y, -y or auto")
    p.add_argument("--voxel", type=float, default=0.0, help="proxy voxel size in metres (default: height / 70)")
    return p.parse_args(argv)


def import_any(path):
    if path.lower().endswith((".glb", ".gltf")): bpy.ops.import_scene.gltf(filepath=path)
    else: bpy.ops.import_scene.fbx(filepath=path)


def real_meshes():
    """The meshes worth keeping: a stray helper (the 42-vertex icosphere the importer brings) is far smaller."""
    ms = [o for o in bpy.data.objects if o.type == 'MESH']
    big = max((len(o.data.vertices) for o in ms), default=0)
    return [o for o in ms if len(o.data.vertices) >= big * 0.05]


def head_end(co, axis):
    """+1 or -1: the end of the long axis the head is at. The head end stands taller over its last fifth than the
    rump does over its first (a tail is thin and low; a head and neck are a block)."""
    lo, hi = min(c[axis] for c in co), max(c[axis] for c in co)
    span = hi - lo
    def bulk(sel):
        return sum(c.z for c in sel) / max(len(sel), 1) + 0.0 * len(sel)
    near_hi = [c for c in co if c[axis] > hi - span * 0.2]
    near_lo = [c for c in co if c[axis] < lo + span * 0.2]
    zh = sorted(c.z for c in near_hi)[int(len(near_hi) * 0.9)] if near_hi else 0
    zl = sorted(c.z for c in near_lo)[int(len(near_lo) * 0.9)] if near_lo else 0
    return 1 if zh > zl else -1


def animal_mesh(a):
    """The source as one mesh in metres, feet on z=0, centred, any rig it had dropped."""
    import_any(a.src)
    meshes = real_meshes()
    for o in meshes:
        mw = o.matrix_world.copy()
        o.modifiers.clear()
        o.parent = None
        o.matrix_world = mw
        for vg in list(o.vertex_groups): o.vertex_groups.remove(vg)
    for o in list(bpy.data.objects):
        if o.type != 'MESH': bpy.data.objects.remove(o)
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes: o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1: bpy.ops.object.join()
    m = bpy.context.view_layer.objects.active
    m.name = a.name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    tris = sum(len(p.vertices) - 2 for p in m.data.polygons)
    if tris > a.triangles:
        d = m.modifiers.new("decimate", 'DECIMATE'); d.ratio = a.triangles / tris
        bpy.ops.object.modifier_apply(modifier=d.name)
    # turned so the head points down -Y, as the rig's does
    co = [v.co for v in m.data.vertices]
    ext = [max(c[i] for c in co) - min(c[i] for c in co) for i in range(2)]
    if a.facing == "auto":
        axis = 0 if ext[0] > ext[1] else 1
        sign = head_end(co, axis)
    else:
        axis, sign = (0 if a.facing[1] == "x" else 1), (1 if a.facing[0] == "+" else -1)
    # rotate the head direction onto -Y
    yaw = {(0, 1): -math.pi / 2, (0, -1): math.pi / 2, (1, 1): math.pi, (1, -1): 0.0}[(axis, sign)]
    m.data.transform(Matrix.Rotation(yaw, 4, 'Z'))
    a.facing_used = ("+" if sign > 0 else "-") + "xy"[axis]
    co = [v.co for v in m.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)]); hi = Vector([max(c[i] for c in co) for i in range(3)])
    m.data.transform(Matrix.Translation(Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))))
    m.data.transform(Matrix.Scale(a.height / max(hi.z - lo.z, 1e-6), 4))
    m.data.update()
    return m


def landmarks(points):
    """Where an animal's parts are, from its surface points (x across, y along with the head at -Y, z up)."""
    import statistics
    ps = list(points)
    top = max(p.z for p in ps)
    ylo, yhi = min(p.y for p in ps), max(p.y for p in ps)
    # the feet: the lowest few percent, split front and back at the middle of the footprints, left and right at x=0
    low = [p for p in ps if p.z < top * 0.06]
    ymid = statistics.median([p.y for p in low]) if low else (ylo + yhi) / 2
    def foot(front, left):
        sel = [p for p in low if (p.y < ymid) == front and (p.x > 0) == left]
        if not sel: return None
        return Vector((statistics.median([p.x for p in sel]), statistics.median([p.y for p in sel]), 0.0))
    feet = {k: foot(*v) for k, v in {"fl": (True, True), "fr": (True, False), "bl": (False, True), "br": (False, False)}.items()}
    fy = statistics.mean([feet[k].y for k in ("fl", "fr") if feet[k]])
    by = statistics.mean([feet[k].y for k in ("bl", "br") if feet[k]])
    fx = statistics.mean([abs(feet[k].x) for k in ("fl", "fr") if feet[k]])
    bx = statistics.mean([abs(feet[k].x) for k in ("bl", "br") if feet[k]])
    # the belly: the lowest point under the middle of the body; the top line over each pair of legs
    mid = [p for p in ps if abs(p.y - (fy + by) / 2) < abs(by - fy) * 0.15 and abs(p.x) < max(fx, bx) * 0.6 and p.z > top * 0.15]
    belly = min(p.z for p in mid) if mid else top * 0.5
    def top_at(y):
        sel = [p for p in ps if abs(p.y - y) < abs(by - fy) * 0.12]
        return max(p.z for p in sel) if sel else top
    return dict(nose=ylo, tail=yhi, front=fy, back=by, front_x=fx, back_x=bx, belly=belly,
                withers=top_at(fy), croup=top_at(by), top=top)


def piecewise(x, xs, ys):
    """Linear through (xs, ys), straight on past the ends."""
    pairs = sorted(zip(xs, ys))
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x <= x1 or (x1, y1) == pairs[-1]:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if abs(x1 - x0) > 1e-9 else y0
    return pairs[-1][1]


def rig_points(arm):
    """The rig's own surface for its landmarks: its bones' heads and tails, sampled densely along each bone."""
    out = []
    for b in arm.data.bones:
        h, t = arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local
        for k in range(6): out.append(h.lerp(t, k / 5))
    return out


def fit(arm, rig_marks, animal):
    """Moves every joint of the rig from its landmarks to the animal's (edit bones, rolls kept)."""
    R, A = rig_marks, animal
    ys = ([R["nose"], R["front"], R["back"], R["tail"]], [A["nose"], A["front"], A["back"], A["tail"]])
    def zmap(z, y):
        # under the belly the legs stretch to the animal's legs; above it the body to its top line, front or back
        topR = piecewise(y, [R["front"], R["back"]], [R["withers"], R["croup"]])
        topA = piecewise(y, [A["front"], A["back"]], [A["withers"], A["croup"]])
        if z <= R["belly"]: return z * A["belly"] / max(R["belly"], 1e-6)
        if z <= topR: return A["belly"] + (z - R["belly"]) * (topA - A["belly"]) / max(topR - R["belly"], 1e-6)
        return topA + (z - topR) * (A["top"] - A["belly"]) / max(R["top"] - R["belly"], 1e-6)
    def xmap(x, y, z):
        legs_front = abs(y - R["front"]) < abs(y - R["back"])
        k = (A["front_x"] / max(R["front_x"], 1e-6)) if legs_front else (A["back_x"] / max(R["back_x"], 1e-6))
        return x * k
    def move(p):
        return Vector((xmap(p.x, p.y, p.z), piecewise(p.y, *ys), zmap(p.z, p.y)))
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    inv = arm.matrix_world.inverted()
    mw = arm.matrix_world
    new = {}
    for eb in arm.data.edit_bones:
        new[eb.name] = (inv @ move(mw @ eb.head), inv @ move(mw @ eb.tail), eb.roll)
    for eb in arm.data.edit_bones:
        h, t, r = new[eb.name]
        eb.head, eb.tail, eb.roll = h, t, r
    bpy.ops.object.mode_set(mode='OBJECT')


def fcurves(act):
    """An action's curves: on the action itself before Blender 4.4, in its layers' channel bags after."""
    if hasattr(act, "fcurves"): return list(act.fcurves)
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags: out.extend(bag.fcurves)
    return out


def scale_travel(actions, sx, sy, sz):
    """The clips' root and hip movement scaled by how the animal's size differs from the rig's."""
    for act in actions:
        for fc in fcurves(act):
            if not fc.data_path.endswith("location"): continue
            k = (sx, sy, sz)[fc.array_index]
            for p in fc.keyframe_points:
                p.co.y *= k; p.handle_left.y *= k; p.handle_right.y *= k


def skin(mesh, arm, voxel):
    """Bone heat on a watertight voxel copy, carried back to the mesh by nearest face."""
    for b in arm.data.bones:
        n = b.name.lower()
        if n.endswith("leaf") or n.endswith("_tip") or n.endswith(".tip") or n.endswith("tip_l") or n.endswith("tip_r") or n == "root":
            b.use_deform = False
    bpy.ops.object.select_all(action='DESELECT')
    proxy = mesh.copy(); proxy.data = mesh.data.copy(); proxy.name = mesh.name + "_proxy"
    bpy.context.scene.collection.objects.link(proxy)
    rem = proxy.modifiers.new("remesh", 'REMESH'); rem.mode = 'VOXEL'; rem.voxel_size = voxel
    bpy.context.view_layer.objects.active = proxy
    bpy.ops.object.modifier_apply(modifier=rem.name)
    proxy.select_set(True); arm.select_set(True); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    unreached = sum(1 for v in proxy.data.vertices if not v.groups)
    bpy.ops.object.select_all(action='DESELECT')
    for b in arm.data.bones:
        if b.use_deform and b.name not in mesh.vertex_groups: mesh.vertex_groups.new(name=b.name)
    dt = mesh.modifiers.new("weights", 'DATA_TRANSFER')
    dt.object = proxy; dt.use_vert_data = True; dt.data_types_verts = {'VGROUP_WEIGHTS'}
    dt.vert_mapping = 'POLYINTERP_NEAREST'; dt.layers_vgroup_select_src = 'ALL'; dt.layers_vgroup_select_dst = 'NAME'
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.modifier_apply(modifier=dt.name)
    bpy.data.objects.remove(proxy)
    # at most four influences, normalised, as an engine takes them
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    mesh.parent = arm
    mod = mesh.modifiers.new("Armature", 'ARMATURE'); mod.object = arm
    bare = sum(1 for v in mesh.data.vertices if not v.groups)
    return unreached, bare


def main():
    a = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = animal_mesh(a)
    A = landmarks([v.co for v in mesh.data.vertices])
    # the rig: its armature and actions; its own sample meshes go
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=a.rig)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o not in before)
    # the rig's own sample animal gives its landmarks on the same terms as ours (surface, not bones)
    sample = [o for o in real_meshes() if o not in before and o is not mesh]
    rig_surface = [o.matrix_world @ v.co for o in sample for v in o.data.vertices]
    for o in list(bpy.data.objects):
        if o not in before and o is not arm: bpy.data.objects.remove(o)
    arm.animation_data_clear()
    bpy.ops.object.select_all(action='DESELECT')
    arm.select_set(True); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    R = landmarks(rig_surface or rig_points(arm))
    keep = {}
    for pair in a.clips.split(","):
        src, dst = pair.split("=")
        keep[src.strip()] = dst.strip()
    actions = [act for act in bpy.data.actions if act.name in keep]
    for act in list(bpy.data.actions):
        if act.name not in keep: bpy.data.actions.remove(act)
    sx = A["front_x"] / max(R["front_x"], 1e-6)
    sy = abs(A["back"] - A["front"]) / max(abs(R["back"] - R["front"]), 1e-6)
    sz = A["top"] / max(R["top"], 1e-6)
    fit(arm, R, A)
    scale_travel(actions, sx, sy, sz)
    unreached, bare = skin(mesh, arm, a.voxel or a.height / 70.0)
    # the clips under the engine's names, each a take of its own
    arm.animation_data_create()
    for act in actions:
        act.name = keep[act.name]
        act.use_fake_user = True
        tr = arm.animation_data.nla_tracks.new(); tr.name = act.name
        tr.strips.new(act.name, int(act.frame_range[0]), act)
    arm.name = "Armature"
    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, a.name + ".fbx")
    bpy.ops.object.select_all(action='DESELECT')
    mesh.select_set(True); arm.select_set(True)
    bpy.ops.export_scene.fbx(filepath=out, use_selection=True, apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL',
                             axis_forward='-Z', axis_up='Y', bake_space_transform=False, add_leaf_bones=False,
                             use_armature_deform_only=True, bake_anim=True, bake_anim_use_all_actions=False,
                             bake_anim_use_nla_strips=True, bake_anim_force_startend_keying=True, path_mode='AUTO')
    blend = os.path.join(a.out, a.name + ".blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    print("M2M_QUADRUPED " + json.dumps({"out": out, "blend": blend, "clips": [keep[k] for k in keep], "facing": a.facing_used,
                                          "landmarks_animal": {k: round(v, 3) for k, v in A.items()},
                                          "landmarks_rig": {k: round(v, 3) for k, v in R.items()},
                                          "proxy_unreached": unreached, "bare_vertices": bare,
                                          "triangles": sum(len(p.vertices) - 2 for p in mesh.data.polygons)}))


main()

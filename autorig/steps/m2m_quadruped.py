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
    p.add_argument("--swing", type=float, default=1.0, help="scale the legs' swing (1 as made; a cow ~0.75)")
    p.add_argument("--tail-swing", dest="tail_swing", type=float, default=0.4, help="scale the tail's swing")
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


class Shape:
    """An animal's surface read as cross-sections: the body slice by slice along its length, each leg level by level
    up from the ground. A joint is placed by where it sits inside its part (how far up, across, along), so the same
    joint lands in the same place inside a differently shaped animal."""

    def __init__(self, points, marks):
        self.p = list(points)
        self.m = marks
        self.length = marks["tail"] - marks["nose"]
        self.floor = marks["belly"] * 0.9          # below this, a slice of the body is legs
        self.ymid = (marks["front"] + marks["back"]) / 2

    @staticmethod
    def _spread(vals):
        vals = sorted(vals)
        n = len(vals)
        lo, hi = vals[int(n * 0.08)], vals[min(n - 1, int(n * 0.92))]
        return (lo + hi) / 2, max((hi - lo) / 2, 1e-3)

    def slice(self, y):
        """The body at y: its top, bottom (above the legs) and half width, widening the window until it holds points."""
        sel = []
        for w in (0.03, 0.06, 0.12, 0.25):
            sel = [q for q in self.p if abs(q.y - y) < self.length * w and q.z > self.floor]
            if len(sel) >= 12: break
        if not sel: sel = self.p
        zs = sorted(q.z for q in sel)
        top, bot = zs[int(len(zs) * 0.97)], zs[int(len(zs) * 0.03)]
        half = sorted(abs(q.x) for q in sel)[int(len(sel) * 0.95)]
        return top, bot, max(half, 1e-3)

    def leg(self, front, left, z):
        """One leg at height z: its centre and half spread across (x) and along (y)."""
        lx = self.m["front_x"] if front else self.m["back_x"]
        sel = []
        for w in (0.02, 0.04, 0.08, 0.16):
            band = self.m["top"] * w
            sel = [q for q in self.p if abs(q.z - z) < band and (q.y < self.ymid) == front and (q.x > 0) == left
                   and abs(q.x) > lx * 0.35]
            if len(sel) >= 6: break
        if not sel: return None
        cx, rx = self._spread([q.x for q in sel])
        cy, ry = self._spread([q.y for q in sel])
        return cx, cy, rx, ry


def chains(arm):
    """The rig's bones sorted into legs (front/back, left/right: from the foot up to where it joins the backbone) and a
    tail (the backbone behind the bone the hind legs hang from). The backbone is the line from the root to the nose
    and to the tip of the tail, which every Mesh2Motion four-legged rig has whatever it names its bones."""
    bones = arm.data.bones
    mw = arm.matrix_world
    top = max((mw @ b.tail_local).z for b in bones)
    def up(b):
        out = []
        while b is not None: out.append(b); b = b.parent
        return out
    tip_y = lambda b: (mw @ b.tail_local).y
    raised = [b for b in bones if (mw @ b.head_local).z > top * 0.3]
    nose, tip = min(raised, key=tip_y), max(raised, key=tip_y)
    spine = {b.name for b in up(nose)} | {b.name for b in up(tip)}
    feet = [b for b in bones if not b.children and (mw @ b.tail_local).z < top * 0.05 and (mw @ b.head_local).z < top * 0.08]
    leg = {}
    for f in feet:
        h = mw @ f.head_local
        for b in up(f):
            if b.name in spine: break
            leg[b.name] = (h.y < 0, h.x > 0)
    tail = []
    for b in up(tip):
        if b.parent is None or len(b.parent.children) > 1 and any(c.name in leg for c in b.parent.children):
            if b.parent is not None: tail.append(b)
            break
        tail.append(b)
    tail.reverse()
    return leg, [b.name for b in tail]


def fit(arm, R, A, rig_surface, animal_surface):
    """Moves every joint of the rig to the same place inside the animal: legs level by level inside each leg, the
    body slice by slice, the tail down the animal's own tail. Each bone's frame is turned the least way onto its new
    direction, so the clips (rotations in those frames) bend the same joints the same way."""
    SR, SA = Shape(rig_surface, R), Shape(animal_surface, A)
    ys = ([R["nose"], R["front"], R["back"], R["tail"]], [A["nose"], A["front"], A["back"], A["tail"]])
    leg_of, tail = chains(arm)
    zk = A["belly"] / max(R["belly"], 1e-6)

    def body(p):
        y2 = piecewise(p.y, *ys)
        tR, bR, hR = SR.slice(p.y)
        tA, bA, hA = SA.slice(y2)
        u = (p.z - bR) / max(tR - bR, 1e-3)
        return Vector((p.x / hR * hA, y2, bA + u * (tA - bA)))

    def in_leg(p, front, left):
        z2 = p.z * zk
        r, a = SR.leg(front, left, p.z), SA.leg(front, left, z2)
        if r is None or a is None:
            k = A["front_x" if front else "back_x"] / max(R["front_x" if front else "back_x"], 1e-6)
            return Vector((p.x * k, piecewise(p.y, *ys), z2))
        ux = max(-1.2, min(1.2, (p.x - r[0]) / r[2]))
        uy = max(-1.2, min(1.2, (p.y - r[1]) / r[3]))
        return Vector((a[0] + ux * a[2], a[1] + uy * a[3], z2))

    def place(name, p):
        if p.z < R["top"] * 0.02 and name not in leg_of:     # the root, on the ground
            return Vector((0.0, piecewise(p.y, *ys), 0.0))
        if name in leg_of and p.z < R["belly"]:
            return in_leg(p, *leg_of[name])
        return body(p)

    # the animal's tail: the thin strand hanging off its back end, top to bottom
    strand = [q for q in animal_surface if q.y > A["tail"] - SA.length * 0.07 and abs(q.x) < A["top"] * 0.06]
    tail_path = None
    if len(strand) > 20:
        zt, zb = max(q.z for q in strand), min(q.z for q in strand)
        if zt - zb > A["top"] * 0.25:
            tail_path = []
            for k in range(9):
                z = zt - (zt - zb) * k / 8
                sel = [q for q in strand if abs(q.z - z) < (zt - zb) / 12] or strand
                tail_path.append(Vector((0.0, sum(q.y for q in sel) / len(sel), z)))

    mw = arm.matrix_world
    bones = arm.data.bones
    heads = {b.name: place(b.name, mw @ b.head_local) for b in bones}
    tail_end = None
    if tail_path and tail:
        # the tail's joints spread down the strand by their share of the rig's tail length
        pts = [mw @ bones[n].head_local for n in tail] + [mw @ bones[tail[-1]].tail_local]
        cum = [0.0]
        for i in range(1, len(pts)): cum.append(cum[-1] + (pts[i] - pts[i - 1]).length)
        def along(f):
            x = f * (len(tail_path) - 1)
            i = min(int(x), len(tail_path) - 2)
            return tail_path[i].lerp(tail_path[i + 1], x - i)
        for i, n in enumerate(tail): heads[n] = along(cum[i] / max(cum[-1], 1e-6))
        tail_end = along(1.0)

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    inv = mw.inverted()
    scale = A["top"] / max(R["top"], 1e-6)
    plan = {}
    for b in arm.data.edit_bones:
        oh, ot = mw @ b.head, mw @ b.tail
        nh = heads[b.name]
        # a child starting where this bone ends gives its new end; otherwise the bone keeps its own vector, scaled
        nxt = [c for c in b.children if ((mw @ c.head) - ot).length < 1e-3]
        if nxt: nt = heads[nxt[0].name]
        elif tail and b.name == tail[-1] and tail_end is not None: nt = tail_end
        else: nt = nh + (ot - oh) * scale
        if (nt - nh).length < 1e-4: nt = nh + (ot - oh) * scale
        q = (ot - oh).rotation_difference(nt - nh)
        plan[b.name] = (inv @ nh, inv @ nt, q @ (mw.to_3x3() @ b.z_axis))
    for b in arm.data.edit_bones:
        h, t, z = plan[b.name]
        b.head, b.tail = h, t
        b.align_roll(inv.to_3x3() @ z)
    bpy.ops.object.mode_set(mode='OBJECT')
    return dict(legs=len(set(leg_of.values())), leg_bones=len(leg_of), tail=len(tail), tail_hangs=tail_path is not None, tail_names=tail), leg_of, SA


def fcurves(act):
    """An action's curves: on the action itself before Blender 4.4, in its layers' channel bags after."""
    if hasattr(act, "fcurves"): return list(act.fcurves)
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags: out.extend(bag.fcurves)
    return out


def scale_travel(actions, k):
    """The clips' root and hip movement scaled by how the animal's legs differ from the rig's."""
    for act in actions:
        for fc in fcurves(act):
            if not fc.data_path.endswith("location"): continue
            for p in fc.keyframe_points:
                p.co.y *= k; p.handle_left.y *= k; p.handle_right.y *= k


def confine(mesh, arm, leg_of, shape, belly):
    """Below the belly a leg bone only moves the skin of its own leg: its weight fades out past the leg's outline (so
    an udder or a belly between the legs isn't dragged by them), and skin left with no weight goes to the nearest
    bone of the body."""
    groups = {mesh.vertex_groups[n].index: leg_of[n] for n in leg_of if n in mesh.vertex_groups}
    body = [b for b in arm.data.bones if b.use_deform and b.name not in leg_of and b.name in mesh.vertex_groups]
    step = shape.m["top"] * 0.02
    cache = {}
    def section(key):
        if key not in cache: cache[key] = shape.leg(key[0], key[1], key[2] * step)
        return cache[key]
    def nearest_body(co):
        best, bd = None, 1e9
        for b in body:
            h, t = b.head_local, b.tail_local
            d = t - h
            f = max(0.0, min(1.0, (co - h).dot(d) / max(d.length_squared, 1e-9)))
            dist = (co - (h + d * f)).length
            if dist < bd: best, bd = b.name, dist
        return best
    moved = 0
    for v in mesh.data.vertices:
        if v.co.z >= belly * 1.05 or v.co.z < belly * 0.2: continue
        changed = False
        for g in v.groups:
            if g.group not in groups: continue
            front, left = groups[g.group]
            sec = section((front, left, int(v.co.z / step)))
            if sec is None or (v.co.x > 0) != left: f = 0.0
            else:
                # only skin inboard of the leg, between the legs (an udder, a belly), lets go; a hoof's toe or
                # heel reaching out front or back is still the leg's
                cx, cy, rx, ry = sec
                d = (abs(cx) - abs(v.co.x)) / rx
                f = 1.0 if d < 1.4 else max(0.0, 1.0 - (d - 1.4) / 0.8)
            if f < 1.0: g.weight *= f; changed = True
        if changed:
            moved += 1
            if sum(g.weight for g in v.groups) < 0.05:
                mesh.vertex_groups[nearest_body(v.co)].add([v.index], 1.0, 'REPLACE')
    return moved


def damp(actions, names, k):
    """The legs' swing scaled down by k (each key's turn from the rest pose eased toward none): a heavy animal on a
    light one's clips lifts its knees less."""
    from mathutils import Quaternion
    for act in actions:
        by_bone = {}
        for fc in fcurves(act):
            if not fc.data_path.endswith("rotation_quaternion"): continue
            n = fc.data_path.split('"')[1]
            if n in names: by_bone.setdefault(n, [None] * 4)[fc.array_index] = fc
        for n, fcs in by_bone.items():
            if None in fcs or len({len(f.keyframe_points) for f in fcs}) != 1: continue
            for i in range(len(fcs[0].keyframe_points)):
                q = Quaternion([f.keyframe_points[i].co.y for f in fcs])
                q = Quaternion().slerp(q, k)
                for j, f in enumerate(fcs):
                    kp = f.keyframe_points[i]
                    d = q[j] - kp.co.y
                    kp.co.y += d; kp.handle_left.y += d; kp.handle_right.y += d


def match_legs(arm, src, actions, leg_of):
    """Rebakes the legs so each leg bone points the way the rig's own does, frame by frame, and the lowest hoof stays
    on the ground. A clip is turns from the rest pose; a cow's legs stand straight where the horse's rest pose is
    angled, so the same turns would leave every cow leg bent all the time. Pointing the bones the same way instead
    gives the cow the horse's leg postures on its own leg lengths. The body, neck, head and tail keep the clip's turns
    from their own rest (a cow's neck isn't a horse's)."""
    from mathutils import Matrix
    pb, sb = arm.pose.bones, src.pose.bones
    order = [b.name for b in arm.data.bones if b.name in leg_of]          # bones come parents first
    tops = {pb[n].parent.name for n in order if pb[n].parent and pb[n].parent.name not in leg_of}
    feet = [n for n in order if not pb[n].children]
    root = next(b for b in arm.data.bones if b.parent is None).name
    sc = bpy.context.scene
    for act in actions:
        for o in (arm, src):
            ad = o.animation_data or o.animation_data_create()
            ad.action = act
            if hasattr(ad, "action_slot") and act.slots: ad.action_slot = act.slots[0]
        f0, f1 = act.frame_range
        n = max(2, int(round(f1 - f0)) + 1)
        frames = [f0 + (f1 - f0) * i / (n - 1) for i in range(n)]
        seen = []
        for f in frames:
            sc.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
            seen.append(({k: sb[k].matrix.copy() for k in order}, {k: pb[k].matrix.copy() for k in tops},
                         pb[root].location.copy()))
        # the legs' own turns go; new ones are keyed below
        for fc in list(fcurves(act)):
            if fc.data_path.startswith('pose.bones["'):
                nm = fc.data_path.split('"')[1]
                if nm in leg_of and fc.data_path.endswith(("rotation_quaternion", "rotation_euler", "location")):
                    fc.keyframe_points.clear()
        for f, (want, parents, root_loc) in zip(frames, seen):
            mats = dict(parents)
            for k in order:
                b = arm.data.bones[k]
                par = mats[b.parent.name]
                rest = b.parent.matrix_local.inverted() @ b.matrix_local
                base = par @ rest                                  # where the bone would be with no turn of its own
                m = Matrix.Translation(base.to_translation()) @ want[k].to_3x3().to_4x4()
                basis = base.inverted() @ m
                mats[k] = base @ basis
                q = basis.to_quaternion()
                pb[k].rotation_mode = 'QUATERNION'
                pb[k].rotation_quaternion = q
                pb[k].location = (0, 0, 0)
                pb[k].keyframe_insert("rotation_quaternion", frame=f)
                pb[k].keyframe_insert("location", frame=f)
            # the lowest hoof on the ground
            low = min((mats[k] @ Matrix.Translation((0, arm.data.bones[k].length, 0))).to_translation().z for k in feet)
            rb = arm.data.bones[root]
            local = rb.matrix_local.to_3x3().inverted() @ Vector((0, 0, -low))
            pb[root].location = root_loc + local
            pb[root].keyframe_insert("location", frame=f)
    for o in (arm, src):
        if o.animation_data: o.animation_data.action = None


def skin(mesh, arm, voxel, after_transfer=None):
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
    if after_transfer: after_transfer()
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
    rig0 = arm.copy(); rig0.data = arm.data.copy(); rig0.name = "rig_as_made"
    bpy.context.scene.collection.objects.link(rig0)
    keep = {}
    for pair in a.clips.split(","):
        src, dst = pair.split("=")
        keep[src.strip()] = dst.strip()
    actions = [act for act in bpy.data.actions if act.name in keep]
    for act in list(bpy.data.actions):
        if act.name not in keep: bpy.data.actions.remove(act)
    fitted, leg_of, shape = fit(arm, R, A, rig_surface or rig_points(arm), [v.co.copy() for v in mesh.data.vertices])
    scale_travel(actions, A["belly"] / max(R["belly"], 1e-6))
    match_legs(arm, rig0, actions, leg_of)
    bpy.data.objects.remove(rig0)
    tail_names = set(fitted.get("tail_names", []))
    if tail_names: damp(actions, tail_names, a.tail_swing)
    if a.swing != 1.0: damp(actions, set(leg_of), a.swing)
    unreached, bare = skin(mesh, arm, a.voxel or a.height / 70.0, lambda: confine(mesh, arm, leg_of, shape, A["belly"]))
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
    print("M2M_QUADRUPED " + json.dumps({"out": out, "blend": blend, "clips": [keep[k] for k in keep], "facing": a.facing_used, "fit": fitted,
                                          "landmarks_animal": {k: round(v, 3) for k, v in A.items()},
                                          "landmarks_rig": {k: round(v, 3) for k, v in R.items()},
                                          "proxy_unreached": unreached, "bare_vertices": bare,
                                          "triangles": sum(len(p.vertices) - 2 for p in mesh.data.polygons)}))


main()

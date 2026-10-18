# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: any mesh on one of Mesh2Motion's skeletons, skinned by us, audited, playing Mesh2Motion's clips.
#
#   blender -b --python autorig/steps/m2m_rig.py -- --in Cow.glb --name CowHolstein --out <folder> [--height 1.6]
#           [--template auto|human|fox|horse|bird|dragon|kaiju|spider|snake|shark] [--archetype quadruped]
#           [--clips walk,idle | Walk=walk,Trot | all] [--m2m <mesh2motion-app>] [--joints joints.json]
#           [--naming default|mixamo] [--target unity|generic] [--format glb,fbx] [--skin autorig|m2m]
#           [--triangles 15000] [--facing auto|+x|-x|+y|-y] [--swing 1] [--tail-swing 0.4] [--voxel 0]
#           [--no-audit] [--no-preview] [--choose-only]
#   blender -b --python autorig/steps/m2m_rig.py -- --model Cow_Holstein_Tripo [the options above]
#   python autorig/cli/run.py m2m-rig <the same arguments>
#
# Mesh2Motion (https://github.com/Mesh2Motion/mesh2motion-app, Scott Petrovic and contributors; app MIT, rigs and
# animations CC0; CREDITS.md) has nine skeletons and about 250 clips made for them. A clip plays only on its own skeleton, so this fits the skeleton itself to the mesh: every bone keeps its
# name, parent and roll frame and moves into the body, and the clips play as authored. core/m2m_templates.py holds
# what is known about each template; the files come from the app's static/rigs and static/animations (--m2m, else
# the third_party/mesh2motion-app submodule, else AUTORIG_M2M or ~/src/mesh2motion-app). For each model:
#   1. the source mesh alone (any rig it came with is dropped), joined, welded, decimated to --triangles, turned to
#      face -Y as the templates do, stood on z=0 at --height metres (default: as it is);
#   2. the template: --template, or picked from the body plan (core/suggest.py: the tips, core/body_plan.py's
#      measures; or the model's rig.json skeleton) by m2m_templates.choose;
#   3. the fit. human: joints measured as rerig_humanoid measures a person, the hands and fingers following the
#      wrist; legged (fox, horse, bird, dragon, kaiju): the shape fit (any number of leg pairs: each leg level by level
#      up from the ground, the body slice by slice, a hanging tail down the strand); body (snake, shark) and spider:
#      every joint slice by slice. --joints replaces any joint with an edited one (rig.json m2m.joints is the shape);
#   4. the clips: position tracks dropped but the tracking bone's (and the root's on a root-motion clip, "... RM"),
#      the travel scaled by the leg-length change; the limbs (legs, arms, wings) rebaked to point the way the
#      template's do frame by frame, the body keeping the clip's own turns, the feet grounded as the template's are;
#   5. skinned (--skin, default the template's): "autorig", bone heat on a watertight voxel copy, carried back by
#      nearest face (below the belly a leg only moves its own leg), at most four influences; or "m2m", Mesh2Motion's
#      own closest-bone skinning (core/m2m_skin.py, ported from its solvers);
#   6. written: <name>.glb (every clip an animation), <name>.fbx (takes; Unity's axes unless --target generic),
#      <name>.blend, preview.glb, rig.json (rig.kind "m2m", m2m: template, joints), the card (model.json) and
#      <name>_m2m.json; graded by steps/audit.py.
# Prints M2M_RIG {json}.
import argparse, json, math, os, re, subprocess, sys, time

import bpy
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "core")
sys.path[:0] = [HERE, CORE]
import m2m_templates as T


def args(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="m2m_rig")
    p.add_argument("--in", dest="src", help="the source mesh (GLB, glTF, FBX, OBJ)")
    p.add_argument("--model", help="an asset-packs model: its source, and its folder for the results")
    p.add_argument("--name", help="output name (default: the model's game_name, or the file's)")
    p.add_argument("--out", help="output folder (default with --model: the model's rig folder)")
    p.add_argument("--height", type=float, default=0.0, help="height in metres (default: as it is)")
    p.add_argument("--template", default="auto", help="auto, or one of: " + ", ".join(T.TEMPLATES))
    p.add_argument("--archetype", default="", help="what it is when --template is auto (quadruped, humanoid...)")
    p.add_argument("--clips", default="", help="roles or clip names, each optionally =engine name; or all")
    p.add_argument("--m2m", default="", help="the Mesh2Motion app checkout (default AUTORIG_M2M)")
    p.add_argument("--joints", default="", help="edited joints: rig.json m2m.joints, or {bone: [x, y, z]}")
    p.add_argument("--naming", default="default", choices=("default", "mixamo"))
    p.add_argument("--target", default="unity", choices=("unity", "generic"))
    p.add_argument("--format", default="glb,fbx")
    p.add_argument("--skin", default="", help="skinning method: autorig or m2m (default: the template's)")
    p.add_argument("--triangles", type=int, default=15000)
    p.add_argument("--facing", default="auto", help="which way the source's head points: +x, -x, +y, -y or auto")
    p.add_argument("--swing", type=float, default=1.0, help="scale the legs' swing (1 as made; a cow ~0.75)")
    p.add_argument("--tail-swing", dest="tail_swing", type=float, default=0.4, help="scale the tail's swing")
    p.add_argument("--wing-swing", dest="wing_swing", type=float, default=0.3,
                   help="scale the wings' swing in clips on the ground (walk, idle); flying clips swing as made")
    p.add_argument("--voxel", type=float, default=0.0, help="proxy voxel size in metres (default: height / 70)")
    p.add_argument("--limbs", default="calibrated", choices=("calibrated", "absolute"),
                   help="four-legged bodies: calibrated keeps each leg's fitted rest where the template stands at rest "
                        "and adds the template's turns (default); absolute points every leg bone as the template's")
    p.add_argument("--rest", default="", choices=("", "template", "as-modelled"),
                   help="the rest pose: the template's (default for snakes) or the body as modelled")
    p.add_argument("--smooth", type=int, default=6, help="weight smoothing passes after the transfer")
    p.add_argument("--choose-only", dest="choose_only", action="store_true",
                   help="print the template choice (M2M_RIG {template, choice}) and stop")
    p.add_argument("--no-audit", dest="audit", action="store_false")
    p.add_argument("--no-preview", dest="preview", action="store_false")
    a = p.parse_args(argv)
    if not a.src and not a.model:
        p.error("--in or --model is needed")
    return a


# ---------------------------------------------------------------- the source mesh

def import_any(path):
    if path.lower().endswith((".glb", ".gltf")): bpy.ops.import_scene.gltf(filepath=path)
    elif path.lower().endswith(".obj"): bpy.ops.wm.obj_import(filepath=path)
    else: bpy.ops.import_scene.fbx(filepath=path)


def real_meshes(objs=None):
    """The meshes worth keeping: a stray helper (the 42-vertex icosphere the importer brings) is far smaller."""
    ms = [o for o in (objs if objs is not None else bpy.data.objects) if o.type == 'MESH']
    big = max((len(o.data.vertices) for o in ms), default=0)
    return [o for o in ms if len(o.data.vertices) >= big * 0.05]


def head_end(co, axis):
    """+1 or -1: the end of the long axis the head is at. The head end stands taller over its last fifth than the
    rump does over its first (a tail is thin and low; a head and neck are a block)."""
    lo, hi = min(c[axis] for c in co), max(c[axis] for c in co)
    span = hi - lo
    near_hi = [c for c in co if c[axis] > hi - span * 0.2]
    near_lo = [c for c in co if c[axis] < lo + span * 0.2]
    zh = sorted(c.z for c in near_hi)[int(len(near_hi) * 0.9)] if near_hi else 0
    zl = sorted(c.z for c in near_lo)[int(len(near_lo) * 0.9)] if near_lo else 0
    return 1 if zh > zl else -1


def source_mesh(path, name, triangles):
    """The source as one welded mesh, any rig it had dropped; its old skeleton's joints kept for the facing."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_any(path)
    joints = {}
    for o in bpy.data.objects:
        if o.type == 'ARMATURE':
            for b in o.data.bones: joints[b.name] = {"pos": o.matrix_world @ b.head_local}
            break
    meshes = real_meshes()
    for o in meshes:
        mw = o.matrix_world.copy()
        o.modifiers.clear()
        o.parent = None
        o.matrix_world = mw
        o.vertex_groups.clear()
        if o.data.shape_keys:
            o.shape_key_clear()
    for o in list(bpy.data.objects):
        if o not in meshes: bpy.data.objects.remove(o)
    for act in list(bpy.data.actions):        # its own clips (a Mesh2Motion GLB's would clash with the template's)
        bpy.data.actions.remove(act)
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes: o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1: bpy.ops.object.join()
    m = bpy.context.view_layer.objects.active
    m.name = name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    try:
        import rerig
        rerig.weld(m)
    except Exception:
        pass
    tris = sum(len(p.vertices) - 2 for p in m.data.polygons)
    if triangles and tris > triangles:
        d = m.modifiers.new("decimate", 'DECIMATE'); d.ratio = triangles / tris
        bpy.ops.object.modifier_apply(modifier=d.name)
    return m, joints


def turn(mesh, yaw):
    mesh.data.transform(Matrix.Rotation(yaw, 4, 'Z'))


def face_minus_y(mesh, facing, how, joints=None):
    """Turns the mesh so its head points down -Y. how: "anatomy" (suggest.detect_mesh_forward: feet, face, knees,
    symmetry; what rerig.normalise's auto facing uses), "long" (the long horizontal axis, head at its taller end: an
    animal standing), or "flip" (anatomy, then the taller end of Y)."""
    co = [v.co for v in mesh.data.vertices]
    if facing != "auto":
        axis, sign = (0 if facing[1] == "x" else 1), (1 if facing[0] == "+" else -1)
    elif how == "long":
        ext = [max(c[i] for c in co) - min(c[i] for c in co) for i in range(2)]
        axis = 0 if ext[0] > ext[1] else 1
        sign = head_end(co, axis)
    else:
        import suggest
        d = suggest.detect_mesh_forward([c[:] for c in co], joints={k: {"pos": list(v["pos"])} for k, v in (joints or {}).items()} or None)
        axis, sign = (0, int(round(d[0]))) if abs(d[0]) > abs(d[1]) else (1, int(round(d[1])) or -1)
    yaw = {(0, 1): -math.pi / 2, (0, -1): math.pi / 2, (1, 1): math.pi, (1, -1): 0.0}[(axis, sign)]
    turn(mesh, yaw)
    if how == "flip" and facing == "auto":
        if head_end([v.co for v in mesh.data.vertices], 1) > 0:
            turn(mesh, math.pi); sign = -sign
    return ("+" if sign > 0 else "-") + "xy"[axis]


def stand(mesh, height):
    """Centred over the origin, feet on z=0, at height metres (0: as it is)."""
    co = [v.co for v in mesh.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)]); hi = Vector([max(c[i] for c in co) for i in range(3)])
    mesh.data.transform(Matrix.Translation(Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))))
    if height and height > 0:
        mesh.data.transform(Matrix.Scale(height / max(hi.z - lo.z, 1e-6), 4))
    mesh.data.update()
    return max(v.co.z for v in mesh.data.vertices)


# ---------------------------------------------------------------- what it is

def suggest_archetype(mesh, name, log):
    """The archetype core/suggest.py picks from the tips and (with the trimesh extra) the body plan."""
    import geo, suggest
    plan = None
    try:
        import suggest_step
        plan = suggest_step.plan_of(mesh)
    except Exception as e:
        log["body_plan_error"] = repr(e)
    s = geo.Surface(mesh)
    tips = [s.norm(s.co[v]) for v, _ in s.tips(most=18, least=0.12)]
    res = suggest.suggest_skeleton(name=name, source_data={"size": list(s.size), "lo": list(s.lo), "hi": list(s.hi),
                                                          "joints": []},
                                   tips=tips, proportions=list(s.size), forward=[0, -1, 0], body_plan=plan)
    log["suggest"] = {"archetype": res.get("archetype"), "confidence": res.get("confidence"),
                      "reasons": (res.get("reasons") or [])[:4]}
    return res.get("archetype"), plan


def body_measures(points, top):
    """What the template choice needs beyond the archetype (core/m2m_templates.choose): ground (the share of the
    footprint whose underside touches the ground: a few feet on a legged body, most of it on a snake or a fish lying
    on its belly), slender (length over the larger of width and height), stand (height over length)."""
    xs, ys = [p.x for p in points], [p.y for p in points]
    x0, y0 = min(xs), min(ys)
    sx, sy = max(max(xs) - x0, 1e-6), max(max(ys) - y0, 1e-6)
    n = 24
    low = {}
    for p in points:
        c = (min(n - 1, int((p.x - x0) / sx * n)), min(n - 1, int((p.y - y0) / sy * n)))
        low[c] = min(low.get(c, 1e9), p.z)
    ground = sum(1 for z in low.values() if z < top * 0.06) / max(len(low), 1)
    return {"ground": round(ground, 3), "slender": round(sy / max(sx, top, 1e-6), 3), "stand": round(top / sy, 3)}


def tail_reach(points):
    """How far a four-legged body reaches back past its hind feet, over the distance between its front and hind
    feet, read along its long horizontal axis with the head at the taller end (as the animal fit turns it): a fox,
    dog or cat carries its tail out behind it, a horse, cow or goat lets it hang (core/m2m_templates.FOX_TAIL)."""
    co = [p.copy() for p in points]
    ext = [max(c[i] for c in co) - min(c[i] for c in co) for i in range(2)]
    axis = 0 if ext[0] > ext[1] else 1
    sign = head_end(co, axis)
    yaw = {(0, 1): -math.pi / 2, (0, -1): math.pi / 2, (1, 1): math.pi, (1, -1): 0.0}[(axis, sign)]
    R = Matrix.Rotation(yaw, 3, 'Z')
    co = [R @ c for c in co]
    L = landmarks(co, 2)
    span = abs(L["back"] - L["front"])
    return round((L["tail"] - L["back"]) / span, 3) if span > 1e-6 else None


def tail_share(points, top):
    """How far the body reaches back past a standing person's depth, over its height: a kaiju's tail."""
    ys = sorted(p.y for p in points)
    if not ys: return 0.0
    n = len(ys)
    body = ys[int(n * 0.5)]
    return max(0.0, (ys[int(n * 0.995)] - body) - 0.25 * top) / max(top, 1e-6)


# ---------------------------------------------------------------- landmarks and shape

def kmeans1(vals, k, iters=25):
    """k centres of a list of numbers, ascending, and each value's centre index."""
    vs = sorted(vals)
    if not vs or k <= 0: return [], []
    k = min(k, len(vs))
    c = [vs[min(len(vs) - 1, int((i + 0.5) * len(vs) / k))] for i in range(k)]
    for _ in range(iters):
        groups = [[] for _ in range(k)]
        for v in vs: groups[min(range(k), key=lambda i: abs(v - c[i]))].append(v)
        c2 = [sum(g) / len(g) if g else c[i] for i, g in enumerate(groups)]
        if c2 == c: break
        c = c2
    c = sorted(c)
    return c, [min(range(k), key=lambda i: abs(v - c[i])) for v in vals]


def landmarks(points, groups):
    """Where a body's parts are, from its surface points (x across, y along with the head at -Y, z up). groups: the
    pairs of feet it stands on (0 for none), front to back. feet {(g, left)}, ys / xs each group's place along and
    out from the middle, the belly (the lowest point of the body between the legs), the top line over the first and
    last group, the nose and tail ends."""
    import statistics
    ps = list(points)
    top = max(p.z for p in ps)
    ylo, yhi = min(p.y for p in ps), max(p.y for p in ps)
    length = max(yhi - ylo, 1e-6)
    out = dict(nose=ylo, tail=yhi, top=top, groups=groups)
    if groups <= 0:
        out.update(front=ylo + length / 3, back=ylo + 2 * length / 3, ys=[], xs=[], feet={}, belly=0.0,
                   front_x=0.0, back_x=0.0, withers=top, croup=top)
        return out
    low = [p for p in ps if p.z < top * 0.06]
    centres, which = kmeans1([p.y for p in low], groups)
    feet, ys, xs = {}, [], []
    for g in range(len(centres)):
        mine = [p for p, w in zip(low, which) if w == g]
        for left in (True, False):
            sel = [p for p in mine if (p.x > 0) == left]
            if sel:
                feet[(g, left)] = Vector((statistics.median([p.x for p in sel]), statistics.median([p.y for p in sel]), 0.0))
        here = [feet[(g, s)] for s in (True, False) if (g, s) in feet]
        ys.append(statistics.mean([f.y for f in here]) if here else centres[g])
        xs.append(statistics.mean([abs(f.x) for f in here]) if here else 0.0)
    fy, by = ys[0], ys[-1]
    span = max(abs(by - fy), length * 0.25)
    mid = [p for p in ps if abs(p.y - (fy + by) / 2) < span * 0.15 and abs(p.x) < max(xs) * 0.6 and p.z > top * 0.15]
    belly = min(p.z for p in mid) if mid else top * 0.5
    def top_at(y):
        sel = [p for p in ps if abs(p.y - y) < span * 0.12]
        return max(p.z for p in sel) if sel else top
    out.update(front=fy, back=by, ys=ys, xs=xs, feet=feet, belly=belly, front_x=xs[0], back_x=xs[-1],
               withers=top_at(fy), croup=top_at(by))
    return out


def piecewise(x, xs, ys):
    """Linear through (xs, ys), straight on past the ends."""
    pairs = []
    for p in sorted(zip(xs, ys)):
        if not pairs or abs(p[0] - pairs[-1][0]) > 1e-6: pairs.append(p)
    if len(pairs) == 1: return pairs[0][1] + (x - pairs[0][0])
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x <= x1 or (x1, y1) == pairs[-1]:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pairs[-1][1]


def rig_points(arm):
    """The rig's own surface for its landmarks when it brings no sample mesh: its bones, sampled along each."""
    out = []
    for b in arm.data.bones:
        h, t = arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local
        for k in range(6): out.append(h.lerp(t, k / 5))
    return out


class Shape:
    """A body's surface read as cross-sections: the body slice by slice along its length, each leg level by level
    up from the ground. A joint is placed by where it sits inside its part (how far up, across, along), so the same
    joint lands in the same place inside a differently shaped body."""

    def __init__(self, points, marks, floor=None):
        self.p = list(points)
        self.m = marks
        self.length = max(marks["tail"] - marks["nose"], 1e-6)
        # below the floor a slice of the body is legs (a legless body has none)
        self.floor = marks["belly"] * 0.9 if floor is None else floor
        ys = marks.get("ys") or []
        # each leg group's band along the body: halfway to its neighbours
        self.bands = [((ys[i - 1] + ys[i]) / 2 if i else -1e9, (ys[i] + ys[i + 1]) / 2 if i + 1 < len(ys) else 1e9)
                      for i in range(len(ys))]

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

    def leg(self, g, left, z):
        """One leg at height z: its centre and half spread across (x) and along (y)."""
        if g >= len(self.bands): return None
        lx = self.m["xs"][g]
        y0, y1 = self.bands[g]
        sel = []
        for w in (0.02, 0.04, 0.08, 0.16):
            band = self.m["top"] * w
            sel = [q for q in self.p if abs(q.z - z) < band and y0 <= q.y < y1 and (q.x > 0) == left
                   and abs(q.x) > lx * 0.35]
            if len(sel) >= 6: break
        if not sel: return None
        cx, rx = self._spread([q.x for q in sel])
        cy, ry = self._spread([q.y for q in sel])
        return cx, cy, rx, ry


def up(b):
    out = []
    while b is not None: out.append(b); b = b.parent
    return out


def chains(arm, groups):
    """The rig's bones sorted into legs ({bone: (group, left)}, group 0 the front pair: each from the foot up to where
    it leaves the body) and its tail (the bones named tail, root end first). A foot is a bone with no children on
    the ground; a leg runs up from it while its parent carries nothing else but tips."""
    bones = arm.data.bones
    mw = arm.matrix_world
    top = max((mw @ b.tail_local).z for b in bones)
    feet = [b for b in bones if not b.children and (mw @ b.tail_local).z < top * 0.05 and (mw @ b.head_local).z < top * 0.08]
    leg = {}
    if groups > 0 and feet:
        centres, which = kmeans1([(mw @ f.head_local).y for f in feet], groups)
        for f, g in zip(feet, which):
            left = (mw @ f.head_local).x > 0
            b = f
            while b is not None and b.parent is not None:
                leg[b.name] = (g, left)
                others = [c for c in b.parent.children if c != b and c.children]
                if others: break
                b = b.parent
    tail = [b.name for b in bones if "tail" in b.name.lower() and b.name not in leg]
    return leg, tail


# ---------------------------------------------------------------- fitting: where every joint goes

def fit_shape(arm, R, A, rig_surface, animal_surface, family):
    """heads {bone: new head} and tails {bone: new tail} (tails only where they do not follow a child), plus what the
    fit found. legged: legs level by level inside each leg, the body slice by slice, the tail down the animal's own
    tail; body and spider: every joint slice by slice."""
    legless = family in ("body", "spider")
    floor = -1.0 if legless else None
    SR, SA = Shape(rig_surface, R, floor), Shape(animal_surface, A, floor)
    ys = ([R["nose"], R["front"], R["back"], R["tail"]], [A["nose"], A["front"], A["back"], A["tail"]])
    leg_of, tail = chains(arm, 0 if legless else R["groups"])
    zk = A["belly"] / max(R["belly"], 1e-6) if not legless else A["top"] / max(R["top"], 1e-6)

    def body(p):
        y2 = piecewise(p.y, *ys)
        tR, bR, hR = SR.slice(p.y)
        tA, bA, hA = SA.slice(y2)
        u = (p.z - bR) / max(tR - bR, 1e-3)
        return Vector((p.x / hR * hA, y2, bA + u * (tA - bA)))

    def in_leg(p, g, left):
        z2 = p.z * zk
        r, a = SR.leg(g, left, p.z), SA.leg(g, left, z2)
        if r is None or a is None:
            k = A["xs"][g] / max(R["xs"][g], 1e-6) if g < len(A["xs"]) and g < len(R["xs"]) else 1.0
            return Vector((p.x * k, piecewise(p.y, *ys), z2))
        ux = max(-1.2, min(1.2, (p.x - r[0]) / r[2]))
        uy = max(-1.2, min(1.2, (p.y - r[1]) / r[3]))
        return Vector((a[0] + ux * a[2], a[1] + uy * a[3], z2))

    def place(name, p):
        if p.z < R["top"] * 0.02 and name not in leg_of and not legless:     # the root, on the ground
            return Vector((0.0, piecewise(p.y, *ys), 0.0))
        if name in leg_of and p.z < R["belly"] and not legless:
            return in_leg(p, *leg_of[name])
        return body(p)

    # the animal's tail: the thin strand hanging off its back end, top to bottom
    tail_path = None
    if not legless:
        strand = [q for q in animal_surface if q.y > A["tail"] - SA.length * 0.07 and abs(q.x) < A["top"] * 0.06]
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
    tails = {}
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
        tails[tail[-1]] = along(1.0)
    found = dict(legs=len(set(leg_of.values())), leg_bones=len(leg_of), tail=len(tail), tail_hangs=tail_path is not None)
    return heads, tails, found, leg_of, tail, SA


def mesh_line(mesh, bins=40):
    """The body's own centre line, head end first: the two ends farthest apart along the surface, the vertices
    binned by their distance along it from one end, each bin's middle a point. A coiled snake's line follows the
    coils (straight slices across one would cut three coils at once). The head end is the thicker one."""
    import geo
    s = geo.Surface(mesh)
    co = s.co
    c = sum(co, Vector()) / len(co)
    seed = max(range(len(co)), key=lambda i: (co[i] - c).length)
    d0 = s.distances([seed])
    a = max(range(len(co)), key=lambda i: d0[i] if d0[i] < float("inf") else -1)
    da = s.distances([a])
    far = max(d for d in da if d < float("inf"))
    groups = [[] for _ in range(bins)]
    for i, d in enumerate(da):
        if d < float("inf"): groups[min(bins - 1, int(d / max(far, 1e-9) * bins))].append(co[i])
    pts, rad = [], []
    for g in groups:
        if not g: continue
        m = sum(g, Vector()) / len(g)
        pts.append(m); rad.append(sum((p - m).length for p in g) / len(g))
    # a little smoothing: a bin's middle wobbles where a fin or a jaw joins it
    sm = [pts[0]] + [(pts[i - 1] + pts[i] * 2 + pts[i + 1]) / 4 for i in range(1, len(pts) - 1)] + [pts[-1]]
    n = max(1, len(rad) // 6)
    if sum(rad[-n:]) > sum(rad[:n]):
        sm.reverse(); rad.reverse()
    return sm, rad


def polyline_param(line, p):
    """(u along the polyline 0..1, the closest point, the segment index) for a point."""
    cum = [0.0]
    for i in range(1, len(line)): cum.append(cum[-1] + (line[i] - line[i - 1]).length)
    best = (1e18, 0.0, line[0], 0)
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        d = b - a
        f = max(0.0, min(1.0, (p - a).dot(d) / max(d.length_squared, 1e-12)))
        q = a + d * f
        dist = (p - q).length
        if dist < best[0]: best = (dist, (cum[i] + d.length * f) / max(cum[-1], 1e-12), q, i)
    return best[1], best[2], best[3]


def polyline_at(line, u):
    """The point at share u of the polyline's length, and the direction there."""
    cum = [0.0]
    for i in range(1, len(line)): cum.append(cum[-1] + (line[i] - line[i - 1]).length)
    x = max(0.0, min(1.0, u)) * cum[-1]
    for i in range(len(line) - 1):
        if x <= cum[i + 1] or i == len(line) - 2:
            seg = max(cum[i + 1] - cum[i], 1e-12)
            f = (x - cum[i]) / seg
            return line[i].lerp(line[i + 1], f), (line[i + 1] - line[i]).normalized()
    return line[-1], (line[-1] - line[-2]).normalized()


def frame(d, hint=Vector((0, 0, 1))):
    """A turn whose Y is d and whose Z is as near hint as d allows (a body's back stays up as it bends)."""
    y = d.normalized()
    z = hint - y * hint.dot(y)
    if z.length < 1e-4:
        z = Vector((0, 1, 0)) - y * y.y
    z.normalize()
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def fit_line(arm, mesh, log):
    """heads, tails and rolls for a body with no legs to stand on (snake, shark): the template's line from its front
    tip to its back tip laid along the body's own centre line, every joint at the same share of the length; a joint
    off the line (a fin, the jaw) keeps its offset from it, turned with the line there (back kept up) and scaled by
    the length change. The mesh is first turned about Z so it heads the template's way, so nothing turns over."""
    mw = arm.matrix_world
    bones = arm.data.bones
    leaves = [b for b in bones if not b.children]
    front = min(leaves, key=lambda b: (mw @ b.tail_local).y)
    back = max(leaves, key=lambda b: (mw @ b.tail_local).y)
    fu, bu = up(front), up(back)
    common = next(b for b in fu if b in bu)
    # front tip to the fork walking each bone tail to head, then the fork down to the back tip head to tail
    walk = [(b, ("tail", "head")) for b in fu[:fu.index(common)]] +            [(b, ("head", "tail")) for b in reversed(bu[:bu.index(common)])]
    tl = [mw @ front.tail_local]
    for b, ends in walk:
        for e in ends:
            p = mw @ (b.tail_local if e == "tail" else b.head_local)
            if (p - tl[-1]).length > 1e-5: tl.append(p)
    ml, rad = mesh_line(mesh)
    flat = lambda v: Vector((v.x, v.y, 0.0))
    ht, hm = flat(tl[0] - tl[-1]), flat(ml[0] - ml[-1])
    yaw = 0.0
    if ht.length > 1e-6 and hm.length > 1e-6:
        yaw = math.atan2(ht.y, ht.x) - math.atan2(hm.y, hm.x)
        R = Matrix.Rotation(yaw, 4, 'Z')
        turn(mesh, yaw)
        ml = [R @ p for p in ml]
        co = [v.co for v in mesh.data.vertices]
        c = Vector(((min(p.x for p in co) + max(p.x for p in co)) / 2, (min(p.y for p in co) + max(p.y for p in co)) / 2, 0))
        mesh.data.transform(Matrix.Translation(-c)); mesh.data.update()
        ml = [p - c for p in ml]
    tlen = sum((tl[i] - tl[i - 1]).length for i in range(1, len(tl)))
    mlen = sum((ml[i] - ml[i - 1]).length for i in range(1, len(ml)))
    k = mlen / max(tlen, 1e-9)

    def turn_at(p):
        u, q, i = polyline_param(tl, p)
        d_t = (tl[min(i + 1, len(tl) - 1)] - tl[i])
        at, d_m = polyline_at(ml, u)
        return at, q, frame(d_m) @ frame(d_t).inverted()

    def place(p):
        at, q, r = turn_at(p)
        return at + r @ ((p - q) * k)

    heads = {b.name: place(mw @ b.head_local) for b in bones}
    tails = {b.name: place(mw @ b.tail_local) for b in bones}
    for b in bones:
        if b.parent is None:            # the root: on the ground under the middle of the body, not off the line
            mid, _ = polyline_at(ml, 0.5)
            heads[b.name] = Vector((mid.x, mid.y, 0.0))
            tails[b.name] = heads[b.name] + (mw.to_3x3() @ (b.tail_local - b.head_local)) * k
    rolls = {b.name: turn_at(mw @ b.head_local.lerp(b.tail_local, 0.5))[2] @ (mw.to_3x3() @ b.z_axis) for b in bones}
    log["line"] = {"template_m": round(tlen, 3), "mesh_m": round(mlen, 3), "points": len(ml),
                   "front": front.name, "back": back.name, "turned_deg": round(math.degrees(yaw), 1)}
    return heads, tails, rolls, k


def fit_human(arm, mesh, spec_h, log):
    """heads and tails for Mesh2Motion's human from the joints rerig_humanoid measures (cross-sections at the
    humanoid section's heights); the fingers follow the hand (turned and scaled with the wrist to the knuckles)."""
    import numpy as np
    import rerig, rerig_humanoid as RH
    co = np.array([v.co[:] for v in mesh.data.vertices])
    lo, hi = co.min(axis=0), co.max(axis=0)
    size = hi - lo
    isl = sorted(rerig.islands_of(mesh), key=len, reverse=True)
    main = np.zeros(len(co), bool)
    for idx in isl:
        if len(idx) >= 0.1 * len(isl[0]): main[idx] = True
    J = RH.measure(co, lo, size, spec_h, main)
    log["measured"] = {k: [round(c, 3) for c in v] for k, v in J.items()}
    want = {"pelvis": J["hip"], "spine_01": J["spine"], "spine_02": J["spine1"], "spine_03": J["spine2"],
            "neck_01": J["neck"], "head": J["head"], "head_leaf": J["top"], "root": Vector((J["hip"].x, J["hip"].y, 0.0))}
    for side, s in (("Left", "l"), ("Right", "r")):
        sh = J[side + "shoulder"]
        want["clavicle_" + s] = Vector((sh.x * 0.3, (J["spine2"].y + J["neck"].y) * 0.5,
                                        J["spine2"].z + (J["neck"].z - J["spine2"].z) * 0.7))
        want.update({"upperarm_" + s: sh, "lowerarm_" + s: J[side + "elbow"], "hand_" + s: J[side + "wrist"],
                     "thigh_" + s: J[side + "hip"], "calf_" + s: J[side + "knee"], "foot_" + s: J[side + "ankle"],
                     "ball_" + s: J[side + "ball"], "ball_leaf_" + s: J[side + "toe"]})
    mw = arm.matrix_world
    bones = arm.data.bones
    heads = {n: v for n, v in want.items() if n in bones}
    tails = {}
    # the hands: every bone under hand_<s> turned and scaled as the wrist-to-knuckle line is
    for side, s in (("Left", "l"), ("Right", "r")):
        hand = bones.get("hand_" + s)
        mid = bones.get("middle_01_" + s)
        if hand is None or mid is None: continue
        oh, om = mw @ hand.head_local, mw @ mid.head_local
        nh, nm = J[side + "wrist"], J[side + "knuckle"]
        q = (om - oh).rotation_difference(nm - nh)
        k = (nm - nh).length / max((om - oh).length, 1e-6)
        follow = lambda p: nh + q @ ((p - oh) * k)
        tails[hand.name] = follow(mw @ hand.tail_local)
        for b in hand.children_recursive:
            heads[b.name] = follow(mw @ b.head_local)
            tails[b.name] = follow(mw @ b.tail_local)
    s = (J["top"].z) / max(max((mw @ b.tail_local).z for b in bones), 1e-6)
    for b in bones:                                     # anything not measured keeps its place, scaled
        if b.name not in heads: heads[b.name] = (mw @ b.head_local) * s
    return heads, tails, s, J


def write_bones(arm, heads, tails, scale, tol, rolls=None):
    """Moves every bone to its new head; its tail is the head of a child that started where it ended, else the new
    tail given, else its old vector scaled. Each bone's frame is turned the least way onto its new direction, so the
    clips (rotations in those frames) bend the same joints the same way."""
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    mw = arm.matrix_world
    inv = mw.inverted()
    plan = {}
    for b in arm.data.edit_bones:
        oh, ot = mw @ b.head, mw @ b.tail
        nh = heads[b.name]
        nxt = [c for c in b.children if ((mw @ c.head) - ot).length < tol]
        if nxt: nt = heads[nxt[0].name]
        elif b.name in tails: nt = tails[b.name]
        else: nt = nh + (ot - oh) * scale
        if (nt - nh).length < 1e-4: nt = nh + (ot - oh) * scale
        q = (ot - oh).rotation_difference(nt - nh)
        z = rolls[b.name] if rolls and b.name in rolls else q @ (mw.to_3x3() @ b.z_axis)
        plan[b.name] = (inv @ nh, inv @ nt, z)
    for b in arm.data.edit_bones:
        b.use_connect = False
    for b in arm.data.edit_bones:
        h, t, z = plan[b.name]
        b.head, b.tail = h, t
        b.align_roll(inv.to_3x3() @ z)
    bpy.ops.object.mode_set(mode='OBJECT')


def read_joints(path):
    """{bone: (head, tail or None)} in Blender's axes, from a file or the JSON itself, in any of the shapes that
    carry them: rig.json ({"m2m": {"joints": ...}}), the API's and the bridge's {"template", "joints"}, or the joints
    alone. Joints are {bone: [x, y, z]} (the head; what rig.json and the bridge use) or {bone: {"head", "tail"}},
    optionally under "bones"; glTF's axes (Y up, facing +Z) unless "axes" says "blender"."""
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else json.loads(path)
    axes = "gltf"
    for key in ("m2m", "joints"):
        if isinstance(data.get(key), dict):
            if isinstance(data.get("axes"), str): axes = data["axes"]
            data = data[key]
    if isinstance(data.get("joints"), dict): data = data["joints"]
    if isinstance(data.get("axes"), str): axes = data["axes"]
    bones = data.get("bones", data)
    conv = T.from_gltf if axes == "gltf" else (lambda p: [float(c) for c in p])
    out = {}
    for n, v in bones.items():
        if n == "axes" or v is None: continue
        if isinstance(v, dict):
            out[n] = (Vector(conv(v["head"])), Vector(conv(v["tail"])) if v.get("tail") else None)
        else:
            out[n] = (Vector(conv(v)), None)
    return out


# ---------------------------------------------------------------- clips

def fcurves(act):
    """An action's curves: on the action itself before Blender 4.4, in its layers' channel bags after."""
    if hasattr(act, "fcurves"): return list(act.fcurves)
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags: out.extend(bag.fcurves)
    return out


def drop_fcurve(act, fc):
    if hasattr(act, "fcurves"):
        act.fcurves.remove(fc); return
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                if any(f == fc for f in bag.fcurves):
                    bag.fcurves.remove(fc); return


def bone_of(path):
    return path.split('"')[1] if path.startswith('pose.bones["') else None


def prune(actions, tracking, root):
    """Mesh2Motion plays rotations only, plus the tracking bone's position (and the root's on a root-motion clip):
    every other position and scale track goes. They are the template's own bone offsets, which would pull the
    fitted joints back to where the template had them."""
    dropped = 0
    for act in actions:
        keep_root = T.is_root_motion(act.name)
        for fc in fcurves(act):
            b = bone_of(fc.data_path)
            if b is None: continue
            if fc.data_path.endswith(".scale") or (fc.data_path.endswith(".location") and b != tracking
                                                   and not (keep_root and b == root)):
                drop_fcurve(act, fc); dropped += 1
    return dropped


def scale_travel(actions, k):
    """The clips' root and hip movement scaled by how the body's legs differ from the rig's."""
    for act in actions:
        for fc in fcurves(act):
            if not fc.data_path.endswith("location"): continue
            for p in fc.keyframe_points:
                p.co.y *= k; p.handle_left.y *= k; p.handle_right.y *= k


def damp(actions, names, k):
    """The swing of these bones scaled by k (each key's turn from the rest pose eased toward none): a heavy animal on
    a light one's clips lifts its knees less."""
    for act in actions:
        by_bone = {}
        for fc in fcurves(act):
            if not fc.data_path.endswith("rotation_quaternion"): continue
            n = bone_of(fc.data_path)
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


def assign(o, act):
    ad = o.animation_data or o.animation_data_create()
    ad.action = act
    if hasattr(ad, "action_slot") and getattr(act, "slots", None):
        ad.action_slot = act.slots[0]


def frames_of(act):
    f0, f1 = act.frame_range
    n = max(2, int(round(f1 - f0)) + 1)
    return [f0 + (f1 - f0) * i / (n - 1) for i in range(n)]


def at(f):
    bpy.context.scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))


def tip(mat, length):
    return (mat @ Matrix.Translation((0, length, 0))).to_translation()


def match_world(arm, src, actions, limb, feet, k, root, calibrate=False):
    """Rebakes the limbs so each limb bone points the way the template's own does, frame by frame, and stands the
    feet as the template's stand (scaled by k, the leg-length change), with the clips' travel scaled by k too.
    A clip is turns from the rest pose; a cow's legs stand straight where the horse's rest pose is angled, a T-posed
    person's arms where the template's hang, so the same turns would leave every cow leg bent all the time. Pointing
    the bones the same way instead gives the body the template's limb postures on its own limb lengths. The body,
    neck, head and tail keep the clip's own turns from their own rest (a cow's neck isn't a horse's). The feet: each
    frame the lowest foot is put as high above the ground as the template's lowest foot is, times k, so a jump
    still leaves the ground and a stand stays on it."""
    pb, sb = arm.pose.bones, src.pose.bones
    order = [b.name for b in arm.data.bones if b.name in limb]          # bones come parents first
    # calibrate (four-legged bodies): each bone keeps its own rest where the template stands at its rest, and takes
    # the template's turns from there. Pointing the bones exactly as the template's do bent a fitted cow's legs by
    # the difference on every frame, 35-50 degrees at the ankles even in Idle, and tore the skin (Cow_Holstein,
    # 2026-10-18). corr maps the template bone's rest frame onto this bone's, both in armature space.
    corr = {}
    if calibrate:
        for n in order:
            if n in src.data.bones:
                corr[n] = arm.data.bones[n].matrix_local.to_3x3() @ src.data.bones[n].matrix_local.to_3x3().inverted()
    tops = {pb[n].parent.name for n in order if pb[n].parent and pb[n].parent.name not in limb}
    feet = [n for n in feet if n in pb]
    for act in actions:
        frames = frames_of(act)
        assign(src, act)
        templ = []
        for f in frames:
            at(f)
            low = min((tip(sb[n].matrix, src.data.bones[n].length).z for n in feet), default=0.0)
            templ.append(({n: sb[n].matrix.copy() for n in order}, low))
        src.animation_data.action = None
        scale_travel([act], k)
        assign(arm, act)
        seen = []
        for f in frames:
            at(f)
            seen.append(({n: pb[n].matrix.copy() for n in tops}, pb[root].location.copy()))
        # the limbs' own turns go; new ones are keyed below
        for fc in list(fcurves(act)):
            b = bone_of(fc.data_path)
            if b in limb and fc.data_path.endswith(("rotation_quaternion", "rotation_euler", "location")):
                drop_fcurve(act, fc)
        for f, (want, low_t), (parents, root_loc) in zip(frames, templ, seen):
            mats = dict(parents)
            for n in order:
                b = arm.data.bones[n]
                par = mats[b.parent.name]
                rest = b.parent.matrix_local.inverted() @ b.matrix_local
                base = par @ rest                                  # where the bone would be with no turn of its own
                aim = corr[n] @ want[n].to_3x3() if n in corr else want[n].to_3x3()
                m = Matrix.Translation(base.to_translation()) @ aim.to_4x4()
                basis = base.inverted() @ m
                mats[n] = base @ basis
                pb[n].rotation_mode = 'QUATERNION'
                pb[n].rotation_quaternion = basis.to_quaternion()
                pb[n].keyframe_insert("rotation_quaternion", frame=f)
            if feet:
                low = min(tip(mats[n], arm.data.bones[n].length).z for n in feet if n in mats)
                rb = arm.data.bones[root]
                local = rb.matrix_local.to_3x3().inverted() @ Vector((0, 0, low_t * k - low))
                pb[root].location = root_loc + local
                pb[root].keyframe_insert("location", frame=f)
        arm.animation_data.action = None


def load_template(t, root, wanted, log):
    """The template's armature (scale and rotation applied), its sample mesh's surface, and the wanted clips'
    actions {M2M name: action}, from the template's animation files (only those holding a wanted clip)."""
    rig_file, anim_files = T.files(t, root)
    listing = T.clips(t, root)
    where = {c["name"]: c["file"] for c in listing}
    need = [f for f in anim_files if os.path.basename(f) == os.path.basename(anim_files[0])
            or any(where.get(w) == os.path.basename(f) for w in wanted)]
    arm, surface, acts = None, [], {}
    for i, f in enumerate(need):
        before = set(bpy.data.objects)
        before_acts = set(bpy.data.actions)
        bpy.ops.import_scene.gltf(filepath=f)
        new = [o for o in bpy.data.objects if o not in before]
        for a in [a for a in bpy.data.actions if a not in before_acts]:
            base = re.sub(r"\.\d{3}$", "", a.name)
            if base in wanted and base not in acts:
                a.name = base
                a.use_fake_user = True
                acts[base] = a
            else:
                bpy.data.actions.remove(a)
        if i == 0:
            arm = next(o for o in new if o.type == 'ARMATURE')
            sample = real_meshes(new)
            surface = [o.matrix_world @ v.co for o in sample for v in o.data.vertices]
            for o in new:
                if o is not arm: bpy.data.objects.remove(o)
        else:
            for o in new: bpy.data.objects.remove(o)
    arm.animation_data_clear()
    bpy.ops.object.select_all(action='DESELECT')
    arm.select_set(True); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    log["template_files"] = [os.path.basename(f) for f in need]
    missing = [w for w in wanted if w not in acts]
    if missing: log["clips_missing"] = missing
    return arm, surface or rig_points(arm), acts


# ---------------------------------------------------------------- skinning

LEAF = re.compile(r"(^|[_.])(leaf|tip)([_.]|$)", re.I)
# bones buried in an animal's body (a shoulder blade, a pelvis bone, a collar, the ribcage helpers): they turn the
# legs hung from them, but skin of their own tore 20-30% gaps across a cow's flank in the audit's bends
GIRDLE = re.compile(r"scapula|pelvis|collar|anchor|ribcage|leg_shoulder", re.I)


def gate(mesh, arm, reach=1.8):
    """Each vertex keeps weight only from its nearest bone, the bones joined to that one, and bones nearly as near
    (within reach x the nearest distance). A voxel copy closes gaps a few voxels wide, so bone heat crosses them: a
    cow's mouth owned skin on its front foot, the tail the hocks it hangs beside. What is cut goes to what stays."""
    import numpy as np
    names = [b.name for b in arm.data.bones if b.use_deform and b.name in mesh.vertex_groups]
    if not names: return 0
    bi = {n: i for i, n in enumerate(names)}
    H = np.array([arm.data.bones[n].head_local[:] for n in names]); Tl = np.array([arm.data.bones[n].tail_local[:] for n in names])
    near = np.eye(len(names), dtype=bool)
    for n in names:
        b = arm.data.bones[n]
        for o in ([b.parent] if b.parent else []) + list(b.children):
            if o.name in bi: near[bi[n], bi[o.name]] = near[bi[o.name], bi[n]] = True
    me = mesh.data
    n = len(me.vertices)
    P = np.empty(n * 3); me.vertices.foreach_get("co", P); P = P.reshape(n, 3)
    D = np.empty((n, len(names)))
    for j in range(len(names)):
        d = Tl[j] - H[j]
        f = np.clip(((P - H[j]) @ d) / max(float(d @ d), 1e-12), 0.0, 1.0)
        D[:, j] = np.linalg.norm(P - (H[j] + f[:, None] * d), axis=1)
    best = D.argmin(1)
    dmin = D[np.arange(n), best]
    keep = near[best] | (D <= dmin[:, None] * reach + 1e-6)
    gi = {mesh.vertex_groups[nm].index: bi[nm] for nm in names}
    cut = 0
    for v in me.vertices:
        drop = [g for g in v.groups if g.group in gi and not keep[v.index, gi[g.group]] and g.weight > 1e-4]
        if not drop: continue
        cut += 1
        if sum(g.weight for g in v.groups if g.group in gi and keep[v.index, gi[g.group]]) < 1e-3:
            mesh.vertex_groups[names[best[v.index]]].add([v.index], 1.0, 'REPLACE')
        for g in drop: g.weight = 0.0
    return cut


def fill_bare(mesh):
    """Vertices bone heat left with no weight take their nearest weighted neighbour's."""
    from mathutils import kdtree
    vs = mesh.data.vertices
    good = [v for v in vs if any(g.weight > 1e-4 for g in v.groups)]
    bare = [v.index for v in vs if not any(g.weight > 1e-4 for g in v.groups)]
    if not good or not bare: return 0
    kd = kdtree.KDTree(len(good))
    for i, v in enumerate(good): kd.insert(v.co, i)
    kd.balance()
    for vi in bare:
        _, gi, _ = kd.find(vs[vi].co)
        for g in good[gi].groups:
            if g.weight > 1e-4: mesh.vertex_groups[g.group].add([vi], g.weight, 'REPLACE')
    return len(bare)


def skin(mesh, arm, voxel, after_transfer=None, girdles=False, smooth=0):
    """Bone heat on a watertight voxel copy, carried back to the mesh by nearest face. The root, the leaf and tip
    bones at the ends of chains and (girdles) the girdle bones carry no skin; smooth: passes of weight smoothing
    after the transfer."""
    off = []
    for b in arm.data.bones:
        if b.parent is None or (not b.children and LEAF.search(b.name)) or (girdles and GIRDLE.search(b.name)):
            if b.use_deform: off.append(b.name)
            b.use_deform = False
    def heat(obj):
        obj.vertex_groups.clear()
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True); arm.select_set(True); bpy.context.view_layer.objects.active = arm
        bpy.ops.object.parent_set(type='ARMATURE_AUTO')
        obj.parent = None
        for m in [m for m in obj.modifiers if m.type == 'ARMATURE']: obj.modifiers.remove(m)
        return sum(1 for v in obj.data.vertices if not v.groups)
    # bone heat's solver gives up on a fine voxel copy of some meshes (every vertex unreached): coarser copies,
    # then the mesh itself, until one is reached nearly everywhere
    unreached, used = None, None
    for vx in (voxel, voxel * 1.7, voxel * 2.8):
        proxy = mesh.copy(); proxy.data = mesh.data.copy(); proxy.name = mesh.name + "_proxy"
        bpy.context.scene.collection.objects.link(proxy)
        proxy.vertex_groups.clear()
        rem = proxy.modifiers.new("remesh", 'REMESH'); rem.mode = 'VOXEL'; rem.voxel_size = vx
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = proxy
        bpy.ops.object.modifier_apply(modifier=rem.name)
        unreached = heat(proxy)
        if unreached <= len(proxy.data.vertices) * 0.05:
            used = round(vx, 4); break
        bpy.data.objects.remove(proxy); proxy = None
    for b in arm.data.bones:
        if b.use_deform and b.name not in mesh.vertex_groups: mesh.vertex_groups.new(name=b.name)
    if proxy is not None:
        dt = mesh.modifiers.new("weights", 'DATA_TRANSFER')
        dt.object = proxy; dt.use_vert_data = True; dt.data_types_verts = {'VGROUP_WEIGHTS'}
        dt.vert_mapping = 'POLYINTERP_NEAREST'; dt.layers_vgroup_select_src = 'ALL'; dt.layers_vgroup_select_dst = 'NAME'
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = mesh
        mesh.select_set(True)
        bpy.ops.object.modifier_apply(modifier=dt.name)
        bpy.data.objects.remove(proxy)
    else:
        unreached = heat(mesh); used = "mesh"
        for b in arm.data.bones:
            if b.use_deform and b.name not in mesh.vertex_groups: mesh.vertex_groups.new(name=b.name)
    fill_bare(mesh)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    gated = gate(mesh, arm)
    if smooth:
        import rerig
        rerig.smooth_weights(mesh, int(smooth))
    if after_transfer: after_transfer()
    # at most four influences, normalised, as an engine takes them
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    mesh.parent = arm
    mod = mesh.modifiers.new("Armature", 'ARMATURE'); mod.object = arm
    bare = sum(1 for v in mesh.data.vertices if not v.groups)
    return unreached, bare, off, gated, used


def side_rule(mesh, arm):
    """Skin on one side of the body is never moved by the other side's bones. Bone heat on a voxel copy runs across
    where two legs nearly touch (a cow's front legs, her hocks), so some of a left leg's skin followed the right leg
    and a walk pulled sheets of skin between them (Cow_Holstein: 572 leg weights on the wrong side). A weight from a
    _l bone on a vertex well to the right goes to the matching _r bone, and the other way round; a vertex near the
    midline (chest, belly, tail) keeps both sides. Returns the vertices changed."""
    pair = {}
    for b in arm.data.bones:
        m = re.match(r"^(.*)_([lrLR])$", b.name)           # horse and dragon say _l, fox and human _L
        if m and b.name in mesh.vertex_groups:
            side = m.group(2)
            other = m.group(1) + "_" + {"l": "r", "r": "l", "L": "R", "R": "L"}[side]
            if other in mesh.vertex_groups: pair[b.name] = (side.lower(), other)
    if not pair: return 0
    xs = {"l": [], "r": []}
    for b in arm.data.bones:
        if b.name in pair: xs[pair[b.name][0]].append(b.head_local.x)
    if not xs["l"] or not xs["r"]: return 0
    lx, rx = sum(xs["l"]) / len(xs["l"]), sum(xs["r"]) / len(xs["r"])
    if abs(lx - rx) < 1e-4: return 0
    mid, margin = (lx + rx) / 2, abs(lx - rx) * 0.15      # inside the margin a vertex is on the midline
    left_sign = 1 if lx > rx else -1
    index = {mesh.vertex_groups[n].index: n for n in pair}
    changed = 0
    for v in mesh.data.vertices:
        off = (v.co.x - mid) * left_sign
        if abs(off) < margin: continue
        side = "l" if off > 0 else "r"
        moves = [(g.group, g.weight) for g in v.groups if g.group in index and pair[index[g.group]][0] != side and g.weight > 0]
        for gi, w in moves:
            name = index[gi]
            mesh.vertex_groups[name].remove([v.index])
            mesh.vertex_groups[pair[name][1]].add([v.index], w, 'ADD')
        if moves: changed += 1
    return changed


def tail_rule(mesh, arm):
    """The tail moves only the tail. A cow's tail hangs against the backs of her hind legs, and bone heat on a voxel
    copy runs across where they touch, so the tail bones took the rump and the backs of the legs down to the hock
    (Cow_Holstein: 512 vertices over 20 cm from the tail): every swish dragged that skin into a sheet. Tail weight on
    a vertex farther from the tail than the tail is thick goes to the nearest other bone. Returns the vertices
    changed."""
    tail = [b for b in arm.data.bones if b.name.lower().startswith("tail") and b.name in mesh.vertex_groups]
    others = [b for b in arm.data.bones if b.use_deform and b.name in mesh.vertex_groups and b not in tail]
    if not tail or not others: return 0
    idx = {mesh.vertex_groups[b.name].index for b in tail}

    def seg_dist(co, b):
        h, t = b.head_local, b.tail_local
        d = t - h
        f = max(0.0, min(1.0, (co - h).dot(d) / max(d.length_squared, 1e-12)))
        return (co - (h + d * f)).length

    def near_tail(co): return min(seg_dist(co, b) for b in tail)
    # how thick the tail is: the skin the tail owns most of, measured from its bones
    own = sorted(near_tail(v.co) for v in mesh.data.vertices
                 if v.groups and max(v.groups, key=lambda g: g.weight).group in idx)
    if not own: return 0
    reach = max(own[len(own) // 2] * 2.5, 0.02)
    changed = 0
    for v in mesh.data.vertices:
        gs = [g for g in v.groups if g.group in idx and g.weight > 0]
        if not gs or near_tail(v.co) <= reach: continue
        for g in gs: g.weight = 0.0
        if sum(g.weight for g in v.groups) < 0.05:
            best = min(others, key=lambda b: seg_dist(v.co, b))
            mesh.vertex_groups[best.name].add([v.index], 1.0, 'REPLACE')
        changed += 1
    return changed


def unweld(mesh, arm, actions, top, grow=0.04, ratio=4.0, samples=8):
    """Cuts the welds a generator leaves where two parts touch. A Tripo cow's tail tip is fused to her hock and the
    insides of her front legs to her chest, so a few tiny faces join skin that moves apart: whatever the weights,
    every step pulls them into a sheet (Cow_Holstein: edges of 1-2 cm stretched to 33 cm). Every clip is played on
    the skinned mesh and a face is removed when one of its edges grows by more than grow x height and to more than
    ratio x its length: skin round a bending joint stretches by half, a weld by ten or twenty times. What is left
    is a small hole where the parts touched. Returns the faces removed."""
    import bmesh
    if not actions or not mesh.data.edges: return 0
    me = mesh.data
    ad = arm.animation_data or arm.animation_data_create()
    was = ad.action
    def where():
        dg = bpy.context.evaluated_depsgraph_get()
        e = mesh.evaluated_get(dg)
        if len(e.data.vertices) != len(me.vertices): return None      # something else changed the topology
        return [e.matrix_world @ v.co for v in e.data.vertices]
    edges = [tuple(ed.vertices) for ed in me.edges]
    arm.data.pose_position = 'REST'
    rest = where()
    arm.data.pose_position = 'POSE'
    if rest is None: return 0
    base = [(rest[a] - rest[b]).length for a, b in edges]
    worst = [0.0] * len(edges)
    for act in actions:
        assign(arm, act)
        fr = frames_of(act)
        for i in range(samples):
            at(fr[int(i * (len(fr) - 1) / max(1, samples - 1))])
            now = where()
            if now is None: continue
            for k, (a, b) in enumerate(edges):
                d = (now[a] - now[b]).length
                if d > worst[k]: worst[k] = d
    ad.action = was
    for pb in arm.pose.bones: pb.matrix_basis.identity()
    bpy.context.view_layer.update()
    torn = {k for k in range(len(edges)) if worst[k] - base[k] > top * grow and worst[k] > base[k] * ratio}
    if not torn: return 0
    bm = bmesh.new(); bm.from_mesh(me); bm.edges.ensure_lookup_table()
    before = {f: len(piece) for piece in islands(bm.faces) for f in piece}     # face -> size of its part
    faces = {f for k in torn for f in bm.edges[k].link_faces}
    # a cut can leave a scrap of the weld hanging loose from it: a piece under 2% of the part it came off goes too
    rest_faces = [f for f in bm.faces if f not in faces]
    for piece in islands(rest_faces):
        whole = before[next(iter(piece))]
        if len(piece) < 0.02 * whole: faces |= piece
    bmesh.ops.delete(bm, geom=list(faces), context='FACES')
    bm.to_mesh(me); me.update(); bm.free()
    return len(faces)


def islands(faces):
    """The faces in connected pieces (joined across shared edges), as a list of sets."""
    faces = set(faces)
    seen, pieces = set(), []
    for f in faces:
        if f in seen: continue
        stack, piece = [f], set()
        seen.add(f)
        while stack:
            g = stack.pop(); piece.add(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h in faces and h not in seen: seen.add(h); stack.append(h)
        pieces.append(piece)
    return pieces


def confine(mesh, arm, leg_of, shape, belly):
    """Below the belly a leg bone only moves the skin of its own leg: its weight fades out past the leg's outline (so
    an udder or a belly between the legs isn't dragged by them), and skin left with no weight goes to the nearest
    bone of the body."""
    groups = {mesh.vertex_groups[n].index: leg_of[n] for n in leg_of if n in mesh.vertex_groups}
    body = [b for b in arm.data.bones if b.use_deform and b.name not in leg_of and b.name in mesh.vertex_groups]
    if not groups or not body: return 0
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
            grp, left = groups[g.group]
            sec = section((grp, left, int(v.co.z / step)))
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


def repose(arm, mesh, rest3):
    """Poses every bone the way the template's rest has it (its frame, so its direction and roll) and makes that the
    rest pose, mesh and all, standing on z=0 over the origin. For bodies whose clips are shapes rather than turns
    from where the model happens to be: a snake modelled rising in an S would side-wind as an S thrashing about,
    where straightened along the template's line its clips play as made. The weights were made on the body as
    modelled, before it is straightened."""
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    for b in arm.data.bones:                                   # parents first
        if b.name not in rest3: continue
        pb = arm.pose.bones[b.name]
        bpy.context.view_layer.update()
        pb.matrix = Matrix.Translation(pb.matrix.to_translation()) @ rest3[b.name].to_4x4()
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    mod = next(m for m in mesh.modifiers if m.type == 'ARMATURE')
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    co = [v.co for v in mesh.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)]); hi = Vector([max(c[i] for c in co) for i in range(3)])
    off = Matrix.Translation(Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z)))
    mesh.data.transform(off); mesh.data.update()
    arm.data.transform(off)
    m2 = mesh.modifiers.new("Armature", 'ARMATURE'); m2.object = arm
    return [round(x, 3) for x in off.to_translation()]


# ---------------------------------------------------------------- writing it out

def rename_bones(arm, mesh, actions, names):
    """Bones renamed (vertex groups and every clip's curves with them)."""
    old = {}
    for b in arm.data.bones:
        if b.name in names: old[b.name] = names[b.name]
    for b in list(arm.data.bones):
        if b.name in old: b.name = old[b.name]
    for g in mesh.vertex_groups:
        if g.name in old: g.name = old[g.name]
    for act in actions:
        for fc in fcurves(act):
            n = bone_of(fc.data_path)
            if n in old: fc.data_path = fc.data_path.replace('"%s"' % n, '"%s"' % old[n], 1)
    return len(old)


def export_fbx(path, mesh, arm):
    bpy.ops.object.select_all(action='DESELECT')
    mesh.select_set(True); arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL',
                             axis_forward='-Z', axis_up='Y', bake_space_transform=False, add_leaf_bones=False,
                             use_armature_deform_only=False, bake_anim=True, bake_anim_use_all_actions=False,
                             bake_anim_use_nla_strips=True, bake_anim_force_startend_keying=True, path_mode='COPY',
                             embed_textures=True)


def export_glb(path, mesh, arm):
    bpy.ops.object.select_all(action='DESELECT')
    mesh.select_set(True); arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    kw = dict(filepath=path, export_format='GLB', use_selection=True, export_animations=True, export_skins=True,
              export_yup=True)
    try:
        bpy.ops.export_scene.gltf(export_animation_mode='NLA_TRACKS', **kw)
    except TypeError:
        bpy.ops.export_scene.gltf(export_nla_strips=True, **kw)


def blender_run(script, *argv, timeout=1800):
    """Another step in its own Blender (the audit and the preview read the files back as an engine would)."""
    cmd = [bpy.app.binary_path, "-b", "--factory-startup", "--python", os.path.join(HERE, script), "--"] + list(argv)
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)


def audit(fbx, slug, out):
    r = blender_run("audit.py", "-fbx", fbx, "-slug", slug, "-out", out, "-render", "0")
    path = os.path.join(out, slug + ".json")
    if not os.path.exists(path):
        return {"error": ((r.stdout or "") + (r.stderr or ""))[-1500:]}
    v = json.load(open(path, encoding="utf-8")).get("verdict") or {}
    import grades
    return {"grade": grades.grade_of(v), "pass": v.get("pass"),
            "checks": {k: c.get("value") for k, c in (v.get("checks") or {}).items()}, "report": path}


def merge_json(path, update):
    cur = {}
    if os.path.exists(path):
        try: cur = json.load(open(path, encoding="utf-8"))
        except ValueError: cur = {}
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict) and k != "m2m":
            cur[k] = dict(cur[k], **v)
        else:
            cur[k] = v
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cur, fh, indent=2)
    return cur


# ---------------------------------------------------------------- the step

def model_paths(a, log):
    """The source, name, output folder and the model's own folder (None without --model)."""
    folder = None
    if a.model:
        import layout
        folder = layout.pack_dir(a.model)
        card = {}
        try: card = json.load(open(os.path.join(folder, "model.json"), encoding="utf-8"))
        except (OSError, ValueError): pass
        src = a.src or (os.path.join(folder, card["model_file"]) if card.get("model_file") else layout.raw_source(a.model))
        a.src = src
        a.name = a.name or card.get("game_name") or layout.leaf(a.model)
        a.out = a.out or layout.rigged_dir(a.model)
        if not a.height and card.get("metres_high"): a.height = float(card["metres_high"])
    if not a.src or not os.path.exists(a.src):
        raise SystemExit("m2m_rig: no source mesh (%s)" % a.src)
    a.name = a.name or os.path.splitext(os.path.basename(a.src))[0]
    a.out = os.path.abspath(a.out or os.path.join(os.path.dirname(os.path.abspath(a.src)), "rigged"))
    log["source"] = a.src
    return folder


def run(a):
    t0 = time.time()
    log = {"name": None}
    folder = model_paths(a, log)
    log["name"] = a.name
    root = T.m2m_root(a.m2m or None)
    if root is None: raise SystemExit("m2m_rig: Mesh2Motion's rigs and animations not found (--m2m or AUTORIG_M2M)")
    log["m2m"] = root
    mesh, src_joints = source_mesh(a.src, a.name, a.triangles)
    t = a.template.lower()
    if t != "auto" and t not in T.TEMPLATES: raise SystemExit("m2m_rig: no template %r" % a.template)

    # what it is, and which way it faces
    if t == "auto":
        log["facing"] = face_minus_y(mesh, a.facing, "anatomy", src_joints)
        top = stand(mesh, a.height)
        arch = a.archetype
        if not arch and a.model:
            try:
                import spec_store
                arch = (spec_store.SPECS.get(a.model) or {}).get("skeleton") or ""
            except Exception:
                arch = ""
        plan = None
        if not arch: arch, plan = suggest_archetype(mesh, a.name, log)
        pts = [v.co for v in mesh.data.vertices]
        meas = dict(body_measures(pts, top), tail=round(tail_share(pts, top), 3))
        if plan and (plan.get("feet") or {}).get("count") is not None: meas["feet"] = plan["feet"]["count"]
        if plan and (plan.get("wings") or {}).get("both"): meas["wings"] = True
        if a.height: meas["metres"] = round(top, 3)     # a real height only: Tripo's come out ~1 m whatever they are
        r = tail_reach(pts)
        if r is not None: meas["tail_reach"] = r
        t, why = T.choose(arch, meas)
        log["choice"] = {"archetype": arch, "measures": meas, "why": why}
        if t is None:
            print("M2M_RIG " + json.dumps({"name": a.name, "template": None, "choice": log["choice"], "suggest": log.get("suggest"),
                                           "fallback": "rerig.py + make_clips.py"}))
            return 3
        fam = T.TEMPLATES[t]["family"]
        if fam in ("legged", "body") and t not in ("bird", "dragon"):
            # an animal: its long axis and the taller end (what m2m_quadruped found reliable on Tripo's animals)
            ext = [max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(2)]
            if a.facing == "auto" and ext[0] > ext[1] * 1.15:
                log["facing"] = face_minus_y(mesh, "auto", "long"); top = stand(mesh, a.height)
            elif a.facing == "auto" and head_end([v.co for v in mesh.data.vertices], 1) > 0:
                turn(mesh, math.pi); top = stand(mesh, 0); log["facing"] += " (turned: the head end is taller)"
    else:
        fam = T.TEMPLATES[t]["family"]
        how = {"human": "anatomy", "spider": "anatomy"}.get(fam, "flip" if t in ("bird", "dragon") else "long")
        log["facing"] = face_minus_y(mesh, a.facing, how, src_joints)
        top = stand(mesh, a.height)
        log["choice"] = {"why": "--template %s" % t}
    log["template"] = t
    log["family"] = fam
    log["height"] = round(top, 3)
    if a.choose_only:                       # what auto would pick, and why, without rigging anything
        print("M2M_RIG " + json.dumps({"name": a.name, "template": t, "choice": log["choice"], "suggest": log.get("suggest"),
                                       "facing": log.get("facing"), "height": log["height"]}))
        return 0
    info = T.TEMPLATES[t]
    method = a.skin or info["skin"]
    if method not in T.SKIN_METHODS: raise SystemExit("m2m_rig: skinning %r is not available yet" % method)
    log["skin"] = method

    # the template and its clips
    names = [c["name"] for c in T.clips(t, root)]
    wanted = T.resolve_clips(t, a.clips, names)
    arm, rig_surface, acts = load_template(t, root, [w for w, _ in wanted], log)
    actions = [acts[w] for w, _ in wanted if w in acts]
    bones = arm.data.bones
    rtop = max((b.tail_local.z for b in bones), default=1.0)
    rig0 = arm.copy(); rig0.data = arm.data.copy(); rig0.name = "template_as_made"
    bpy.context.scene.collection.objects.link(rig0)
    root_bone = next(b.name for b in bones if b.parent is None)
    tracking = info["tracking"] if info["tracking"] in bones else root_bone
    animal = [v.co.copy() for v in mesh.data.vertices]

    # the fit
    feet_groups = 0 if fam in ("body", "spider") else max(1, len([b for b in bones if not b.children and b.tail_local.z < rtop * 0.05
                                                                 and b.head_local.z < rtop * 0.08]) // 2)
    R = landmarks(rig_surface, feet_groups)
    A = landmarks(animal, feet_groups)
    limb, tail_names, shape = {}, [], Shape(animal, A, -1.0 if fam in ("body", "spider") else None)
    if fam == "human":
        try:
            import spec_store
            spec_h = spec_store.HUMANOIDS.get(a.model) if a.model else None
        except Exception:
            spec_h = None
        import rerig_humanoid
        heads, tails, scale, J = fit_human(arm, mesh, spec_h or rerig_humanoid.DEFAULT_HUMANOID, log)
        leg_of, _ = chains(arm, 1)
        k = J["hip"].z / max(bones[tracking].head_local.z, 1e-6) if tracking in bones else scale
        found = {"legs": 1, "leg_bones": len(leg_of)}
    elif fam == "body":
        heads, tails, rolls, k = fit_line(arm, mesh, log)
        scale, leg_of, found = k, {}, {"line": True}
        animal = [v.co.copy() for v in mesh.data.vertices]
    else:
        heads, tails, found, leg_of, tail_names, shape = fit_shape(arm, R, A, rig_surface, animal, fam)
        scale = A["top"] / max(R["top"], 1e-6)
        if fam == "legged": k = A["belly"] / max(R["belly"], 1e-6)
        else: k = (A["tail"] - A["nose"]) / max(R["tail"] - R["nose"], 1e-6)
    if a.joints:
        edits = read_joints(a.joints)
        for n, (h, tl) in edits.items():
            if n in heads:
                heads[n] = h
                if tl is not None: tails[n] = tl
        log["joints_edited"] = sorted(n for n in edits if n in heads)
    write_bones(arm, heads, tails, scale, max(1e-3, rtop * 0.012), rolls if fam == "body" else None)
    log["fit"] = found
    log["landmarks"] = {"mesh": {k_: round(v, 3) for k_, v in A.items() if isinstance(v, float)},
                        "template": {k_: round(v, 3) for k_, v in R.items() if isinstance(v, float)}}
    log["travel_scale"] = round(k, 3)

    # the clips
    log["position_tracks_dropped"] = prune(actions, tracking, root_bone)
    # the limbs: the legs (an animal's below the belly: the shoulder blade, upper arm and thigh sit inside the body,
    # and pointing them the template's way pushed a cow's legs a fifth of a metre out of its own skin); a person's
    # arms from the upper arm down; a kaiju's arms. Not wings: the bird's rest has them spread, and pointing a hen's
    # folded wings that way opened them out behind her like a cape on every walking step. They keep the clip's turns
    # from the wings as modelled.
    limb_set = set(n for n in leg_of if fam == "human" or bones[n].head_local.z < A["belly"])
    for b in bones:
        if (fam == "human" and b.name.startswith("upperarm_")) or (fam == "legged" and b.name.startswith("Front_Leg_Upper")):
            limb_set.add(b.name)
            limb_set.update(c.name for c in b.children_recursive)
    limb_set.discard(root_bone)
    feet = [n for n in leg_of if not bones[n].children]
    if fam in ("human", "legged") and limb_set:
        match_world(arm, rig0, actions, limb_set, feet, k, root_bone, calibrate=fam == "legged" and a.limbs == "calibrated")
    else:
        scale_travel(actions, k)
    rest3 = {b.name: b.matrix_local.to_3x3() for b in rig0.data.bones}
    bpy.data.objects.remove(rig0)
    if tail_names and fam == "legged": damp(actions, set(tail_names), a.tail_swing)
    if a.swing != 1.0: damp(actions, set(leg_of), a.swing)
    # wings on the ground: the template's walk and idle turn its wings from their spread rest, and the same turns
    # open a bird's folded wings out from her sides; off the ground (flap, glide, fly) they swing as made
    wings = {b.name for b in bones if b.name.lower().startswith(("wing_", "shoulder_"))}
    grounded = [act for act in actions if not re.search(r"fly|flap|glide", act.name, re.I)]
    if wings and grounded and a.wing_swing != 1.0: damp(grounded, wings, a.wing_swing)
    log["limbs_rebaked"] = len(limb_set) if fam in ("human", "legged") else 0

    # the skin
    voxel = a.voxel or top / (110.0 if fam == "human" else 70.0)
    def after():
        if fam in ("legged", "human"): log["side_fixed"] = side_rule(mesh, arm)      # each side's bones, its own skin
        if fam == "legged": log["tail_fixed"] = tail_rule(mesh, arm)                 # the tail moves only the tail
        if leg_of and fam in ("legged", "human"): log["confined"] = confine(mesh, arm, leg_of, shape, A["belly"])
    t_skin = time.time()
    if method == "m2m":
        # Mesh2Motion's own: closest bone midpoint, its correctors and smoothing, on the fitted joints as they are
        import m2m_skin_blender
        unreached, bare, off = m2m_skin_blender.skin(mesh, arm, t)
        gated, used = 0, None
    else:
        unreached, bare, off, gated, used = skin(mesh, arm, voxel, after, girdles=fam == "legged", smooth=a.smooth)
    log["unwelded"] = unweld(mesh, arm, actions, top)                              # the generator's welds, cut
    log.update(proxy_unreached=unreached, bare_vertices=bare, no_skin_bones=len(off), gated=gated, voxel=used,
               skin_seconds=round(time.time() - t_skin, 2))

    # the joints as fitted: each bone's head by Mesh2Motion's name, in glTF's axes, on the body as modelled (the
    # rigged files' own coordinates, before a template rest straightens it): what the API's skeleton and the
    # bridge read and write, and what --joints takes back
    joints = {b.name: T.to_gltf(b.head_local) for b in arm.data.bones}
    # a snake modelled in an S is straightened along the template's line; a shark is modelled near straight already,
    # and straightening it with its weights pulled its head off the body (Tornado Titans' sharks, 2026-10-18)
    rest = a.rest or ("template" if t == "snake" else "as-modelled")
    if rest == "template":
        log["reposed_offset"] = repose(arm, mesh, rest3)
    log["rest"] = rest
    # the clips under the engine's names, each a take of its own
    arm.animation_data_create()
    engine = dict(wanted)
    for act in actions:
        act.name = engine[act.name]
        act.use_fake_user = True
        tr = arm.animation_data.nla_tracks.new(); tr.name = act.name
        tr.strips.new(act.name, int(act.frame_range[0]), act)
    if a.naming == "mixamo":
        if t != "human": raise SystemExit("m2m_rig: --naming mixamo is for the human template")
        log["renamed"] = rename_bones(arm, mesh, actions, T.MIXAMO_HUMAN)
    arm.name = "Armature"
    os.makedirs(a.out, exist_ok=True)
    out = {}
    fmts = {f.strip().lower() for f in a.format.split(",")}
    blend = os.path.join(a.out, a.name + ".blend")
    tmp_fbx = os.path.join(a.out, a.name + "_generic.fbx")
    export_fbx(tmp_fbx, mesh, arm)
    if "glb" in fmts:
        out["glb"] = os.path.join(a.out, a.name + ".glb"); export_glb(out["glb"], mesh, arm)
    bpy.context.preferences.filepaths.save_version = 0          # no .blend1 beside it
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    out["blend"] = blend
    if a.audit:
        log["audit"] = audit(tmp_fbx, a.name, os.path.join(a.out, "audit"))
    if "fbx" in fmts:
        out["fbx"] = os.path.join(a.out, a.name + ".fbx")
        if a.target == "unity":
            import export_passes
            rec = export_passes.unity_fbx(tmp_fbx, out["fbx"], run_blender=lambda s, *x: blender_run(s, *x))
            log["unity_fbx"] = rec.get("status")
        else:
            os.replace(tmp_fbx, out["fbx"])
    if os.path.exists(tmp_fbx): os.remove(tmp_fbx)
    if a.preview:
        r = blender_run("preview_glb.py", "-file", blend, "-out", os.path.join(a.out, "preview.glb"))
        if os.path.exists(os.path.join(a.out, "preview.glb")): out["preview"] = os.path.join(a.out, "preview.glb")
        else: log["preview_error"] = ((r.stdout or "") + (r.stderr or ""))[-800:]
    clips = [{"name": engine[w], "m2m": w, "root_motion": T.is_root_motion(w),
              "seconds": next((c["seconds"] for c in T.clips(t, root) if c["name"] == w), None)}
             for w, _ in wanted if w in acts]

    # rig.json, the card and the log
    m2m = {"template": t, "joints": joints, "axes": "gltf", "rest": rest, "clips": [c["m2m"] for c in clips], "skin": method,
           "naming": a.naming, "target": a.target, "swing": a.swing, "tail_swing": a.tail_swing}
    spec_dir = folder or a.out
    rel = (lambda p: os.path.relpath(p, spec_dir).replace(os.sep, "/"))
    merge_json(os.path.join(spec_dir, "rig.json"), {"schema": "autorig-spec/1",
                                                     "rig": {"kind": "m2m", "skeleton": log["choice"].get("archetype") or t,
                                                             "forward": [0, -1, 0]},
                                                     "m2m": m2m})
    grade = (log.get("audit") or {}).get("grade")
    merge_json(os.path.join(spec_dir, "model.json"), {
        "name": os.path.basename(folder) if folder else a.name,
        "rig": {"kind": "m2m", "template": t, "bones": len(arm.data.bones),
                "deform_bones": sum(1 for b in arm.data.bones if b.use_deform),
                "audit": {"grade": grade, **(log.get("audit") or {}).get("checks", {})} if grade else None,
                "clips": {"names": [c["name"] for c in clips], "fbx": rel(out["fbx"]) if "fbx" in out else None,
                          "glb": rel(out["glb"]) if "glb" in out else None}},
        "rigged_fbx": rel(out["fbx"]) if "fbx" in out else None,
        "rigged_glb": rel(out["glb"]) if "glb" in out else None,
        "rigged_blend": rel(blend), "preview": rel(out["preview"]) if "preview" in out else None})
    log.update(out=out, clips=clips, seconds=round(time.time() - t0, 1),
               triangles=sum(len(p.vertices) - 2 for p in mesh.data.polygons))
    with open(os.path.join(a.out, a.name + "_m2m.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(log, joints=joints), fh, indent=1, default=str)
    print("M2M_RIG " + json.dumps(log, default=str))
    return 0


def main():
    a = args()
    try:
        code = run(a)
    except SystemExit as e:
        if isinstance(e.code, str):
            print("M2M_RIG " + json.dumps({"error": e.code})); code = 2
        else: code = e.code or 0
    sys.stdout.flush()
    os._exit(code)          # Blender's own exit would print its quit chatter after ours, and return 0


if __name__ == "__main__":
    main()

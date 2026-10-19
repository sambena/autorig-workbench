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
#      nearest face, every joint faded as far as a --fade-degree bend there needs (core/skin_fade.py; below the
#      belly a leg only moves its own leg), at most four influences; or "m2m", Mesh2Motion's
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
    p.add_argument("--height", type=float, default=0.0,
                   help="height in metres (default: the card's metres_high, else a known animal's typical height "
                        "(ANIMAL_HEIGHT), else as it is)")
    p.add_argument("--no-kind-height", dest="height_auto", action="store_false",
                   help="no typical height by kind: as it is when nothing gives one")
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
    p.add_argument("--folded-flap", dest="folded_flap", type=float, default=0.35,
                   help="scale folded wings' swing in flying clips (flap, glide) where they barely leave the body")
    p.add_argument("--no-stride", dest="stride", action="store_false",
                   help="a bird's walk keeps the template's leg turns, not scaled to the template's stride")
    p.add_argument("--swing", type=float, default=None,
                   help="scale the legs' swing (1 as made; a cow ~0.75; default: from the legs' length, auto_swing)")
    p.add_argument("--gait", default="auto", choices=("auto", "walk", "hop"),
                   help="hop: walk on a hop made from the template's run, each pair of legs together (auto: a "
                        "rabbit or a hare on the Fox; HOP_KINDS)")
    p.add_argument("--tail-swing", dest="tail_swing", type=float, default=0.4, help="scale the tail's swing")
    p.add_argument("--wing-swing", dest="wing_swing", type=float, default=0.3,
                   help="scale the wings' swing in clips on the ground (walk, idle); flying clips swing as made")
    p.add_argument("--voxel", type=float, default=0.0, help="proxy voxel size in metres (default: height / 70)")
    p.add_argument("--limbs", default="calibrated", choices=("calibrated", "absolute"),
                   help="legs and arms: calibrated keeps each leg's fitted rest where the template stands at rest "
                        "and adds the template's turns (default); absolute points every leg bone as the template's")
    p.add_argument("--rest", default="", choices=("", "template", "as-modelled"),
                   help="the rest pose: the template's (default for snakes) or the body as modelled")
    p.add_argument("--smooth", type=int, default=6, help="ring smoothing passes after the transfer (--fade 0 only)")
    p.add_argument("--no-clip-bends", dest="clip_bends", action="store_false",
                   help="fade and audit every bone at 40 degrees, not as far as its clips turn it")
    p.add_argument("--fade-rounds", dest="fade_rounds", type=int, default=20,
                   help="fade, limit to four influences, fade again: this many rounds (core/skin_fade.py)")
    p.add_argument("--fade", type=float, default=40.0,
                   help="fade every joint as far as a bend of this many degrees needs (core/skin_fade.py; 0: the "
                        "old ring smoothing)")
    p.add_argument("--people", default="classic", choices=("classic", "m2m"),
                   help="a person (auto picks the human template, no tail): classic rigs them with the humanoid rig "
                        "(steps/rerig_humanoid.py, A-pose kept, walker clips); m2m uses the human template")
    p.add_argument("--no-classic", dest="classic", action="store_false",
                   help="a body with no template (auto) stops with exit 3 instead of going to the classic rig")
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
    if path.lower().endswith(".glb"):
        # Tripo's rigged GLBs put every joint at the origin (core/tripo_glb.py): imported as they are, the Tornado
        # Titans horse twin lay on its side, 0.26 m high, and Auto took it for a prop. The API mends uploads; a file
        # already in the collection is mended here (a copy; nothing when it needs none)
        import tripo_glb
        path = tripo_glb.mend(path, report=lambda *_: None)
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
        co = [v.co for v in mesh.data.vertices]
        toes = toes_point(co)
        if (toes > 0) if toes else head_end(co, 1) > 0:
            turn(mesh, math.pi); sign = -sign
    return ("+" if sign > 0 else "-") + "xy"[axis]


def toes_point(co):
    """+1 or -1: the way along Y a standing bird's toes point from its ankles (three forward, one back), or 0 when no
    feet stand clear of the body. A hen's or a rooster's tail stands taller than its head, so the taller end
    (head_end) turned Tornado Titans' chickens backwards (2026-10-19)."""
    zs = [c.z for c in co]
    z0 = min(zs); h = max(zs) - z0
    if h < 1e-6: return 0
    low = [c for c in co if c.z < z0 + 0.03 * h]
    if len(low) < 8: return 0
    x0, x1 = min(c.x for c in low), max(c.x for c in low)
    y0, y1 = min(c.y for c in low), max(c.y for c in low)
    pad = 0.05 * h
    ank = [c for c in co if z0 + 0.08 * h < c.z < z0 + 0.16 * h and x0 - pad <= c.x <= x1 + pad and y0 - pad <= c.y <= y1 + pad]
    # the ankles are a leg's thin column: the band over the toes holds a small share of the footprint's width
    if len(ank) < 8 or (max(c.x for c in ank) - min(c.x for c in ank)) > 0.9 * (x1 - x0 + 2 * pad): return 0
    d = sum(c.y for c in low) / len(low) - sum(c.y for c in ank) / len(ank)
    if abs(d) < 0.015 * h: return 0
    return 1 if d > 0 else -1


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


def landmarks(points, groups, leg_roots=False):
    """Where a body's parts are, from its surface points (x across, y along with the head at -Y, z up). groups: the
    pairs of feet it stands on (0 for none), front to back. feet {(g, left)}, ys / xs each group's place along and
    out from the middle, the belly (the lowest point of the body between the legs), the top line over the first and
    last group, the nose and tail ends. leg_roots (four-legged): also front_root and back_root, the y where each pair
    of legs meets the body (leg_root), which the shape fit's body map is anchored on."""
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
    if leg_roots:
        # where each pair of legs meets the body: the neck and head are placed between the nose and the front legs,
        # and Heartroot's snap_jaw lunges with its front paws under its jaw: from its feet the 0.9 m of the horse's
        # neck and head went into 6 cm and the head had no skin (0.2%). Each leg is traced up from its foot.
        roots = []
        for g in range(len(centres)):
            here = [leg_root(ps, feet[(g, s)], belly, top) for s in (True, False) if (g, s) in feet]
            roots.append(statistics.mean(here) if here else ys[g])
        out.update(front_root=roots[0], back_root=roots[-1])
    return out


def leg_root(ps, foot, belly, top, levels=8):
    """The y where a leg standing on this foot meets the body: up from the foot, level by level to 0.8 of the
    belly's height, each level's points near the last level's centre (so the head or a paw beside it is not taken),
    their median the next centre. (Tighter, 0.07 of the height and off the middle line, scored worse on the clips.)"""
    import statistics
    cx, cy = foot.x, foot.y
    z1 = belly * 0.8
    band = max(top * 0.03, z1 / levels * 0.6)
    reach = top * 0.12
    for k in range(1, levels + 1):
        z = z1 * k / levels
        sel = [p for p in ps if abs(p.z - z) < band and (p.x > 0) == (foot.x > 0)
               and math.hypot(p.x - cx, p.y - cy) < reach]
        if len(sel) < 4: break
        # the nearer half: a lantern hung from a cat's collar beside her front legs pulled the median off the leg
        sel.sort(key=lambda p: math.hypot(p.x - cx, p.y - cy))
        sel = sel[:max(4, len(sel) // 2)]
        cx, cy = statistics.median([p.x for p in sel]), statistics.median([p.y for p in sel])
    return cy


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

    def __init__(self, points, marks, floor=None, profile=False):
        self.p = list(points)
        self.m = marks
        self.length = max(marks["tail"] - marks["nose"], 1e-6)
        # below the floor a slice of the body is legs (a legless body has none)
        self.floor = marks["belly"] * 0.9 if floor is None else floor
        ys = marks.get("ys") or []
        # each leg group's band along the body: halfway to its neighbours
        self.bands = [((ys[i - 1] + ys[i]) / 2 if i else -1e9, (ys[i] + ys[i + 1]) / 2 if i + 1 < len(ys) else 1e9)
                      for i in range(len(ys))]
        self.prof = self._profile() if profile else None

    PROFILE_BINS = 48

    def _profile(self):
        """The body's slices read on a grid along its length, each value the median of its five neighbours. A slice
        is a percentile of the points near it, so where a sparse surface has its vertices bunched on one part (the
        Fox template's sample has 1250, crowded on the thigh at the hip) a lone slice read the rump 0.3 m low, and
        the hip joint placed by its share of that height went above a cat's back: her thigh then owned the whole
        haunch and folded her flank on every stride (Cat_Tabby_Tripo, 2026-10-18). slice() drops such a lone slice
        for the median."""
        n = self.PROFILE_BINS
        y0 = self.m["nose"]
        step = self.length / (n - 1)
        raw = [self._slice(y0 + i * step) for i in range(n)]
        out = []
        for i in range(n):
            near = raw[max(0, i - 2):i + 3]
            out.append(tuple(sorted(r[k] for r in near)[len(near) // 2] for k in range(3)))
        return y0, step, out

    # a slice is an outlier where it is further from its neighbours' median than this share of the body's depth
    OUTLIER = 0.12

    def slice(self, y):
        """The body at y: its top, bottom (above the legs) and half width; with a profile, each read the profile's
        way where the slice's own is an outlier (elsewhere as it is: a dense mesh's head and rump keep their shape)."""
        raw = self._slice(y)
        if self.prof is None: return raw
        y0, step, prof = self.prof
        x = max(0.0, min(len(prof) - 1.0, (y - y0) / step))
        i = min(int(x), len(prof) - 2)
        f = x - i
        med = tuple(a + (b - a) * f for a, b in zip(prof[i], prof[i + 1]))
        tol = self.OUTLIER * max(med[0] - med[1], 1e-3)
        return tuple(m if abs(r - m) > tol else r for r, m in zip(raw, med))

    @staticmethod
    def _spread(vals):
        vals = sorted(vals)
        n = len(vals)
        lo, hi = vals[int(n * 0.08)], vals[min(n - 1, int(n * 0.92))]
        return (lo + hi) / 2, max((hi - lo) / 2, 1e-3)

    def _slice(self, y):
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

# the four-legged templates: what this file's quadruped rules (the smoothed body profile, ...) are for
FOUR_LEGGED = ("fox", "horse")

def fit_shape(arm, R, A, rig_surface, animal_surface, family, profile=False):
    """heads {bone: new head} and tails {bone: new tail} (tails only where they do not follow a child), plus what the
    fit found. legged: legs level by level inside each leg, the body slice by slice, the tail down the animal's own
    tail; body and spider: every joint slice by slice."""
    legless = family in ("body", "spider")
    floor = -1.0 if legless else None
    # profile: the template's sample read with outlier slices dropped (Shape._profile); the animal's own mesh is
    # dense, and smoothing it moved a sheep's nose joint (and its head's skin) for nothing
    SR, SA = Shape(rig_surface, R, floor, profile), Shape(animal_surface, A, floor)
    ys = ([R["nose"], R["front"], R["back"], R["tail"]], [A["nose"], A["front"], A["back"], A["tail"]])
    if "front_root" in R and "front_root" in A:     # four-legged: the body by where the legs meet it (landmarks)
        ys = ([R["nose"], R["front_root"], R["back_root"], R["tail"]], [A["nose"], A["front_root"], A["back_root"], A["tail"]])
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
    if family == "spider":
        found["head_reach"] = reach_front(heads, tails, bones, animal_surface)
    return heads, tails, found, leg_of, tail, SA


def reach_front(heads, tails, bones, surface):
    """A spider's head as far forward as the body goes. The template's head sits behind long forward legs, and slice
    by slice that proportion put a crab's and a hexapod's head bone in the middle of the body, nearer no skin than the
    legs and fangs around it: bone heat gave it 0%. Here head_tip (and so the head's tail) moves out to the front of
    the body on its middle line (the legs are off it), 80% of the way, its tail there, when that is further forward
    than the fit put it. Returns the move in metres (0: none)."""
    if "head" not in bones or "head_tip" not in bones: return 0.0
    xs = [abs(q.x) for q in surface]
    width = max(xs) if xs else 0.0
    mid = [q for q in surface if abs(q.x) < width * 0.08]
    if len(mid) < 10: return 0.0
    front = min(q.y for q in mid)
    near = [q for q in mid if q.y < front + 0.03 * (max(q.y for q in mid) - front)]
    tip = Vector((0.0, front, sorted(q.z for q in near)[len(near) // 2]))
    h = heads["head"]
    old = heads["head_tip"]
    if tip.y > old.y - 1e-3 or tip.y > h.y: return 0.0
    heads["head_tip"] = h.lerp(tip, 0.8)
    tails["head_tip"] = tip
    return round((heads["head_tip"] - old).length, 3)


# ---------------------------------------------------------------- the spider's legs, found on the body

SPIDER_LEGS = "abcd"
# which of the template's leg pairs a body with n pairs uses, front first. Its walk lifts L a, L c, R b and R d
# together, then the others: three pairs on a, b, c lift front and back on one side with the middle on the other (an
# insect's tripod); two pairs on a and d lift each front leg with the opposite back one (a trot)
SPIDER_USE = {1: "a", 2: "ad", 3: "abc", 4: "abcd"}
SPIDER_FOOT = 0.3         # a lifted foot's tip lies in the lowest this share of the height, its leg reaching down
SPIDER_CLAW_FRONT = 0.45  # a claw reaches out of the front this share of the length
SPIDER_GROUND = 0.08      # a limb ending this low (a share of the height) stands on the ground
SPIDER_REACH = 0.5        # a leg or a claw reaches at least this share of what a standing limb does


def spider_chain(bones, letter, side):
    """The template's leg bones for one leg, body end first (leg_a_1_l .. leg_a_tip_l), or None."""
    names = ["leg_%s_%d_%s" % (letter, i, side) for i in (1, 2, 3)] + ["leg_%s_tip_%s" % (letter, side)]
    return names if all(n in bones for n in names) else None


def limb_tips(surface, top, length, width, ylo, body_at):
    """The pairs of limbs sticking out to the sides of a body, as {"l"/"r": [(tip, kind, reach)]} front first, the
    same pairs on both sides. Each tip off the centre plane is followed in to where it meets the body
    (geo.Surface.junction). One tip per limb: two tips are the same limb when they are nearer each other along the
    surface than either is to the body (a leg's toe and a spike on its knee). Generated bodies are symmetric but the
    search is not, so the limbs of both sides are matched on the body folded across its centre plane and judged as
    pairs: a pair with no limb leaving the body (a shell's rim) or a fraction as long as the legs standing on the
    ground (a spike) is none; "foot" for one ending near the ground or reaching down to it from where it starts (a
    beetle's lifted front legs), "claw" for one at least as thick as the legs reaching forward and not up (a crab's
    pincers); antennae, which reach up, are neither. A limb found on one side only is mirrored onto the other."""
    cands = [v for v, _ in surface.tips(most=40, least=0.04) if abs(surface.co[v].x) >= width * 0.3]
    info = {}
    for v in cands:
        tip = surface.co[v]
        b = body_at(tip.y)
        j = surface.junction(tip, b)
        root, radius = (j[0], j[2]) if j else (b.lerp(tip, 0.25), None)
        d = surface.distances([v])
        r = d[surface.nearest(root)]
        info[v] = dict(tip=tip, root=root, radius=radius, d=d, reach=r if r < float("inf") else (tip - root).length)
    keep = []
    for v in sorted(cands, key=lambda c: -info[c]["reach"]):
        if any(info[k]["d"][v] < 0.8 * min(info[v]["reach"], info[k]["reach"]) for k in keep): continue
        keep.append(v)
    fold = lambda p: Vector((abs(p.x), p.y, p.z))
    near = 0.12 * max(width * 2, length)
    pairs = []                                     # [{"l": v, "r": v}], strongest first
    for v in keep:
        side = "l" if info[v]["tip"].x > 0 else "r"
        for pr in pairs:
            o = next(iter(pr.values()))
            if side not in pr and (fold(info[o]["tip"]) - fold(info[v]["tip"])).length < near:
                pr[side] = v; break
        else:
            pairs.append({side: v})
    ground = [info[v] for v in keep if info[v]["tip"].z < top * SPIDER_GROUND] or [info[v] for v in keep]
    reach = sorted(i["reach"] for i in ground)[len(ground) // 2] if ground else 0.0
    radii = sorted(i["radius"] for i in ground if i["radius"])
    leg_r = radii[len(radii) // 2] if radii else 0.0
    sides = {"l": [], "r": []}
    for pr in pairs:
        ii = [info[v] for v in pr.values()]
        if all(i["radius"] is None for i in ii): continue           # no limb leaves the body there
        best = max(ii, key=lambda i: (i["radius"] is not None, i["reach"]))
        if best["reach"] < SPIDER_REACH * reach: continue
        tip, root = fold(best["tip"]), fold(best["root"])
        down = root.z - tip.z
        if tip.z < top * SPIDER_GROUND * 1.5 or (tip.z < top * SPIDER_FOOT and down > 0.5 * (tip - root).length):
            kind = "foot"
        elif (tip.y < root.y and tip.y < ylo + length * SPIDER_CLAW_FRONT and down > -0.3 * (tip - root).length
              and max(i["radius"] or 0.0 for i in ii) >= 0.8 * leg_r):
            kind = "claw"
        else:
            continue
        for side, sign in (("l", 1.0), ("r", -1.0)):
            if side in pr: p = info[pr[side]]["tip"].copy()
            else: p = surface.co[surface.nearest(Vector((sign * tip.x, tip.y, tip.z)))].copy()
            sides[side].append((p, kind, round(best["reach"] / max(reach, 1e-6), 2),
                                round(tip.z / top, 2), round(down / max((tip - root).length, 1e-6), 2)))
    for sd in sides.values(): sd.sort(key=lambda t: t[0].y)
    return sides


def body_point(points, y, width, top, length):
    """The middle of the body at y: on the centre plane, half way up the points near it there."""
    for w in (0.04, 0.08, 0.16, 0.5):
        sel = [p.z for p in points if abs(p.y - y) < length * w and abs(p.x) < width * 0.15]
        if len(sel) >= 6: break
    zs = sorted(sel) if sel else [top * 0.5]
    return Vector((0.0, y, (zs[int(len(zs) * 0.1)] + zs[int(len(zs) * 0.9)]) / 2))


def place_chain(path, lengths):
    """Joints down a limb's centre line (body end first) for bones of these template lengths: each joint by its share
    of the template chain, then the first inner joint moved to the line's sharpest corner (a knee) when the line
    bends there by more than 20 degrees, the rest shared out after it."""
    cum = [0.0]
    for a, b in zip(path, path[1:]): cum.append(cum[-1] + (b - a).length)
    total = max(cum[-1], 1e-6)
    def at(s):
        for i in range(1, len(cum)):
            if cum[i] >= s or i == len(cum) - 1:
                f = (s - cum[i - 1]) / max(cum[i] - cum[i - 1], 1e-9)
                return path[i - 1].lerp(path[i], max(0.0, min(1.0, f)))
    share = [sum(lengths[:k]) / max(sum(lengths), 1e-9) for k in range(len(lengths) + 1)]
    joints = [at(total * s) for s in share]
    if len(lengths) >= 2 and len(path) >= 5:
        best, where = 0.0, None
        for i in range(1, len(path) - 1):
            if not 0.2 * total <= cum[i] <= 0.7 * total: continue
            a, b = path[i] - path[max(0, i - 2)], path[min(len(path) - 1, i + 2)] - path[i]
            if a.length < 1e-6 or b.length < 1e-6: continue
            ang = a.angle(b)
            if ang > best: best, where = ang, i
        if where is not None and best > math.radians(20):
            s1 = cum[where]
            rest = sum(lengths[1:])
            joints[1] = path[where].copy()
            for k in range(2, len(lengths)):
                joints[k] = at(s1 + (total - s1) * sum(lengths[1:k]) / max(rest, 1e-9))
    return joints


def fit_spider_legs(mesh, arm, heads, tails, top, log):
    """Each of the template's legs on one of the body's own: the template's legs are set by its body's proportions
    (slice by slice), which put a crab's or a beetle's leg bones in the air above its shell and its feet nowhere near
    its feet (every spider-family rig failed with hundreds of tears, 2026-10-19). Here the legs are found on the mesh:
    the tips near the ground off the centre plane, each followed in to where it meets the body (geo.Surface.junction,
    the limb's radius jumping) down its centre line (medial_axis). Per side, front to back, they take the template's
    legs by SPIDER_USE; with more than four pairs, four spread from front to back. A pair of thick limbs reaching
    forward off the ground (a crab's claws) takes the fangs, so a bite closes them. A leg the body has no match for is
    folded inside the body and carries no skin. Returns what it found, and {bone: (group, left)} for the legs used."""
    import geo
    bones = arm.data.bones
    s = geo.Surface(mesh)
    pts = s.co
    width = max(abs(p.x) for p in pts)
    ylo, yhi = min(p.y for p in pts), max(p.y for p in pts)
    length = max(yhi - ylo, 1e-6)
    sides = limb_tips(s, top, length, width, ylo, lambda y: body_point(pts, y, width, top, length))
    feet = {k: [t[0] for t in v if t[1] == "foot"] for k, v in sides.items()}
    claws = {k: [t[0] for t in v if t[1] == "claw"] for k, v in sides.items()}
    pairs = min(len(feet["l"]), len(feet["r"]))
    found = {"feet": [len(feet["l"]), len(feet["r"])], "claws": [len(claws["l"]), len(claws["r"])]}
    if pairs == 0:
        found["legs_fitted"] = 0
        return found, {}
    if pairs in SPIDER_USE: pick = list(range(pairs)); letters = SPIDER_USE[pairs]
    else: pick = sorted({round(i * (pairs - 1) / 3.0) for i in range(4)}); letters = SPIDER_USE[len(pick)]
    leg_of, roots = {}, []

    def limb(tip):
        """(root, centre line body end first, radius) of the limb ending at tip."""
        b = body_point(pts, tip.y, width, top, length)
        j = s.junction(tip, b)
        root = j[0] if j else b.lerp(tip, 0.25)
        path = s.medial_axis(root, tip, 12, first=root)
        path[-1] = tip.copy()
        return root, path, (j[2] if j else None)

    for side in ("l", "r"):
        fs = feet[side]
        if len(fs) > pairs:          # keep the pairs both sides share, from the front
            fs = fs[:pairs]
        for g, (i, letter) in enumerate(zip(pick, letters)):
            chain = spider_chain(bones, letter, side)
            if chain is None: continue
            root, path, _ = limb(fs[i])
            lengths = [bones[n].length for n in chain[:3]]
            j = place_chain(path, lengths)
            for n, p in zip(chain[:3], j[:3]): heads[n] = p
            heads[chain[3]] = j[3]
            d = (j[3] - j[2]).normalized() if (j[3] - j[2]).length > 1e-6 else Vector((0, 0, -1))
            d = Vector((d.x, d.y, 0.0)).normalized() if Vector((d.x, d.y)).length > 1e-3 else d
            tails[chain[3]] = j[3] + d * max(bones[chain[3]].length * (j[3] - j[0]).length /
                                             max(sum(bones[n].length for n in chain[:3]), 1e-6), 1e-3)
            k = "legs_anchor_%d_%s" % ("abcd".index(letter) + 1, side)
            for n in chain: leg_of[n] = (g, side == "l")
            roots.append((side, letter, root))
        # the template's legs this body has none for: folded into the body at the nearest used leg's root, no skin
        for letter in SPIDER_LEGS:
            if letter in letters: continue
            chain = spider_chain(bones, letter, side)
            if chain is None: continue
            mine = [r for sd, _, r in roots if sd == side]
            base = min(mine, key=lambda r: abs(r.y - heads[chain[0]].y)) if mine else heads[chain[0]]
            for i, n in enumerate(chain):
                heads[n] = base + Vector((0, 0, 0.002 * i)); bones[n].use_deform = False
            tails[chain[-1]] = base + Vector((0, 0, 0.002 * len(chain)))
    # the anchors: from the body's middle at the legs out to each leg's root
    for side in ("l", "r"):
        mine = [(l, r) for sd, l, r in roots if sd == side]
        if not mine: continue
        c = body_point(pts, sum(r.y for _, r in mine) / len(mine), width, top, length)
        a = "legs_anchor_%s" % side
        if a in bones:
            heads[a] = c
            tails[a] = c.lerp(sum((r for _, r in mine), Vector()) / len(mine), 0.35)
        for letter in SPIDER_LEGS:
            k = "legs_anchor_%d_%s" % ("abcd".index(letter) + 1, side)
            if k not in bones: continue
            heads[k] = tails.get(a, c)
            used = [r for l, r in mine if l == letter]
            tails[k] = used[0] if used else heads[k] + Vector((0, 0, 0.002))
    # the fangs on a pair of claws
    if claws["l"] and claws["r"] and all(n in bones for n in ("teeth_l", "teeth_tip_l", "teeth_r", "teeth_tip_r")):
        for side in ("l", "r"):
            root, path, _ = limb(claws[side][0])
            j = place_chain(path, [1.0, 1.0])
            heads["teeth_" + side], heads["teeth_tip_" + side] = j[0], j[1]
            tails["teeth_tip_" + side] = j[2]
            leg_of["teeth_" + side] = leg_of["teeth_tip_" + side] = (-1, side == "l")
        found["claws_on_fangs"] = True
    # the body's own bones on its centre line: the slice fit reads the template's slices, which its long legs
    # dominate, and put a crab's abdomen under the floor and its head over its shell
    used = set(leg_of)
    mid = [p.y for p in pts if abs(p.x) < width * 0.15] or [ylo, yhi]
    ylo, yhi = min(mid), max(mid)
    for n in [b.name for b in bones if b.name not in used and b.parent is not None and not b.name.startswith(("leg", "teeth"))]:
        p = heads.get(n)
        if p is None: continue
        y = max(ylo, min(yhi, p.y))
        heads[n] = body_point(pts, y, width, top, length)
        if n in tails:
            t = tails[n]; tails[n] = body_point(pts, max(ylo, min(yhi, t.y)), width, top, length)
    found["legs_fitted"] = len(roots)
    found["legs_used"] = letters
    return found, leg_of


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
    import numpy as np
    pts, rad, thick, minor = [], [], [], []
    for g in groups:
        if not g: continue
        m = sum(g, Vector()) / len(g)
        pts.append(m); rad.append(sum((p - m).length for p in g) / len(g))
        # the ring's thickness across its narrower way: a round head is thick both ways, a flat tail fin (the glass
        # eel's) is wide one way only, and by the mean radius alone the eel's head went on its tail; weighted by the
        # ring's size, so the bulk of a head counts and the whisker the line starts from (the sea serpent's) does not
        if len(g) >= 4:
            sv = np.linalg.svd(np.array([p - m for p in g]), compute_uv=False) / math.sqrt(len(g))
            minor.append(float(sv[1]))
        else:
            minor.append(rad[-1])
        thick.append(minor[-1] * len(g))
    # a little smoothing: a bin's middle wobbles where a fin or a jaw joins it
    sm = [pts[0]] + [(pts[i - 1] + pts[i] * 2 + pts[i + 1]) / 4 for i in range(1, len(pts) - 1)] + [pts[-1]]
    n = max(1, len(rad) // 3)
    if sum(thick[-n:]) > sum(thick[:n]):
        sm.reverse(); rad.reverse(); minor.reverse()
    # the line starts where the head does, not at the tip of a whisker or a horn ahead of it: the sea serpent's
    # head bone went on its whisker and its head on the neck (head 0.06% of the skin)
    mid = sorted(minor)[len(minor) // 2] if minor else 0.0
    cut = 0
    while cut < len(sm) // 4 and minor[cut] < WHISKER * mid: cut += 1
    sm, rad, minor = sm[cut:], rad[cut:], minor[cut:]
    return sm, rad, neck_share(minor)


def neck_share(minor):
    """Where the head ends along a long body, as a share of its length from the front: the narrowest ring behind
    the thickest in the front third, when it narrows there by a fifth or more (a neck); None when nothing narrows."""
    n = len(minor)
    third = max(3, n // 3)
    if n < 6: return None
    top = max(range(third), key=lambda i: minor[i])
    if top >= third - 1: return None
    neck = min(range(top + 1, third), key=lambda i: minor[i])
    if minor[neck] > 0.8 * minor[top]: return None
    return (neck + 0.5) / n


# a ring at the front end of a long body thinner than this share of the body's median is a whisker, not the head
WHISKER = 0.3


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


YAW_SNAP = 25.0


def fit_line(arm, mesh, log, head_at=None):
    """heads, tails and rolls for a body with no legs to stand on (snake, shark): the template's line from its front
    tip to its back tip laid along the body's own centre line, every joint at the same share of the length; a joint
    off the line (a fin, the jaw) keeps its offset from it, turned with the line there (back kept up) and scaled by
    the length change. The mesh is first turned about Z so it heads the template's way, so nothing turns over.
    head_at: the share of the length (from the front) the head bone's joint goes to, the line ahead of it and behind
    it each stretched evenly to fit (None: where the template has it)."""
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
    ml, rad, neck = mesh_line(mesh)
    if head_at == "neck":                 # the head as long as the body's own (a snake's template head is a stub)
        head_at = min(0.25, max(0.03, neck)) if neck else None
        log["neck_at"] = round(neck, 3) if neck else None
    flat = lambda v: Vector((v.x, v.y, 0.0))
    # the way the head points (the front of the line), not the nose-to-tail chord: a shark modelled curving round
    # (the megalodon) was turned 43 degrees off the way it was modelled heading
    # a body modelled heading along an axis (as generators make them) stays on it: the turn snaps to the nearest
    # quarter turn when the chord or the head is within YAW_SNAP of one (a round fish's fins tilt its front)
    ht = flat(tl[0] - tl[-1])
    yaw = 0.0
    turns = []
    for hm in (flat(ml[0] - ml[-1]), flat(ml[0] - ml[max(1, len(ml) * 3 // 10)])):
        if ht.length > 1e-6 and hm.length > 1e-6: turns.append(math.atan2(ht.y, ht.x) - math.atan2(hm.y, hm.x))
    if turns:
        yaw = turns[-1]
        for y in turns:
            q = round(y / (math.pi / 2)) * (math.pi / 2)
            if abs(y - q) < math.radians(YAW_SNAP): yaw = q; break
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

    u_head = polyline_param(tl, mw @ bones["head"].head_local)[0] if head_at and "head" in bones else None
    def share(u):
        if not u_head or not 0 < u_head < 1: return u
        return u * head_at / u_head if u < u_head else head_at + (u - u_head) * (1 - head_at) / (1 - u_head)

    def turn_at(p):
        u, q, i = polyline_param(tl, p)
        d_t = (tl[min(i + 1, len(tl) - 1)] - tl[i])
        at, d_m = polyline_at(ml, share(u))
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
                   "head_at": [round(u_head, 3), head_at] if u_head and head_at else None,
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
        # a stub whose tip bone (a leaf, no skin) starts well ahead along it: the snake's head is 1.3 cm long with
        # head_tip 13 cm on, so bone heat gave the head 0-0.3% of the body. It reaches the tip instead; its direction,
        # and so its frame and the clips' turns, stay the same
        ahead = [c for c in b.children if not c.children and LEAF.search(c.name)
                 and ((mw @ c.head) - oh).length > (ot - oh).length * 1.5
                 and ((mw @ c.head) - oh).angle(ot - oh, math.pi) < math.radians(15)]
        if nxt: nt = heads[nxt[0].name]
        elif ahead: nt = heads[ahead[0].name]
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


# Leg swing from the legs' length. A clip turns each leg joint by the same angle on any body, so the step it takes
# keeps to the leg, but the knee a horse's walk folds up 114 degrees lifts a short-legged pig's hoof into its own
# belly and folds the flank above it (Pig_Tripo, 2026-10-18). Legs shorter against the body than the template's
# (the belly's height over the distance between the front and hind feet) swing less by that share, never under
# SWING_MIN: horse 1.04 -> 1, goat 0.9, cat 0.8, dogs and rabbit 0.75, sheep 0.7, pig 0.63 -> 0.65. The lesson Sam
# set by hand for the cows was 0.75.
SWING_MIN = 0.65


def auto_swing(A, R):
    """The legs' swing for a four-legged body (A its landmarks, R the template's): 1 down to SWING_MIN."""
    def legs(m): return m["belly"] / max(abs(m["back"] - m["front"]), 1e-6)
    r = legs(A) / max(legs(R), 1e-6)
    return round(max(SWING_MIN, min(1.0, r)) * 20) / 20


# The animals that hop rather than walk, by their kind (core/lessons.kind: the first word of the name, as the
# collections name their models). Mesh2Motion has no hop; hop_from() makes one. Only for a rabbit standing on its
# legs: Heartroot's hopper sits on folded haunches, and the Fox's run on that rest crushed it (score 69; the Horse's
# walk the route gives it, 99.7).
HOP_KINDS = ("rabbit", "hare", "bunny", "jackrabbit", "kangaroo", "wallaby")
HOP_SOURCE = "Run"
HOP_NAME = "Hop"


def hop_kind(a):
    import lessons
    return lessons.kind(a.name or "") in HOP_KINDS or lessons.kind(os.path.basename(a.model or "")) in HOP_KINDS


def wants_hop(a, t):
    if a.gait != "auto": return a.gait == "hop"
    return t == "fox" and hop_kind(a)


def hop_from(src, name=HOP_NAME):
    """A hop from a gallop: a copy of the clip with each right leg moving exactly as its left one, mirrored, so each
    pair of legs pushes off and lands together (a rabbit's bound), and the body bones' side-to-side turns and the
    hips' sideways sway dropped, so it travels straight. The Fox's left and right bones are exact mirrors across X,
    so a left turn (w, x, y, z) mirrored is (w, x, -y, -z) on the right; a bone on the middle line keeps the part of
    its turn that is its own mirror, (w, x, 0, 0) normalised: it bends up and down only."""
    act = src.copy()
    act.name = name
    act.use_fake_user = True
    curves = {}
    for fc in fcurves(act):
        curves[(fc.data_path, fc.array_index)] = fc
    def keys(fc): return [(k.co.x, k.co.y) for k in fc.keyframe_points]
    def write(fc, pts):
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(pts))
        for k, (x, y) in zip(fc.keyframe_points, pts):
            k.co = (x, y); k.interpolation = 'LINEAR'
        fc.update()
    done = set()
    for (path, i), fc in list(curves.items()):
        b = bone_of(path)
        if b is None: continue
        m = re.match(r"^(.*)_R$", b)
        if m and path.endswith("rotation_quaternion"):
            left = curves.get((path.replace('"%s"' % b, '"%s_L"' % m.group(1)), i))
            if left is not None:
                pts = keys(left)
                write(fc, [(x, -y if i >= 2 else y) for x, y in pts])
                done.add(b)
            continue
        if re.search(r"_[LR]$", b): continue
        if path.endswith("rotation_quaternion") and i == 0:
            q = [curves.get((path, j)) for j in range(4)]
            if None in q: continue
            frames = sorted({x for c in q for x, _ in keys(c)})
            vals = []
            for f in frames:
                w, x = q[0].evaluate(f), q[1].evaluate(f)
                n = max(math.hypot(w, x), 1e-9)
                vals.append((w / n, x / n))
            write(q[0], [(f, v[0]) for f, v in zip(frames, vals)])
            write(q[1], [(f, v[1]) for f, v in zip(frames, vals)])
            for j in (2, 3): write(q[j], [(f, 0.0) for f in frames])
        elif path.endswith("location") and i == 0:
            write(fc, [(x, 0.0) for x, _ in keys(fc)])
    return act, sorted(done)


def amplify(actions, names, k):
    """The turns of these bones from their rest scaled by k (k over 1 swings them further: damp() only eases)."""
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
                if q.magnitude < 1e-9: continue
                axis, ang = q.normalized().to_axis_angle()
                if ang > math.pi: ang -= 2 * math.pi
                q = Quaternion(axis, ang * k)
                for j, f in enumerate(fcs):
                    kp = f.keyframe_points[i]
                    d = q[j] - kp.co.y
                    kp.co.y += d; kp.handle_left.y += d; kp.handle_right.y += d


def foot_sweep(obj, act, feet):
    """How far the feet travel forward and back in a clip, over the hips' height at rest (the mean of the feet)."""
    assign(obj, act)
    span = []
    for n in feet:
        ys = []
        for f in frames_of(act)[::max(1, len(frames_of(act)) // 24)]:
            at(f)
            ys.append(tip(obj.matrix_world @ obj.pose.bones[n].matrix, obj.data.bones[n].length).y)
        span.append(max(ys) - min(ys))
    obj.animation_data.action = None
    return sum(span) / max(len(span), 1)


# wings reaching out of the body less than this share of its height are folded (Tornado Titans' hen 0.27, rooster
# 0.24; the candle moth's open wings 0.72)
WING_SPREAD = 0.4


# a bird's legs swing at most this much further than the template's (the turns, not the stride)
STRIDE_GAIN_MAX = 1.6


def stride_gain(arm, src, actions, limb, feet, k, root, legs):
    """A walk's stride matched to the template's for the body's own legs: the same turns on a hen's short, nearly
    straight legs moved her feet 0.55 of her hip height a step, where the template's long bent ones move 0.94
    (Chicken_Hen_Tripo, 2026-10-19: "the walk barely moves"). Each walking clip is rebaked once on a copy, its feet's
    sweep over the hip height measured on both, and the clip's leg turns scaled by the ratio (1 to STRIDE_GAIN_MAX)
    before the real rebake, so the feet are grounded on the larger stride. Returns {clip: gain}."""
    tops = [n for n in legs if arm.data.bones[n].parent and arm.data.bones[n].parent.name not in legs]
    h_arm = sum((arm.matrix_world @ arm.data.bones[n].head_local).z for n in tops) / max(len(tops), 1)
    h_src = sum((src.matrix_world @ src.data.bones[n].head_local).z for n in tops) / max(len(tops), 1)
    out = {}
    for act in actions:
        if not re.search(r"walk|run|trot", act.name, re.I) or h_arm < 1e-6 or h_src < 1e-6: continue
        want = foot_sweep(src, act, feet) / h_src
        tmp = act.copy()
        match_world(arm, src, [tmp], limb, feet, k, root, calibrate=True)
        got = foot_sweep(arm, tmp, feet) / h_arm
        bpy.data.actions.remove(tmp)
        if got < 1e-6: continue
        g = max(1.0, min(STRIDE_GAIN_MAX, want / got))
        if g > 1.02: amplify([act], legs, g)
        out[act.name] = {"template": round(want, 3), "body": round(got, 3), "gain": round(g, 2)}
    return out


# The audit's single-bone bend is 40 degrees, a combined pose 30 on limbs; a bone that never turns that far in its
# clips (a hen's spine turns a few degrees) is tested at what it does turn, with headroom, and never past 40 (the
# old test) nor under 15 (a game adds turns of its own: a look-at, a hit reaction). docs/SKIN-BAKEOFF.md, "M9".
CLIP_BEND_HEADROOM, CLIP_BEND_MIN, CLIP_BEND_MAX = 1.25, 15.0, 40.0


def clip_bends(arm, actions):
    """{bone: degrees}: the most each bone turns from its rest in these clips (its rotation curves, every frame),
    times the headroom, between CLIP_BEND_MIN and CLIP_BEND_MAX; and {bone: the raw most}. Both kinds of rotation
    curve count; a bone with no curves turns 0."""
    most = {b.name: 0.0 for b in arm.data.bones}
    for act in actions:
        quat, eul = {}, {}
        for fc in fcurves(act):
            n = bone_of(fc.data_path)
            if n not in most: continue
            if fc.data_path.endswith("rotation_quaternion"): quat.setdefault(n, {})[fc.array_index] = fc
            elif fc.data_path.endswith("rotation_euler"): eul.setdefault(n, {})[fc.array_index] = fc
        frames = frames_of(act)
        for n, fcs in quat.items():
            for f in frames:
                q = Quaternion([fcs[i].evaluate(f) if i in fcs else (1.0 if i == 0 else 0.0) for i in range(4)])
                if q.magnitude < 1e-9: continue
                q.normalize()
                most[n] = max(most[n], math.degrees(2 * math.acos(min(1.0, abs(q.w)))))
        for n, fcs in eul.items():
            from mathutils import Euler
            for f in frames:
                q = Euler([fcs[i].evaluate(f) if i in fcs else 0.0 for i in range(3)]).to_quaternion()
                most[n] = max(most[n], math.degrees(2 * math.acos(min(1.0, abs(q.w)))))
    test = {n: round(min(CLIP_BEND_MAX, max(CLIP_BEND_MIN, v * CLIP_BEND_HEADROOM)), 1) for n, v in most.items()}
    return test, {n: round(v, 1) for n, v in most.items()}


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
    # calibrate (four-legged bodies and humans): each bone keeps its own rest where the template stands at its rest, and takes
    # the template's turns from there. Pointing the bones exactly as the template's do bent a fitted cow's legs by
    # the difference on every frame, 35-50 degrees at the ankles even in Idle, and tore the skin (Cow_Holstein,
    # 2026-10-18); on Tornado Titans' people the absolute walk was a crouch on bent knees. corr maps the template bone's rest frame onto this bone's, both in armature space.
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


def plant_legs(arm, src, actions, legs, k, root):
    """A spider-family body's legs, each frame solved by FABRIK (core/limb_ik.py) so its foot follows the template
    foot's path from where it stands: the template foot's move from its rest, scaled by the leg's length against the
    template leg's, added to this foot's rest. Turning each leg bone as the template's turns (match_world) drove the
    feet of legs standing at another angle (a crab's splay sideways, a beetle's bend under it) through the floor and
    across it: Smeltdown's crawler sank 18% of its height in its walk and its feet skated 145 (steps/score.py,
    2026-10-19). Each leg starts from its own rest under the body as the clip moves it, so its knee stays on its side;
    the body, the anchors and the fangs keep the clip's own turns. legs: [[leg_1, leg_2, leg_3, tip], ...]."""
    import limb_ik
    pb, sb = arm.pose.bones, src.pose.bones
    ab, sd = arm.data.bones, src.data.bones
    smw, amw = src.matrix_world, arm.matrix_world
    legs = [c for c in legs if all(n in pb and n in sb for n in c)]
    limb = {n for c in legs for n in c}
    span = lambda bones, c: sum(bones[n].length for n in c[:3])
    for act in actions:
        frames = frames_of(act)
        assign(src, act)
        moves = []
        for f in frames:
            at(f)
            moves.append({c[3]: (smw @ sb[c[3]].head) - (smw @ sd[c[3]].head_local) for c in legs})
        src.animation_data.action = None
        scale_travel([act], k)
        for fc in list(fcurves(act)):
            if bone_of(fc.data_path) in limb and fc.data_path.endswith(("rotation_quaternion", "rotation_euler", "location")):
                drop_fcurve(act, fc)
        assign(arm, act)
        keys = []
        for f, mv in zip(frames, moves):
            at(f)
            pose = {}
            for c in legs:
                par = pb[c[0]].parent.matrix.copy()
                # the leg at its own rest under the body as the clip has moved it
                joints, m = [], par
                for n in c[:3]:
                    b = ab[n]
                    m = m @ (b.parent.matrix_local.inverted() @ b.matrix_local)
                    joints.append(m.to_translation())
                joints.append((m @ Matrix.Translation((0, ab[c[2]].length, 0))).to_translation())
                target = amw.inverted() @ ((amw @ ab[c[3]].head_local) + mv[c[3]] * (span(ab, c) / max(span(sd, c), 1e-9)))
                knee = joints[1] - joints[0].lerp(joints[-1], 0.5)
                solved = limb_ik.fabrik([tuple(p) for p in joints], tuple(target), pole=tuple(knee))
                solved = [Vector(p) for p in solved]
                m = par
                for i, n in enumerate(c[:3]):
                    b = ab[n]
                    base = m @ (b.parent.matrix_local.inverted() @ b.matrix_local)
                    cur = base.to_3x3() @ Vector((0, 1, 0))
                    want = solved[i + 1] - solved[i]
                    turn_ = cur.rotation_difference(want).to_matrix() if want.length > 1e-9 else Matrix.Identity(3)
                    new = Matrix.Translation(base.to_translation()) @ (turn_ @ base.to_3x3()).to_4x4()
                    pose[n] = (base.inverted() @ new).to_quaternion()
                    m = new
            keys.append((f, pose))
        for f, pose in keys:
            for n, q in pose.items():
                pb[n].rotation_mode = 'QUATERNION'
                pb[n].rotation_quaternion = q
                pb[n].keyframe_insert("rotation_quaternion", frame=f)
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


def _weights(mesh, arm):
    """The mesh's weights as (vertices, edges, W n x bones, parent indices, deform flags, heads, bone names)."""
    import numpy as np
    me = mesh.data
    n = len(me.vertices)
    bones = list(arm.data.bones)
    names = [b.name for b in bones]
    bi = {nm: i for i, nm in enumerate(names)}
    parent = [bi[b.parent.name] if b.parent else -1 for b in bones]
    deform = [bool(b.use_deform and b.name in mesh.vertex_groups) for b in bones]
    gb = {g.index: bi[g.name] for g in mesh.vertex_groups if g.name in bi}
    W = np.zeros((n, len(bones)))
    for v in me.vertices:
        for g in v.groups:
            if g.group in gb and deform[gb[g.group]]: W[v.index, gb[g.group]] += g.weight
    P = np.empty(n * 3); me.vertices.foreach_get("co", P); P = P.reshape(n, 3)
    E = np.empty(len(me.edges) * 2, int); me.edges.foreach_get("vertices", E); E = E.reshape(-1, 2)
    heads = np.array([b.head_local[:] for b in bones])
    return P, E, W, parent, deform, heads, names


def _write_weights(mesh, W, names, deform):
    import numpy as np
    n = W.shape[0]
    for j, nm in enumerate(names):
        if not deform[j]: continue
        g = mesh.vertex_groups[nm]
        g.remove(list(range(n)))
        for i in np.nonzero(W[:, j] > 0)[0]: g.add([int(i)], float(W[i, j]), 'REPLACE')


def fade(mesh, arm, angle=40.0, rounds=20, gate_rounds=False):
    """core/skin_fade.fade_rounds on the mesh's weights: every joint faded as far as a bend of angle degrees there
    needs, then four influences at most, `rounds` times; gate_rounds: each round first keeps each vertex's weight to
    its near bones (core/skin_fade.near_share). Returns the relaxation passes."""
    import skin_fade
    import numpy as np
    P, E, W, parent, deform, heads, names = _weights(mesh, arm)
    near = None
    if gate_rounds:
        tails = np.array([b.tail_local[:] for b in arm.data.bones])
        near = skin_fade.near_share(P, heads, tails, deform)
    out, passes = skin_fade.fade_rounds(P, E, W, parent, deform, heads, angle, rounds, near=near)
    _write_weights(mesh, out, names, deform)
    return passes


def skin(mesh, arm, voxel, after_transfer=None, girdles=False, smooth=0, width=0.0, rounds=20, gate_rounds=False):
    """Bone heat on a watertight voxel copy, carried back to the mesh by nearest face. The root, the leaf and tip
    bones at the ends of chains and (girdles) the girdle bones carry no skin. width: fade() for a bend of that many
    degrees, `rounds` rounds, after the gate (0: none, and smooth: passes of ring smoothing instead, as before M9).
    At most four influences, normalised, as an engine takes them."""
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
    if width:
        # the leg confine's cuts first, so the fade smooths them too; then four influences at most
        if after_transfer: after_transfer()
        fade(mesh, arm, width, rounds, gate_rounds)
    else:
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


def audit(fbx, slug, out, bends=None):
    extra = []
    if bends:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, slug + "_clip_bends.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh: json.dump(bends, fh, indent=1)
        extra = ["-clip-bends", path]
    r = blender_run("audit.py", "-fbx", fbx, "-slug", slug, "-out", out, "-render", "0", *extra)
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

# Standing height (ground to the top of the head, metres) by kind (core/lessons.kind: the first word of the name),
# for a four-legged animal whose card and caller give none. Tripo's meshes come out about 1 m tall whatever they are,
# so a cat, a rabbit and a horse all stood a metre high in the game. Typical adults: a house cat 0.3, a cottontail
# 0.3 to its ear tips, a medium dog (shepherd, hound) 0.65 to the top of its head, a domestic pig and a sheep 0.9,
# a goat 0.95, a dairy cow 1.5, a horse 1.7. The card's metres_high wins, and the step writes back what it used.
ANIMAL_HEIGHT = {"cat": 0.3, "kitten": 0.2, "rabbit": 0.3, "hare": 0.4, "bunny": 0.3, "dog": 0.65, "puppy": 0.35,
                 "fox": 0.45, "wolf": 0.85, "pig": 0.9, "boar": 0.95, "sheep": 0.9, "lamb": 0.6, "goat": 0.95,
                 "deer": 1.4, "cow": 1.5, "bull": 1.6, "calf": 1.0, "horse": 1.7, "pony": 1.3, "donkey": 1.3}


def kind_height(*names):
    """ANIMAL_HEIGHT for the first of these names whose kind it knows, as (metres, kind), or (0, None)."""
    import lessons
    for n in names:
        k = lessons.kind(os.path.basename(n or ""))
        if k in ANIMAL_HEIGHT: return ANIMAL_HEIGHT[k], k
    return 0.0, None


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
    if not a.height and a.height_auto:
        a.height, k = kind_height(a.model, a.name, a.src)
        if k: log["height_from"] = "a %s's typical height (ANIMAL_HEIGHT)" % k
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
        meas.update(T.plan_measures(plan))          # feet, wings, flat faces, belly clearance (core/body_plan.py)
        if a.height: meas["metres"] = round(top, 3)     # a real height only: Tripo's come out ~1 m whatever they are
        r = tail_reach(pts)
        if r is not None: meas["tail_reach"] = r
        t, why = T.choose(arch, meas)
        log["choice"] = {"archetype": arch, "measures": meas, "why": why}
        if t is None:
            if a.model and not a.choose_only and not a.joints and a.classic:
                return classic_creature(a, log)
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
    if a.people == "classic" and a.model and not a.joints and not a.choose_only and is_person(a, t, log):
        return people_classic(a, log)
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
    hop = wants_hop(a, t) and HOP_SOURCE in names
    if hop:     # the walk is a hop made from the run (hop_from)
        walk = info["roles"].get("walk")
        wanted = [(HOP_NAME if w == walk else w, e) for w, e in wanted]
    load = [w for w, _ in wanted if w != HOP_NAME] + ([HOP_SOURCE] if hop else [])
    arm, rig_surface, acts = load_template(t, root, load, log)
    if hop and HOP_SOURCE in acts:
        acts[HOP_NAME], mirrored = hop_from(acts[HOP_SOURCE])
        log["gait"] = {"hop": "the walk is the %s with each pair of legs together" % HOP_SOURCE, "mirrored": len(mirrored)}
        if HOP_SOURCE not in [w for w, _ in wanted]:
            bpy.data.actions.remove(acts.pop(HOP_SOURCE))
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
    R = landmarks(rig_surface, feet_groups, leg_roots=t in FOUR_LEGGED)
    A = landmarks(animal, feet_groups, leg_roots=t in FOUR_LEGGED)
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
        heads, tails, rolls, k = fit_line(arm, mesh, log, info.get("head_at") or ("neck" if t == "snake" else None))
        scale, leg_of, found = k, {}, {"line": True}
        animal = [v.co.copy() for v in mesh.data.vertices]
    else:
        heads, tails, found, leg_of, tail_names, shape = fit_shape(arm, R, A, rig_surface, animal, fam,
                                                                   profile=t in FOUR_LEGGED)
        if fam == "spider":
            legs, leg_of = fit_spider_legs(mesh, arm, heads, tails, top, log)
            found.update(legs)
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
    limb_set = set(n for n in leg_of if fam in ("human", "spider") or bones[n].head_local.z < A["belly"])
    for b in bones:
        if (fam == "human" and b.name.startswith("upperarm_")) or (fam == "legged" and b.name.startswith("Front_Leg_Upper")):
            limb_set.add(b.name)
            limb_set.update(c.name for c in b.children_recursive)
    limb_set.discard(root_bone)
    feet = [n for n in leg_of if not bones[n].children and leg_of[n][0] >= 0]
    if a.swing is None:
        a.swing = auto_swing(A, R) if t in FOUR_LEGGED else 1.0
        log["swing_auto"] = a.swing
    # four-legged: the swing is eased on the template's own turns, before the legs are rebaked and the feet stood
    # where the template's stand, so the feet stand on the eased legs. Eased afterwards, on the rebaked legs, the
    # feet the rebake had stood on the ground left it: at 0.65 the pig, the sheep and the cows sank 8-11% of their
    # height into the floor and their planted feet slid (the quality score's sink 10%, skate 130; 2.5% and 70 at 1)
    early = t in FOUR_LEGGED and a.swing != 1.0
    if early: damp(actions, set(leg_of), a.swing)
    if t == "bird" and feet and a.limbs == "calibrated" and a.stride:
        log["stride_gain"] = stride_gain(arm, rig0, actions, limb_set, feet, k, root_bone, set(leg_of))
    if fam == "spider" and leg_of:
        legs = [[n for n in leg_of if leg_of[n] == key] for key in sorted({v for v in leg_of.values() if v[0] >= 0})]
        legs = [sorted(c, key=lambda n: len(up(bones[n]))) for c in legs]
        plant_legs(arm, rig0, actions, [c for c in legs if len(c) == 4], k, root_bone)
    elif fam in ("human", "legged") and limb_set:
        match_world(arm, rig0, actions, limb_set, feet, k, root_bone, calibrate=fam in ("legged", "human") and a.limbs == "calibrated")
    else:
        scale_travel(actions, k)
    rest3 = {b.name: b.matrix_local.to_3x3() for b in rig0.data.bones}
    bpy.data.objects.remove(rig0)
    if tail_names and fam == "legged": damp(actions, set(tail_names), a.tail_swing)
    if a.swing != 1.0 and not early: damp(actions, set(leg_of), a.swing)
    # wings on the ground: the template's walk and idle turn its wings from their spread rest, and the same turns
    # open a bird's folded wings out from her sides; off the ground (flap, glide, fly) they swing as made
    wings = {b.name for b in bones if b.name.lower().startswith(("wing_", "shoulder_"))}
    grounded = [act for act in actions if not re.search(r"fly|flap|glide", act.name, re.I)]
    if wings and grounded and a.wing_swing != 1.0: damp(grounded, wings, a.wing_swing)
    # folded wings in the air: a hen's wings are a patch of her side, and the template's flap (its wings turn 80
    # degrees and more) tore her flanks open; they flap as far as they reach out of the body
    if wings and t in ("bird", "dragon"):
        spread = max(abs(p.x) for p in animal) / max(top, 1e-6)
        log["wing_spread"] = round(spread, 3)
        if spread < WING_SPREAD and toes_point(animal):       # a bird standing on its toes (a moth's rise up)
            flying = [act for act in actions if act not in grounded]
            if flying: damp(flying, wings, a.folded_flap)
            log["folded_flap"] = a.folded_flap
    log["limbs_rebaked"] = len(limb_set) if fam in ("human", "legged", "spider") else 0
    bends, bends_raw = clip_bends(arm, actions)
    log["clip_bends"] = {"turns": {n: v for n, v in bends_raw.items() if v}, "tested_at": bends}

    # the skin
    voxel = a.voxel or top / (110.0 if fam == "human" else 70.0)
    def after():
        if fam in ("legged", "human", "spider"): log["side_fixed"] = side_rule(mesh, arm)   # each side's bones, its own skin
        if fam == "legged": log["tail_fixed"] = tail_rule(mesh, arm)                 # the tail moves only the tail
        if leg_of and fam in ("legged", "human"): log["confined"] = confine(mesh, arm, leg_of, shape, A["belly"])
    t_skin = time.time()
    if method == "m2m":
        # Mesh2Motion's own: closest bone midpoint, its correctors and smoothing, on the fitted joints as they are
        import m2m_skin_blender
        unreached, bare, off = m2m_skin_blender.skin(mesh, arm, t)
        gated, used = 0, None
    else:
        unreached, bare, off, gated, used = skin(mesh, arm, voxel, after, girdles=fam == "legged", smooth=a.smooth,
                                                 width=a.fade, rounds=a.fade_rounds, gate_rounds=fam == "body")
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
        log["audit"] = audit(tmp_fbx, a.name, os.path.join(a.out, "audit"), bends if a.clip_bends else None)
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
              "seconds": next((c["seconds"] for c in T.clips(t, root) if c["name"] == (HOP_SOURCE if w == HOP_NAME else w)), None)}
             for w, _ in wanted if w in acts]

    # rig.json, the card and the log
    m2m = {"template": t, "joints": joints, "axes": "gltf", "rest": rest, "clips": [c["m2m"] for c in clips], "skin": method,
           "naming": a.naming, "target": a.target, "swing": a.swing, "tail_swing": a.tail_swing}
    spec_dir = folder or a.out
    rel = (lambda p: os.path.relpath(p, spec_dir).replace(os.sep, "/"))
    merge_json(os.path.join(spec_dir, "rig.json"), {"schema": "autorig-spec/1",
                                                     "rig": {"kind": "m2m", "skeleton": log["choice"].get("archetype") or t,
                                                             "forward": [0, -1, 0],
                                                             "audit_clip_bends": bends if a.clip_bends else None},
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
        "rigged_blend": rel(blend), "preview": rel(out["preview"]) if "preview" in out else None,
        **({"metres_high": round(top, 3), "metres_high_from": log["height_from"]} if log.get("height_from") else {})})
    log.update(out=out, clips=clips, seconds=round(time.time() - t0, 1),
               triangles=sum(len(p.vertices) - 2 for p in mesh.data.polygons))
    with open(os.path.join(a.out, a.name + "_m2m.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(log, joints=joints), fh, indent=1, default=str)
    print("M2M_RIG " + json.dumps(log, default=str))
    return 0


def is_person(a, t, log):
    """Whether this goes to the humanoid rig. The Human tile (--template human) says so outright, and so does auto
    choosing the human template: that template never suits anything but a person here (a sword read as a short tail
    must not stop it: Heartroot's squire). choose() also picks it for "winged" with no tail standing tall like a
    person: a T-pose's arms and coat tails read as wings (the ticket taker stands 3.3; a hen or a rooster about 1)."""
    if "--out" in sys.argv: return False          # a scratch run (Fix skin's tries) never re-rigs the model's own folder
    if a.template.lower() == "human": return True
    if a.template.lower() != "auto": return False
    ch = log.get("choice") or {}
    m = ch.get("measures") or {}
    return T.is_person(t, ch.get("archetype"), m)


def host_python():
    """The Python that runs the workbench's own scripts beside Blender: python3, or on Windows python (its python3 is
    the Microsoft Store's stub, which only prints where to install Python)."""
    import shutil
    if os.name == "nt": return shutil.which("python") or shutil.which("py") or sys.executable
    return shutil.which("python3") or shutil.which("python") or sys.executable


def classic_steps(name, steps_wanted):
    """Runs run.py's steps for a model one after another; {step: exit code}, stopping when the rig fails."""
    import subprocess
    run_py = os.path.join(os.path.dirname(HERE), "cli", "run.py")
    steps = {}
    for step in steps_wanted:
        print("classic: %s %s" % (step, name), flush=True)
        r = subprocess.run([host_python(), run_py, step, name], capture_output=True, text=True)
        sys.stdout.write((r.stdout or "")[-4000:]); sys.stdout.flush()
        steps[step] = r.returncode
        if r.returncode != 0:
            sys.stdout.write((r.stderr or "")[-4000:])
            if step == "rig": break
    return steps


# rig.json kinds that are a classic rig's spec (rerig.py builds them)
CLASSIC_KINDS = ("build", "placed", "tripo", "rigid", "humanoid", "custom")


def classic_creature(a, log):
    """Auto on a body with no Mesh2Motion template (a squid, a jelly, a drone, a chest; choose() sends props and
    floaters here) used to stop with exit 3 and the page reported the rig as failed. It runs the classic rig instead:
    the model's own spec when it has one (a classic rig already built from it is kept: Heartroot's were measured by
    hand), else one suggest.py proposes, merged into its rig.json beside the card and budget; then the audit, the clips
    (their archetype read from the rig when the spec names none: spec_store.infer_clip_archetype) and the preview."""
    import layout, spec_store
    name = layout.leaf(a.model)
    folder = layout.pack_dir(a.model)
    path = os.path.join(folder, "rig.json")
    try: spec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError): spec = {}
    rig = spec.get("rig") or {}
    built = os.path.exists(os.path.join(layout.rigged_dir(a.model), name + ".blend"))
    classic = rig.get("kind") in CLASSIC_KINDS
    lifted = bool(spec_store.lifted_spec(name))
    log["classic"] = {"spec": "own" if classic else ("lifted" if lifted else "suggested"), "kept_rig": False}
    want = ["rig", "audit", "clips", "preview"]
    if classic and built and rig.get("kind") != "m2m":
        want = ["audit", "clips", "preview"]; log["classic"]["kept_rig"] = True
    elif lifted and built and not classic:
        want = ["clips", "preview"]; log["classic"]["kept_rig"] = True       # a lift's rig is its source's
    elif not classic:
        import watch_daemon
        tmp = os.path.join(layout.work_dir("suggest"), name + "_suggested_rig.json")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        arch = watch_daemon.suggest_spec(name, spec_file=tmp)
        if not arch:
            print("M2M_RIG " + json.dumps({"name": a.name, "template": None, "choice": log["choice"],
                                           "classic": "no spec could be suggested"}))
            return 1
        sug = json.load(open(tmp, encoding="utf-8"))
        spec.pop("m2m", None)
        for k, v in sug.items():
            if k == "rig" or k not in spec: spec[k] = v
        spec.setdefault("notes", {})["classic"] = ("Auto found no Mesh2Motion template (%s): the classic rig from a "
                                                   "suggested spec (m2m_rig.classic_creature)" % log["choice"]["why"])
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(spec, fh, indent=2)
        log["classic"]["archetype"] = arch
    steps = classic_steps(name, want)
    log.update(steps=steps)
    print("M2M_RIG " + json.dumps({"name": a.name, "template": None, "classic": log["classic"], "choice": log["choice"],
                                   "steps": steps}, default=str))
    return 0 if all(v == 0 for k, v in steps.items() if k in ("rig", "clips")) else 1


def people_classic(a, log):
    """A person (is_person) goes to the humanoid rig, not the human template. On Tornado Titans' cast the template's walk held
    the arms stiff and out and the jacket sleeves stayed on the body; the humanoid rig, resting in the A-pose as
    modelled with the arms mirrored, walked clean (2026-10-18). rig.json gets the humanoid sections if it has none
    (a rig.json made for the template is replaced), then the classic steps run: rig, audit, walker clips, preview."""
    import subprocess, shutil
    import layout
    folder = layout.pack_dir(a.model)
    path = os.path.join(folder, "rig.json")
    try: spec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError): spec = {}
    if (spec.get("rig") or {}).get("kind") != "humanoid":
        import rerig_humanoid
        # a humanoid section someone tuned (Smeltdown's player_blue: faces +Y, sculpted mid-stride) is kept; only
        # what is missing is filled in, and a rig made on the template (rig.m2m, the m2m section) is dropped
        spec.pop("m2m", None)
        spec["schema"] = "autorig-spec/1"
        rig = {k: v for k, v in (spec.get("rig") or {}).items() if k not in ("m2m", "audit_clip_bends")}  # template bones
        rig.update(kind="humanoid", skeleton="humanoid")
        rig.setdefault("clips", {"archetype": "walker"})
        spec["rig"] = rig
        if not isinstance(spec.get("humanoid"), dict) or not spec["humanoid"].get("z"):
            # "auto": the rig measures this person's heights and spans (humanoid_fit.auto_landmarks); the default's
            # numbers are only what is left when a part cannot be measured
            spec["humanoid"] = dict(json.loads(json.dumps(rerig_humanoid.DEFAULT_HUMANOID)), auto=True)
        spec.setdefault("clips", {"archetype": "walker"})
        spec.setdefault("notes", {})["people"] = ("Auto rig took it for a person: the humanoid rig, arms as modelled "
                                                  "and mirrored (m2m_rig --people classic)")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(spec, fh, indent=2)
    # on Windows "python3" is the Microsoft Store's stub (it exits 9009), so "python" there
    py = (shutil.which("python") if os.name == "nt" else None) or shutil.which("python3") or shutil.which("python")         or sys.executable
    run_py = os.path.join(os.path.dirname(HERE), "cli", "run.py")
    name = layout.leaf(a.model)
    steps = {}

    def run(step, skin=None, legs=None):
        print("people: %s %s%s" % (step, name, " (%s)" % (skin or legs) if (skin or legs) else ""), flush=True)
        env = dict(os.environ)
        if skin: env["AUTORIG_HUMANOID_SKIN"] = skin
        if legs: env["AUTORIG_HUMANOID_LEGS"] = legs
        r = subprocess.run([py, run_py, step, name], capture_output=True, text=True, env=env)
        sys.stdout.write((r.stdout or "")[-4000:]); sys.stdout.flush()
        steps[step] = r.returncode
        if r.returncode != 0:
            sys.stdout.write((r.stderr or "")[-4000:])
        return r.returncode == 0

    def score(tag):
        out = os.path.join(layout.work_dir("people_choice"), name, tag)
        shutil.rmtree(out, ignore_errors=True)
        subprocess.run([py, run_py, "score", name, "--out", out, "--audit", "skip", "--render", "0"],
                       capture_output=True, text=True)
        try: return float(json.load(open(os.path.join(out, name, "score.json"), encoding="utf-8"))["score"])
        except (OSError, ValueError, KeyError, TypeError): return -1.0

    # a person whose source came rigged and skinned (Tripo's converter) is rigged both ways, our skeleton and the
    # source's own rig kept, each clipped and scored (run.py score); the better stays. Neither always wins: Heartroot's
    # stoker scored 91.9 kept against 65.3 rebuilt, the hooded figure uploaded 2026-10-18 82.2 rebuilt against 65.6 kept
    #
    # and a sculpt that stood mid-stride (its legs turned more than 8 degrees to stand them straight: Smeltdown's
    # player_blue) is also rigged with its legs resting as sculpted; the scorer's rest check counts the straightening
    folder_now = layout.pack_dir(a.model)
    out_dirs = lambda: [d for d in (layout.rigged_dir(a.model), os.path.join(folder_now, "clips")) if os.path.isdir(d)]
    best = {"tag": None, "score": -1.0}

    def stash(tag):
        dst = os.path.join(layout.work_dir("people_choice"), name, tag + "_files")
        shutil.rmtree(dst, ignore_errors=True); os.makedirs(dst, exist_ok=True)
        for d in out_dirs(): shutil.copytree(d, os.path.join(dst, os.path.basename(d)))
        return dst

    def unstash(src):
        for d in (layout.rigged_dir(a.model), os.path.join(folder_now, "clips")):
            shutil.rmtree(d, ignore_errors=True)
            if os.path.isdir(os.path.join(src, os.path.basename(d))):
                shutil.copytree(os.path.join(src, os.path.basename(d)), d)

    def attempt(tag, skin=None, legs=None):
        ok = run("rig", skin, legs) and run("clips") and run("preview")
        sc = score(tag) if ok else -1.0
        scores[tag] = sc
        if sc > best["score"]:
            best.update(tag=tag, score=sc, files=stash(tag))

    scores = {}
    if run("rig", "rebuild"):
        try: qa = json.load(open(os.path.join(layout.work_dir("qa"), name + ".json"), encoding="utf-8"))
        except (OSError, ValueError): qa = {}
        kept, stride = qa.get("kept_possible"), float(qa.get("legs_turned_deg") or 0) > 8.0
        if kept or stride:
            attempt("rebuild", "rebuild")
            if kept: attempt("kept", "kept")
            if stride: attempt("legs_kept", "rebuild", "keep")
            if best["tag"] and best["tag"] != list(scores)[-1]:
                unstash(best["files"])                   # the best one's files back in place
            log["skin_scores"] = dict(scores, chosen=best["tag"])
            print("PEOPLE_CHOICE " + json.dumps(log["skin_scores"]), flush=True)
            run("audit")
        else:
            for step in ("audit", "clips", "preview"):
                run(step)
    log.update(people="classic", steps=steps)
    print("M2M_RIG " + json.dumps({"name": a.name, "template": "human", "people": "classic", "choice": log["choice"],
                                   "steps": steps}, default=str))
    return 0 if steps.get("rig") == 0 else 1


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

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the rig step. A new armature with named, connected, left/right bones, IK foot controls and
# fresh skin weights, from the model's spec (kind "tripo": reuse a Tripo-style skeleton's joints; kind "build":
# trace chains through the mesh). Sources are never touched; results go to <model>/rigged/<model>.blend and .fbx,
# and a bend-test picture to <AUTORIG_WORK>/qa/<model>.png.
#
#   blender -b --python autorig/steps/rerig.py -- [-only a,b] [-noExport] [-qa <dir>]
#
# Conventions of the result: the creature faces Blender's -Y (front view shows its face), its left is +X (.L bones),
# walkers stand on z=0 over the origin. Bones: root > hips > spine_N > neck > head, leg_front_N.L ..., tail_N,
# and ik_<leg> controls (not exported: the FBX carries deform bones only).
import bpy, sys, os, math, json, time
import numpy as np
from mathutils import Vector, Matrix, kdtree
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from spec_store import SPECS
from layout import ROOT, source_fbx, rigged_dir, leaf, work_dir
import source_io
import placed_rules

ROLE_COLOURS = {"tentacle": (0.8, 0.3, 0.6), "flipper": (0.0, 0.7, 0.75), "fluke": (0.95, 0.5, 0.05), "pod": (0.0, 0.7, 0.75),
                "wisp": (0.95, 0.6, 0.1), "flame": (0.9, 0.3, 0.05), "lid": (0.75, 0.1, 0.1), "tongue": (0.85, 0.1, 0.45),
                "lure": (0.95, 0.85, 0.1), "barbel": (0.6, 0.15, 0.75),"ear": (0.6, 0.15, 0.75), "jaw": (0.85, 0.1, 0.45), "claw": (0.75, 0.1, 0.1), "mandible": (0.85, 0.1, 0.45),
                "spine": (0.1, 0.35, 0.9), "leg": (0.1, 0.65, 0.2), "tail": (0.95, 0.5, 0.05), "head": (0.6, 0.15, 0.75),
                "ik": (0.95, 0.85, 0.1), "extra": (0.45, 0.45, 0.45), "fin": (0.0, 0.7, 0.75), "wing": (0.0, 0.7, 0.75)}

def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    def val(n): return a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    return val("-only"), os.path.abspath(val("-qa") or work_dir("qa")), "-noExport" in a

def select_only(*objs):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1]

def find_fbx(key, spec=None):
    return source_fbx(key)

# ---------------------------------------------------------------- loading

def load(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    source_io.import_source(path)
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    joints = {}
    if arms:
        a = arms[0]
        for b in a.data.bones:
            joints[b.name] = {"pos": a.matrix_world @ b.head_local, "parent": b.parent.name if b.parent else None}
    for m in meshes:
        mw = m.matrix_world.copy()
        m.parent = None
        for md in list(m.modifiers): m.modifiers.remove(md)
        m.matrix_world = Matrix.Identity(4)
        m.data.transform(mw)
    if len(meshes) > 1:
        select_only(*meshes); bpy.ops.object.join()
    mesh = bpy.context.view_layer.objects.active if len(meshes) > 1 else meshes[0]
    for o in list(bpy.data.objects):
        if o is not mesh: bpy.data.objects.remove(o, do_unlink=True)
    return mesh, joints

def bounds(mesh):
    n = len(mesh.data.vertices)
    co = np.empty(n * 3, dtype=np.float64); mesh.data.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    return Vector(co.min(axis=0)), Vector(co.max(axis=0))

def normalise(mesh, joints, spec):
    """Head to -Y, body centred on x=0, feet (or the body's middle) on the origin."""
    if spec["kind"] == "tripo" and not spec.get("forward"):
        d = joints[spec["head"]]["pos"] - joints[spec["hips"]]["pos"]
    else:
        d = Vector(spec.get("forward", (0, -1, 0)))
    turn = math.atan2(-1.0, 0.0) - math.atan2(d.y, d.x)
    if not spec.get("straighten"): turn = round(turn / (math.pi / 2)) * (math.pi / 2)
    R = Matrix.Rotation(turn, 4, 'Z')
    mesh.data.transform(R)
    for j in joints.values(): j["pos"] = R @ j["pos"]
    lo, hi = bounds(mesh)
    if spec.get("body") == "single":  # no spine to go by: the middle of where its limbs join the body
        tops = [joints[n]["pos"].x for n in spec.get("legs", []) if n in joints]
        cx = sum(tops) / len(tops) if tops else (lo.x + hi.x) * 0.5
    elif spec["kind"] == "tripo":
        cx = (joints[spec["head"]]["pos"].x + joints[spec["hips"]]["pos"].x) * 0.5
    else:
        cx = (lo.x + hi.x) * 0.5
    if spec.get("origin") == "center": off = Vector((-cx, -(lo.y + hi.y) * 0.5, -(lo.z + hi.z) * 0.5))
    else: off = Vector((-cx, -(lo.y + hi.y) * 0.5, -lo.z))
    T = Matrix.Translation(off)
    mesh.data.transform(T)
    for j in joints.values(): j["pos"] = T @ j["pos"]
    mesh.data.update()
    return round(math.degrees(turn), 1)

# ---------------------------------------------------------------- chains from a Tripo-style skeleton's joints

def repair(joints, spec, size):
    # Tripo-style skeletons sometimes stack two joints on one spot (a bone of no length): the child folds into its parent.
    alias = {}
    for n in list(joints):
        p = joints[n]["parent"]
        if p in joints and p != "bone_0" and (joints[n]["pos"] - joints[p]["pos"]).length < max(size) * 0.004:
            for c in joints.values():
                if c["parent"] == n: c["parent"] = p
            alias[n] = alias.get(p, p); joints.pop(n)
    fix = lambda n: alias.get(n, n)
    for k in ("head", "hips", "shell"):
        if spec.get(k): spec[k] = fix(spec[k])
    if spec.get("legs"): spec["legs"] = [fix(n) for n in spec["legs"]]
    if spec.get("chains"): spec["chains"] = {r: [fix(n) for n in tops] for r, tops in spec["chains"].items()}
    kids = {}
    for n, j in joints.items(): kids.setdefault(j["parent"], []).append(n)
    def drop(n):
        for c in kids.get(n, []): drop(c)
        joints.pop(n, None)
    for n in spec.get("delete", []):
        if n in joints: drop(n)
    for chain in spec.get("mirror", []):
        prev = joints[chain[0]]["parent"]
        for n in chain:
            p = joints[n]["pos"]
            joints[n + "_m"] = {"pos": Vector((-p.x, p.y, p.z)), "parent": prev}
            prev = n + "_m"
    # reparent={joint: new parent joint}: a chain the source hung from the wrong place. A moth's wing tails (its
    # streamers) hung from the hips, so a wingbeat tore the wing from its own tail; hung from the hind wing, they beat
    # with it. After mirror, so a mirrored joint (<joint>_m) can be named.
    for n, par in spec.get("reparent", {}).items():
        if n in joints and par in joints: joints[n]["parent"] = par

def move_joints(joints, spec, lo, size):
    """move={joint: [x, y, z]}: a source joint put where it belongs, in 0..1 of the turned model's bounds (measured
    with measure.py). A Tripo-style skeleton sometimes plants a joint outside the body: the Lurker's scapulae start
    on the spikes above its withers, next to the spine, so the girdles could not own the shoulder blades without
    also owning the back."""
    for n, u in spec.get("move", {}).items():
        if n in joints:
            joints[n]["pos"] = Vector(tuple(lo[k] + size[k] * u[k] for k in range(3)))

def tip_of(bvh, at, direction, lo_len, hi_len):
    """Where a chain's last bone should end: at the surface it points to, within sensible lengths."""
    d = direction.normalized() if direction.length > 1e-9 else Vector((0, 0, 1))
    hit = bvh.ray_cast(at + d * 1e-4, d, hi_len * 1.5)
    dist = hit[3] * 0.92 if hit[0] is not None else lo_len  # nothing ahead: the joint sits at the surface already
    return at + d * max(lo_len, min(hi_len, dist))

def tripo_chains(joints, spec, bvh, size, mesh):
    single = spec.get("body") == "single"
    legs_in = list(spec.get("legs", []))
    named_in = {top: role for role, tops in spec.get("chains", {}).items() for top in tops}
    if single:
        # A shell on legs: the generator hung every limb off its floor bone and left loose markers on the shell. One rigid
        # body bone at the middle of the mesh; the listed limbs hang off it; nothing else is kept.
        lo, hi = bounds(mesh)
        c = Vector((0.0, (lo.y + hi.y) * 0.5, lo.z + (hi.z - lo.z) * spec.get("body_height", 0.5)))
        joints["__body"] = {"pos": c, "parent": None}
        listed = set(legs_in) | set(named_in)
        def under_listed(n):
            p = joints[n]["parent"]
            while p in joints:
                if p in listed: return True
                p = joints[p]["parent"]
            return False
        for top in [t for t in listed if t in joints and not under_listed(t)]: joints[top]["parent"] = "__body"
        spec = dict(spec, hips="__body", head="__body")
    names = [n for n in joints if n != "bone_0"]
    adj = {n: set() for n in names}
    for n in names:
        p = joints[n]["parent"]
        if p in adj: adj[n].add(p); adj[p].add(n)
    hips, head = spec["hips"], spec["head"]
    def reach(s):
        seen, st = {s}, [s]
        while st:
            for c in adj[st.pop()]:
                if c not in seen: seen.add(c); st.append(c)
        return seen
    got = reach(hips)
    if not single:
        for n in names:  # whatever hung off the floor bone now hangs off the hips
            if n not in got and joints[n]["parent"] not in adj:
                adj[n].add(hips); adj[hips].add(n); got |= reach(n)
    par, kids, order = {hips: None}, {}, [hips]
    for n in order:
        for c in sorted(adj[n]):
            if c not in par: par[c] = n; kids.setdefault(n, []).append(c); order.append(c)
    P = lambda n: joints[n]["pos"]
    depth = {}
    def deep(n):
        depth[n] = 1 + max([deep(c) for c in kids.get(n, [])] or [0]); return depth[n]
    deep(hips)

    spine = [head]
    while spine[-1] != hips: spine.append(par[spine[-1]])
    spine.reverse()
    legs = [n for n in legs_in if n in par]
    named = {t: r for t, r in named_in.items() if t in par}
    height = size.z
    chains = []

    def guess(top, at):
        if top in legs: return "leg"
        if top in named: return named[top]
        end = top
        while kids.get(end): end = max(kids[end], key=lambda c: depth[c])
        if at == head or (len(spine) > 2 and at == spine[-2]):
            h = P(end) - P(head)
            if abs(P(end).x) > 0.06 * size.x and h.z > -0.05 * height: return "ear"
            if abs(P(end).x) <= 0.06 * size.x and h.z < 0: return "jaw"
            return "head_part"
        e = P(end) - P(hips)
        if e.y > 0.1 * size.y and abs(P(top).x) < 0.1 * size.x and depth[top] >= 2 and at in spine[:2]: return "tail"
        return "extra"

    def grow(top, role, parent_ref, at):
        path = [top]
        while True:  # a chain never runs on into a leg or another named chain
            nxt = [c for c in kids.get(path[-1], []) if c not in legs and c not in named]
            if not nxt: break
            path.append(max(nxt, key=lambda c: (depth[c], -(P(c) - P(path[-1])).length)))
        pts = [P(n).copy() for n in path]
        if len(path) > 1: d, plen = pts[-1] - pts[-2], (pts[-1] - pts[-2]).length
        else: d, plen = P(top) - P(at), max((P(top) - P(at)).length, height * 0.05)
        pts.append(tip_of(bvh, pts[-1], d, plen * 0.25, min(plen, height * 0.2)))
        ci = len(chains)
        chains.append({"role": role, "joints": path, "points": pts, "parent": parent_ref, "ik": role == "leg"})
        for i, n in enumerate(path):
            for c in kids.get(n, []):
                if i + 1 < len(path) and c == path[i + 1]: continue
                if c in legs: sub = "leg"
                elif c in named: sub = named[c]
                else:
                    sub = role + "_toe" if role == "leg" and i >= len(path) - 2 else role + "_b"
                    if depth[c] == 1 and role not in ("leg",): continue  # a stray end marker
                grow(c, sub, (ci, i), n)

    pts = [P(n).copy() for n in spine]
    if single: pts.append(pts[0] + Vector((0, -size.y * 0.3, 0)))
    else:
        d = pts[-1] - pts[-2] if len(pts) > 1 else Vector((0, -1, 0))
        pts.append(tip_of(bvh, pts[-1], d, d.length * 0.4, max(d.length, height * 0.25)))
    chains.append({"role": "spine", "joints": spine, "points": pts, "parent": None, "ik": False, "single": single})
    for i, n in enumerate(spine):
        for c in kids.get(n, []):
            if i + 1 < len(spine) and c == spine[i + 1]: continue
            role = guess(c, n)
            if role == "extra" and depth[c] == 1: continue
            grow(c, role, (0, i), n)

    # A tail the source skeleton gave no bones: the middle of the mesh, slice by slice, from where the rump ends to the tail's tip.
    t = spec.get("add_tail")
    if t and not any(c["role"] == "tail" for c in chains):
        co = np.array([v.co[:] for v in mesh.data.vertices])
        y0 = co[:, 1].min() + (co[:, 1].max() - co[:, 1].min()) * t["from"]
        y1 = co[:, 1].max()
        n = t.get("bones", 4)
        edges = [y0 + (y1 - y0) * k / n for k in range(n + 1)]
        tp = []
        for k in range(n + 1):
            a = edges[k] - (y1 - y0) / n * 0.5; b = edges[k] + (y1 - y0) / n * 0.5
            sel = co[(co[:, 1] >= a) & (co[:, 1] <= b) & (np.abs(co[:, 0]) < size.x * t.get("width", 0.5))]
            if t.get("above") is not None and edges[k] < y0 + size.y * t.get("clear", 0.1):  # past the hind legs nothing is in the way
                sel = sel[sel[:, 2] > co[:, 2].min() + size.z * t["above"]]
            if len(sel): tp.append(Vector(((sel[:, 0].min() + sel[:, 0].max()) / 2, edges[k], (sel[:, 2].min() + sel[:, 2].max()) / 2)))
        if len(tp) >= 2:
            chains.append({"role": "tail", "joints": [], "points": tp, "parent": (0, 0), "ik": False})
    return chains

# ---------------------------------------------------------------- chains traced through a mesh that has no skeleton

def build_chains(mesh, spec, size):
    import geo
    s = geo.Surface(mesh)
    co = np.array([p[:] for p in s.co])
    chains, by_name = [], {}

    def slice_centres(y0, y1, n, width, stations=None):
        """The middle of the body at n+1 stations along its length. Fins lie in the centre plane and are thin, so the
        body's height at a station is read from the vertices out at its sides. `stations` puts the joints where the
        body actually hinges (a neck, the back edge of a thorax) instead of evenly."""
        ys = list(stations) if stations else [y0 + (y1 - y0) * k / n for k in range(n + 1)]
        half = abs(y1 - y0) / n * 0.5
        out = []
        for y in ys:
            wy = s.lo.y + s.size.y * y
            sel = co[(np.abs(co[:, 1] - wy) <= s.size.y * half) & (np.abs(co[:, 0]) <= s.size.x * width)]
            if len(sel) == 0: out.append(None); continue
            reach = np.abs(sel[:, 0]).max()
            side = sel[np.abs(sel[:, 0]) >= reach * 0.35] if reach > s.size.x * 0.02 else sel
            zc = (side[:, 2].min() + side[:, 2].max()) * 0.5
            upper = sel[sel[:, 2] >= zc]
            xc = (upper[:, 0].min() + upper[:, 0].max()) * 0.5 if len(upper) else float(np.median(sel[:, 0]))
            out.append(Vector((xc, wy, zc)))
        known = [p for p in out if p is not None]
        if not known: return []
        out = [p if p is not None else known[-1].copy() for p in out]
        for _ in range(2):  # barnacles and fin roots jog the line: ease it, ends held
            out = [out[0]] + [Vector(((out[k - 1].x + 2 * out[k].x + out[k + 1].x) / 4, out[k].y, (out[k - 1].z + 2 * out[k].z + out[k + 1].z) / 4))
                              for k in range(1, len(out) - 1)] + [out[-1]]
        return out

    isl = islands_of(mesh)
    main = max(isl, key=len)
    island_of = {}
    for k, idx in enumerate(isl):
        for i in idx: island_of[i] = k
    mv = mesh.data.vertices
    main_set = set(main)
    main_polys = [tuple(p.vertices) for p in mesh.data.polygons if p.vertices[0] in main_set]
    main_bvh = BVHTree.FromPolygons([v.co.copy() for v in mv], main_polys) if main_polys else None

    def inside_main(p):
        loc, nrm, _, _ = main_bvh.find_nearest(p)
        return loc is not None and (p - loc).dot(nrm) < 0

    def loose_limb_root(tip):
        """Where a limb that is its own loose piece (an insect's legs, often) goes into the body.

        Such a limb is sculpted pushed into the body, its root hidden inside. Walk its centreline from the far end
        of the piece to the tip; the first station outside the main body is where the limb shows, and the joint."""
        ti = s.nearest(tip)
        k = island_of.get(ti)
        if main_bvh is None or k is None or isl[k] is main or len(isl[k]) < 30: return None
        idx = isl[k]
        d = s.distances([ti])
        far = max(idx, key=lambda i: d[i] if d[i] < float("inf") else -1)
        line = s.tube(mv[far].co, s.co[ti], 24)
        for p in line:
            if not inside_main(p): return p
        return None

    def seg_dist(p, a, b):
        ab = b - a; t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-12, ab.dot(ab)))); return (p - (a + ab * t)).length

    def on_polyline(pts, p):
        best = None
        for a, b in zip(pts[:-1], pts[1:]):
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-12, ab.dot(ab))))
            q = a + ab * t
            if best is None or (p - q).length < (p - best).length: best = q
        return best

    for c in spec["chains"]:
        n = c.get("bones", 1)
        pi = by_name[c["parent"][0]] if c.get("parent") else (0 if chains else None)
        if "slice" in c:
            pts = slice_centres(c["slice"][0], c["slice"][1], n, c.get("width", 0.5), c.get("stations"))
        elif "points" in c:
            pts = [s.point(p) for p in c["points"]]
            if c.get("snap_end"): pts[-1] = s.co[s.nearest(pts[-1])].copy()
            if len(pts) == 2 and n > 1: pts = [pts[0].lerp(pts[1], k / n) for k in range(n + 1)]
        elif "tube" in c:
            a, b = s.point(c["tube"][0]), s.point(c["tube"][1])
            pts = s.tube(a, b, n, first=s.point(c["first"]) if c.get("first") else None)
        else:  # a limb, known by where it ends
            tip = s.co[s.nearest(s.point(c["tip"]))].copy()
            # Rule C: a limb starts where it leaves the body. `base` is that point, measured (measure.py); the
            # old base_f fraction of the way from the spine is kept only for specs that have not been measured, and
            # it is what ran an insect's legs up through its shell.
            base = s.point(c["base"]) if c.get("base") else loose_limb_root(tip)
            if base is None:
                base = on_polyline(chains[pi]["points"], tip).lerp(tip, c.get("base_f", 0.45))
            pts = [base, tip] if n == 1 else s.tube(base, tip, n, first=base)
        if c.get("girdle") and pi is not None:
            # A shoulder or pelvis bone (index 0 of the chain) from the spine out to where the limb leaves the body:
            # the scapula a quadruped's front leg swings from, a humanoid's clavicle.
            pts = [on_polyline(chains[pi]["points"], pts[0])] + list(pts)
        pidx = c["parent"][1] if c.get("parent") else 0
        if pi is not None and pidx < 0: pidx += len(chains[pi]["points"]) - 1
        if pi is not None and c.get("parent_nearest"):
            # the parent bone is whichever body bone (spine, abdomen, head chains) the limb's root is nearest
            root_pt = pts[1] if c.get("girdle") else pts[0]
            cands = [(seg_dist(root_pt, ch_["points"][k], ch_["points"][k + 1]), j, k)
                     for j, ch_ in enumerate(chains) if j == 0 or ch_["role"] in ("spine", "abdomen", "head", "neck")
                     for k in range(len(ch_["points"]) - 1)]
            _, pi, pidx = min(cands)
            if c.get("girdle"): pts[0] = on_polyline(chains[pi]["points"], pts[1])
        ch = {"role": c.get("role", c["name"]), "joints": [], "points": pts, "ik": bool(c.get("ik")),
              "parent": None if pi is None else (pi, pidx), "girdle": bool(c.get("girdle")) and pi is not None,
              "centre": c.get("centre")}
        if c.get("names"): ch["bones"] = list(c["names"]); ch["base"] = c["name"]
        by_name.setdefault(c["name"], len(chains))
        chains.append(ch)
    chains[0]["role"] = "spine"
    return chains

# ---------------------------------------------------------------- naming

def name_chains(chains, size, girdles=(), neck=None):
    eps = size.x * 0.03
    for c in chains:
        x = c["points"][-2].x if len(c["points"]) > 1 else c["points"][0].x
        c["side"] = ".L" if x > eps else ".R" if x < -eps else ""
        # Rule D: a chain that stays near the centre plane is centred, whatever side a slightly crooked sculpt
        # leans it to (a tail named `tail_N.R`, a middle tendril `tentacle1_*.L`). A spec can say so.
        xs = [p.x for p in c["points"][1:]] or [c["points"][0].x]
        if c.get("centre") or (c.get("centre") is None and abs(sum(xs) / len(xs)) < size.x * 0.06
                               and max(abs(v) for v in xs) < size.x * 0.15):
            c["side"] = ""
    # legs: front/hind for a quadruped, numbered pairs otherwise
    for side in (".L", ".R", ""):
        ls = sorted([c for c in chains if c["role"] == "leg" and c["side"] == side], key=lambda c: c["points"][-2].y)
        for i, c in enumerate(ls):
            c["base"] = "leg" if len(ls) == 1 else ("leg_front", "leg_hind")[i] if len(ls) == 2 else "leg%d" % (i + 1)
    groups = {}
    for c in chains:
        if c["role"] in ("leg", "spine") or c.get("bones"): continue
        groups.setdefault((c["role"], c["side"], c["parent"][0] if c["parent"] else -1), []).append(c)
    taken = {}
    for (role, side, pc), cs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        prefix = role
        if role.endswith("_toe") or role.endswith("_b"):
            parent = chains[pc]
            prefix = parent.get("base", parent["role"]) + ("_toe" if role.endswith("_toe") else "_b")
        cs.sort(key=lambda c: c["points"][0].y)
        for c in cs:
            k = (prefix, side); taken[k] = taken.get(k, 0) + 1
            c["base"] = prefix if taken[k] == 1 and len(cs) == 1 else "%s%d" % (prefix, taken[k])
    for c in chains:
        # a source leg whose first bone is really the scapula (spec girdle=("leg_front",)): link 0, blended as a girdle
        if c.get("base") in girdles and len(c["points"]) - 1 >= 3: c["girdle"] = True
    for c in chains:
        n = len(c["points"]) - 1
        if c.get("bones"): continue
        if c["role"] == "spine":
            if n == 1: c["bones"] = ["body" if c.get("single") else "hips"]
            elif n == 2: c["bones"] = ["hips", "head"]
            elif n == 3: c["bones"] = ["hips", "spine_1", "head"]
            elif neck and neck > 1 and n - 1 - neck >= 1:
                # spec neck=k: the last k links before the head are the neck (a quadruped's long neck: SKELETONS.md
                # neck_1..k), so the card names them and a game that lowers the neck lowers all of it
                c["bones"] = ["hips"] + ["spine_%d" % (i + 1) for i in range(n - 2 - neck)] +                              ["neck_%d" % (i + 1) for i in range(neck)] + ["head"]
            else: c["bones"] = ["hips"] + ["spine_%d" % (i + 1) for i in range(n - 3)] + ["neck", "head"]
        elif c.get("girdle"):
            c["bones"] = ["%s_%d%s" % (c["base"], i, c["side"]) for i in range(n)]
        else:
            c["bones"] = [c["base"] + c["side"]] if n == 1 else ["%s_%d%s" % (c["base"], i + 1, c["side"]) for i in range(n)]
    used = {"root"}  # the armature's own root: a spine bone named root came out as root.001
    for c in chains:  # never two bones of one name
        for i, b in enumerate(c["bones"]):
            nb, k = b, 1
            while nb in used:
                k += 1; stem, suf = (b[:-2], b[-2:]) if b[-2:] in (".L", ".R") else (b, ""); nb = "%s_v%d%s" % (stem, k, suf)
            c["bones"][i] = nb; used.add(nb)

# ---------------------------------------------------------------- armature

def build_armature(key, chains, size):
    ad = bpy.data.armatures.new(key + "_rig")
    arm = bpy.data.objects.new(key + "_rig", ad)
    bpy.context.scene.collection.objects.link(arm)
    select_only(arm)
    bpy.ops.object.mode_set(mode='EDIT')
    eb = ad.edit_bones
    root = eb.new("root"); root.head = (0, 0, 0); root.tail = (0, -size.z * 0.3, 0); root.use_deform = False
    for c in chains:
        prev = None
        for i, bn in enumerate(c["bones"]):
            b = eb.new(bn)
            b.head, b.tail = c["points"][i], c["points"][i + 1]
            if (b.tail - b.head).length < 1e-4: b.tail = b.head + Vector((0, 0, size.z * 0.02))
            d = (b.tail - b.head).normalized()
            b.align_roll(Vector((0, -1, 0)) if abs(d.z) > 0.7 else Vector((0, 0, 1)))
            if prev is not None: b.parent = prev; b.use_connect = True
            elif c["parent"] is None: b.parent = root
            else:
                pc, pi = c["parent"]; b.parent = eb[chains[pc]["bones"][pi]]
            b.use_deform = c.get("deform", True)
            prev = b
    iks = []
    for c in chains:
        if not c.get("ik") or len(c["bones"]) < 2: continue
        n = len(c["bones"])
        foot = eb[c["bones"][n - 1]] if n - (1 if c.get("girdle") else 0) >= 3 else None
        ctl = eb.new("ik_" + c["base"] + c["side"])
        if foot is not None:
            ctl.head, ctl.tail, ctl.roll = foot.head, foot.tail, foot.roll
        else:
            last = eb[c["bones"][n - 1]]
            ctl.head = last.tail; ctl.tail = last.tail + (last.tail - last.head) * 0.5; ctl.roll = last.roll
        ctl.parent = root; ctl.use_deform = False
        iks.append((c, ctl.name))
    bpy.ops.object.mode_set(mode='OBJECT')
    deform, ctrl = ad.collections.new("Deform"), ad.collections.new("Controls")
    for b in ad.bones: (deform if b.use_deform else ctrl).assign(b)
    ad.display_type = 'OCTAHEDRAL'; arm.show_in_front = True
    return arm, iks

def add_ik(arm, iks):
    for c, ctl in iks:
        n = len(c["bones"])
        if n >= 3:
            pb = arm.pose.bones[c["bones"][n - 2]]; count = n - 1 - (1 if c.get("girdle") else 0)
            foot = arm.pose.bones[c["bones"][n - 1]]
            cr = foot.constraints.new('COPY_ROTATION'); cr.target = arm; cr.subtarget = ctl
        else:
            pb = arm.pose.bones[c["bones"][n - 1]]; count = n
        k = pb.constraints.new('IK'); k.target = arm; k.subtarget = ctl; k.chain_count = count; k.use_stretch = False

# ---------------------------------------------------------------- rules A and B: head, jaw, torso envelope

TORSO_ROLES = ("spine", "abdomen", "head", "neck")


def _seg(P, a, b):
    """Distance from each row of P to segment a-b, and where along it (0..1) the nearest point is."""
    ab = b - a; L2 = max(1e-12, float(ab @ ab))
    t = np.clip(((P - a) @ ab) / L2, 0.0, 1.0)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1), t


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def head_to_snout(chains, mesh, size, spec):
    """Rule A: a creature's head bone runs to the front of its face.

    A Tripo-style head joint sits a few centimetres from the base of the skull, so bone heat handed the skull, muzzle
    and ears to the neck and spine (one head bone owned 0.4% of the surface). Only for a head that points forward; an
    upright humanoid's head points up and is left alone. Returns how far the tip moved (fraction of size)."""
    if spec.get("head_to_snout") is False: return None
    for c in chains:
        if "head" not in c.get("bones", []): continue
        i = c["bones"].index("head")
        if i != len(c["bones"]) - 1 or i >= len(c["points"]) - 1: return None
        h, t = c["points"][i], c["points"][i + 1]
        d = t - h
        if d.length < 1e-9 or abs(d.normalized().z) > 0.7: return None
        co = np.array([v.co[:] for v in mesh.data.vertices])
        r = max(d.length * 2.5, max(size) * 0.12)
        near = co[np.linalg.norm(co - np.array(h[:]), axis=1) < r]
        near = near[np.abs(near[:, 2] - h.z) < r * 0.6]
        if not len(near): return None
        # along the head's own line, as far forward as the face goes: aiming at the front-most vertex instead tipped
        # the Dirt Creator's head 50 degrees up to its brow
        if d.y >= -1e-6: return None
        k = (float(near[:, 1].min()) + size.y * 0.02 - h.y) / d.y
        tip = h + d * k
        if k > 1.0 and tip.y < t.y - size.y * 0.01:
            c["points"][i + 1] = tip
            return round((tip - t).length / max(size), 3)
        return 0.0
    return None


def head_line(chains, mesh, spec):
    """Rule A for a source spine that climbs the neck to the top of the skull: head_line=(start, tip), in 0..1
    of the bounds, measured. The spine is cut at the joint nearest `start` (moved onto it) and the head bone runs from
    there to `tip`, the front of the face. Chains that hung from the cut-off joints hang from the head."""
    hl = spec.get("head_line")
    if not hl: return
    lo, hi = bounds(mesh)
    P = lambda u: Vector(tuple(lo[k] + (hi[k] - lo[k]) * u[k] for k in range(3)))
    start, tip = P(hl[0]), P(hl[1])
    sp = chains[0]
    k = min(range(1, len(sp["points"]) - 1), key=lambda i: (sp["points"][i] - start).length)
    sp["points"] = sp["points"][:k] + [start, tip]
    sp["joints"] = sp["joints"][:k + 1]
    for c in chains[1:]:
        if c["parent"] and c["parent"][0] == 0 and c["parent"][1] > k: c["parent"] = (0, k)


def add_jaw(chains, mesh, size, spec):
    """Rule A: a jaw where there is a mouth. spec jaw=dict(hinge=(x,y,z), tip=(x,y,z)) in 0..1 of the bounds, measured
    with measure.py. The jaw hangs off the head and is skinned by region in skin_jaw()."""
    j = spec.get("jaw")
    if not j or any(c.get("base") == "jaw" or c["role"] == "jaw" for c in chains): return
    lo, hi = bounds(mesh)
    P = lambda u: Vector(tuple(lo[k] + (hi[k] - lo[k]) * u[k] for k in range(3)))
    head = next((ci, c["bones"].index("head")) for ci, c in enumerate(chains) if "head" in c.get("bones", []))
    chains.append({"role": "jaw", "joints": [], "points": [P(j["hinge"]), P(j["tip"])], "ik": False,
                   "parent": head, "bones": ["jaw"], "base": "jaw", "side": ""})


def skin_jaw(mesh, arm, spec, size, log):
    """Everything under the mouth line and ahead of the hinge moves from the head (and neck) to the jaw, blended over
    a narrow band at the lips so a closed sculpt opens without a tear along the seam."""
    if "jaw" not in arm.data.bones or not isinstance(spec.get("jaw"), dict): return
    b = arm.data.bones["jaw"]
    hinge, tip = np.array(b.head_local[:]), np.array(b.tail_local[:])
    vg = mesh.vertex_groups
    donors = [g for g in vg if g.name == "head" or g.name == "neck" or g.name.startswith("neck_")]
    jg = vg.get("jaw") or vg.new(name="jaw")
    along = tip - hinge; L = float(np.linalg.norm(along)); ax = along / max(L, 1e-9)
    up = np.cross(ax, np.array((1.0, 0.0, 0.0))); up /= max(np.linalg.norm(up), 1e-9)
    if up[2] < 0: up = -up
    band = L * spec["jaw"].get("band", 0.08)
    lift = spec["jaw"].get("lift", 0.0) * L
    moved = 0
    for v in mesh.data.vertices:
        p = np.array(v.co[:]) - hinge
        s = float(p @ ax)
        if s < -0.1 * L or s > 1.25 * L: continue
        if abs(v.co.x - hinge[0]) > spec["jaw"].get("width", 0.5) * size.x: continue
        h = float(p @ up) - lift
        w = float(_smooth(band, -band, np.array(h))) * float(_smooth(-0.1 * L, 0.15 * L, np.array(s)))
        if w <= 0.01: continue
        took = 0.0
        for g in donors:
            try: x = g.weight(v.index)
            except RuntimeError: continue
            g.add([v.index], x * (1 - w), 'REPLACE'); took += x * w
        if took > 0: jg.add([v.index], took, 'ADD'); moved += 1
    log["jaw_verts"] = moved


def girdle_pass(mesh, arm, chains, spec, log):
    """With bone heat alone (no envelope), a short girdle bone inside the body - a clavicle - is given almost nothing,
    so raising an arm stretches the top of the back. Here each girdle takes a smooth share of the spine's weights
    around it, strongest toward the limb and fading over `girdle_reach` of its length, as Mixamo's Shoulder bones own
    the trapezius and the top of the shoulder blade. The limb's own weights are left alone."""
    girdles = [c["bones"][0] for c in chains if c.get("girdle")]
    if not girdles or spec.get("envelope", "root") == "full": return
    reach = spec.get("girdle_reach", 1.6)
    torso = {b for c in chains if c["role"] in TORSO_ROLES for b in c["bones"]}
    vg = mesh.vertex_groups
    n = len(mesh.data.vertices)
    P = np.empty(n * 3); mesh.data.vertices.foreach_get("co", P); P = P.reshape(n, 3)
    tidx = {g.index for g in vg if g.name in torso}
    moved = 0
    for gname in girdles:
        b = arm.data.bones[gname]
        H, T = np.array(b.head_local[:]), np.array(b.tail_local[:])
        Lg = float(np.linalg.norm(T - H))
        d, t = _seg(P, H, T)
        w = _smooth(reach * Lg, 0.4 * Lg, d) * _smooth(0.05, 0.7, t)
        gg = vg.get(gname) or vg.new(name=gname)
        for i in np.nonzero(w > 0.01)[0]:
            v = mesh.data.vertices[int(i)]
            take = 0.0
            for g in v.groups:
                if g.group in tidx and g.weight > 0:
                    x = g.weight * float(w[i]); vg[g.group].add([v.index], g.weight - x, 'REPLACE'); take += x
            if take > 0:
                gg.add([v.index], take, 'ADD'); moved += 1
    log["girdle_verts"] = moved


def root_mask(mesh, arm, chains, spec, log):
    """envelope="root": bone heat kept, but no limb reaches behind the plane where it starts.

    On a person bone heat is the smooth diffusion an armpit or a hip needs, and the envelope's capsules only add seams
    there; what bone heat does get wrong is the arm reaching across the back (one humanoid's LeftArm held 19% at the
    middle of its back). So only the root rule: each limb's weights fade in over the first `fade` of its first bone
    from its root plane, and what it gives up goes to the torso bones the vertex already had (the nearest torso bone
    when it had none)."""
    if spec.get("envelope", "root") != "root": return
    bones = {b.name: b for b in arm.data.bones if b.use_deform}
    vg = mesh.vertex_groups
    n = len(mesh.data.vertices)
    P = np.empty(n * 3); mesh.data.vertices.foreach_get("co", P); P = P.reshape(n, 3)
    names = [g.name for g in vg]
    W = np.zeros((n, len(names)))
    for v in mesh.data.vertices:
        for g in v.groups: W[v.index, g.group] = g.weight
    col = {nm: k for k, nm in enumerate(names)}
    torso_chains = {ci for ci, c in enumerate(chains) if c["role"] in TORSO_ROLES or ci == 0}
    girdles = {c["bones"][0] for c in chains if c.get("girdle")}
    torso = [col[b] for ci in torso_chains for b in chains[ci]["bones"] if b in col] + [col[g] for g in girdles if g in col]
    kids = {}
    for ci, c in enumerate(chains):
        if c["parent"] is not None: kids.setdefault(c["parent"][0], []).append(ci)
    def subtree(ci):
        out, st = [], [ci]
        while st:
            x = st.pop(); out += [b for b in chains[x]["bones"] if b in col and b not in girdles]; st += kids.get(x, [])
        return out
    TD = np.stack([_seg(P, np.array(bones[names[k]].head_local[:]), np.array(bones[names[k]].tail_local[:]))[0]
                   for k in torso], axis=1)
    freed_total = 0.0
    for ci, c in enumerate(chains):
        if ci in torso_chains or c["parent"] is None or c["parent"][0] not in torso_chains: continue
        pts = [np.array(p[:]) for p in (c["points"][1:] if c.get("girdle") else c["points"])]
        if len(pts) < 2: continue
        L1 = max(1e-9, float(np.linalg.norm(pts[1] - pts[0])))
        s = (P - pts[0]) @ ((pts[1] - pts[0]) / L1)
        w = _smooth(-0.1 * L1, L1 * c.get("fade", 1 / 3.0), s)
        sub = [col[b] for b in subtree(ci)]
        if not sub: continue
        limb = W[:, sub].sum(1)
        freed = limb * (1 - w)
        if not (freed > 1e-4).any(): continue
        W[:, sub] *= w[:, None]
        tw = W[:, torso].copy(); tsum = tw.sum(1)
        none = tsum < 1e-6
        tw[none, TD[none].argmin(1)] = 1.0; tsum[none] = 1.0
        W[:, torso] += tw / tsum[:, None] * freed[:, None]
        freed_total += float(freed.sum())
    for g in vg:
        g.remove(list(range(n)))
        colw = W[:, g.index]
        for i in np.nonzero(colw > 1e-4)[0]: g.add([int(i)], float(colw[i]), 'REPLACE')
    log["root_mask_freed"] = round(freed_total, 1)


def smooth_weights(mesh, passes, factor=0.5):
    """Each pass moves every vertex's weights halfway to the mean of its neighbours', then renormalises."""
    vg = mesh.vertex_groups
    n, G = len(mesh.data.vertices), len(vg)
    if not G or passes <= 0: return
    W = np.zeros((n, G))
    for v in mesh.data.vertices:
        for g in v.groups: W[v.index, g.group] = g.weight
    E = np.array([tuple(e.vertices) for e in mesh.data.edges])
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    for _ in range(passes):
        acc = np.zeros_like(W)
        np.add.at(acc, E[:, 0], W[E[:, 1]]); np.add.at(acc, E[:, 1], W[E[:, 0]])
        has = deg > 0
        W[has] = (1 - factor) * W[has] + factor * acc[has] / deg[has, None]
    W /= np.maximum(W.sum(1, keepdims=True), 1e-9)
    for g in vg:
        g.remove(list(range(n)))
        col = W[:, g.index]
        for i in np.nonzero(col > 1e-4)[0]: g.add([int(i)], float(col[i]), 'REPLACE')


def torso_weights(P, bones, names, col, torso_set, girdles, spec, S):
    """Weights along the torso by projection: each vertex to the torso bone beside it, split between neighbours at the
    plane bisecting their joint and blended across it by signed distance; girdles blended in by distance.
    Returns an (n, len(names)) matrix whose rows sum to 1 over the torso bones."""
    n = len(P)
    H = {nm: np.array(bones[nm].head_local[:]) for nm in names}
    T = {nm: np.array(bones[nm].tail_local[:]) for nm in names}
    # The spine bones (no girdles): a vertex belongs to the bone whose length it lies beside, bounded at each joint by
    # the plane bisecting that joint, so two bones meeting at an angle split the body along it as a hinge does; the
    # chain's two ends are open (below the hips, above the head). Outside every slab: the nearest bone. Plain
    # nearest-segment gave the top of the Dirt Creator's tall thorax to its head.
    tn = [b for b in names if b in torso_set and b not in girdles]
    TD, Tt = zip(*[_seg(P, H[b], T[b]) for b in tn])
    TD = np.stack(TD, axis=1); Tt = np.stack(Tt, axis=1)
    def unit(v): return v / max(1e-12, float(np.linalg.norm(v)))
    L = {b: max(1e-9, float(np.linalg.norm(T[b] - H[b]))) for b in tn}
    inslab = np.ones((n, len(tn)), bool)
    head_plane, tail_plane = {}, {}      # bone -> (normal, neighbour) for joints that continue the chain
    for k, b in enumerate(tn):
        db = unit(T[b] - H[b])
        par = bones[b].parent
        if par is not None and par.name in tn:
            joined = np.linalg.norm(T[par.name] - H[b]) < 1e-5 * S
            branch = np.linalg.norm(H[par.name] - H[b]) < 1e-5 * S   # starts where its parent starts (abdomen, body)
            nh = unit(db + unit(T[par.name] - H[par.name])) if joined else unit(db - unit(T[par.name] - H[par.name])) if branch else db
            if joined: head_plane[b] = (nh, par.name)
            inslab[:, k] &= (P - H[b]) @ nh >= -0.01 * S
        # a bone that another torso bone branches from at its head (an insect's body, the abdomen running the other
        # way from the same point) is closed there by the plane bisecting the V, not left open
        for c in bones[b].children:
            if c.name in tn and np.linalg.norm(H[c.name] - H[b]) < 1e-5 * S:
                inslab[:, k] &= (P - H[b]) @ unit(db - unit(T[c.name] - H[c.name])) >= -0.01 * S
        cont = [c.name for c in bones[b].children if c.name in tn]
        if cont:
            nt = db
            joined = [c for c in cont if np.linalg.norm(H[c] - T[b]) < 1e-5 * S]
            if len(joined) == 1:
                nt = unit(db + unit(T[joined[0]] - H[joined[0]])); tail_plane[b] = (nt, joined[0])
            inslab[:, k] &= (P - T[b]) @ nt <= 0.01 * S
    ti = np.where(inslab.any(1), np.where(inslab, TD, np.inf).argmin(1), TD.argmin(1))
    JB = spec.get("joint_blend", 0.4)  # how far either side of a joint it blends, as a share of the shorter bone
    torso_w = np.zeros((n, len(names)))
    for v in range(n):
        b = tn[ti[v]]; t = Tt[v, ti[v]]
        wv = {b: 1.0}
        par = bones[b].parent
        # Across a joint that continues the chain, blend by signed distance from the plane bisecting it: both sides
        # agree at the plane (50/50), so nothing splits there however far the vertex is from the spine. Blending by
        # where the vertex falls along each bone disagreed at the plane on a curved spine, and the players' backs tore.
        if b in head_plane:
            nh, p = head_plane[b]; band = JB * min(L[b], L[p])
            x = 1.0 - float(_smooth(-band, band, np.array((P[v] - H[b]) @ nh)))
            if x > 0: wv[p] = x; wv[b] -= x
        elif t < JB and par is not None and par.name in tn:  # a branch off a bone's head (the abdomen off the body)
            x = 0.5 * (1 - t / JB); wv[par.name] = x; wv[b] = 1 - x
        if b in tail_plane:
            nt, c = tail_plane[b]; band = JB * min(L[b], L[c])
            x = float(_smooth(-band, band, np.array((P[v] - T[b]) @ nt)))
            if x > 0: wv[c] = wv.get(c, 0.0) + x; wv[b] -= x
        elif t > 1 - JB:
            ch = [c.name for c in bones[b].children if c.name in tn]
            if ch:
                cn = min(ch, key=lambda x_: float(np.linalg.norm(P[v] - H[x_])))
                x = 0.5 * (t - (1 - JB)) / JB; wv[cn] = wv.get(cn, 0.0) + x; wv[b] -= x
        for nm, x in wv.items(): torso_w[v, col[nm]] += max(0.0, x)
    # Girdles (clavicles, scapulae) blend in smoothly by distance, strongest toward the limb end: a shrug lifts the
    # shoulder, not a slab of the back cut off at the girdle's reach.
    for g in girdles:
        Lg = max(1e-9, float(np.linalg.norm(T[g] - H[g])))
        d, t = _seg(P, H[g], T[g])
        gr = spec.get("girdle_blend", 0.9)   # how far round the girdle it reaches, as a share of its length
        w = _smooth(gr * Lg, 0.35 * gr * Lg, d) * _smooth(0.0, 0.7, t)
        torso_w *= (1 - w)[:, None]
        torso_w[:, col[g]] += w

    return torso_w


def envelope(mesh, arm, chains, spec, size, log):
    """Rule B: after bone heat, the torso belongs to the spine and each limb fades in from where it leaves the body.

    Bone heat gives a vertex to whatever bone it can see nearest, so a leg bone running under a shell took the shell
    (one insect's middle leg bones owned 8-9% of the surface each) and a leg root pulled the belly. Here:
      - the torso is the spine chain and anything named as body (abdomen, head, neck), plus girdle bones;
      - each limb is a capsule round its chain from its root, the radius measured from the limb itself;
      - a vertex in a limb's capsule takes that limb's own bone-heat weights, faded in over the first third of the
        limb's first bone; everything else is weighted along the torso by projection, blended across each joint.
    The loose-piece and shell passes run after this, as before. spec envelope=False turns it off."""
    if spec.get("envelope", "root") != "full": return
    bones = {b.name: b for b in arm.data.bones if b.use_deform}
    if not bones: return
    vg = mesh.vertex_groups
    n = len(mesh.data.vertices)
    P = np.empty(n * 3); mesh.data.vertices.foreach_get("co", P); P = P.reshape(n, 3)
    names = list(bones)
    col = {nm: k for k, nm in enumerate(names)}
    W = np.zeros((n, len(names)))
    gi = {g.index: col.get(g.name) for g in vg}
    for v in mesh.data.vertices:
        for g in v.groups:
            k = gi.get(g.group)
            if k is not None: W[v.index, k] += g.weight
    H = {nm: np.array(bones[nm].head_local[:]) for nm in names}
    T = {nm: np.array(bones[nm].tail_local[:]) for nm in names}
    S = max(size)

    torso_chains = {ci for ci, c in enumerate(chains) if c["role"] in TORSO_ROLES or ci == 0}
    skip = set(spec.get("envelope_skip", [])) | {"jaw"}
    torso = [b for ci in torso_chains for b in chains[ci]["bones"] if b in bones]
    girdles = [c["bones"][0] for c in chains if c.get("girdle") and c["bones"][0] in bones]
    torso_set = set(torso) | set(girdles)
    if not torso: return

    kids = {}
    for ci, c in enumerate(chains):
        if c["parent"] is not None: kids.setdefault(c["parent"][0], []).append(ci)

    def subtree(ci):
        out, st = [], [ci]
        while st:
            x = st.pop(); out += [b for b in chains[x]["bones"] if b in bones]; st += kids.get(x, [])
        return out
    limbs = [ci for ci, c in enumerate(chains) if ci not in torso_chains and c["parent"] is not None
             and c["parent"][0] in torso_chains and c.get("base") not in skip and c["role"] not in skip]
    limb_bones = {ci: [b for b in subtree(ci) if b not in girdles] for ci in limbs}

    D = np.stack([_seg(P, H[nm], T[nm])[0] for nm in names], axis=1)
    nearest = D.argmin(1)
    # A limb that is its own loose piece (an insect's legs, often) owns its piece and nothing else: no capsule reaching
    # out onto the shell it is pushed into.
    isl = islands_of(mesh)
    main_k = max(range(len(isl)), key=lambda k: len(isl[k]))
    island = np.zeros(n, int)
    for k, idx in enumerate(isl): island[idx] = k
    from mathutils import kdtree as _kd
    kd = _kd.KDTree(n)
    for i in range(n): kd.insert(P[i], i)
    kd.balance()

    limb_w = np.zeros(n); limb_of = np.full(n, -1); best_q = np.full(n, np.inf)
    report = {}
    for ci in limbs:
        c = chains[ci]
        pts = [np.array(p[:]) for p in c["points"]]
        if c.get("girdle"): pts = pts[1:]
        sub_idx = [col[b] for b in limb_bones[ci]]
        if len(pts) < 2 or not sub_idx: continue
        seglen = [max(1e-9, float(np.linalg.norm(b - a))) for a, b in zip(pts[:-1], pts[1:])]
        acc = np.concatenate([[0.0], np.cumsum(seglen)]); total = acc[-1]
        d = np.full(n, np.inf); s = np.zeros(n)
        for k in range(len(pts) - 1):
            dk, tk = _seg(P, pts[k], pts[k + 1])
            better = dk < d; d[better] = dk[better]; s[better] = acc[k] + tk[better] * seglen[k]
        # behind the root the projection clamps to 0: say how far behind instead, so the fade can start there
        ax0 = (pts[1] - pts[0]) / seglen[0]
        s = np.where(s <= 1e-9, np.minimum((P - pts[0]) @ ax0, 0.0), s)
        mine = np.isin(nearest, sub_idx) & (s > 0.3 * total)
        r = float(np.percentile(d[mine], 80)) if mine.sum() > 8 else S * 0.03
        # never wider than half the limb is long: a short mandible measured its radius off the face it sits on
        R = min(max(1.6 * r, S * 0.02), 0.45 * total) * spec.get("limb_radius", 1.0)
        L1 = seglen[0]
        fade = c.get("fade", 1 / 3.0)  # how much of the first bone the limb takes to fade in
        # the capsule, and past the root anything whose nearest bone is this limb's within three radii of it: the
        # spikes and claws a capsule sized on the limb's body leaves out, and which then tore off it
        # spike_reach: how far out, in capsule radii, a spike may be claimed (3 for the spiky insects; a smooth deep-chested
        # animal wants little, or its belly, nearer the elbow than the high spine, goes with the front leg)
        near_own = np.isin(nearest, sub_idx) & (d <= spec.get("spike_reach", 3.0) * R) & (s > 0)
        w = _smooth(-0.1 * L1, L1 * fade, s) * np.maximum(_smooth(R, 0.7 * R, d), near_own.astype(float))
        tk = island[kd.find(pts[-1])[1]]
        if tk != main_k and len(isl[tk]) >= 30:
            w = np.where(island == tk, _smooth(-0.1 * L1, L1 * fade, s), 0.0)
        q = d / R
        take = (w > 1e-6) & ((w > limb_w + 1e-6) | ((np.abs(w - limb_w) <= 1e-6) & (q < best_q)))
        limb_w[take] = w[take]; limb_of[take] = ci; best_q[take] = q[take]
        report[limb_bones[ci][0]] = round(R / S, 3)

    torso_w = torso_weights(P, bones, names, col, torso_set, girdles, spec, S)

    out = torso_w.copy()
    for ci in set(limb_of[limb_of >= 0].tolist()):
        m = limb_of == ci
        sub_idx = [col[b] for b in limb_bones[ci]]
        lw = W[np.ix_(m, sub_idx)]
        tot = lw.sum(1)
        weak = tot < 0.02
        if weak.any():  # bone heat gave the limb nothing here: the nearest of its own bones
            dd = D[np.ix_(m, sub_idx)][weak]
            fb = np.zeros_like(dd); fb[np.arange(len(dd)), dd.argmin(1)] = 1.0
            lw[weak] = fb; tot = lw.sum(1)
        lw = lw / tot[:, None]
        f = limb_w[m][:, None]
        blk = out[m] * (1 - f)
        blk[:, sub_idx] += lw * f
        out[m] = blk
    # bones in neither (a jaw, a skipped chain) keep what bone heat gave them
    covered = torso_set | {b for ci in limbs for b in limb_bones[ci]}
    other = [col[nm] for nm in names if nm not in covered]
    if other:
        keep = np.clip(W[:, other].sum(1), 0, 1)
        out *= (1 - keep)[:, None]
        out[:, other] += W[:, other]
    for nm in names:
        g = vg.get(nm) or vg.new(name=nm)
        g.remove(list(range(n)))
        colw = out[:, col[nm]]
        for i in np.nonzero(colw > 1e-4)[0]: g.add([int(i)], float(colw[i]), 'REPLACE')
    log["envelope"] = {"limbs": len(limbs), "limb_radius": report,
                       "torso_verts": int((limb_w < 1e-3).sum()),
                       "blended_verts": int(((limb_w > 1e-3) & (limb_w < 0.999)).sum())}

# ---------------------------------------------------------------- skin

def skinned(v): return any(g.weight > 1e-4 for g in v.groups)

def islands_of(mesh):
    n = len(mesh.data.vertices)
    parent = list(range(n))
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e in mesh.data.edges:
        a, b = find(e.vertices[0]), find(e.vertices[1])
        if a != b: parent[a] = b
    out = {}
    for i in range(n): out.setdefault(find(i), []).append(i)
    return list(out.values())

def skin(mesh, arm, chains, spec, size, log):
    height = size.z
    verts = mesh.data.vertices
    jmap = {j: b for c in chains for j, b in zip(c["joints"], c["bones"])} if spec["kind"] == "tripo" else {}
    if spec.get("rip_welds"):
        placed_rules.rip_welds_pass(mesh, arm, chains, spec, size, log)
        verts = mesh.data.vertices
    select_only(mesh, arm)
    mesh.vertex_groups.clear()
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    bare = [v.index for v in verts if not skinned(v)]
    log["unreached"] = len(bare)
    proxy = None
    if len(bare) > len(verts) * 0.05:
        # Bone heat gives up on a sculpt made of loose pieces: skin a watertight voxel copy and carry the weights over.
        for voxel in (0.016, 0.022, 0.012, 0.03):
            select_only(mesh); bpy.ops.object.duplicate()
            proxy = bpy.context.active_object
            bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
            for m in list(proxy.modifiers): proxy.modifiers.remove(m)
            proxy.vertex_groups.clear()
            proxy.data.remesh_voxel_size = max(size) * voxel; proxy.data.remesh_voxel_adaptivity = 0.0
            bpy.ops.object.voxel_remesh()
            select_only(proxy, arm); bpy.ops.object.parent_set(type='ARMATURE_AUTO')
            pbare = sum(1 for v in proxy.data.vertices if not skinned(v))
            log["proxy"] = {"voxel": voxel, "verts": len(proxy.data.vertices), "unreached": pbare}
            if pbare <= len(proxy.data.vertices) * 0.05: break
            bpy.data.objects.remove(proxy, do_unlink=True); proxy = None
        if proxy is None and len(bare) > len(verts) * 0.5:
            log["error"] = "bone heat failed, on voxel copies too"; return False
    if proxy is not None:
        mesh.vertex_groups.clear()
        for g in proxy.vertex_groups: mesh.vertex_groups.new(name=g.name)
        dt = mesh.modifiers.new("weights", 'DATA_TRANSFER')
        dt.object = proxy; dt.use_vert_data = True; dt.data_types_verts = {'VGROUP_WEIGHTS'}
        dt.vert_mapping = 'POLYINTERP_NEAREST'; dt.layers_vgroup_select_src = 'ALL'; dt.layers_vgroup_select_dst = 'NAME'
        select_only(mesh)
        while mesh.modifiers[0] != dt: bpy.ops.object.modifier_move_up(modifier=dt.name)
        bpy.ops.object.modifier_apply(modifier=dt.name)
        bpy.data.objects.remove(proxy, do_unlink=True)
        verts = mesh.data.vertices
        bare = [v.index for v in verts if not skinned(v)]
        log["unreached_after_proxy"] = len(bare)
    if bare:
        good = [v for v in verts if skinned(v)]
        kd = kdtree.KDTree(len(good))
        for i, v in enumerate(good): kd.insert(v.co, i)
        kd.balance()
        for vi in bare:
            _, gi, _ = kd.find(verts[vi].co)
            for g in good[gi].groups:
                if g.weight > 1e-4: mesh.vertex_groups[g.group].add([vi], g.weight, 'REPLACE')

    envelope(mesh, arm, chains, spec, size, log)
    root_mask(mesh, arm, chains, spec, log)
    girdle_pass(mesh, arm, chains, spec, log)
    skin_jaw(mesh, arm, spec, size, log)
    if spec.get("membranes"):
        placed_rules.membrane_pass(mesh, arm, chains, spec, size, log)
    if spec.get("parts"):
        placed_rules.parts_rules_pass(mesh, arm, chains, spec, size, log)
    if spec.get("blends"):
        placed_rules.blend_joins_pass(mesh, arm, chains, spec, size, log)
    if spec.get("rigid_islands"):
        placed_rules.rigid_islands_pass(mesh, arm, chains, spec, size, log)
    if spec.get("smooth"):
        # Bone heat on a thick body leaves patchy weights behind it (a humanoid's back, behind the chest); a few
        # smoothing passes even them out. Before the rigid-piece pass, so loose pieces still end up rigid.
        smooth_weights(mesh, int(spec["smooth"]))

    gname ={g.index: g.name for g in mesh.vertex_groups}
    to_body = set()
    body = jmap.get(spec.get("shell"), spec.get("shell")) if spec.get("shell") else None
    if body:
        # A rigid body on limbs: whatever the limbs' bones don't mostly own belongs to the body alone.
        limb_bones = {b for c in chains if c["role"] != "spine" for b in c["bones"]}
        for v in verts:
            if sum(g.weight for g in v.groups if gname.get(g.group) in limb_bones) < 0.5: to_body.add(v.index)
    body = body or chains[0]["bones"][0]
    leg_chains = [c for c in chains if c["role"] == "leg"]
    # A loose piece near a leg that the leg's bones took, but which isn't joined to the leg, goes back to the body.
    near = {}
    for e in mesh.data.edges:
        a, b = e.vertices; near.setdefault(a, []).append(b); near.setdefault(b, []).append(a)
    def to_segment(p, a, b):
        ab = b - a; t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-12, ab.dot(ab)))); return (p - (a + ab * t)).length
    strays = 0
    # With the envelope run, a leg only owns what lies along it, so this older pass (which snapped whatever a leg
    # took away from it onto the body bone, whole) has nothing left to fix and only undoes the torso's blending.
    for c in ([] if log.get("envelope") or spec.get("envelope", "root") == "root" else leg_chains):
        cn = set(c["bones"]) | {b for s in chains if s["parent"] and chains[s["parent"][0]] is c for b in s["bones"]}
        segs = list(zip(c["points"][:-1], c["points"][1:]))
        owned = {}
        for v in verts:
            if v.index in to_body: continue
            if sum(g.weight for g in v.groups if gname.get(g.group) in cn) >= 0.5:
                owned[v.index] = min(to_segment(v.co, a, b) for a, b in segs)
        if not owned: continue
        thick = sorted(owned.values())[len(owned) // 2]
        seen = {i for i, d in owned.items() if d <= thick * 1.5}
        stack = list(seen)
        while stack:
            for j in near.get(stack.pop(), []):
                if j in owned and j not in seen: seen.add(j); stack.append(j)
        loose = set(owned) - seen
        while loose:
            patch, stack = set(), [next(iter(loose))]
            while stack:
                i = stack.pop()
                if i in patch: continue
                patch.add(i); stack.extend(j for j in near.get(i, []) if j in loose and j not in patch)
            loose -= patch
            ds = sorted(owned[i] for i in patch)
            if ds[len(ds) // 2] > thick * 2.5: to_body |= patch; strays += len(patch)
    log["stray_verts"] = strays
    if to_body:
        sg = mesh.vertex_groups.get(body) or mesh.vertex_groups.new(name=body)
        for i in to_body:
            for g in list(verts[i].groups): mesh.vertex_groups[g.group].remove([i])
            sg.add([i], 1.0, 'REPLACE')
    log["body_verts"] = len(to_body)

    # A small loose piece (a buckle, a mushroom, a lamp) moves as one: every vertex takes the piece's mean weights.
    rigid = 0
    isl = islands_of(mesh)
    biggest = max(len(i) for i in isl)
    limit = spec.get("rigid_pieces", 0.12)
    soft = {b for c in chains if c.get("base") in spec.get("soft", []) or c["role"] in spec.get("soft", []) for b in c["bones"]}
    gname = {g.index: g.name for g in mesh.vertex_groups}
    # rigid_to=[(bone, (x0, y0, z0), (x1, y1, z1)), ...]: a loose piece whose middle lies in the box (0..1 of the
    # model's bounds, as the chains' points) rides that bone whole. A fish's teeth, for one: bone heat gave the
    # upper row to a nearby chain's root and some to a fin, and they hung in the open mouth when those moved.
    boxes = spec.get("rigid_to", [])
    lo3 = Vector([min(v.co[k] for v in verts) for k in range(3)])
    span3 = Vector([max(1e-9, max(v.co[k] for v in verts) - lo3[k]) for k in range(3)])
    for idx in isl:
        if len(idx) == biggest and limit < 1.0: continue
        cs = [verts[i].co for i in idx]
        ext = Vector((max(p.x for p in cs) - min(p.x for p in cs), max(p.y for p in cs) - min(p.y for p in cs), max(p.z for p in cs) - min(p.z for p in cs)))
        if ext.length > max(size) * limit: continue
        mid = sum(cs, Vector()) / len(cs)
        n = Vector([(mid[k] - lo3[k]) / span3[k] for k in range(3)])
        boxed = next((b for b, a, z in boxes if all(a[k] <= n[k] < z[k] for k in range(3))), None)
        if boxed and mesh.vertex_groups.get(boxed):
            for i in idx:
                for g in list(verts[i].groups): mesh.vertex_groups[g.group].remove([i])
            mesh.vertex_groups[boxed].add(idx, 1.0, 'REPLACE')
            rigid += 1
            continue
        acc = {}
        for i in idx:
            for g in verts[i].groups: acc[g.group] = acc.get(g.group, 0.0) + g.weight
        top = sorted(acc.items(), key=lambda kv: -kv[1])[:2]
        if not top or top[0][1] <= 0: continue
        if gname.get(top[0][0]) in soft: continue
        # a tooth, a rivet, a whole lid: one bone. Anything larger that two bones share rides both.
        if spec.get("rigid_single") or ext.length < max(size) * 0.1 or top[0][1] >= 0.6 * sum(acc.values()): top = top[:1]
        tot = sum(w for _, w in top)
        for i in idx:
            for g in list(verts[i].groups): mesh.vertex_groups[g.group].remove([i])
        for gi, w in top: mesh.vertex_groups[gi].add(idx, w / tot, 'REPLACE')
        rigid += 1
    log["rigid_pieces"] = rigid

    if spec.get("rigid_parts"):
        # A machine made of parts (a drone with pods): the main piece rides the body whole, every other piece rides
        # whichever bone it sits nearest, whole. Before, bone heat split the dome into quadrants among the four pods
        # (74% of the shell), and tilting a thruster warped the dome.
        isl = islands_of(mesh)
        main_i = max(range(len(isl)), key=lambda k: len(isl[k]))
        dbones = [b for b in arm.data.bones if b.use_deform]
        body_name = chains[0]["bones"][0]
        def seg(p, a, b):
            ab = b - a; t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-12, ab.dot(ab)))); return (p - (a + ab * t)).length
        # parts=[{"bone", "at": [x, y, z], "verts": n}, ...]: the loose piece of n vertices whose bounds centre is
        # nearest `at` (0..1 of the bounds, as the chains' points) rides that bone. A machine's moving part is often
        # one piece inside another (a fan's rotor in its duct: their centres all but coincide), so nearest-bone
        # guesses the duct as readily as the rotor. With rigid_parts "listed", every piece not listed rides the body.
        listed = {}
        lo3 = Vector([min(v.co[k] for v in verts) for k in range(3)])
        span3 = Vector([max(1e-9, max(v.co[k] for v in verts) - lo3[k]) for k in range(3)])
        def centre(idx):
            cs = [verts[i].co for i in idx]
            return Vector([((min(p[k] for p in cs) + max(p[k] for p in cs)) * 0.5 - lo3[k]) / span3[k] for k in range(3)])
        for part in spec.get("parts", []):
            cands = [k for k, idx in enumerate(isl) if abs(len(idx) - part["verts"]) <= max(2, 0.02 * part["verts"])]
            if not cands: log.setdefault("parts_missing", []).append(part["bone"]); continue
            k = min(cands, key=lambda k: (centre(isl[k]) - Vector(part["at"])).length)
            listed[k] = part["bone"]
        only_listed = spec.get("rigid_parts") == "listed"
        for k, idx in enumerate(isl):
            if k in listed: owner = listed[k]
            elif k == main_i or only_listed: owner = body_name
            else:
                mid = sum((verts[i].co for i in idx), Vector()) / len(idx)
                owner = min(dbones, key=lambda b: seg(mid, b.head_local, b.tail_local)).name
            g = mesh.vertex_groups.get(owner) or mesh.vertex_groups.new(name=owner)
            for i in idx:
                for ge in list(verts[i].groups): mesh.vertex_groups[ge.group].remove([i])
            g.add(idx, 1.0, 'REPLACE')
        log["rigid_parts"] = len(isl)
        if listed: log["parts"] = {b: sum(1 for v in listed.values() if v == b) for b in set(listed.values())}

    hs = spec.get("hard_split")
    if hs:  # a lid: everything above a height belongs to one bone, everything below to the other
        lo_z = min(v.co.z for v in verts)
        cut = lo_z + size.z * hs["above"]
        up = mesh.vertex_groups.get(hs["bone"]) or mesh.vertex_groups.new(name=hs["bone"])
        down = mesh.vertex_groups.get(hs["else"]) or mesh.vertex_groups.new(name=hs["else"])
        for v in verts:
            for g in list(v.groups): mesh.vertex_groups[g.group].remove([v.index])
            (up if v.co.z > cut else down).add([v.index], 1.0, 'REPLACE')
    select_only(mesh)
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    names = {g.index: g.name for g in mesh.vertex_groups}
    used = {names[g.group] for v in mesh.data.vertices for g in v.groups if g.weight > 1e-4}
    deform = [b.name for b in arm.data.bones if b.use_deform]
    log.update(verts=len(mesh.data.vertices), bones=len(arm.data.bones), deform_bones=len(deform),
               bones_without_skin=[b for b in deform if b not in used])
    return True

# ---------------------------------------------------------------- pictures

def sticks(chains, iks_arm, thickness):
    obs = []
    for c in chains:
        verts, faces = [], []
        for h, t in zip(c["points"][:-1], c["points"][1:]):
            d = t - h
            if d.length < 1e-9: continue
            z = d.normalized(); x = z.orthogonal().normalized(); y = z.cross(x)
            base = h + d * 0.15; i = len(verts)
            verts += [h, base + x * thickness, base + y * thickness, base - x * thickness, base - y * thickness, t]
            for k in range(4):
                a, b = i + 1 + k, i + 1 + (k + 1) % 4
                faces += [(i, b, a), (i + 5, a, b)]
        role = c["role"].split("_")[0]
        me = bpy.data.meshes.new("s"); me.from_pydata([tuple(v) for v in verts], [], faces)
        ob = bpy.data.objects.new("qa_sticks", me); bpy.context.scene.collection.objects.link(ob)
        mat = bpy.data.materials.new("s"); mat.diffuse_color = (*ROLE_COLOURS.get(role, ROLE_COLOURS["extra"]), 1.0); me.materials.append(mat)
        obs.append(ob)
    return obs

def qa_pictures(key, mesh, arm, chains, size, out_dir, log):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = scene.render.resolution_y = 520
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    sh = scene.display.shading
    sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'; sh.show_cavity = True; sh.cavity_type = 'BOTH'; sh.show_object_outline = True
    for s in mesh.material_slots:
        if s.material: s.material.diffuse_color = (0.7, 0.72, 0.78, 1.0)
    cd = bpy.data.cameras.new("qa_cam"); cd.type = 'ORTHO'; cd.clip_start = 0.0001
    cam = bpy.data.objects.new("qa_cam", cd); scene.collection.objects.link(cam); scene.camera = cam
    arm.hide_render = True
    lo, hi = bounds(mesh)
    centre = (lo + hi) * 0.5; ext = max(hi - lo) * 1.18; dist = ext * 4 + 1
    cd.ortho_scale = ext; cd.clip_end = dist * 3
    st = sticks(chains, arm, max(size) * 0.011)
    def shot(direction, tag, with_sticks):
        d = Vector(direction).normalized()
        cam.location = centre + d * dist
        cam.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
        def grab(name):
            fp = os.path.join(out_dir, "_tmp_%s_%s.png" % (key, name)); scene.render.filepath = fp
            bpy.ops.render.render(write_still=True)
            img = bpy.data.images.load(fp); w, h = img.size
            px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
            bpy.data.images.remove(img); os.remove(fp); return px
        for o in st: o.hide_render = True
        mesh.hide_render = False
        body = grab(tag + "m")
        tile = np.ones_like(body); tile[..., :3] = 0.97
        a = body[..., 3:4] * (0.55 if with_sticks else 1.0)
        tile[..., :3] = tile[..., :3] * (1 - a) + body[..., :3] * a
        if with_sticks:
            for o in st: o.hide_render = False
            mesh.hide_render = True
            sk = grab(tag + "s"); a = sk[..., 3:4]
            tile[..., :3] = tile[..., :3] * (1 - a) + sk[..., :3] * a
        tile[:, :2, :3] = 0.55
        return tile
    tiles = [shot((1, 0, 0), "rs", True), shot((0, 0, 1), "rt", True), shot((0.35, -1, 0.3), "rq", True)]

    # the bend test: feet by their IK controls, everything else turned bone by bone
    select_only(arm); bpy.ops.object.mode_set(mode='POSE')
    h = size.z
    def turn(pb, axis, deg):
        p = (arm.matrix_world @ pb.matrix).to_translation()
        pb.matrix = arm.matrix_world.inverted() @ Matrix.Translation(p) @ Matrix.Rotation(math.radians(deg), 4, axis) @ Matrix.Translation(-p) @ arm.matrix_world @ pb.matrix
        bpy.context.view_layer.update()
    for c in chains:
        role = c["role"]
        if c.get("ik") and len(c["bones"]) >= 2:
            ctl = arm.pose.bones["ik_" + c["base"] + c["side"]]
            front = c["points"][0].y < 0
            fwd = (c["side"] == ".L") == front
            delta = Vector((0, -h * 0.17, h * 0.05)) if fwd else Vector((0, h * 0.15, 0))
            ctl.matrix = Matrix.Translation(delta) @ ctl.matrix
            bpy.context.view_layer.update()
        elif role == "spine":
            for bn in c["bones"]:
                if bn in ("hips", "body"): continue
                turn(arm.pose.bones[bn], Vector((0, 0, 1)), 18 if bn == "head" or bn.startswith("neck") else 6)
            if "head" in c["bones"]: turn(arm.pose.bones["head"], Vector((1, 0, 0)), 14)
        elif role == "tail":
            for bn in c["bones"]: turn(arm.pose.bones[bn], Vector((0, 0, 1)), 14)
        elif role == "lid":
            turn(arm.pose.bones[c["bones"][0]], Vector((1, 0, 0)), -30)
        elif role == "jaw":
            turn(arm.pose.bones[c["bones"][0]], Vector((1, 0, 0)), 22)
        elif not role.endswith("_toe"):
            for bn in c["bones"]: turn(arm.pose.bones[bn], Vector((0, 1, 0)), 14 if c["side"] != ".R" else -14)
    bpy.ops.object.mode_set(mode='OBJECT')
    posed = [shot((1, 0, 0), "ps", False), shot((-1, 0, 0), "po", False), shot((-0.8, -1, 0.55), "pq", False)]
    sheet = np.concatenate([np.concatenate(posed, axis=1), np.concatenate(tiles, axis=1)], axis=0)  # image rows run bottom-up
    hh, ww = sheet.shape[:2]
    im = bpy.data.images.new("sheet", width=ww, height=hh, alpha=True)
    im.pixels = sheet.ravel().tolist()
    im.filepath_raw = os.path.join(out_dir, key + ".png"); im.file_format = 'PNG'; im.save()

# ---------------------------------------------------------------- saving

def save_blend(path, log):
    """The whole scene as a .blend, texture paths relative to it, no .blend1 copy beside it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
    try: bpy.ops.file.make_paths_relative(); bpy.ops.wm.save_mainfile()
    except Exception as e: log["paths_note"] = repr(e)

def export_rig_fbx(path, mesh, arm):
    """Mesh and deform bones for an engine: no controls, no animation, Y up, facing +Z."""
    select_only(mesh, arm)
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={'ARMATURE', 'MESH'},
                             apply_scale_options='FBX_SCALE_UNITS', bake_space_transform=True, axis_forward='-Z', axis_up='Y',
                             add_leaf_bones=False, use_armature_deform_only=True, bake_anim=False,
                             mesh_smooth_type='OFF', path_mode='AUTO', embed_textures=False)

def save_rig(key, mesh, arm, log):
    """<rig folder>/<model>.blend (to animate) and .fbx (for the engine)."""
    out = rigged_dir(key)
    blend, fbx = os.path.join(out, leaf(key) + ".blend"), os.path.join(out, leaf(key) + ".fbx")
    save_blend(blend, log)
    export_rig_fbx(fbx, mesh, arm)
    log["saved"] = [os.path.relpath(blend, ROOT), os.path.relpath(fbx, ROOT)]

# ---------------------------------------------------------------- one model

def rerig(key, spec, qa_dir, export):
    t0 = time.time()
    spec = dict(spec)
    log = {"model": key, "kind": spec["kind"]}
    path = find_fbx(key, spec)
    if not path: log["error"] = "no fbx"; return log
    mesh, joints = load(path)
    mesh.name = key
    log["turned_deg"] = normalise(mesh, joints, spec)
    lo, hi = bounds(mesh); size = hi - lo
    me = mesh.data
    bvh = BVHTree.FromPolygons([v.co.copy() for v in me.vertices], [tuple(p.vertices) for p in me.polygons])
    if spec["kind"] == "tripo":
        move_joints(joints, spec, lo, size)
        repair(joints, spec, size)
        chains = tripo_chains(joints, spec, bvh, size, mesh)
    elif spec["kind"] in ("build", "placed"):
        chains = build_chains(mesh, spec, size)
    else:
        log["error"] = "unknown kind: " + spec["kind"]; return log
    head_line(chains, mesh, spec)
    name_chains(chains, size, spec.get("girdle", ()), spec.get("neck"))
    log["head_extended"] = head_to_snout(chains, mesh, size, spec) if not spec.get("head_line") else "head_line"
    add_jaw(chains, mesh, size, spec)
    jmap = {j: b for c in chains for j, b in zip(c["joints"], c["bones"])}
    dead = {jmap[j] for j in spec.get("nodeform", []) if j in jmap}
    arm, iks = build_armature(key, chains, size)
    for bn in dead: arm.data.bones[bn].use_deform = False
    if not skin(mesh, arm, chains, spec, size, log): return log
    add_ik(arm, iks)

    # does the rig, with its IK switched on, still stand exactly as sculpted?
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg)
    shift = max(((ev.data.vertices[i].co - mesh.data.vertices[i].co).length for i in range(len(mesh.data.vertices))), default=0.0)
    log["rest_shift"] = round(shift / max(size), 5)
    log["chains"] = [{"bones": c["bones"], "from": c["joints"]} for c in chains]
    import skeletons
    log["skeleton"] = skeletons.describe(chains, spec.get("skeleton"))

    if export: save_rig(key, mesh, arm, log)
    os.makedirs(qa_dir, exist_ok=True)
    try: qa_pictures(key, mesh, arm, chains, size, qa_dir, log)
    except Exception as e: log["qa_error"] = repr(e)
    log["seconds"] = round(time.time() - t0, 1)
    return log

def main():
    only, qa, no_export = args()
    wanted = only.split(",") if only else list(SPECS)
    os.makedirs(qa, exist_ok=True)
    done = 0
    for k in wanted:
        if k not in SPECS: print("RERIG", k, "has no spec"); continue
        if SPECS[k]["kind"] == "humanoid": print("RERIG", k, "is a humanoid: use rerig_humanoid.py"); continue
        if SPECS[k]["kind"] == "custom": print("RERIG", k, "is built by", SPECS[k]["builder"]); continue
        try: r = rerig(k, SPECS[k], qa, not no_export)
        except Exception as e:
            import traceback; traceback.print_exc()
            r = {"model": k, "error": repr(e)}
        json.dump(r, open(os.path.join(qa, k + ".json"), "w"), indent=1, default=str)
        print("RERIG", json.dumps({x: r[x] for x in r if x != "chains"}, default=str))
        done += 0 if r.get("error") else 1
    print("RERIG_DONE", done, "of", len(wanted))

if __name__ == "__main__":
    main()

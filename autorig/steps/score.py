# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: measure one rigged, animated model for the quality score (cli/score_all.py, docs/QUALITY.md).
#
#   blender -b --python autorig/steps/score.py -- --glb rigged/preview.glb --source Cow.glb --out <dir> --name Cow
#           [--render 1] [--tiles 6] [--max-frames 40] [--speed walk=1.2 ...]
#
# Writes <out>/measure.json and (unless --render 0) <out>/sheet_<clip>.png, a side row over a front row per clip and
# for the rest pose. Numbers only; core/quality_score.py turns them into the score.
#
# Every clip starts from the same state: the NLA tracks muted, every pose bone's matrix_basis reset and the objects at
# their rest matrices, then the clip's action set. Without that a clip's renders and numbers carry the last clip's
# pose (or the importer's active take) and lie.
import argparse, json, math, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from rig_quality import coords
import foot_contact
import quality_score as QS
from clip_quality import foot_ends, foot_points, published_speeds

A = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--glb", required=True); ap.add_argument("--source"); ap.add_argument("--out", required=True)
ap.add_argument("--name", default="model"); ap.add_argument("--render", default="1")
ap.add_argument("--tiles", type=int, default=6); ap.add_argument("--max-frames", type=int, default=24)
ap.add_argument("--speed", action="append", default=[])
a = ap.parse_args(A)
os.makedirs(a.out, exist_ok=True)


def log(*x): print("SCORE", *x, flush=True)


def import_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)
    elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj": bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path) as (src, dst): dst.objects = src.objects
        for o in dst.objects:
            if o is not None: bpy.context.scene.collection.objects.link(o)
    else: raise ValueError("cannot import " + path)


def world_mesh(objs):
    """(points, triangles) of meshes as evaluated now, in world space, concatenated."""
    pts, tris, off = [], [], 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        ev = o.evaluated_get(dg); m = ev.to_mesh()
        p = np.empty(len(m.vertices) * 3); m.vertices.foreach_get("co", p); p = p.reshape(-1, 3)
        mw = np.asarray(ev.matrix_world); pts.append(p @ mw[:3, :3].T + mw[:3, 3])
        m.calc_loop_triangles()
        t = np.empty(len(m.loop_triangles) * 3, dtype=np.int64); m.loop_triangles.foreach_get("vertices", t)
        tris.append(t.reshape(-1, 3) + off); off += len(m.vertices)
        ev.to_mesh_clear()
    return np.concatenate(pts) if pts else np.zeros((0, 3)), np.concatenate(tris) if tris else np.zeros((0, 3), int)


# ---------------------------------------------------------------- the source mesh
source_pts = source_tris = None
if a.source and os.path.exists(a.source):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        import_any(a.source)
        for o in bpy.context.scene.objects:
            if o.type == "ARMATURE": o.data.pose_position = "REST"
            if o.animation_data: o.animation_data.action = None
        bpy.context.view_layer.update()
        shapes = {b.custom_shape for o in bpy.context.scene.objects if o.type == "ARMATURE" for b in o.pose.bones if b.custom_shape}
        src_obs = [o for o in bpy.context.scene.objects if o.type == "MESH" and o not in shapes]
        source_pts, source_tris = world_mesh(src_obs)
        log("source", os.path.basename(a.source), len(source_pts), "vertices")
    except Exception as e:
        log("source not read:", e); source_pts = None

# ---------------------------------------------------------------- the rigged model
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.glb, merge_vertices=False)
scene = bpy.context.scene
arms = [o for o in scene.objects if o.type == "ARMATURE"]
helpers = {b.custom_shape for arm in arms for b in arm.pose.bones if b.custom_shape}
obs = [o for o in scene.objects if o.type == "MESH" and o not in helpers]
fps = scene.render.fps
object_rest = {o: o.matrix_basis.copy() for o in scene.objects}

owners = [o for o in scene.objects if o.animation_data]
owners += [o.data.shape_keys for o in obs if o.data.shape_keys and o.data.shape_keys.animation_data]
takes = {}
for owner in owners:
    ad = owner.animation_data; ad.action = None; ad.use_nla = False
    for tr in ad.nla_tracks:
        tr.is_solo = False; tr.mute = True
        if tr.strips: takes.setdefault(tr.name, []).append((owner, tr))


def reset_pose():
    for o, m in object_rest.items(): o.matrix_basis = m
    for arm in arms:
        arm.data.pose_position = "POSE"
        for b in arm.pose.bones: b.matrix_basis.identity()
    for owner in owners:
        owner.animation_data.action = None
    for o in obs:
        if o.data.shape_keys:
            for k in o.data.shape_keys.key_blocks[1:]: k.value = 0
    scene.frame_set(0); bpy.context.view_layer.update()


reset_pose()
rest, tris = world_mesh(obs)
lo, hi = rest.min(0), rest.max(0)
height = max(float(hi[2] - lo[2]), 1e-9)
size = max(float((hi - lo).max()), 1e-9)
ground = float(lo[2])
log("rigged", len(obs), "meshes", len(rest), "vertices", len(takes), "clips", "height %.3f" % height)

# per-vertex dominant bone and its body part
bones = [(b.name, b.parent.name if b.parent else None) for arm in arms for b in arm.data.bones]
part = QS.parts(bones)
dominant = []
for o in obs:
    names = {g.index: g.name for g in o.vertex_groups}
    for v in o.data.vertices:
        g = max(v.groups, key=lambda g: g.weight) if len(v.groups) else None
        dominant.append(names.get(g.group, "") if g is not None and g.weight > 0 else "")
dominant = np.array(dominant, dtype=object)
info = [part.get(d, ("other", None, False, None)) for d in dominant]
vpart = np.array([i[0] for i in info]); vside = np.array([i[1] or "" for i in info])
vdist = np.array([i[2] for i in info]); vend = np.array([i[3] or "" for i in info])

# edges (unique, from the triangles) and their rest lengths
e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
edges = np.unique(e, axis=0)
rest_len = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
live = rest_len > 1e-4 * size                      # micro edges where sculpt surfaces meet say nothing


# ---------------------------------------------------------------- rest vs source
def rest_vs_source():
    if source_pts is None or not len(source_pts) or not len(source_tris): return None

    def norm(p):
        l, h = p.min(0), p.max(0); s = max(float(h[2] - l[2]), 1e-9)
        c = np.array([(l[0] + h[0]) / 2, (l[1] + h[1]) / 2, l[2]])
        return (p - c) / s
    r = norm(rest); sp = norm(source_pts)
    rng = np.random.default_rng(1)
    probe0 = r if len(r) <= 20000 else r[rng.choice(len(r), 20000, replace=False)]
    tree = BVHTree.FromPolygons([tuple(x) for x in sp], [tuple(int(i) for i in t) for t in source_tris], all_triangles=True)

    def turned(p, deg):
        """The rig's points turned by deg about Z and re-centred on their own footprint: the rig may face any way
        the source does not (m2m_rig turns a model to face -Y; the megalodon is modelled heading 43 degrees off)."""
        c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        q = p @ np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]]).T
        l, h = q.min(0), q.max(0)
        return q - np.array([(l[0] + h[0]) / 2, (l[1] + h[1]) / 2, 0.0])

    sub0 = probe0[:: max(1, len(probe0) // 1500)]

    def cost(deg):
        return float(np.mean([(tree.find_nearest(Vector(p))[3] or 0.0) for p in turned(sub0, deg)]))
    deg = min(range(0, 360, 15), key=cost)            # every 15 degrees, then to the nearest degree
    for step in (5, 2, 1):
        deg = min((deg - step, deg, deg + step), key=cost)
    probe = turned(probe0, deg)
    d = np.array([(tree.find_nearest(Vector(p))[3] or 0.0) for p in probe]) * 100
    # the other way: source surface the rig lost or moved away from
    kd = KDTree(len(probe))
    for i, p in enumerate(probe): kd.insert(Vector(p), i)
    kd.balance()
    back_probe = sp if len(sp) <= 20000 else sp[rng.choice(len(sp), 20000, replace=False)]
    back = np.array([kd.find(Vector(p))[2] for p in back_probe]) * 100
    return {"mean_pct": round(float(d.mean()), 3), "p99_pct": round(float(np.percentile(d, 99)), 3),
            "max_pct": round(float(d.max()), 3), "source_p99_pct": round(float(np.percentile(back, 99)), 3),
            "turn_degrees": int(deg % 360), "source_vertices": int(len(source_pts))}


rest_report = rest_vs_source()
log("rest vs source", rest_report)


# ---------------------------------------------------------------- self-intersection proxies
def probe_sets():
    """[(name, probe vertex indices, target vertex indices, check, target key)]: each forearm and hand against the
    body without its arms, and each leg against the body without that leg. The target is the rest of the body, not
    a single part: a part cut out of the mesh is open where it was cut, and a point near the cut has no inside."""
    sets = []
    every = np.arange(len(rest))
    no_arms = every[vpart != "arm"]
    for s in ("L", "R"):
        arm = np.where((vpart == "arm") & vdist & (vside == s))[0]
        if len(arm) >= 10 and len(no_arms) >= 30: sets.append(("arm_" + s, arm, no_arms, "arm_in_torso_pct", "no_arms"))
    for end in sorted(set(vend[vpart == "leg"])):
        for s in ("L", "R"):
            leg = (vpart == "leg") & (vside == s) & (vend == end)
            probe = np.where(leg & vdist)[0]
            other = (vpart == "leg") & (vside == ("R" if s == "L" else "L")) & (vend == end)
            if len(probe) >= 10 and other.sum() >= 10:
                sets.append(("leg%s_%s" % (end and "_" + end, s), probe, every[~leg], "leg_cross_pct", "no_leg%s_%s" % (end, s)))
    return sets


SETS = probe_sets()
DEPTH = 0.01 * height


PROBE_MAX = 1500
REACH = 0.06 * height          # how far from the body's surface a point is still judged in or out


def target_tris(target):
    """The triangles wholly inside a part (renumbered over its own vertices), and how far into it a point can be
    (half its thinnest rest extent)."""
    keep = np.full(len(rest), -1); keep[target] = np.arange(len(target))
    t = keep[tris[(keep[tris] >= 0).all(axis=1)]]
    ext = np.ptp(rest[target], axis=0)
    # which way the part's faces point: outward when they face away from its middle on average (a mesh wound the
    # other way, or a transform that mirrors it, would otherwise put everything near it "inside")
    q = rest[target]
    n = np.cross(q[t[:, 1]] - q[t[:, 0]], q[t[:, 2]] - q[t[:, 0]])
    c = (q[t[:, 0]] + q[t[:, 1]] + q[t[:, 2]]) / 3 - q.mean(axis=0)
    out = float(np.sum(n * c)) >= 0
    return [tuple(int(i) for i in f) for f in t], min(0.5 * float(ext.min()), REACH), 1.0 if out else -1.0


TARGETS = {}


def inside(p, probe, target, key, tree=None):
    """Which probe vertices are inside the target part: the nearest point on the part's surface faces away from them
    by more than DEPTH, and they are no deeper than the part is thick (past that the nearest surface is the far side
    of an open part, not a sign of being in it)."""
    if key not in TARGETS: TARGETS[key] = target_tris(target)
    t, reach, sign = TARGETS[key]
    out = np.zeros(len(probe), bool)
    if not t: return out
    if tree is None: tree = BVHTree.FromPolygons([tuple(x) for x in p[target]], t, all_triangles=True)
    for k, i in enumerate(probe):
        loc, nor, _, dist = tree.find_nearest(Vector(p[i]), reach)
        if loc is None: continue
        out[k] = sign * (Vector(p[i]) - loc).dot(nor) < -DEPTH
    return out


def thin(idx):
    return idx if len(idx) <= PROBE_MAX else idx[np.linspace(0, len(idx) - 1, PROBE_MAX).astype(int)]


SETS = [(n, thin(pr), tg, k, key) for n, pr, tg, k, key in SETS]
HIT_FRAMES = 8                  # frames a clip is tested for limbs inside the body (a BVH of the body each)
def inside_all(p):
    """{set name: inside flags} for one pose, one BVH per target."""
    trees, out = {}, {}
    for name, pr, tg, _, key in SETS:
        if key not in TARGETS: TARGETS[key] = target_tris(tg)
        if key not in trees and TARGETS[key][0]:
            trees[key] = BVHTree.FromPolygons([tuple(x) for x in p[tg]], TARGETS[key][0], all_triangles=True)
        out[name] = inside(p, pr, tg, key, trees.get(key)) if key in trees else np.zeros(len(pr), bool)
    return out


CLEAR = 0.03 * height


def clear_of_body():
    """Drop the probe vertices within CLEAR of the rest of the body at rest: where a limb joins the body its skin moves
    with the body's, and that is the joint deforming, not the limb going in."""
    global SETS
    kept = []
    for name, pr, tg, chk, key in SETS:
        if key not in TARGETS: TARGETS[key] = target_tris(tg)
        t = TARGETS[key][0]
        if not t: continue
        tree = BVHTree.FromPolygons([tuple(x) for x in rest[tg]], t, all_triangles=True)
        far = np.array([tree.find_nearest(Vector(rest[i]), CLEAR)[0] is None for i in pr], bool)
        if far.sum() >= 10: kept.append((name, pr[far], tg, chk, key))
    SETS = kept


clear_of_body()
rest_inside = inside_all(rest)
log("part facing", {k: v[2] for k, v in TARGETS.items()})
log("intersection sets (probe vertices clear of the body)", [(s[0], len(s[1]), len(s[2])) for s in SETS])


# ---------------------------------------------------------------- feet
ends = foot_points(foot_ends(arms), obs, rest)
foot_floor = min((float(rest[i, 2].mean()) for i in ends.values()), default=None)
speeds = published_speeds(a.glb)
for item in a.speed:
    c, _, v = item.partition("="); speeds[c] = float(v)


# ---------------------------------------------------------------- contact sheets
def setup_render():
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"; scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x = scene.render.resolution_y = 260
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("score_world"); world.color = (0.55, 0.58, 0.62); scene.world = world
    scene.render.image_settings.file_format = "PNG"
    for arm in arms: arm.hide_render = True
    cd = bpy.data.cameras.new("score_cam"); cd.type = "ORTHO"; cd.ortho_scale = size * 1.35
    cam = bpy.data.objects.new("score_cam", cd); scene.collection.objects.link(cam); scene.camera = cam
    bpy.ops.mesh.primitive_plane_add(size=size * 6, location=((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, ground))
    return cam


VIEWS = {"side": Vector((1, 0, 0)), "front": Vector((0, -1, 0))}


def shoot(cam, frames, path):
    W = H = scene.render.resolution_x
    tmp = tempfile.mkdtemp()
    sheet = np.ones((H * 2, W * len(frames), 4), np.float32)
    cen = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
    for vi, (vn, d) in enumerate(VIEWS.items()):
        cam.location = cen + d * size * 4
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        for fi, f in enumerate(frames):
            if f is not None: scene.frame_set(f)
            fp = os.path.join(tmp, "%d_%d.png" % (vi, fi)); scene.render.filepath = fp
            bpy.ops.render.render(write_still=True)
            im = bpy.data.images.load(fp)
            px = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)
            bpy.data.images.remove(im)
            row = (1 - vi) * H                      # images are stored bottom row first: side ends up on top
            sheet[row:row + H, fi * W:(fi + 1) * W] = px[:H, :W]
    img = bpy.data.images.new("sheet", sheet.shape[1], sheet.shape[0])
    img.pixels.foreach_set(sheet.ravel())
    img.filepath_raw = path; img.file_format = "PNG"; img.save()
    bpy.data.images.remove(img)


RENDER = a.render == "1"
cam = None
if RENDER:
    try:
        cam = setup_render()
        reset_pose(); shoot(cam, [None], os.path.join(a.out, "sheet_rest.png"))
    except Exception as ex:
        log("render failed:", ex); RENDER = False


# ---------------------------------------------------------------- clips
def owner_of(i):
    return dominant[i]


clips = []
for name, bindings in sorted(takes.items()):
    reset_pose()
    for owner, tr in bindings:
        st = tr.strips[0]; owner.animation_data.action = st.action
        if getattr(st, "action_slot", None): owner.animation_data.action_slot = st.action_slot
    start = int(math.floor(min(st.action_frame_start for _, tr in bindings for st in tr.strips)))
    end = int(math.ceil(max(st.action_frame_end for _, tr in bindings for st in tr.strips)))
    step = max(1, math.ceil((end - start + 1) / a.max_frames))
    frames = list(range(start, end + 1, step))
    ratio_max = np.ones(len(edges)); ratio_min = np.ones(len(edges)); worst_frame = np.full(len(edges), start)
    gap_max = np.zeros(len(edges))
    ins = {s[0]: np.zeros(len(s[1]), bool) for s in SETS}
    hit_frames = set(frames[int(round(i * (len(frames) - 1) / max(HIT_FRAMES - 1, 1)))] for i in range(min(HIT_FRAMES, len(frames))))
    tracks = {k: [] for k in ends}; low = float("inf")
    for f in frames:
        scene.frame_set(f); bpy.context.view_layer.update()
        p, _ = world_mesh(obs)
        ln = np.linalg.norm(p[edges[:, 0]] - p[edges[:, 1]], axis=1)
        r = np.divide(ln, rest_len, out=np.ones_like(ln), where=live)
        up = r > ratio_max; worst_frame[up] = f
        ratio_max = np.maximum(ratio_max, r); ratio_min = np.minimum(ratio_min, r)
        gap_max = np.maximum(gap_max, ln - rest_len)
        low = min(low, float(p[:, 2].min()))
        if f in hit_frames:
            for sname, flags in inside_all(p).items(): ins[sname] |= flags & ~rest_inside[sname]
        for k, idx in ends.items(): tracks[k].append(tuple(float(x) for x in p[idx].mean(axis=0)))
    stretched = live & (ratio_max > 3) & (gap_max > .004 * size)
    squashed = live & (ratio_min < 1 / 3) & (rest_len > .001 * size)
    order = np.argsort(-np.where(live, ratio_max, 0))[:5]
    worst_edges = [{"ratio": round(float(ratio_max[i]), 2), "frame": int(worst_frame[i]),
                    "bones": sorted({owner_of(int(edges[i, 0])), owner_of(int(edges[i, 1]))}),
                    "at": [round(float(x), 3) for x in rest[edges[i, 0]]]} for i in order if ratio_max[i] > 1.5]
    per_set = {s[0]: round(100.0 * float(ins[s[0]].mean()), 2) for s in SETS}
    entry = {"name": name, "frames": len(frames), "range": [start, end],
             "stretch3_edges": int(stretched.sum()),
             "stretch3_permille": round(1000.0 * float(stretched.sum()) / max(int(live.sum()), 1), 3),
             "max_stretch": round(float(ratio_max[live].max(initial=1)), 3),
             "squash_edges": int(squashed.sum()),
             "squash_permille": round(1000.0 * float(squashed.sum()) / max(int(live.sum()), 1), 3),
             "worst_edges": worst_edges, "intersections": per_set,
             "arm_in_torso_pct": max([v for k, v in per_set.items() if k.startswith("arm")], default=None),
             "leg_cross_pct": max([v for k, v in per_set.items() if k.startswith("leg")], default=None),
             "sink_pct": round(max(0.0, ground - low) / height * 100, 3)}
    # feet: skate (in the ground's frame under a travelling clip) and whether they reach the floor at all
    if ends and len(frames) > 1:
        if not foot_contact.travels(name): vel = (0.0, 0.0, 0.0)
        elif name in speeds and arms:
            back = arms[0].matrix_world.to_3x3() @ Vector((0.0, 1.0, 0.0)); back.z = 0.0
            back = back.normalized() * speeds[name] * step / fps if back.length > 1e-9 else back
            vel = (back.x, back.y, 0.0)
        else: vel = None
        sk = foot_contact.foot_skate(tracks, size, fps=fps / step, velocity=vel, floor=foot_floor)
        entry["foot_skate"] = round(float(sk["score"]), 3)
        entry["foot_skate_ratio"] = round(float(sk["ratio"]), 3)
        entry["ground_speed"] = round(float(sk.get("ground_speed") or 0), 4)
        entry["foot_tracks"] = {k: [[round(x, 4) for x in pt] for pt in t] for k, t in tracks.items()}
        if foot_contact.travels(name) and foot_floor is not None:
            lows = [min(pt[2] for pt in t) - foot_floor for t in tracks.values() if t]
            entry["hover_pct"] = round(max(0.0, min(lows)) / height * 100, 3) if lows else None
    clips.append(entry)
    log("clip", json.dumps({k: v for k, v in entry.items() if k != "worst_edges"}))
    if RENDER:
        try:
            shots = [frames[int(round(i * (len(frames) - 1) / max(a.tiles - 1, 1)))] for i in range(a.tiles)]
            shoot(cam, shots, os.path.join(a.out, "sheet_%s.png" % "".join(c if c.isalnum() or c in "-_" else "_" for c in name)))
        except Exception as ex:
            log("render failed:", ex)
    for owner, tr in bindings: owner.animation_data.action = None

report = {"format": "autorig-score-measure/1", "model": a.name, "glb": os.path.abspath(a.glb), "source": a.source,
          "vertices": int(len(rest)), "edges": int(live.sum()), "height": round(height, 4), "bones": len(bones),
          "parts": {p: int((vpart == p).sum()) for p in sorted(set(vpart))}, "feet": sorted(ends),
          "rest": rest_report, "clips": clips, "rendered": RENDER}
with open(os.path.join(a.out, "measure.json"), "w", encoding="utf-8") as fh: json.dump(report, fh, indent=1)
log("DONE", a.name)

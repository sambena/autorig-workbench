# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the rig audit, the pipeline's QA step. Measures one rigged FBX and grades it PASS, CHECK or FAIL.
#
#   blender -b --python autorig/steps/audit.py -- -model wolf               the model's rigged/<model>.fbx
#   blender -b --python autorig/steps/audit.py -- -fbx <path> -slug <name>  any FBX
#   options: -out <dir> (default <AUTORIG_WORK>/audit)   -render 0 (numbers only, no sheets)
#   python autorig/cli/audit_all.py wolf,moth ...                           several, one Blender each, and a table
#   python autorig/cli/audit_all.py all                                     every rigged model
#
# Written because rigs that passed a visual bend test were still broken in ways a number catches: a head bone owning 0.4% of the surface, leg bones owning the shell.
# What it measures, per vertex and per bone:
#   bleed       vertices dominated by a bone that is neither their nearest bone nor next to it
#   ownership   each bone's share of the surface, and its reach (90th percentile distance of what it owns)
#   joints      how smoothly each joint blends parent into child
#   bends       each bone turned 40 degrees about its own X ("bend") and twisted 60 ("twist"): edges stretched past
#               2x ("tear edges"), the widest gap they open, and surface outside the bone's subtree that moves
#               ("collateral")
#   combined    every joint at once (limbs 30, spine 12, tail 14): the pose the old bend test only showed as a picture
# Writes <out>/<slug>.json with a "verdict" block (grade, pass, checks), "tears" (per bone) and "tear_sites" (where the
# tear edges are, clustered, for a viewer to mark), and (unless -render 0) <slug>_skin.png and <slug>_bend.png.
#
# THRESHOLDS and the warn bands live in autorig/core/grades.py (docs/PIPELINE.md, "Grades"). Pass limits: bleed <= 2%;
# no tear edges in the combined pose or any single-joint bend; head (+ jaw) owns at least 2.5% of the surface where
# there is a head; at most 4 influences per vertex. A model can tighten or loosen these in its spec with audit={...}
# (same keys as THRESHOLDS), and says why beside it.
# Warnings (reported, not graded): twist tears, a limb bone reaching over 0.2 of the model, mirrored chains that
# differ in bone count (docs/PIPELINE.md, rule F).
import bpy, sys, os, math, json, re, time
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
# A tear is an edge stretched past 2x that also opens a real gap: more than 0.4% of the model's size. A micro-edge
# (0.03% of the model, left where two surfaces of the sculpt meet) reaches 10x on a weight difference of 0.05 and opens
# a gap far under a pixel at any normal camera distance; tear_edges_raw keeps the plain 2x count.
from grades import GAP, THRESHOLDS, grade_audit
SITE_CAP = 12        # tear clusters kept per pose
POINT_CAP = 40       # tear edges kept per pose (the widest), for a viewer to mark one by one
CLUSTER_R = 0.05     # tear edges closer than this x the model's size are one site

A = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def arg(n, d=None): return A[A.index(n) + 1] if n in A else d
MODEL = arg("-model")
SPEC_AUDIT = {}
if MODEL:
    from layout import rigged_dir, leaf
    FBX = os.path.join(rigged_dir(MODEL), leaf(MODEL) + ".fbx")
    try:
        from spec_store import SPECS
        SPEC_AUDIT = dict(SPECS.get(MODEL, {}).get("audit", {}))
    except Exception:
        pass
else:
    FBX = arg("-fbx")
SLUG = arg("-slug", MODEL or os.path.splitext(os.path.basename(FBX or "x"))[0])
OUT = arg("-out") or __import__("layout").work_dir("audit")
RENDER = arg("-render", "1") == "1"
OUT = os.path.abspath(OUT); os.makedirs(OUT, exist_ok=True)
t0 = time.time()

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=FBX, ignore_leaf_bones=False, automatic_bone_orientation=False)
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
meshes = [o for o in bpy.data.objects if o.type == 'MESH' and any(m.type == 'ARMATURE' for m in o.modifiers)]
unskinned_meshes = [o.name for o in bpy.data.objects if o.type == 'MESH' and o not in meshes]
for o in bpy.data.objects:
    if o.type == 'MESH' and o not in meshes: o.hide_render = True
M = arm.matrix_world
bones = list(arm.data.bones)
BN = [b.name for b in bones]
BI = {n: i for i, n in enumerate(BN)}
nb = len(bones)
heads = np.array([tuple(M @ b.head_local) for b in bones])
tails = np.array([tuple(M @ b.tail_local) for b in bones])
parent = [BI[b.parent.name] if b.parent else -1 for b in bones]
children = [[BI[c.name] for c in b.children] for b in bones]

# ---------------------------------------------------------------- mesh + weights (all skinned meshes, stacked)
P, W, E, F_area, island_of, groups_unmatched = [], [], [], [], [], set()
off = 0; isl_off = 0; per_mesh = []
for o in meshes:
    me = o.data
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(n, 3)
    mw = np.array(o.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    P.append(co)
    w = np.zeros((n, nb))
    gmap = {}
    for g in o.vertex_groups:
        if g.name in BI: gmap[g.index] = BI[g.name]
        else: groups_unmatched.add(g.name)
    ninf = np.zeros(n, int)
    for v in me.vertices:
        for g in v.groups:
            if g.weight > 1e-5:
                ninf[v.index] += 1
                if g.group in gmap: w[v.index, gmap[g.group]] += g.weight
    W.append(w)
    ed = np.empty(len(me.edges) * 2, int); me.edges.foreach_get("vertices", ed); ed = ed.reshape(-1, 2)
    E.append(ed + off)
    # vertex areas
    va = np.zeros(n)
    for p in me.polygons:
        a = p.area / max(1, len(p.vertices))
        for vi in p.vertices: va[vi] += a
    F_area.append(va)
    # islands
    par = np.arange(n)
    def find(x):
        r = x
        while par[r] != r: r = par[r]
        while par[x] != r: par[x], x = r, par[x]
        return r
    for a_, b_ in ed:
        ra, rb = find(a_), find(b_)
        if ra != rb: par[ra] = rb
    roots = np.array([find(i) for i in range(n)])
    _, lab = np.unique(roots, return_inverse=True)
    island_of.append(lab + isl_off); isl_off += lab.max() + 1
    per_mesh.append(dict(name=o.name, verts=n, max_influences=int(ninf.max()) if n else 0,
                         verts_over_4=int((ninf > 4).sum())))
    off += n
P = np.concatenate(P); W = np.concatenate(W); E = np.concatenate(E); VA = np.concatenate(F_area); ISL = np.concatenate(island_of)
NV = len(P)
lo, hi = P.min(0), P.max(0); size = hi - lo; S = float(size.max())
area_total = float(VA.sum())
wsum = W.sum(1)
unweighted = wsum < 1e-4
dom = np.where(unweighted, -1, W.argmax(1))
domw = np.where(unweighted, 0, W.max(1) / np.maximum(wsum, 1e-9))

deform = [i for i in range(nb) if W[:, i].sum() > 0]
weighted_set = set(deform)

# ---------------------------------------------------------------- naming / chains
SIDE_RE = re.compile(r"(\.L|\.R|_L|_R|\.l|\.r)$")
def side_of(n):
    if "Left" in n: return "L"
    if "Right" in n: return "R"
    m = SIDE_RE.search(n)
    return m.group(1)[-1].upper() if m else ""
def base_of(n):
    n = n.split(":")[-1]
    n = SIDE_RE.sub("", n)
    n = n.replace("Left", "").replace("Right", "")
    return re.sub(r"_?\d+$", "", n)
ROLE_WORDS = [("finger", "finger"), ("thumb", "finger"), ("index", "finger"), ("middle", "finger"), ("ring", "finger"),
              ("pinky", "finger"), ("jaw", "jaw"), ("mandible", "mandible"), ("wing", "wing"), ("tail", "tail"), ("neck", "neck"),
              ("head", "head"), ("arm", "arm"), ("hand", "arm"), ("shoulder", "arm"),
              ("upleg", "leg"), ("foot", "leg"), ("toe", "leg"), ("leg", "leg"), ("claw", "claw"), ("fin", "fin"),
              ("fluke", "fin"), ("flipper", "fin"), ("tentacle", "tentacle"), ("filament", "tentacle"),
              ("tendril", "tentacle"), ("oral", "tentacle"), ("barbel", "barbel"), ("antenna", "antenna"),
              ("ear", "ear"), ("spine", "spine"), ("hips", "spine"), ("body", "spine"), ("chest", "spine"),
              ("abdomen", "spine"), ("pod", "spine"), ("stalk", "spine"), ("root", "root"), ("lid", "lid"),
              ("streamer", "tentacle"), ("wisp", "tentacle"), ("flame", "tentacle"), ("lure", "lure"),
              ("dome", "spine"), ("bell", "spine"), ("mantle", "spine"), ("nozzle", "head")]
def role_of(n):
    l = n.split(":")[-1].lower()
    for k, r in ROLE_WORDS:
        if k in l: return r
    if re.match(r"bone_\d+", l): return "unnamed"
    return "other"

# chains: maximal runs of single-child links with the same base name
chains = []
seen = set()
for i in range(nb):
    if i in seen: continue
    p = parent[i]
    if p >= 0 and len(children[p]) == 1 and base_of(BN[p]) == base_of(BN[i]) and side_of(BN[p]) == side_of(BN[i]):
        continue
    ch = [i]; seen.add(i); cur = i
    while len(children[cur]) == 1 and base_of(BN[children[cur][0]]) == base_of(BN[cur]) and side_of(BN[children[cur][0]]) == side_of(BN[cur]):
        cur = children[cur][0]; ch.append(cur); seen.add(cur)
    chains.append(ch)

CONV = re.compile(r"^(root|hips|body|head|neck|jaw|chest|dome|stalk|pod|bell|mantle|nozzle|lid|"
                  r"spine_\d+|body_\d+|tail_\d+|tail|abdomen_\d+|"
                  r"[a-z_]+?\d*(_v\d+)?(_\d+)?(\.L|\.R)?)$")
nonconv = [n for n in BN if not CONV.match(n)]

def depth(i):
    d = 0
    while parent[i] >= 0: i = parent[i]; d += 1
    return d

# ---------------------------------------------------------------- nearest bone / bleed
def seg_dist(Pts, a, b):
    ab = b - a; L2 = max(1e-12, float(ab @ ab))
    t = np.clip(((Pts - a) @ ab) / L2, 0, 1)
    return np.linalg.norm(Pts - (a + t[:, None] * ab), axis=1)
D = np.full((NV, nb), np.inf)
for i in deform: D[:, i] = seg_dist(P, heads[i], tails[i])
nearest = D.argmin(1) if deform else np.zeros(NV, int)
def ancestor(a, b):
    """True when bone a is upstream of bone b (b hangs, however far down, from a)."""
    while b >= 0:
        b = parent[b]
        if b == a: return True
    return False
def related(a, b):
    return a == b or parent[a] == b or parent[b] == a or (parent[a] == parent[b] and parent[a] >= 0 and
            base_of(BN[a]) == base_of(BN[b]) and side_of(BN[a]) == side_of(BN[b]))
# Bleed is a vertex that moves with the wrong part: owned by a bone that is not its nearest, nor next to it, nor
# upstream of it. Upstream ownership - the body keeping the shell over a leg's root, as the envelope means it to -
# makes a region stiff, not wrong, and tears are what measure stiffness. The first audit counted it too; that figure
# is kept as bleed_legacy_pct.
bleed_pairs = {}
mismatch = np.zeros(NV, bool)
legacy = np.zeros(NV, bool)
for v in range(NV):
    d = dom[v]
    if d < 0: continue
    n_ = nearest[v]
    if not related(d, n_) and D[v, d] > 1.5 * D[v, n_] + 0.01 * S:
        legacy[v] = True
        if ancestor(d, n_): continue
        mismatch[v] = True
        k = (BN[d], BN[n_]); bleed_pairs[k] = bleed_pairs.get(k, 0.0) + VA[v]
bleed_legacy = round(100 * float(VA[legacy].sum()) / area_total, 2)
bleed = sorted(([a, b, round(100 * x / area_total, 2)] for (a, b), x in bleed_pairs.items()), key=lambda r: -r[2])
bleed_total = round(100 * float(VA[mismatch].sum()) / area_total, 2)

share = []
for i in deform:
    m = dom == i
    if not m.any(): continue
    dd = D[m, i]
    L = float(np.linalg.norm(tails[i] - heads[i]))
    share.append(dict(bone=BN[i], area_pct=round(100 * float(VA[m].sum()) / area_total, 2), verts=int(m.sum()),
                      reach_p90=round(float(np.percentile(dd, 90)) / S, 3), len=round(L / S, 3)))
share.sort(key=lambda r: -r["area_pct"])

# ---------------------------------------------------------------- rigid islands
isl_ids = np.unique(ISL)
isl_info = []
for k in isl_ids:
    m = ISL == k
    ws = W[m]
    single = bool(((ws.max(1) / np.maximum(ws.sum(1), 1e-9)) > 0.99).all() and len(set(dom[m])) == 1)
    isl_info.append((int(m.sum()), single, BN[dom[m][0]] if dom[m][0] >= 0 else None, float(VA[m].sum())))
isl_info.sort(key=lambda r: -r[0])
rigid_islands = [r for r in isl_info if r[1]]

# ---------------------------------------------------------------- joint blend
nbr = [[] for _ in range(NV)]
for a_, b_ in E: nbr[a_].append(b_); nbr[b_].append(a_)
joints = []
for c in deform:
    p = parent[c]
    if p < 0 or p not in weighted_set: continue
    J = heads[c]
    lp = np.linalg.norm(tails[p] - heads[p]); lc = np.linalg.norm(tails[c] - heads[c])
    r = max(0.5 * min(lp, lc), 0.04 * S)
    dist = np.linalg.norm(P - J, axis=1)
    m = (dist < r) & ((W[:, p] + W[:, c]) > 0.5 * np.maximum(wsum, 1e-9))
    if m.sum() < 3:
        joints.append(dict(joint=BN[c], parent=BN[p], verts=int(m.sum()), blend=None, hard_edges=0)); continue
    t = W[m, c] / (W[m, p] + W[m, c])
    blend = float(((t > 0.15) & (t < 0.85)).mean())
    tt = np.full(NV, np.nan); tt[m] = t
    em = m[E[:, 0]] & m[E[:, 1]]
    hard = int((np.abs(tt[E[em, 0]] - tt[E[em, 1]]) > 0.7).sum())
    joints.append(dict(joint=BN[c], parent=BN[p], verts=int(m.sum()), blend=round(blend, 2), hard_edges=hard))

# ---------------------------------------------------------------- bend tests
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='POSE')
for pb in arm.pose.bones: pb.rotation_mode = 'XYZ'
rest_len = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
valid = rest_len > 1e-7
def posed_coords():
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for o in meshes:
        ev = o.evaluated_get(dg); me = ev.to_mesh()
        n = len(me.vertices); co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(n, 3)
        mw = np.array(ev.matrix_world); out.append(co @ mw[:3, :3].T + mw[:3, 3]); ev.to_mesh_clear()
    return np.concatenate(out)
def clear_pose():
    for pb in arm.pose.bones: pb.rotation_euler = (0, 0, 0); pb.location = (0, 0, 0); pb.scale = (1, 1, 1)
def tears_in(Q):
    """Edge lengths of a posed mesh, their stretch, which are tears, and the gap each opens (a fraction of S)."""
    L = np.linalg.norm(Q[E[:, 0]] - Q[E[:, 1]], axis=1)
    ratio = np.where(valid, L / np.maximum(rest_len, 1e-9), 1.0)
    gap = np.where(valid, L - rest_len, 0.0) / S
    return L, ratio, (ratio > 2.0) & (gap > GAP), gap
def gap_pct(tear, gap):
    """The widest gap the tears open, in % of the model's size (0 with no tears)."""
    return round(100 * float(gap[tear].max()), 2) if tear.any() else 0.0
def at_bbox(p): return [round(float((p[k] - lo[k]) / max(size[k], 1e-9)), 3) for k in range(3)]
def tear_site(tear, gap, pose, bone=None, rotation=None):
    """Where one pose's tear edges are, for a viewer to mark: the edges' midpoints at rest (world space of the
    imported FBX, and as fractions of its bounding box), greedily clustered widest gap first, the widest kept as
    points too. Each cluster names the bone that owns most of its vertices."""
    idx = np.nonzero(tear)[0]
    if not len(idx): return None
    idx = idx[np.argsort(-gap[idx])]
    mid = (P[E[idx, 0]] + P[E[idx, 1]]) * 0.5
    R = CLUSTER_R * S
    cl = []
    for j in range(len(idx)):
        d = [float(np.linalg.norm(mid[j] - c["seed"])) for c in cl]
        k = int(np.argmin(d)) if d else -1
        if k < 0 or (d[k] > R and len(cl) < SITE_CAP): cl.append({"seed": mid[j], "m": [j]})
        else: cl[k]["m"].append(j)
    clusters = []
    for c in cl:
        m = np.array(c["m"]); e = idx[m]
        owners = {}
        for v in np.concatenate([E[e, 0], E[e, 1]]):
            if dom[v] >= 0: owners[BN[dom[v]]] = owners.get(BN[dom[v]], 0) + 1
        own = sorted(owners, key=lambda b: -owners[b])
        at = mid[m].mean(0)
        clusters.append(dict(at=[round(float(x), 4) for x in at], at_bbox=at_bbox(at), edges=int(len(m)),
                             gap_pct=round(100 * float(gap[e].max()), 2), bone=own[0] if own else None, owners=own[:3]))
    clusters.sort(key=lambda c: (-c["edges"], -c["gap_pct"]))
    pts = [[round(float(x), 4) for x in mid[j]] + [round(100 * float(gap[idx[j]]), 2)] for j in range(min(POINT_CAP, len(idx)))]
    return dict(pose=pose, bone=bone, rotation_deg=rotation, edges=int(len(idx)), worst_gap_pct=gap_pct(tear, gap),
                clusters=clusters, points=pts)
tear_sites = []
def subtree(i):
    s = {i}; st = [i]
    while st:
        for c in children[st.pop()]:
            if c not in s: s.add(c); st.append(c)
    return s
bends = []
for c in deform:
    if parent[c] < 0: continue
    sub = subtree(c)
    for mode, ang in (("bend", 40), ("twist", 60)):
        clear_pose()
        pb = arm.pose.bones[BN[c]]
        pb.rotation_euler = (math.radians(ang), 0, 0) if mode == "bend" else (0, math.radians(ang), 0)
        bpy.context.view_layer.update()
        Q = posed_coords()
        L, ratio, tear, gap = tears_in(Q)
        site = tear_site(tear, gap, mode, BN[c], [ang, 0, 0] if mode == "bend" else [0, ang, 0])
        if site: tear_sites.append(site)
        disp = np.linalg.norm(Q - P, axis=1)
        moved = disp > 0.01 * S
        outside = np.array([d not in sub and d >= 0 for d in dom])
        # vertices the bone should not carry: dominated by a bone outside its subtree and nearest to one too
        near_out = np.array([nn not in sub for nn in nearest])
        coll = moved & outside & near_out & (disp > 0.03 * S)
        worst = int(np.where(tear, ratio, 0).argmax()) if tear.any() else int(ratio.argmax())
        mid = (P[E[worst, 0]] + P[E[worst, 1]]) * 0.5
        bends.append(dict(bone=BN[c], mode=mode, max_stretch=round(float(ratio.max()), 2),
                          worst_at=[round(float((mid[k] - lo[k]) / max(size[k], 1e-9)), 2) for k in range(3)],
                          worst_edge_owners=sorted({BN[dom[E[worst, 0]]], BN[dom[E[worst, 1]]]}),
                          tear_edges=int(tear.sum()), tear_edges_raw=int((ratio > 2.0).sum()), worst_gap_pct=gap_pct(tear, gap),
                          stretch_edges=int((ratio > 1.5).sum()),
                          crush_edges=int((ratio < 0.3).sum()),
                          collateral_pct=round(100 * float(VA[coll].sum()) / area_total, 2),
                          moved_pct=round(100 * float(VA[moved].sum()) / area_total, 2)))
clear_pose(); bpy.context.view_layer.update()

# ---------------------------------------------------------------- combined pose (graded; also drawn on the bend sheet)
def combined_angles():
    """Each weighted bone's bend about its own X in the combined pose, in degrees."""
    out = {}
    for i in deform:
        if parent[i] < 0: continue
        r = role_of(BN[i])
        ang = {"spine": 12, "neck": 20, "head": 15, "tail": 14, "jaw": 25, "finger": 0, "root": 0}.get(r, 30)
        if r == "unnamed": ang = 20
        out[BN[i]] = ang
    return out
COMBINED = combined_angles()
def pose_combined():
    """Poses the combined bend, measures it into result["combined_pose"] (and its tear site), returns the stretch."""
    for n, ang in COMBINED.items(): arm.pose.bones[n].rotation_euler = (math.radians(ang), 0, 0)
    bpy.context.view_layer.update()
    L, ratio, tear, gap = tears_in(posed_coords())
    result["combined_pose"] = dict(max_stretch=round(float(ratio.max()), 2), tear_edges=int(tear.sum()),
                                   tear_edges_raw=int((ratio > 2).sum()), worst_gap_pct=gap_pct(tear, gap),
                                   stretch_edges=int((ratio > 1.5).sum()), crush_edges=int((ratio < 0.3).sum()),
                                   angles_deg=COMBINED)
    site = tear_site(tear, gap, "combined")
    if site: tear_sites.insert(0, site)
    return ratio

# ---------------------------------------------------------------- rest pose
def find_bone(*cands):
    for c in cands:
        for n in BN:
            if n.split(":")[-1].lower() == c.lower(): return BI[n]
    return None
L_bones = [i for i in range(nb) if side_of(BN[i]) == "L"]
R_bones = [i for i in range(nb) if side_of(BN[i]) == "R"]
lx = float(heads[L_bones, 0].mean()) if L_bones else None
rx = float(heads[R_bones, 0].mean()) if R_bones else None
# where the .L bones' skin actually sits
Lskin = np.isin(dom, L_bones); Rskin = np.isin(dom, R_bones)
lskin_x = float(P[Lskin, 0].mean()) if Lskin.any() else None
rskin_x = float(P[Rskin, 0].mean()) if Rskin.any() else None
hips = find_bone("hips", "Hips", "body")
headb = find_bone("head", "Head")
rest = dict(bbox_min=[round(x, 3) for x in lo], bbox_max=[round(x, 3) for x in hi],
            L_mean_x=lx and round(lx, 3), R_mean_x=rx and round(rx, 3), L_skin_x=lskin_x and round(lskin_x, 3),
            R_skin_x=rskin_x and round(rskin_x, 3), feet_on_floor=round(float(lo[2]), 3))
if hips is not None:
    rest["hips_height_frac"] = round(float((heads[hips, 2] - lo[2]) / size[2]), 3)
if hips is not None and headb is not None:
    d = heads[headb] - heads[hips]
    rest["hips_to_head"] = [round(float(x), 3) for x in d]
# arm angle for humanoid-looking rigs
arm_ang = {}
for s in ("L", "R"):
    ua = [i for i in range(nb) if side_of(BN[i]) == s and (BN[i].split(":")[-1] in ("LeftArm", "RightArm") or re.match(r"arm_?1", BN[i]))]
    if ua:
        i = ua[0]; d = tails[i] - heads[i]
        arm_ang[s] = round(math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))), 1)
rest["upper_arm_elevation_deg"] = arm_ang

# ---------------------------------------------------------------- colours
def hue_rgb(h, s=0.75, v=0.95):
    i = int(h * 6) % 6; f = h * 6 - int(h * 6); p = v * (1 - s); q = v * (1 - f * s); t = v * (1 - (1 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
order = sorted(range(nb), key=lambda i: (depth(i), BN[i]))
col = np.zeros((nb, 3))
for k, i in enumerate(order):
    col[i] = hue_rgb((k * 0.618034) % 1.0, 0.7 if k % 2 else 0.9, 0.95 if k % 3 else 0.75)
bone_colours = {BN[i]: [round(float(x), 3) for x in col[i]] for i in range(nb)}

result = dict(slug=SLUG, fbx=FBX, meshes=per_mesh, unskinned_meshes=unskinned_meshes, verts=NV,
              size_units=[round(float(x), 3) for x in size], bones=nb, deform_weighted=len(deform),
              bones_without_skin=[BN[i] for i in range(nb) if i not in weighted_set],
              groups_unmatched=sorted(groups_unmatched),
              hierarchy=[dict(bone=BN[i], parent=BN[parent[i]] if parent[i] >= 0 else None, depth=depth(i),
                              head=[round(float(x), 3) for x in heads[i]], tail=[round(float(x), 3) for x in tails[i]],
                              role=role_of(BN[i])) for i in range(nb)],
              chains=[dict(bones=[BN[i] for i in ch], role=role_of(BN[ch[0]]), side=side_of(BN[ch[0]]),
                           parent=BN[parent[ch[0]]] if parent[ch[0]] >= 0 else None) for ch in chains],
              nonconventional_names=nonconv,
              unweighted_verts=int(unweighted.sum()), unweighted_area_pct=round(100 * float(VA[unweighted].sum()) / area_total, 2),
              islands=len(isl_ids), rigid_islands=len(rigid_islands),
              largest_islands=[dict(verts=a, rigid=b, bone=c, area_pct=round(100 * d / area_total, 2)) for a, b, c, d in isl_info[:8]],
              bleed_total_pct=bleed_total, bleed_legacy_pct=bleed_legacy, bleed_pairs=bleed[:15], dominant_share=share, joints=joints, bends=bends,
              rest=rest, bone_colours=bone_colours)

# ---------------------------------------------------------------- pictures
def render_sheets():
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    R = 440
    scene.render.resolution_x = scene.render.resolution_y = R
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    sh = scene.display.shading
    sh.light = 'STUDIO'; sh.color_type = 'VERTEX'; sh.show_cavity = False; sh.show_object_outline = True
    arm.hide_render = True
    # dominant colour attribute (per mesh)
    def set_attr(values_rgb, name):
        o_ = 0
        for o in meshes:
            me = o.data; n = len(me.vertices)
            a = me.color_attributes.get(name) or me.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
            rgba = np.ones((n, 4)); rgba[:, :3] = values_rgb[o_:o_ + n]
            a.data.foreach_set("color", rgba.ravel()); me.color_attributes.active_color = a
            me.attributes.active_color = a
            o_ += n
    dcol = np.where(dom[:, None] >= 0, col[np.maximum(dom, 0)], np.array([1.0, 0.0, 1.0]))
    # unweighted = magenta; mismatched = darkened a touch so bleed reads
    set_attr(dcol, "dom")
    cd = bpy.data.cameras.new("c"); cd.type = 'ORTHO'; cd.clip_start = 0.0001
    cam = bpy.data.objects.new("c", cd); scene.collection.objects.link(cam); scene.camera = cam
    centre = Vector(((lo + hi) * 0.5).tolist()); ext = S * 1.18; dist = ext * 4 + 1
    cd.ortho_scale = ext; cd.clip_end = dist * 3
    # sticks
    sverts, sfaces, scols = [], [], []
    th = S * 0.009
    for i in deform:
        h = Vector(heads[i].tolist()); t = Vector(tails[i].tolist()); d = t - h
        if d.length < 1e-9: continue
        z = d.normalized(); x = z.orthogonal().normalized(); y = z.cross(x); base = h + d * 0.15; k = len(sverts)
        sverts += [h, base + x * th, base + y * th, base - x * th, base - y * th, t]
        for j in range(4):
            a_, b_ = k + 1 + j, k + 1 + (j + 1) % 4; sfaces += [(k, b_, a_), (k + 5, a_, b_)]
        scols += [col[i]] * 6
    sme = bpy.data.meshes.new("sticks"); sme.from_pydata([tuple(v) for v in sverts], [], sfaces)
    sob = bpy.data.objects.new("sticks", sme); scene.collection.objects.link(sob)
    sa = sme.color_attributes.new("dom", 'FLOAT_COLOR', 'POINT'); rgba = np.ones((len(sverts), 4)); rgba[:, :3] = scols
    sa.data.foreach_set("color", rgba.ravel()); sme.attributes.active_color = sa
    def grab(tag):
        fp = os.path.join(OUT, "_tmp_%s_%s.png" % (SLUG, tag)); scene.render.filepath = fp
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(fp); w, h = img.size
        px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
        bpy.data.images.remove(img); os.remove(fp); return px
    def shot(direction, tag, sticks, mesh_alpha=1.0):
        d = Vector(direction).normalized()
        cam.location = centre + d * dist
        cam.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
        sob.hide_render = True
        for o in meshes: o.hide_render = False
        body = grab(tag + "m")
        tile = np.ones_like(body); tile[..., :3] = 0.97
        a = body[..., 3:4] * mesh_alpha
        tile[..., :3] = tile[..., :3] * (1 - a) + body[..., :3] * a
        if sticks:
            sob.hide_render = False
            for o in meshes: o.hide_render = True
            sk = grab(tag + "s"); a = sk[..., 3:4]
            tile[..., :3] = tile[..., :3] * (1 - a) + sk[..., :3] * a
            for o in meshes: o.hide_render = False
            sob.hide_render = True
        tile[:, :2, :3] = 0.55; tile[:2, :, :3] = 0.55
        return tile
    VIEWS = [(1, 0, 0.05), (0, -1, 0.05), (0, 0, 1), (-0.8, -1, 0.55)]  # side (+X), front (-Y), top, quarter
    top = [shot(v, "r%d" % k, False) for k, v in enumerate(VIEWS)]
    bot = [shot(v, "s%d" % k, True, 0.35) for k, v in enumerate(VIEWS)]
    save(np.concatenate([np.concatenate(bot, 1), np.concatenate(top, 1)], 0), SLUG + "_skin.png")
    # combined bend pose
    ratio = pose_combined()
    vs = np.ones(NV)
    np.maximum.at(vs, E[:, 0], ratio); np.maximum.at(vs, E[:, 1], ratio)
    vc = np.ones(NV)
    np.minimum.at(vc, E[:, 0], ratio); np.minimum.at(vc, E[:, 1], ratio)
    heat = np.tile(np.array([0.78, 0.8, 0.84]), (NV, 1))
    s1 = np.clip((vs - 1.25) / 0.75, 0, 1)[:, None]  # 1.25x..2x stretch: grey->red
    heat = heat * (1 - s1) + np.array([0.95, 0.05, 0.05]) * s1
    c1 = np.clip((0.5 - vc) / 0.3, 0, 1)[:, None]    # crushed below 0.5: blue
    heat = heat * (1 - c1) + np.array([0.1, 0.3, 1.0]) * c1
    set_attr(heat, "heat")
    top = [shot(v, "b%d" % k, False) for k, v in enumerate(VIEWS)]
    set_attr(dcol, "dom")
    bot = [shot(v, "d%d" % k, False) for k, v in enumerate(VIEWS)]
    save(np.concatenate([np.concatenate(bot, 1), np.concatenate(top, 1)], 0), SLUG + "_bend.png")

def save(arr, name):
    h, w = arr.shape[:2]
    im = bpy.data.images.new(name, width=w, height=h, alpha=True)
    im.pixels = arr.ravel().tolist()
    im.filepath_raw = os.path.join(OUT, name); im.file_format = 'PNG'; im.save()

if RENDER:
    bpy.ops.object.mode_set(mode='OBJECT')
    render_sheets()
# ---------------------------------------------------------------- verdict
if "combined_pose" not in result:  # the numbers-only run still poses it; it is graded
    pose_combined()
    clear_pose(); bpy.context.view_layer.update()
head_pct = sum(x["area_pct"] for x in share if x["bone"].split(":")[-1].lower() in ("head", "jaw"))
has_head = any(n.split(":")[-1].lower() == "head" for n in BN)
max_inf = max(m["max_influences"] for m in per_mesh) if per_mesh else 0
single = [b for b in bends if b["mode"] == "bend"]
bend_tears = max([b["tear_edges"] for b in single] or [0])
bend_gap = max([b["worst_gap_pct"] for b in single] or [0.0])
twist_tears = max([b["tear_edges"] for b in bends if b["mode"] == "twist"] or [0])
values = {"bleed_pct": bleed_total, "combined_tears": result["combined_pose"]["tear_edges"], "bend_tears": bend_tears,
          "head_pct": round(head_pct, 2) if has_head else None, "max_influences": max_inf}
graded = grade_audit(values, SPEC_AUDIT, {"combined_tears": result["combined_pose"]["worst_gap_pct"], "bend_tears": bend_gap})
# per bone: how many edges each bone's own bend and twist tear, and the widest gap each opens
by_bone = {}
for b in bends:
    if not b["tear_edges"]: continue
    r = by_bone.setdefault(b["bone"], dict(bone=b["bone"], bend=0, bend_gap_pct=0.0, twist=0, twist_gap_pct=0.0))
    r[b["mode"]] = b["tear_edges"]; r[b["mode"] + "_gap_pct"] = b["worst_gap_pct"]
by_bone = sorted(by_bone.values(), key=lambda r: (-r["bend"], -r["bend_gap_pct"], -r["twist"], r["bone"]))
comb_site = next((t for t in tear_sites if t["pose"] == "combined"), None)
if by_bone and by_bone[0]["bend"]: worst_bone = by_bone[0]["bone"]
elif comb_site: worst_bone = comb_site["clusters"][0]["bone"]
else: worst_bone = None                      # twist tears alone are a warning, not graded
result["tears"] = dict(combined=values["combined_tears"], bend_max=bend_tears,
                       bones_tearing=sum(1 for r in by_bone if r["bend"]),
                       worst_gap_pct=max(result["combined_pose"]["worst_gap_pct"], bend_gap), worst_bone=worst_bone,
                       by_bone=by_bone)
result["tear_sites"] = tear_sites
warn = []
if twist_tears: warn.append("twist tears %d (%s)" % (twist_tears, max((b for b in bends if b["mode"] == "twist"), key=lambda b: b["tear_edges"])["bone"]))
for x in share:
    if role_of(x["bone"]) in ("leg", "arm", "wing", "tentacle", "claw") and x["reach_p90"] > 0.2:
        warn.append("%s reaches %.2f" % (x["bone"], x["reach_p90"]))
sides = {}
for ch in chains:
    s = side_of(BN[ch[0]])
    if s: sides.setdefault(base_of(BN[ch[0]]), {}).setdefault(s, []).append(len(ch))
for b, d in sides.items():
    if "L" in d and "R" in d and sorted(d["L"]) != sorted(d["R"]): warn.append("%s: L %s vs R %s bones" % (b, d["L"], d["R"]))
result["verdict"] = dict(graded, warnings=warn)
print("AUDIT_%s %s %s" % (graded["grade"], SLUG, json.dumps(values)))
result["seconds"] = round(time.time() - t0, 1)
json.dump(result, open(os.path.join(OUT, SLUG + ".json"), "w"), indent=1)
print("AUDIT_DONE", SLUG, result["seconds"])

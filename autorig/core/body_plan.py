# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: body-plan measures of a mesh, for the suggest step (core/suggest.py weighs them).
#
# The tips the suggester pairs up say where the extremities are, not what they are: a T-posed figure's hands, a
# dog's legs seen across its narrow body and a pedestal's corners all pair up as "wide". These measures look at
# the whole shape instead:
#   parts        connected pieces that matter (2% of the surface or more)
#   mirror       how well the shape matches its own mirror image left to right (and front to back), 0..1
#   feet         separate patches of the mesh touching the ground, and how much of the footprint they fill
#   wings        thin flat sheets standing out to both sides above the legs
#   height       height over the longer of width and length; stand: height over the length front to back
#   rest         whether the mesh, as it stands, rests stably (the chance it lands this way up if dropped)
#
# Needs numpy and the trimesh extra (core/extras.py; trimesh's graph and hull calls need scipy or networkx, which
# open-clip's torch brings). measure() returns None without trimesh, and a measure that cannot be taken is left
# out, so the suggester falls back to its tips alone. Deterministic: the surface sample uses a fixed seed.

SAMPLES = 20000
GRID = 32                  # mirror test: voxels along the longest side
GROUND_BAND = 0.05         # feet: the lowest 5% of the height
FOOT_GRID = 48             # feet: cells along the longer side of the footprint
FOOT_MIN = 0.02            # a patch holding under 2% of the ground band's surface is a toe, a claw or noise


def _sample(tm, n, np):
    """n points spread evenly over the surface (area-weighted), the same ones every run."""
    import trimesh
    try:
        pts, fi = trimesh.sample.sample_surface(tm, n, seed=7)
    except TypeError:                                   # an older trimesh with no seed: a fixed numpy state
        state = np.random.get_state()
        np.random.seed(7)
        pts, fi = trimesh.sample.sample_surface(tm, n)
        np.random.set_state(state)
    return np.asarray(pts, dtype=np.float64)


def _parts(tm, np):
    import trimesh
    area = tm.area_faces
    total = float(area.sum()) or 1.0
    comps = trimesh.graph.connected_components(tm.face_adjacency, nodes=np.arange(len(tm.faces)))
    shares = sorted((float(area[c].sum()) / total for c in comps), reverse=True)
    return {"count": sum(1 for s in shares if s >= 0.02), "pieces": len(shares),
            "largest": round(shares[0], 3) if shares else 0.0}


def _mirror(pts, lo, size, np):
    """Voxel overlap (intersection over union) of the sampled surface with its mirror image, across the plane
    halfway along x (left to right) and along y (front to back)."""
    step = max(size) / GRID
    out = {}
    for axis, name in ((0, "x"), (1, "y")):
        dims = np.maximum(1, np.ceil(size / step).astype(int))
        idx = np.clip(((pts - lo) / step).astype(int), 0, dims - 1)
        occ = np.zeros(dims, dtype=bool)
        occ[idx[:, 0], idx[:, 1], idx[:, 2]] = True
        flip = np.flip(occ, axis=axis)
        # one voxel of slack: a dilated mirror, so a surface half a voxel off its twin still counts
        grown = flip.copy()
        for a in range(3):
            grown |= np.roll(flip, 1, axis=a) | np.roll(flip, -1, axis=a)
        inter = np.logical_and(occ, grown).sum()
        out[name] = round(float(inter) / max(1, int(occ.sum())), 3)
    return out


def _feet(pts, lo, size, np):
    """Patches of the surface in the lowest band of the height, seen from above."""
    band = pts[pts[:, 2] <= lo[2] + GROUND_BAND * size[2]]
    if len(band) < 10:
        return None
    foot = max(size[0], size[1])
    cell = foot / FOOT_GRID
    dims = (int(np.ceil(size[0] / cell)) + 1, int(np.ceil(size[1] / cell)) + 1)
    ij = ((band[:, :2] - lo[:2]) / cell).astype(int)
    count = np.zeros(dims, dtype=int)
    np.add.at(count, (np.clip(ij[:, 0], 0, dims[0] - 1), np.clip(ij[:, 1], 0, dims[1] - 1)), 1)
    cells = count > 0
    # the patches, and the share of the band's samples in each (a cell's count, summed over the patch)
    h, w = cells.shape
    label = -np.ones(cells.shape, dtype=int)
    shares = []
    k = 0
    for i in range(h):
        for j in range(w):
            if cells[i, j] and label[i, j] < 0:
                stack, n = [(i, j)], 0
                label[i, j] = k
                while stack:
                    a, b = stack.pop()
                    n += int(count[a, b])
                    for da in (-1, 0, 1):
                        for db in (-1, 0, 1):
                            c, d = a + da, b + db
                            if 0 <= c < h and 0 <= d < w and cells[c, d] and label[c, d] < 0:
                                label[c, d] = k
                                stack.append((c, d))
                shares.append(n / float(len(band)))
                k += 1
    feet = sorted((s for s in shares if s >= FOOT_MIN), reverse=True)
    # how much of the footprint (width x length) the touching cells cover: a base fills it, feet dot it
    fill = float(cells.sum()) * cell * cell / max(1e-9, size[0] * size[1])
    return {"count": len(feet), "fill": round(min(1.0, fill), 3), "largest": round(feet[0], 3) if feet else 0.0}


def _wings(pts, lo, size, np):
    """Thin flat sheets out to both sides, above the legs: the surface farther out than 1.6x the torso's own half
    width, in the top 70% of the height. A wing spread out is a sheet whose plane takes in x; a row of legs at the
    side of a narrow body is a "sheet" along y and z only, so a sheet facing sideways counts only when it stands
    high (the middle of it in the top 40% of the height: wings folded up over the back). A held-out arm is a rod."""
    cx = lo[0] + size[0] * 0.5
    mid = pts[(pts[:, 2] > lo[2] + 0.35 * size[2]) & (pts[:, 2] < lo[2] + 0.75 * size[2])]
    if len(mid) < 50:
        return None
    torso = float(np.percentile(np.abs(mid[:, 0] - cx), 50))
    reach = max(1.6 * torso, 0.18 * max(size[0], size[1]))
    up = pts[pts[:, 2] > lo[2] + 0.3 * size[2]]
    sides = []
    for sign in (1, -1):
        side = up[(up[:, 0] - cx) * sign > reach]
        share = len(side) / float(len(pts))
        if len(side) < 30:
            sides.append({"share": round(share, 3), "sheet": False})
            continue
        c = side - side.mean(axis=0)
        w, v = np.linalg.eigh(np.cov(c.T))          # ascending: v[:, 0] is the sheet's normal
        e = np.sqrt(np.maximum(w, 0))
        thin = e[0] / max(1e-9, e[1])               # thickness against the sheet's narrower spread
        broad = e[1] / max(1e-9, e[2])              # a sheet spreads both ways in its plane; a rod one way
        outward = abs(float(v[0, 0]))               # the normal's x: near 1 for a plane facing sideways (no wing)
        reach_out = float(np.max((side[:, 0] - cx) * sign)) / max(1e-9, max(size[1], size[2]))
        high = (float(side[:, 2].mean()) - lo[2]) / size[2]
        sheet = thin < 0.35 and broad > 0.3 and (outward < 0.7 or high > 0.6) and share > 0.04 and reach_out > 0.3
        sides.append({"share": round(share, 3), "thin": round(float(thin), 3), "broad": round(float(broad), 3),
                      "outward": round(outward, 3), "high": round(high, 3), "reach": round(reach_out, 3),
                      "sheet": bool(sheet)})
    return {"both": all(s["sheet"] for s in sides), "left": sides[0], "right": sides[1],
            "torso": round(torso / max(1e-9, max(size[0], size[1])), 3)}


def _rest(tm, pts, np):
    """The chance the mesh comes to rest standing as it stands now (trimesh's stable poses, over its convex hull)
    and the most likely pose's chance."""
    import trimesh
    com = tm.center_mass if tm.is_watertight and tm.is_volume else pts.mean(axis=0)
    poses, probs = trimesh.poses.compute_stable_poses(tm, center_mass=com, sigma=0.0, n_samples=1)
    here = 0.0
    for T, p in zip(poses, probs):
        # a pose keeps this orientation when it keeps +Z up (its rotation sends z to z)
        if float(np.asarray(T)[2, 2]) > 0.97:
            here = max(here, float(p))
    return {"upright": round(here, 3), "best": round(float(max(probs)) if len(probs) else 0.0, 3),
            "poses": int(len(probs))}


def measure(verts, faces, log=None):
    """Body-plan measures of a mesh (vertices and triangles, Z up, facing -Y, as rerig.normalise leaves it).
    Returns a dict, or None when trimesh is not installed. A measure that fails is left out (and logged)."""
    try:
        import numpy as np
        import trimesh
    except Exception:
        return None
    v = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
    f = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if len(v) < 4 or len(f) < 4:
        return None
    tm = trimesh.Trimesh(vertices=v, faces=f, process=False)
    lo, hi = v.min(axis=0), v.max(axis=0)
    size = np.maximum(hi - lo, 1e-9)
    out = {"size": [round(float(s), 4) for s in size],
           "height": round(float(size[2] / max(size[0], size[1])), 3),
           "stand": round(float(size[2] / size[1]), 3),
           "length": round(float(size[1] / size[0]), 3)}
    pts = _sample(tm, SAMPLES, np)
    for name, fn in (("parts", lambda: _parts(tm, np)), ("mirror", lambda: _mirror(pts, lo, size, np)),
                     ("feet", lambda: _feet(pts, lo, size, np)), ("wings", lambda: _wings(pts, lo, size, np)),
                     ("rest", lambda: _rest(tm, pts, np))):
        try:
            got = fn()
            if got is not None:
                out[name] = got
        except Exception as e:                          # one missing measure (no scipy for the hull) is not fatal
            if log:
                log("body_plan: %s not measured (%s: %s)" % (name, type(e).__name__, e))
    return out

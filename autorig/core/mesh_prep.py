# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: mesh preparation with the optional mesh libraries (core/extras.py), on plain arrays.
#
# No bpy here: everything takes and returns numpy arrays, so it runs inside a Blender step (core/mesh_bridge.py moves
# a Blender mesh in and out) and in Blender's Python on its own (the tests). A mesh is
#
#   V   (n, 3) float  vertex positions
#   F   (m, 3) int    triangles
#   UV  (m*3, 2) float or None   a UV per triangle corner ("wedge"), so a seam needs no split vertices
#   FD  (m,) int or None         a number per triangle carried through: the material index
#
#   decimate(V, F, UV, FD, target)           texture-safe quadric collapse (PyMeshLab): UVs and silhouette kept
#   repair(V, F, UV, FD)                     PyMeshLab: duplicates, null faces, non-manifold edges/vertices, holes
#   watertight(V, F)                         PyMeshFix: one closed surface (UVs are not kept: re-unwrap and bake)
#   unwrap(V, F, resolution, padding)        xatlas: a fresh UV atlas, per corner
#   convex_parts(V, F, ...)                  CoACD: convex pieces for colliders
#   Solid.box / Solid.disc, split(...)       manifold3d: a mesh cut in two by a solid, closed where it is cut
#
# Each function needs its extra; check with extras.have(name) (or available()) and keep the plain path otherwise.
import math, os

LIBS = {"decimate": "pymeshlab", "repair": "pymeshlab", "watertight": "pymeshfix", "unwrap": "xatlas",
        "convex_parts": "coacd", "split": "manifold3d"}


def available():
    """Which of the functions here can run: name -> True/False (imports each library once)."""
    out = {}
    for fn, mod in LIBS.items():
        try:
            __import__(mod)
            out[fn] = True
        except Exception:
            out[fn] = False
    return out


def _np():
    import numpy as np
    return np


def triangle_count(F):
    return int(len(F))


# ------------------------------------------------------------------ pure helpers (numpy only)

def weld(V, F, tol=1e-6):
    """Vertices closer than tol made one (positions only): (V', F', old -> new index). A GLB imported without merging
    has every UV seam split, which every library here reads as an open border."""
    np = _np()
    V = np.asarray(V, dtype=np.float64)
    if not len(V):
        return V, np.asarray(F), np.zeros(0, dtype=np.int64)
    key = np.round(V / max(tol, 1e-12)).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    return V[first], inverse[np.asarray(F)], inverse


def components(F, n_verts):
    """A connected-component label per vertex (by shared triangles), 0..k-1."""
    np = _np()
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    lab = np.arange(n_verts, dtype=np.int64)
    while len(F):
        m = lab[F].min(axis=1)
        new = lab.copy()
        for c in range(3):
            np.minimum.at(new, F[:, c], m)          # the corner itself
            np.minimum.at(new, lab[F[:, c]], m)     # and the root it pointed at
        while True:                                 # pointer jumping to the roots
            nxt = new[new]
            if np.array_equal(nxt, new): break
            new = nxt
        if np.array_equal(new, lab): break
        lab = new
    _, labels = np.unique(lab, return_inverse=True)
    return labels.reshape(-1)


def face_components(F, n_verts):
    """A component label per triangle."""
    np = _np()
    labels = components(F, n_verts)
    return labels[np.asarray(F)[:, 0]] if len(F) else np.zeros(0, dtype=np.int64)


def corner_uv(UV, F):
    """A per-vertex UV array (n, 2) indexed by F turned into per-corner (m*3, 2)."""
    np = _np()
    return np.asarray(UV)[np.asarray(F).reshape(-1)]


def surface_distance(A_V, A_F, points, samples=4096):
    """For checks and tests: the largest distance from a sample of points to the surface (A_V, A_F), by brute force
    over triangles in chunks (no scipy). Used for before/after numbers."""
    np = _np()
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) > samples:
        pts = pts[np.linspace(0, len(pts) - 1, samples).astype(np.int64)]
    T = np.asarray(A_V, dtype=np.float64)[np.asarray(A_F)]
    best = np.full(len(pts), np.inf)
    for s in range(0, len(T), 2048):
        t = T[s:s + 2048]
        d = _point_triangle_distance(pts[:, None, :], t[None, :, 0], t[None, :, 1], t[None, :, 2])
        best = np.minimum(best, d.min(axis=1))
    return best


def _point_triangle_distance(p, a, b, c):
    """Distance from points p to triangles abc, broadcast (Ericson, Real-Time Collision Detection 5.1.5)."""
    np = _np()
    ab, ac, ap = b - a, c - a, p - a
    d1 = (ab * ap).sum(-1); d2 = (ac * ap).sum(-1)
    bp = p - b; d3 = (ab * bp).sum(-1); d4 = (ac * bp).sum(-1)
    cp = p - c; d5 = (ab * cp).sum(-1); d6 = (ac * cp).sum(-1)
    va = d3 * d6 - d5 * d4; vb = d5 * d2 - d1 * d6; vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-30, 1e-30, denom)
    v = vb / denom; w = vc / denom
    closest = a + ab * v[..., None] + ac * w[..., None]
    # the regions outside the face: clamp to edges and corners
    def seg(x, y):
        xy = y - x
        t = np.clip(((p - x) * xy).sum(-1) / np.maximum((xy * xy).sum(-1), 1e-30), 0, 1)
        return x + xy * t[..., None]
    inside = (va >= 0) & (vb >= 0) & (vc >= 0)
    cands = [seg(a, b), seg(b, c), seg(c, a)]
    dist = np.linalg.norm(p - closest, axis=-1)
    edge = np.min([np.linalg.norm(p - q, axis=-1) for q in cands], axis=0)
    return np.where(inside, dist, edge)


def hausdorff(V0, F0, V1, F1, samples=4096):
    """Two-way largest surface distance between two meshes, over a sample of each one's vertices, divided by the
    first one's bounding-box diagonal: 0.01 means 1% of the model's size."""
    np = _np()
    V0 = np.asarray(V0, dtype=np.float64); V1 = np.asarray(V1, dtype=np.float64)
    size = float(np.linalg.norm(V0.max(0) - V0.min(0))) or 1.0
    fwd = surface_distance(V0, F0, V1, samples).max()
    back = surface_distance(V1, F1, V0, samples).max()
    return float(max(fwd, back)) / size


# ------------------------------------------------------------------ PyMeshLab: decimation and repair

def _meshlab(V, F, UV=None, FD=None):
    import pymeshlab
    np = _np()
    kw = {"vertex_matrix": np.asarray(V, dtype=np.float64), "face_matrix": np.asarray(F, dtype=np.int32)}
    if FD is not None:
        kw["f_scalar_array"] = np.asarray(FD, dtype=np.float64)
    if UV is not None:
        kw["w_tex_coords_matrix"] = np.asarray(UV, dtype=np.float64).reshape(-1, 2)
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(**kw))
    return ms


def _from_meshlab(ms, UV=None, FD=None):
    np = _np()
    m = ms.current_mesh()
    V = m.vertex_matrix().copy(); F = m.face_matrix().astype(np.int64)
    uv = m.wedge_tex_coord_matrix().copy().reshape(-1, 2) if UV is not None and m.has_wedge_tex_coord() else None
    fd = np.rint(m.face_scalar_array()).astype(np.int64) if FD is not None and m.has_face_scalar() else None
    return V, F, uv, fd


def _meshlab_textured(V, F, UV, FD, folder):
    """A MeshSet loaded from an OBJ written to `folder`: PyMeshLab's texture-aware collapse wants every face's UVs tied
    to a texture, which only its file loaders set (a mesh built from arrays has none). One material per face-data
    value; each face carries its index into F as the custom attribute "orig"."""
    import pymeshlab
    np = _np()
    F = np.asarray(F, dtype=np.int64); UV = np.asarray(UV, dtype=np.float64).reshape(-1, 2)
    FD = np.zeros(len(F), dtype=np.int64) if FD is None else np.asarray(FD, dtype=np.int64)
    obj, mtl = os.path.join(folder, "mesh.obj"), os.path.join(folder, "mesh.mtl")
    groups = sorted(set(FD.tolist()))
    with open(mtl, "w") as f:
        for g in groups:
            f.write("newmtl m%d\nmap_Kd m%d.png\n" % (g, g))
    order = np.concatenate([np.nonzero(FD == g)[0] for g in groups]) if len(F) else np.zeros(0, dtype=np.int64)
    with open(obj, "w") as f:
        f.write("mtllib mesh.mtl\n")
        np.savetxt(f, np.asarray(V, dtype=np.float64), fmt="v %.9g %.9g %.9g")
        np.savetxt(f, UV, fmt="vt %.9g %.9g")
        for g in groups:
            f.write("usemtl m%d\n" % g)
            idx = np.nonzero(FD == g)[0]
            rows = np.column_stack([F[idx, 0] + 1, idx * 3 + 1, F[idx, 1] + 1, idx * 3 + 2, F[idx, 2] + 1, idx * 3 + 3])
            np.savetxt(f, rows, fmt="f %d/%d %d/%d %d/%d")
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(obj)
    m = ms.current_mesh()
    if m.face_number() != len(F):
        raise RuntimeError("PyMeshLab read %d faces of %d" % (m.face_number(), len(F)))
    m.add_face_custom_scalar_attribute(order.astype(np.float64), "orig")
    return ms


def decimate(V, F, UV, FD, target, weld_tol=None, preserve_boundary=True, quality=0.3, texture_weight=1.0,
             optimal_placement=True, planar=True, preserve_topology=False):
    """Quadric edge collapse to `target` triangles with PyMeshLab, the UV-aware variant when there are UVs (it adds the
    texture coordinates to the error, so a seam is not dragged across and the silhouette is held). Returns
    (V, F, UV, FD). weld_tol merges split seam vertices first (default: a millionth of the model's size)."""
    import pymeshlab, shutil, tempfile
    np = _np()
    V = np.asarray(V, dtype=np.float64)
    if weld_tol is None:
        weld_tol = max(float(np.linalg.norm(V.max(0) - V.min(0))) * 1e-6, 1e-9) if len(V) else 1e-9
    folder = tempfile.mkdtemp(prefix="autorig-meshlab-")
    try:
        ms = _meshlab_textured(V, F, UV, FD, folder) if UV is not None else _meshlab(V, F, None, FD)
        ms.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(weld_tol))
        if len(F) > target:
            if UV is not None:
                ms.meshing_decimation_quadric_edge_collapse_with_texture(
                    targetfacenum=int(target), qualitythr=quality, extratcoordw=1.0,
                    preserveboundary=preserve_boundary, boundaryweight=1.0, optimalplacement=True,
                    preservenormal=True, planarquadric=True)
            else:
                ms.meshing_decimation_quadric_edge_collapse(
                    targetfacenum=int(target), qualitythr=quality, preserveboundary=preserve_boundary,
                    boundaryweight=1.0, optimalplacement=True, preservenormal=True, planarquadric=True,
                    preservetopology=preserve_topology, autoclean=True)
        ms.meshing_remove_unreferenced_vertices()
        if UV is None:
            return _from_meshlab(ms, None, FD)
        m = ms.current_mesh()
        orig = np.rint(m.face_custom_scalar_attribute_array("orig")).astype(np.int64)
        fd = None if FD is None else np.asarray(FD)[orig]
        return (m.vertex_matrix().copy(), m.face_matrix().astype(np.int64),
                m.wedge_tex_coord_matrix().copy().reshape(-1, 2), fd)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def topology(V, F, weld_first=True):
    """PyMeshLab's topological measures, the counts Mesh Doctor reports; with weld_first, split UV seams merged first
    (a GLB imported as it is has every seam split, which would count as open border)."""
    np = _np()
    if weld_first and len(V):
        V, F, _ = weld(V, F, max(float(np.linalg.norm(np.ptp(np.asarray(V), axis=0))) * 1e-6, 1e-9))
    ms = _meshlab(V, F)
    t = ms.get_topological_measures()
    return {"verts": t["vertices_number"], "faces": t["faces_number"], "components": t["connected_components_number"],
            "boundary_edges": t["boundary_edges"], "non_manifold_edges": t["non_two_manifold_edges"],
            "non_manifold_verts": t["non_two_manifold_vertices"], "two_manifold": bool(t["is_mesh_two_manifold"]),
            "holes": t["number_holes"]}


def repair(V, F, UV=None, FD=None, weld_tol=None, max_hole=30):
    """PyMeshLab repair, keeping UVs and face data on the faces that stay: split seams welded, duplicate and null faces
    removed, non-manifold edges fixed by removing the fewest faces, non-manifold vertices split, holes up to max_hole
    edges closed (their new faces take UV (0, 0)). Returns (V, F, UV, FD, report)."""
    import pymeshlab
    np = _np()
    V = np.asarray(V, dtype=np.float64)
    if weld_tol is None:
        weld_tol = max(float(np.linalg.norm(V.max(0) - V.min(0))) * 1e-6, 1e-9) if len(V) else 1e-9
    # measured on its own copy, after the weld (split UV seams are not defects): measuring the MeshSet being repaired
    # left it in a state where meshing_close_holes crashed the process (an access violation on a Tripo truck)
    probe = _meshlab(V, F)
    probe.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(weld_tol))
    before = probe.get_topological_measures()
    del probe
    ms = _meshlab(V, F, UV, FD)
    ms.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(weld_tol))
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_null_faces()
    ms.meshing_repair_non_manifold_edges(method="Remove Faces")
    ms.meshing_repair_non_manifold_vertices(vertdispratio=0)
    closed = 0
    try:
        n0 = ms.current_mesh().face_number()
        ms.meshing_close_holes(maxholesize=int(max_hole), selfintersection=False, newfaceselected=False)
        closed = ms.current_mesh().face_number() - n0
    except Exception as e:      # PyMeshLab refuses to close holes on a mesh it still finds non-manifold
        print("mesh_prep.repair: holes left open (%s)" % e)
    ms.meshing_remove_unreferenced_vertices()
    after = ms.get_topological_measures()
    V2, F2, UV2, FD2 = _from_meshlab(ms, UV, FD)
    report = {"faces": [before["faces_number"], after["faces_number"]],
              "verts": [before["vertices_number"], after["vertices_number"]],
              "non_manifold_edges": [before["non_two_manifold_edges"], after["non_two_manifold_edges"]],
              "non_manifold_verts": [before["non_two_manifold_vertices"], after["non_two_manifold_vertices"]],
              "boundary_edges": [before["boundary_edges"], after["boundary_edges"]],
              "hole_faces_added": int(closed)}
    return V2, F2, UV2, FD2, report


# ------------------------------------------------------------------ PyMeshFix: one watertight surface

def watertight(V, F, union=True):
    """One closed, manifold surface. PyMeshFix closes each shell on its own (run on the whole mesh it keeps only the
    largest shell, which on a multi-shell Tripo model drops the eyes, the tail...), then manifold3d joins the closed
    shells into one solid (union=False leaves them as separate closed shells). UVs do not survive: unwrap() and a bake
    bring the texture back. Returns (V, F, report)."""
    import pymeshfix
    np = _np()
    V = np.asarray(V, dtype=np.float64)
    size = float(np.linalg.norm(np.ptp(V, axis=0))) if len(V) else 1.0
    V, F, _ = weld(V, F, size * 1e-6)
    shell = face_components(F, len(V))
    closed, dropped = [], 0
    for k in range(int(shell.max()) + 1 if len(shell) else 0):
        sV, sF, _, _ = _subset(V, F, None, None, np.nonzero(shell == k)[0])
        if len(sF) < 4:
            dropped += 1
            continue
        fix = pymeshfix.MeshFix(sV, sF.astype(np.int32))
        fix.repair(joincomp=True, remove_smallest_components=False)
        v, f = np.asarray(fix.points, dtype=np.float64), np.asarray(fix.faces, dtype=np.int64)
        if len(f) >= 4:
            closed.append((v, f))
        else:
            dropped += 1
    report = {"shells": len(closed) + dropped, "closed": len(closed), "dropped": dropped, "joined": False}
    if union and len(closed) > 1:
        try:
            import manifold3d as m3
            solids = []
            for v, f in closed:
                M = m3.Manifold(m3.Mesh(vert_properties=v.astype(np.float32), tri_verts=f.astype(np.uint32)))
                if M.status() == m3.Error.NoError and not M.is_empty():
                    solids.append(M)
            if len(solids) == len(closed):
                out = m3.Manifold.batch_boolean(solids, m3.OpType.Add).to_mesh()
                V2, F2 = _merged(out)
                report["joined"] = True
                return V2, F2, report
        except ImportError:
            pass
    out = _concat([(v, f, None, None) for v, f in closed])
    return out[0], out[1], report


# ------------------------------------------------------------------ xatlas: a fresh UV atlas

def unwrap(V, F, resolution=1024, padding=4):
    """xatlas: charts packed into one atlas. Returns per-corner UVs (m*3, 2) in face order (xatlas keeps the faces as
    given), ready to bake into."""
    import xatlas
    np = _np()
    atlas = xatlas.Atlas()
    atlas.add_mesh(np.asarray(V, dtype=np.float32), np.asarray(F, dtype=np.uint32))
    co = xatlas.ChartOptions()
    po = xatlas.PackOptions()
    po.resolution = int(resolution)
    po.padding = int(padding)
    po.bilinear = True
    po.bruteForce = False
    atlas.generate(co, po)
    vmap, faces, uvs = atlas[0]
    if len(faces) != len(F):
        raise RuntimeError("xatlas returned %d faces for %d" % (len(faces), len(F)))
    return np.asarray(uvs, dtype=np.float64)[np.asarray(faces).reshape(-1)]


# ------------------------------------------------------------------ CoACD: convex pieces for colliders

def convex_parts(V, F, threshold=0.08, max_hulls=16, max_hull_verts=60, resolution=2000, seed=0, simplify_to=4000,
                 remesh_resolution=30, search=(30, 8, 2)):
    """CoACD's approximate convex decomposition: [(V, F)] hulls. max_hull_verts keeps each hull under Unity's 255-
    triangle limit for a convex MeshCollider (a hull of n vertices has at most 2n - 4 triangles).

    A generated model is not manifold, so CoACD remeshes it first (a voxel remesh at remesh_resolution, which also
    fuses its thousands of loose boards and fragments into one solid). Its time is mostly the search and the merging
    of pieces down to max_hulls: with its defaults a Tripo barn took nearly three minutes, with search (MCTS
    iterations, nodes, depth) = (30, 8, 2) and a remesh at 30 about 40 s. A dense input is cut to simplify_to
    triangles first with PyMeshLab when it is installed: a collider needs no detail."""
    import coacd
    np = _np()
    V, F, _ = weld(V, F, max(float(np.linalg.norm(np.ptp(np.asarray(V), axis=0))) * 1e-6, 1e-9))
    if simplify_to and len(F) > simplify_to:
        try:
            V, F, _, _ = decimate(V, F, None, None, simplify_to, preserve_boundary=False)
        except ImportError:
            pass
    try:
        coacd.set_log_level("error")
    except Exception:
        pass
    iters, nodes, depth = search
    mesh = coacd.Mesh(np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int32))
    parts = coacd.run_coacd(mesh, threshold=threshold, max_convex_hull=int(max_hulls), resolution=int(resolution),
                            max_ch_vertex=int(max_hull_verts), decimate=True, merge=True, seed=int(seed),
                            preprocess_mode="auto", preprocess_resolution=int(remesh_resolution),
                            mcts_iterations=int(iters), mcts_nodes=int(nodes), mcts_max_depth=int(depth))
    return [(np.asarray(v, dtype=np.float64), np.asarray(f, dtype=np.int64)) for v, f in parts]


def collider_names(name, count):
    """<Name>_Collider_00, _01, ...: the objects a game's importer turns into convex MeshColliders."""
    return ["%s_Collider_%02d" % (name, i) for i in range(count)]


# ------------------------------------------------------------------ manifold3d: cutting a mesh by a solid

class Solid:
    """A cutting solid: a box or a disc (a cylinder along an axis), as manifold3d builds it and as a point test, so a
    shell that cannot be cut exactly is still sorted by where its faces lie."""

    def __init__(self, kind, **p):
        self.kind, self.p = kind, p

    @classmethod
    def box(cls, lo, hi):
        return cls("box", lo=tuple(map(float, lo)), hi=tuple(map(float, hi)))

    @classmethod
    def disc(cls, centre, axis, radius, slab):
        np = _np()
        a = np.asarray(axis, dtype=np.float64); a = a / np.linalg.norm(a)
        return cls("disc", centre=tuple(map(float, centre)), axis=tuple(a.tolist()), radius=float(radius),
                   slab=(float(slab[0]), float(slab[1])))

    def contains(self, P):
        np = _np()
        P = np.asarray(P, dtype=np.float64)
        if self.kind == "box":
            return np.all((P >= np.asarray(self.p["lo"])) & (P <= np.asarray(self.p["hi"])), axis=1)
        d = P - np.asarray(self.p["centre"]); a = np.asarray(self.p["axis"])
        t = d @ a
        r = np.linalg.norm(d - t[:, None] * a, axis=1)
        return (r <= self.p["radius"]) & (t >= self.p["slab"][0]) & (t <= self.p["slab"][1])

    def manifold(self, nprop=3, segments=64):
        import manifold3d as m3
        np = _np()
        if self.kind == "box":
            lo, hi = np.asarray(self.p["lo"]), np.asarray(self.p["hi"])
            s = m3.Manifold.cube(tuple((hi - lo).tolist()), False).translate(tuple(lo.tolist()))
        else:
            t0, t1 = self.p["slab"]
            s = m3.Manifold.cylinder(t1 - t0, self.p["radius"], -1.0, int(segments), False).translate((0.0, 0.0, t0))
            a = np.asarray(self.p["axis"]); z = np.array([0.0, 0.0, 1.0])
            R = _rotation_between(z, a)
            M = np.zeros((3, 4)); M[:, :3] = R; M[:, 3] = self.p["centre"]
            s = s.transform(M.astype(np.float32))
        if nprop > 3:
            m = s.to_mesh()
            vp = np.zeros((len(m.vert_properties), nprop), dtype=np.float32); vp[:, :3] = m.vert_properties[:, :3]
            s = m3.Manifold(m3.Mesh(vert_properties=vp, tri_verts=np.asarray(m.tri_verts, dtype=np.uint32)))
        return s


def _rotation_between(a, b):
    np = _np()
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b)
    v = np.cross(a, b); c = float(a @ b)
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k * (1.0 / (1.0 + c))


def _manifold_of(V, F, UV, tol):
    """A manifold3d Manifold of one shell with its UVs as properties (x, y, z, u, v), each triangle's original index as
    its face id; None when the shell is not a closed 2-manifold even after its seams are merged."""
    import manifold3d as m3
    np = _np()
    F = np.asarray(F, dtype=np.int64)
    if UV is None:
        vp = np.asarray(V, dtype=np.float32); tri = F.astype(np.uint32)
    else:
        uv = np.asarray(UV, dtype=np.float64).reshape(-1, 2)
        corner = np.column_stack([F.reshape(-1), np.round(uv * 1e6).astype(np.int64)])
        uniq, first, inv = np.unique(corner, axis=0, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        vp = np.column_stack([np.asarray(V)[F.reshape(-1)[first]], uv[first]]).astype(np.float32)
        tri = inv.reshape(-1, 3).astype(np.uint32)
    mesh = m3.Mesh(vert_properties=np.ascontiguousarray(vp), tri_verts=np.ascontiguousarray(tri),
                   face_id=np.arange(len(F), dtype=np.uint32), tolerance=float(tol))
    mesh.merge()
    M = m3.Manifold(mesh)
    if M.status() != m3.Error.NoError or M.is_empty():
        return None
    return M


def _merged(mesh):
    """A manifold3d output mesh as (V, F) with one vertex per position: its merge vectors join the copies it made where
    properties differ (a position-only weld would also join vertices it kept apart on purpose, where two parts touch)."""
    np = _np()
    vp = np.asarray(mesh.vert_properties, dtype=np.float64)
    tri = np.asarray(mesh.tri_verts, dtype=np.int64)
    to = np.arange(len(vp))
    mf, mt = mesh.merge_from_vert, mesh.merge_to_vert
    if mf is not None and len(mf):
        to[np.asarray(mf, dtype=np.int64)] = np.asarray(mt, dtype=np.int64)
    used, inv = np.unique(to[tri].reshape(-1), return_inverse=True)
    return vp[used, :3], inv.reshape(-1, 3)


def _from_manifold(M, orig_id, UV, FD_shell, V_shell):
    """Back to (V, F, UV, FD): faces from the shell keep their material; the new cap faces take the shell's most
    common material and the UV of the nearest shell vertex (a cut through a tyre shows the tyre's colour inside)."""
    np = _np()
    m = M.to_mesh()
    vp = np.asarray(m.vert_properties, dtype=np.float64); tri = np.asarray(m.tri_verts, dtype=np.int64)
    if not len(tri):
        return None
    fid = np.asarray(m.face_id, dtype=np.int64) if m.face_id is not None and len(m.face_id) else np.full(len(tri), -1)
    run_index = np.asarray(m.run_index, dtype=np.int64); run_orig = np.asarray(m.run_original_id, dtype=np.int64)
    mine = np.zeros(len(tri), dtype=bool)
    for k in range(len(run_orig)):
        if run_orig[k] == orig_id:
            mine[run_index[k] // 3:run_index[k + 1] // 3] = True
    V = vp[:, :3]
    uv = None
    V_w, tri_w = _merged(m)
    if UV is not None and vp.shape[1] >= 5:
        uv = vp[tri.reshape(-1), 3:5].copy()
        cap = np.repeat(~mine, 3)
        if cap.any():
            pts = V[tri.reshape(-1)[cap]]
            src = np.asarray(V_shell, dtype=np.float64)
            src_uv = np.asarray(UV, dtype=np.float64).reshape(-1, 2)
            # the UV of the nearest shell corner, in chunks
            corners = src.reshape(-1, 3) if src.ndim == 3 else src
            near = np.empty(len(pts), dtype=np.int64)
            step = max(1, 4_000_000 // max(len(corners), 1))
            for s in range(0, len(pts), step):
                d = ((pts[s:s + step, None, :] - corners[None, :, :]) ** 2).sum(-1)
                near[s:s + step] = d.argmin(1)
            uv[cap] = src_uv[near]
    fd = None
    if FD_shell is not None:
        FD_shell = np.asarray(FD_shell)
        common = int(np.bincount(FD_shell).argmax()) if len(FD_shell) else 0
        fd = np.full(len(tri), common, dtype=np.int64)
        ok = mine & (fid >= 0) & (fid < len(FD_shell))
        fd[ok] = FD_shell[fid[ok]]
    return V_w, tri_w, uv, fd


def _concat(parts):
    np = _np()
    parts = [p for p in parts if p is not None and len(p[1])]
    if not parts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), None, None
    Vs, Fs, UVs, FDs, off = [], [], [], [], 0
    for V, F, UV, FD in parts:
        Vs.append(V); Fs.append(np.asarray(F) + off); off += len(V)
        UVs.append(UV); FDs.append(FD)
    UV = None if any(u is None for u in UVs) else np.concatenate(UVs)
    FD = None if any(f is None for f in FDs) else np.concatenate(FDs)
    return np.concatenate(Vs), np.concatenate(Fs), UV, FD


def _subset(V, F, UV, FD, faces):
    np = _np()
    faces = np.asarray(faces, dtype=np.int64)
    sub = np.asarray(F)[faces]
    used, inv = np.unique(sub.reshape(-1), return_inverse=True)
    corner = (faces[:, None] * 3 + np.arange(3)).reshape(-1)
    return (np.asarray(V)[used], inv.reshape(-1, 3),
            None if UV is None else np.asarray(UV).reshape(-1, 2)[corner],
            None if FD is None else np.asarray(FD)[faces])


def split(V, F, UV, FD, solid, segments=64):
    """Cuts the mesh by `solid` into (inside, outside), each (V, F, UV, FD), with a report. Shell by shell: a shell the
    solid does not touch goes whole to the side its faces lie on; a shell it crosses is cut exactly with manifold3d
    (closed where it is cut, the caps textured from the nearest surface) when it is a closed 2-manifold once its
    seams are merged, and otherwise its faces are sorted by their centres, as the plain path does. Without the
    manifold3d extra every crossing shell is sorted by its faces."""
    np = _np()
    try:
        import manifold3d  # noqa: F401
        exact = True
    except ImportError:
        exact = False
    V = np.asarray(V, dtype=np.float64); F = np.asarray(F, dtype=np.int64)
    size = float(np.linalg.norm(np.ptp(V, axis=0))) if len(V) else 1.0
    tol = size * 1e-6
    Vw, Fw, _ = weld(V, F, tol)
    shell = face_components(Fw, len(Vw))
    centres = V[F].mean(axis=1)
    inside_face = solid.contains(centres)
    cutter = None
    ins, outs = [], []
    report = {"shells": int(shell.max() + 1) if len(shell) else 0, "cut_exactly": 0, "sorted_by_faces": 0,
              "whole_inside": 0, "whole_outside": 0}
    for k in range(report["shells"]):
        faces = np.nonzero(shell == k)[0]
        flags = inside_face[faces]
        if flags.all() or not flags.any():
            # whole, unless the solid still crosses it without a face centre inside (rare: a big face)
            (ins if flags.all() else outs).append(_subset(V, F, UV, FD, faces))
            report["whole_inside" if flags.all() else "whole_outside"] += 1
            continue
        sV, sF, sUV, sFD = _subset(V, F, UV, FD, faces)
        M = _manifold_of(sV, sF, sUV, tol) if exact else None
        if M is not None:
            if cutter is None:
                cutter = solid.manifold(nprop=5 if UV is not None else 3, segments=segments)
            M = M.as_original(); oid = M.original_id()
            a = M ^ cutter; b = M - cutter
            ins.append(_from_manifold(a, oid, sUV, sFD, sV[sF].reshape(-1, 3)))
            outs.append(_from_manifold(b, oid, sUV, sFD, sV[sF].reshape(-1, 3)))
            report["cut_exactly"] += 1
        else:
            ins.append(_subset(V, F, UV, FD, faces[flags]))
            outs.append(_subset(V, F, UV, FD, faces[~flags]))
            report["sorted_by_faces"] += 1
    return _concat(ins), _concat(outs), report

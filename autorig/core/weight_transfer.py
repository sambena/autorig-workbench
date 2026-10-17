# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: robust skin-weight transfer from a clean proxy onto the mesh it was made from.
#
# The algorithm is "Robust Skin Weights Transfer via Weight Inpainting" (Abdrashitov, Raichstat, Monsen, Hill;
# SIGGRAPH Asia 2023 Technical Communications), reimplemented here in numpy alone after the authors' MIT reference
# code (github.com/rin-23/RobustSkinWeightsTransferCode), so it runs inside Blender's bundled Python, which has numpy
# but neither scipy nor libigl:
#
#   1. match: every target vertex takes the weights of the closest point on the source surface (barycentric over
#      its triangle) when that point is near enough and its normal agrees (either way round: a generated mesh's
#      normals often point inwards on some of its shells);
#   2. inpaint: the vertices left unmatched (inner shells, folds, pieces that dip inside the body) get weights that
#      minimise a smoothness energy trace(W^T Q W), Q = K M^-1 K (K the cotangent stiffness matrix, M the lumped
#      mass), with the matched weights held fixed; solved by Jacobi-preconditioned conjugate gradients, warm-started
#      from the plain nearest-point weights;
#   3. clean: weights clamped to [0, 1] and renormalised; a connected piece with no matched vertex keeps its
#      nearest-point weights, since nothing on it can anchor the solve.
#
# Pure numpy; the Blender side (rerig.skin) finds the closest points with mathutils' BVHTree and passes them in.
import numpy as np


def closest_on_triangles(P, A, B, C):
    """Closest point to each P[i] on triangle (A[i], B[i], C[i]), and its barycentric coordinates (Ericson,
    Real-Time Collision Detection 5.1.5), vectorised. Returns (Q, bary) with Q (n, 3) and bary (n, 3)."""
    P, A, B, C = (np.asarray(x, dtype=np.float64) for x in (P, A, B, C))
    ab, ac, ap = B - A, C - A, P - A
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = P - B
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = P - C
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    n = len(P)
    bary = np.zeros((n, 3))
    done = np.zeros(n, bool)

    def put(mask, u, v, w):
        m = mask & ~done
        bary[m, 0], bary[m, 1], bary[m, 2] = u[m] if np.ndim(u) else u, v[m] if np.ndim(v) else v, w[m] if np.ndim(w) else w
        done[m] = True

    with np.errstate(divide="ignore", invalid="ignore"):
        put((d1 <= 0) & (d2 <= 0), 1.0, 0.0, 0.0)                                   # vertex A
        put((d3 >= 0) & (d4 <= d3), 0.0, 1.0, 0.0)                                  # vertex B
        t = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), 1 - t, t, np.zeros(n))               # edge AB
        put((d6 >= 0) & (d5 <= d6), 0.0, 0.0, 1.0)                                  # vertex C
        t = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), 1 - t, np.zeros(n), t)               # edge AC
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), np.zeros(n), 1 - t, t)  # edge BC
        den = va + vb + vc
        v, w = vb / den, vc / den
        put(np.ones(n, bool), 1 - v - w, v, w)                                      # inside the face
    bary = np.nan_to_num(bary, nan=1.0 / 3)
    Q = bary[:, :1] * A + bary[:, 1:2] * B + bary[:, 2:] * C
    return Q, bary


def nearest_brute(P, V, F):
    """Closest point on mesh (V, F) to each of P, by trying every triangle: (face, point). For tests and small
    meshes; Blender uses its BVHTree."""
    P = np.asarray(P, float)
    best_d = np.full(len(P), np.inf)
    best_f = np.zeros(len(P), int)
    best_q = np.zeros((len(P), 3))
    for f, (a, b, c) in enumerate(np.asarray(F)):
        k = len(P)
        q, _ = closest_on_triangles(P, np.repeat(V[a][None], k, 0), np.repeat(V[b][None], k, 0), np.repeat(V[c][None], k, 0))
        d = ((q - P) ** 2).sum(1)
        m = d < best_d
        best_d[m], best_f[m], best_q[m] = d[m], f, q[m]
    return best_f, best_q


def face_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)


def vertex_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])   # area-weighted
    out = np.zeros_like(V, dtype=np.float64)
    for k in range(3): np.add.at(out, F[:, k], n)
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-20)


def match(P, N, V, F, W, face, point, max_dist, max_angle_deg=30.0, flip=True):
    """Step 1. P, N: target vertices and normals; (V, F, W): the source mesh and its per-vertex weights (nv, G);
    face, point: each target vertex's closest source triangle and the closest point on it. Returns (weights
    (n, G), matched (n,) bool)."""
    F = np.asarray(F)
    tri = F[face]
    _, bary = closest_on_triangles(point, V[tri[:, 0]], V[tri[:, 1]], V[tri[:, 2]])
    Wt = (bary[:, :, None] * W[tri]).sum(1)
    dist = np.linalg.norm(np.asarray(point) - P, axis=1)
    fn = face_normals(V, F)[face]
    cos = (fn * N).sum(1)
    if flip: cos = np.abs(cos)
    ok = (dist <= max_dist) & (cos >= np.cos(np.radians(max_angle_deg)))
    return Wt, ok


# ---------------------------------------------------------------- sparse matrices, numpy only

class Sparse:
    """A square sparse matrix in CSR-like form: rows sorted, duplicates summed. Enough for a matvec and a diagonal."""
    def __init__(self, n, rows, cols, vals):
        rows, cols, vals = np.asarray(rows, np.int64), np.asarray(cols, np.int64), np.asarray(vals, np.float64)
        key = rows * n + cols
        uk, inv = np.unique(key, return_inverse=True)
        self.vals = np.bincount(inv, weights=vals, minlength=len(uk))
        self.rows, self.cols, self.n = uk // n, uk % n, n
        self.starts = np.searchsorted(self.rows, np.arange(n))
        self.has = np.bincount(self.rows, minlength=n) > 0

    def dot(self, X):
        X = np.asarray(X, np.float64)
        prod = self.vals[:, None] * X.reshape(len(X), -1)[self.cols] if len(self.vals) else np.zeros((0, X.reshape(len(X), -1).shape[1]))
        out = np.zeros((self.n, prod.shape[1]))
        if len(self.vals):
            s = self.starts[self.has]
            out[self.has] = np.add.reduceat(prod, s, axis=0)
        return out.reshape((self.n,) + X.shape[1:])

    def diagonal(self):
        d = np.zeros(self.n)
        m = self.rows == self.cols
        d[self.rows[m]] = self.vals[m]
        return d


def cotan_stiffness(V, F, cap=1e3):
    """K (positive semi-definite: K = D - A) with cotangent edge weights clamped to [0, cap], so a degenerate or
    obtuse triangle of a generated mesh can neither go negative nor blow up; and the lumped mass (a third of each
    triangle's area per corner, floored at a small fraction of the mean)."""
    V, F = np.asarray(V, np.float64), np.asarray(F, np.int64)
    n = len(V)
    I, J, Wc = [], [], []
    for k in range(3):
        i, j, o = F[:, (k + 1) % 3], F[:, (k + 2) % 3], F[:, k]
        u, v = V[i] - V[o], V[j] - V[o]
        cr = np.linalg.norm(np.cross(u, v), axis=1)
        cot = (u * v).sum(1) / np.maximum(cr, 1e-12)
        I.append(i); J.append(j); Wc.append(np.clip(0.5 * cot, 0.0, cap))
    I, J, Wc = np.concatenate(I), np.concatenate(J), np.concatenate(Wc)
    # a graph floor keeps every edge connected even where all its cotangents clamp to 0 (a sliver)
    Wc = Wc + 1e-3
    rows = np.concatenate([I, J, I, J]); cols = np.concatenate([J, I, I, J])
    vals = np.concatenate([-Wc, -Wc, Wc, Wc])
    K = Sparse(n, rows, cols, vals)
    area = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    M = np.zeros(n)
    for k in range(3): np.add.at(M, F[:, k], area / 3)
    M = np.maximum(M, 1e-3 * max(M.mean(), 1e-20))
    return K, M


def components(n, F):
    """Connected-piece label per vertex (union-find over the triangles' edges)."""
    parent = np.arange(n)
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    F = np.asarray(F)
    for a, b in np.concatenate([F[:, [0, 1]], F[:, [1, 2]]]).tolist():
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def inpaint(V, F, W0, known, iters=2000, tol=1e-6):
    """Step 2. Weights for the unknown vertices minimising trace(W^T Q W), Q = K M^-1 K, with the known rows of W0
    fixed; W0's unknown rows are the starting guess. Returns (W, stats)."""
    V, F = np.asarray(V, np.float64), np.asarray(F, np.int64)
    n = len(V)
    W = np.array(W0, dtype=np.float64)
    known = np.asarray(known, bool).copy()
    # A piece with nothing matched has no anchor: keep its starting weights (the nearest-point ones).
    comp = components(n, F)
    anchored = np.isin(comp, np.unique(comp[known]))
    orphan = ~anchored & ~known
    known |= orphan
    unk = np.nonzero(~known)[0]
    stats = {"unknown": int(len(unk)), "orphan": int(orphan.sum()), "iterations": 0, "residual": 0.0}
    if not len(unk):
        return W, stats
    K, M = cotan_stiffness(V, F)
    Minv = 1.0 / M

    def Q(X):                       # Q X = K M^-1 K X, on full-size X
        return K.dot(Minv[:, None] * K.dot(X))

    def Quu(Xu):
        X = np.zeros_like(W); X[unk] = Xu
        return Q(X)[unk]

    # diagonal of Q for the preconditioner: Q_ii = sum_k K_ik^2 / M_k
    dQ = np.bincount(K.rows, weights=K.vals ** 2 * Minv[K.cols], minlength=n)
    # (K symmetric, so summing over row i's entries K_ik^2 / M_k is the same as over column i's)
    Pinv = 1.0 / np.maximum(dQ[unk], 1e-30)
    Wk = W.copy(); Wk[unk] = 0.0
    b = -Q(Wk)[unk]
    x = W[unk]
    r = b - Quu(x)
    z = Pinv[:, None] * r
    p = z.copy()
    rz = (r * z).sum(0)
    bnorm = np.maximum(np.linalg.norm(b, axis=0), 1e-12)
    it = 0
    for it in range(1, iters + 1):
        Ap = Quu(p)
        pAp = (p * Ap).sum(0)
        alpha = np.where(np.abs(pAp) > 1e-30, rz / np.where(np.abs(pAp) > 1e-30, pAp, 1), 0.0)
        x += alpha * p
        r -= alpha * Ap
        res = float((np.linalg.norm(r, axis=0) / bnorm).max())
        if res < tol: break
        z = Pinv[:, None] * r
        rz_new = (r * z).sum(0)
        beta = np.where(np.abs(rz) > 1e-30, rz_new / np.where(np.abs(rz) > 1e-30, rz, 1), 0.0)
        p = z + beta * p
        rz = rz_new
    W[unk] = x
    stats["iterations"], stats["residual"] = it, round(res, 8)
    return W, stats


def normalise(W, floor=1e-4):
    """Step 3: clamp to [0, 1], drop dust under `floor`, rows summing to 1 (a row left empty stays empty)."""
    W = np.clip(np.asarray(W, np.float64), 0.0, 1.0)
    W[W < floor] = 0.0
    s = W.sum(1, keepdims=True)
    return np.where(s > 0, W / np.maximum(s, 1e-12), 0.0)


def robust_transfer(P, N, Ft, V, F, W, face, point, max_dist, max_angle_deg=30.0, flip=True, iters=2000):
    """The whole transfer. Target: vertices P, normals N, triangles Ft. Source: (V, F) with weights W (nv, G).
    face, point: each target vertex's closest source triangle and point. Returns (weights (n, G), stats)."""
    P, V, W = np.asarray(P, np.float64), np.asarray(V, np.float64), np.asarray(W, np.float64)
    Wt, ok = match(P, np.asarray(N, np.float64), V, np.asarray(F), W, np.asarray(face), np.asarray(point, np.float64),
                   max_dist, max_angle_deg, flip)
    stats = {"matched": int(ok.sum()), "verts": int(len(P))}
    if len(Ft) and not ok.all():
        Wt, s = inpaint(P, Ft, Wt, ok, iters=iters)
        stats.update(s)
    return normalise(Wt), stats

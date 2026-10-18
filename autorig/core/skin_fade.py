# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: weights faded across every joint just as much as a bend there needs, at most four influences
# per vertex. Plain numpy (no bpy), so the tests run it without Blender; steps/m2m_rig.skin() reads the weights out
# of the mesh, calls fade_rounds() and writes them back.
#
# Why. Turning bone j by an angle a about an axis through its head h moves a vertex v by up to 2 sin(a/2) |v - h|
# F_j(v), where F_j(v) is the weight v takes from j and everything hanging from it (its subtree). The two ends of an
# edge of length e pull apart by that times the difference in F_j across the edge. The audit calls an edge torn when
# it more than doubles and the gap it opens is over GAP (0.4%) of the model's size, so an edge stays whole while
#     |dF_j| <= max(e, GAP x size) / (2 sin(a/2) |v - h|).
# A tear far from the joint needs only a small step. In the M2b bake-off, bending the Tripo hen's spine_1 tore its
# tail 0.6 of the model away, where the tail took 5-20% from the wings and the spine in steps (the gate's cuts and
# the four-influence limit). Ring smoothing (six passes) blurred about two rings, nowhere near enough; a diffusion
# wide enough for that (tried first) also blurred the limbs' own skin, so a dog's hind foot moved only 60% with its
# thigh. So each F_j is changed only where it breaks the bound, as little as it can be:
#   1. F_j for every bone from the weights given (bone heat, gated): own weight plus its children's F.
#   2. Each deforming bone's F_j made Lipschitz in the bound's own measure: an edge costs
#      max(e, GAP x size) x margin / (2 sin(a/2) |mid - h_j|); the lower envelope is max over u of F(u) - cost(u, v)
#      and the upper min over u of F(u) + cost(u, v) (shortest paths, by min-plus relaxation over the edges), and
#      F_j becomes their mean. Both envelopes keep the bound, so their mean does; where F_j already keeps it, both
#      equal F_j and nothing moves. A 0-to-1 step becomes a ramp of cost 1 across, centred on the step.
#   3. Nested again from the top: a bone's children together never take more than it does (scaled down where they
#      would), and a bone that deforms nothing (a girdle, a leaf tip) is exactly the sum of its children.
#   4. Each bone's weight is its F less its children's.
# The fade makes more influences where joints' ramps overlap (on the hen, 59% of vertices had more than four, the
# fifth about 6%), and limiting them to four again cuts steps of that size: one fade then the limit undid most of it.
# fade_rounds therefore alternates: fade, limit (limit_up: the smallest weight goes to its bone's nearest deforming
# ancestor, so only that bone's F changes), fade again on what the limit left, and so on. Each limit gathers weight
# into fewer bones, so each fade needs fewer, and the steps left shrink round by round (bake-off: 20 rounds took the
# hen from 2869 combined tears to 909, the dragon from 4388 to 1196). Measured from the head, not the bend axis:
# measuring from each bone's X axis (as the audit bends it) left a person's spine unfaded along the arms and tore more.
import numpy as np

GAP = 0.004          # the audit's tear gap, a share of the model's size (core/grades.py)


def _order(parent):
    """Bones parents first (by depth)."""
    depth = []
    for i in range(len(parent)):
        d, p = 0, parent[i]
        while p >= 0: d += 1; p = parent[p]
        depth.append(d)
    return sorted(range(len(parent)), key=lambda i: depth[i])


def subtree_sums(W, parent):
    """F: each bone's weight plus everything below it, per vertex (columns as W's)."""
    F = W.copy()
    for i in reversed(_order(parent)):
        if parent[i] >= 0: F[:, parent[i]] += F[:, i]
    return F


def _directed(E):
    """Both directions of every edge, sorted by source: (src, dst, the rows that have edges, where each starts)."""
    src = np.concatenate([E[:, 0], E[:, 1]]); dst = np.concatenate([E[:, 1], E[:, 0]])
    o = np.argsort(src, kind="stable")
    src, dst = src[o], dst[o]
    rows, starts = np.unique(src, return_index=True)
    return src, dst, rows, starts


def lower_envelope(F, cost, graph, iters=400, tol=1e-4):
    """max over u of F(u) - cost(u, v), per column: shortest paths from every vertex at once, by relaxing every edge
    until no weight moves by more than tol. cost: per directed edge (graph's order) and column. Returns it and the
    passes taken."""
    src, dst, rows, starts = graph
    G = F.copy()
    for it in range(iters):
        best = np.maximum.reduceat(G[dst] - cost, starts, axis=0)
        new = np.maximum(G[rows], best)
        step = float((new - G[rows]).max()) if new.size else 0.0
        G[rows] = new
        if step <= tol: return G, it + 1
    return G, iters


class _Mesh:
    """What fade() needs of the mesh, worked out once for every round."""

    def __init__(self, P, E, heads, angle, margin):
        self.S = float((P.max(0) - P.min(0)).max()) or 1.0
        self.graph = _directed(E)
        src, dst = self.graph[0], self.graph[1]
        e = np.linalg.norm(P[src] - P[dst], axis=1)
        self.mid = (P[src] + P[dst]) * 0.5
        self.allow = np.maximum(e, GAP * self.S) * margin / (2 * np.sin(np.radians(angle) / 2))
        self.heads = heads
        self.cost = {}

    def cost_of(self, cols):
        for j in cols:
            if j not in self.cost:
                r = np.linalg.norm(self.mid - self.heads[j], axis=1)
                self.cost[j] = self.allow / np.maximum(r, 1e-6 * self.S)
        return np.stack([self.cost[j] for j in cols], axis=1)


def fade(P, E, W, parent, deform, heads, angle=40.0, margin=0.7, iters=400, mesh=None):
    """Weights (n x bones, columns in the bones' order; non-deforming bones' columns 0) faded across every joint as
    far as a bend of `angle` degrees there needs, with `margin` (< 1) of the audit's bound. P the vertices, E the edges
    (pairs), parent each bone's parent index (-1 the root), deform which bones may take weight, heads each bone's
    head. Returns (weights, relaxation passes)."""
    n, nb = W.shape
    if n < 3 or not len(E) or angle <= 0: return W.copy(), 0
    m = mesh or _Mesh(P, E, heads, angle, margin)
    F = subtree_sums(W, parent)
    # the joints worth fading: deforming bones below the root whose F is not 0 or 1 everywhere
    cols = [j for j in range(nb) if deform[j] and parent[j] >= 0 and F[:, j].max() > 1e-4 and F[:, j].min() < 1 - 1e-4]
    passes = 0
    if cols:
        cost = m.cost_of(cols)
        Fc = F[:, cols]
        lo, p1 = lower_envelope(Fc, cost, m.graph, iters)
        hi, p2 = lower_envelope(1.0 - Fc, cost, m.graph, iters)
        F[:, cols] = np.clip((lo + (1.0 - hi)) * 0.5, 0.0, 1.0)
        passes = p1 + p2
    order = _order(parent)
    kids = [[] for _ in range(nb)]
    for i in range(nb):
        if parent[i] >= 0: kids[parent[i]].append(i)
    zero = np.zeros(n)
    # a bone that deforms nothing is the sum of its children (bottom up, so a girdle over a girdle adds up too)
    for i in reversed(order):
        if not deform[i]: F[:, i] = sum((F[:, c] for c in kids[i]), zero)
    # nested from the top: the children never take more than their parent
    for i in order:
        if not kids[i] or parent[i] < 0: continue
        s = sum(F[:, c] for c in kids[i])
        f = np.where(s > F[:, i], F[:, i] / np.maximum(s, 1e-12), 1.0)
        for c in kids[i]: F[:, c] *= f
    out = np.zeros_like(W)
    for i in range(nb):
        if deform[i]:
            out[:, i] = np.maximum(F[:, i] - sum((F[:, c] for c in kids[i]), zero), 0.0)
    out /= np.maximum(out.sum(1, keepdims=True), 1e-12)
    return out, passes


def limit_up(W, parent, deform, most=4, least=0.01):
    """At most `most` influences per vertex, none under `least`: one at a time, the smallest weight goes to its bone's
    nearest deforming ancestor (which may then be one of the kept; the largest other bone when it has none), until it
    fits. Moving bone d's weight up changes only d's subtree weight. Every vertex at once, one move each per pass.
    Rows renormalised to 1."""
    W = W.copy()
    nb = W.shape[1]
    up = []
    for i in range(nb):
        p = parent[i]
        while p >= 0 and not deform[p]: p = parent[p]
        up.append(p)
    up = np.array(up)
    for _ in range(4 * nb):
        nz = W > 0
        cnt = nz.sum(1)
        small = nz & (W < least)
        rows = np.nonzero(((cnt > most) | small.any(1)) & (cnt > 1))[0]
        if not len(rows): break
        sub = W[rows]
        # the smallest weight of each row (among the small ones when there are any)
        pick = np.where(small[rows].any(1)[:, None], np.where(small[rows], sub, np.inf),
                        np.where(sub > 0, sub, np.inf))
        j = pick.argmin(1)
        dest = up[j].copy()
        lone = dest < 0
        if lone.any():
            other = np.where(sub[lone] > 0, sub[lone], -np.inf)
            other[np.arange(lone.sum()), j[lone]] = -np.inf
            dest[lone] = other.argmax(1)
        moved = sub[np.arange(len(rows)), j]
        W[rows, j] = 0.0
        np.add.at(W, (rows, dest), moved)
    W /= np.maximum(W.sum(1, keepdims=True), 1e-12)
    return W


def near_share(P, heads, tails, deform, inner=1.8, outer=3.0):
    """How much of its weight each vertex may keep from each bone (n x bones, 0..1): all of it from bones within
    inner x its nearest bone's distance, none past outer x, a straight ramp between. steps/m2m_rig.gate's cut (keep
    within 1.8x, the nearest's neighbours too) is a step where a bone crosses the line, and a step is a tear; the ramp
    is not."""
    nb = len(deform)
    D = np.full((len(P), nb), np.inf)
    for j in range(nb):
        if not deform[j]: continue
        d = tails[j] - heads[j]
        f = np.clip(((P - heads[j]) @ d) / max(float(d @ d), 1e-12), 0.0, 1.0)
        D[:, j] = np.linalg.norm(P - (heads[j] + f[:, None] * d), axis=1)
    ratio = D / np.maximum(D.min(1, keepdims=True), 1e-9)
    return np.clip((outer - ratio) / (outer - inner), 0.0, 1.0)


def fade_rounds(P, E, W, parent, deform, heads, angle=40.0, rounds=20, most=4, margin=0.7, near=None):
    """fade() and limit_up() in turn, `rounds` times, ending on the limit: the weights to write, at most `most`
    influences each. near (near_share): from the second round on, each weight is first scaled by how much of it the
    vertex may keep and the row renormalised, so the rounds cannot carry skin off to a far bone (without it a cat's
    right thigh took the inside of its left leg, a hen's left wing her right). Returns (weights, relaxation passes)."""
    m = _Mesh(P, E, heads, angle, margin) if len(P) >= 3 and len(E) and angle > 0 else None
    passes = 0
    for r in range(max(1, rounds)):
        if near is not None and r:
            kept = W * near
            ks = kept.sum(1, keepdims=True)
            W = np.where(ks > 1e-6, kept / np.maximum(ks, 1e-12), W)
        if m is not None:
            W, p = fade(P, E, W, parent, deform, heads, angle, margin, mesh=m)
            passes += p
        W = limit_up(W, parent, deform, most)
    return W, passes


def over_bound(P, E, W, parent, heads, angle=40.0, margin=1.0, bones=None):
    """{bone index: edges whose subtree weight changes faster than a bend of `angle` there allows (margin 1: the
    audit's own tear bound)}, for the bones given (default every bone below the root): what fade() should leave 0."""
    n, nb = W.shape
    if n < 3 or not len(E): return {}
    S = float((P.max(0) - P.min(0)).max()) or 1.0
    F = subtree_sums(W, parent)
    e = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
    mid = (P[E[:, 0]] + P[E[:, 1]]) * 0.5
    allow = np.maximum(e, GAP * S) * margin / (2 * np.sin(np.radians(angle) / 2))
    out = {}
    for j in (bones if bones is not None else [j for j in range(nb) if parent[j] >= 0]):
        r = np.linalg.norm(mid - heads[j], axis=1)
        c = int((np.abs(F[E[:, 0], j] - F[E[:, 1], j]) * r > allow).sum())
        if c: out[j] = c
    return out

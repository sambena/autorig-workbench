# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared numeric deformation metrics for audits, LODs and repair acceptance."""
import numpy as np


def seam_pairs(points, tolerance=1e-5, ignored=None, labels=None):
    """Coincident seam twins, excluding explicitly marked cuts and unrelated rigid objects.

    Weight disagreement is deliberately NOT an exclusion: it is a common cause of cracks.
    """
    p = np.asarray(points)
    _, inv = np.unique(np.round(p / tolerance).astype(np.int64), axis=0, return_inverse=True)
    groups, pairs = {}, []
    for i, key in enumerate(inv.ravel()):
        if ignored is not None and ignored[i]: continue
        candidates = groups.setdefault((int(key), labels[i] if labels is not None else None), [])
        match = next((j for j in candidates if np.linalg.norm(p[i] - p[j]) <= tolerance), None)
        if match is not None: pairs.append((match, i))
        else: candidates.append(i)
    return np.asarray(pairs, dtype=np.int64).reshape(-1, 2)


def metrics(rest, posed, edges, triangles=None, pairs=None, scale=None):
    rest, posed, edges = np.asarray(rest), np.asarray(posed), np.asarray(edges, dtype=int).reshape(-1, 2)
    size = float(scale or max(np.ptp(rest, axis=0).max(), 1e-9))
    a = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    b = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)
    ratio = np.divide(b, a, out=np.ones_like(b), where=a > 1e-9)
    tear = (ratio > 2) & (b - a > .004 * size)
    out = {"tear_edges": int(tear.sum()), "max_stretch": float(ratio.max(initial=1)),
           "compressed_edges": int(((ratio < .25) & (a > .001 * size)).sum()),
           "worst_gap_pct": float(max((b-a)[tear], default=0) / size * 100), "seam_gap": 0.0}
    if pairs is not None and len(pairs):
        out["seam_gap"] = float(np.linalg.norm(posed[pairs[:,0]] - posed[pairs[:,1]], axis=1).max(initial=0))
    if triangles is not None and len(triangles):
        t = np.asarray(triangles, dtype=int)
        def areas(p): return np.linalg.norm(np.cross(p[t[:,1]]-p[t[:,0]], p[t[:,2]]-p[t[:,0]]), axis=1)
        ar, aq = areas(rest), areas(posed)
        out["collapsed_triangles"] = int(((ar > size*size*1e-10) & (aq < ar*.05)).sum())
    return out


def fold_edges(rest, posed, triangles, degrees=60.0):
    """Edges where the surface creases or folds over in the pose: the angle between the two faces on an edge changes
    by more than `degrees` from rest. A stretch count misses this. A jacket crumpling at the shoulder when the arms
    come down squashes and folds, but tears nothing (Tornado Titans' cast, 2026-10-17). Returns (count, edge
    indices into a (vertex, vertex) array of the folded edges' end points)."""
    t = np.asarray(triangles, dtype=int).reshape(-1, 3)
    if not len(t): return 0, np.empty((0, 2), dtype=int)
    rest, posed = np.asarray(rest, float), np.asarray(posed, float)
    def normals(p):
        n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        return np.divide(n, ln, out=np.zeros_like(n), where=ln > 1e-12), ln.ravel()
    nr, ar = normals(rest); nq, _ = normals(posed)
    # each interior edge and its two faces (edges with more than two faces are skipped)
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    f = np.tile(np.arange(len(t)), 3)
    order = np.lexsort((e[:, 1], e[:, 0])); e, f = e[order], f[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    first = np.where(same)[0]
    # an edge shared by exactly two faces: the run is two long
    if len(first):
        after = (first + 2 >= len(e)) | ~np.append(same, False)[first + 1]
        before = (first == 0) | ~same[np.maximum(first - 1, 0)]
        two = first[after & before]
    else:
        two = first
    if not len(two): return 0, np.empty((0, 2), dtype=int)
    a, b = f[two], f[two + 1]
    ok = (ar[a] > 1e-12) & (ar[b] > 1e-12)
    cos_r = np.clip(np.sum(nr[a] * nr[b], axis=1), -1, 1)
    cos_q = np.clip(np.sum(nq[a] * nq[b], axis=1), -1, 1)
    change = np.degrees(np.abs(np.arccos(cos_q) - np.arccos(cos_r)))
    hit = ok & (change > degrees)
    return int(hit.sum()), e[two][hit]


def no_regression(before, after):
    """Every sampled pose must avoid worse tearing, compression or seam separation."""
    keys = ("tear_edges", "compressed_edges", "collapsed_triangles", "worst_gap_pct", "seam_gap", "max_stretch")
    return len(before) == len(after) and all(all(b.get(k, 0) <= a.get(k, 0) + 1e-6 for k in keys) for a,b in zip(before, after))

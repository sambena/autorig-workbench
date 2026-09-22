# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: surface geometry helpers for building skeletons where a model came with none: distances
# measured along the mesh's surface find the tips of its limbs, and rings of equal distance between two points give
# the line down the middle of a limb, a tail or a whole serpent.
import heapq
from mathutils import Vector, kdtree

class Surface:
    def __init__(self, mesh, bridge=0.02):
        me = mesh.data
        self.co = [v.co.copy() for v in me.vertices]
        n = len(self.co)
        self.adj = [[] for _ in range(n)]
        for e in me.edges:
            a, b = e.vertices
            d = (self.co[a] - self.co[b]).length
            self.adj[a].append((b, d)); self.adj[b].append((a, d))
        lo = Vector((min(p.x for p in self.co), min(p.y for p in self.co), min(p.z for p in self.co)))
        hi = Vector((max(p.x for p in self.co), max(p.y for p in self.co), max(p.z for p in self.co)))
        self.lo, self.hi, self.size = lo, hi, hi - lo
        # Generated sculpts are often loose pieces pushed into one another: join what touches, or distances stop at every seam.
        parent = list(range(n))
        def find(x):
            while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for e in me.edges:
            a, b = find(e.vertices[0]), find(e.vertices[1])
            if a != b: parent[a] = b
        self.kd = kdtree.KDTree(n)
        for i, p in enumerate(self.co): self.kd.insert(p, i)
        self.kd.balance()
        r = max(self.size) * bridge
        for i, p in enumerate(self.co):
            for _, j, d in self.kd.find_range(p, r):
                if j > i and find(i) != find(j):
                    self.adj[i].append((j, d * 1.5)); self.adj[j].append((i, d * 1.5))
        # pieces still apart (a floating tentacle): one bridge each to the nearest vertex of the main body
        for i in range(n):
            for j, _ in self.adj[i]:
                a, b = find(i), find(j)
                if a != b: parent[a] = b
        groups = {}
        for i in range(n): groups.setdefault(find(i), []).append(i)
        main = max(groups.values(), key=len)
        mkd = kdtree.KDTree(len(main))
        for k, i in enumerate(main): mkd.insert(self.co[i], k)
        mkd.balance()
        for g in groups.values():
            if g is main: continue
            best = min(((mkd.find(self.co[i]), i) for i in g[::max(1, len(g) // 60)]), key=lambda t: t[0][2])
            (_, k, d), i = best
            j = main[k]
            self.adj[i].append((j, d * 1.5)); self.adj[j].append((i, d * 1.5))

    def norm(self, p): return tuple(round((p[k] - self.lo[k]) / max(1e-9, self.size[k]), 2) for k in range(3))
    def point(self, uvw): return Vector(tuple(self.lo[k] + self.size[k] * uvw[k] for k in range(3)))
    def nearest(self, p): return self.kd.find(p)[1]

    def distances(self, sources):
        dist = [float("inf")] * len(self.co)
        heap = []
        for s in sources: dist[s] = 0.0; heap.append((0.0, s))
        heapq.heapify(heap)
        while heap:
            d, i = heapq.heappop(heap)
            if d > dist[i]: continue
            for j, w in self.adj[i]:
                nd = d + w
                if nd < dist[j]: dist[j] = nd; heapq.heappush(heap, (nd, j))
        return dist

    def tips(self, most=16, least=0.16):
        """The ends of whatever sticks out, most prominent first: (vertex, how far it stands from the rest)."""
        centre = (self.lo + self.hi) * 0.5
        seed = max(range(len(self.co)), key=lambda i: (self.co[i] - centre).length)
        d0 = self.distances([seed])
        first = max(range(len(d0)), key=lambda i: d0[i] if d0[i] < float("inf") else -1)
        chosen, near = [first], self.distances([first])
        span = None
        out = [(first, 1.0)]
        while len(chosen) < most:
            nxt = max(range(len(near)), key=lambda i: near[i] if near[i] < float("inf") else -1)
            if span is None: span = near[nxt]
            if near[nxt] < span * least: break
            out.append((nxt, round(near[nxt] / span, 2)))
            chosen.append(nxt)
            dn = self.distances([nxt])
            near = [min(a, b) for a, b in zip(near, dn)]
        return out

    def tube(self, start, end, bones, slack=0.3, first=None):
        """Points down the middle of the mesh between two surface points: start, the rings between, end."""
        a, b = self.nearest(start), self.nearest(end)
        da, db = self.distances([a]), self.distances([b])
        length = da[b]
        if length == float("inf"): return [Vector(start), Vector(end)]
        on = [i for i in range(len(self.co)) if da[i] + db[i] <= length * (1.0 + slack)]
        pts = []
        for k in range(bones + 1):
            t = length * k / bones
            half = length / bones * 0.5
            ring = [self.co[i] for i in on if abs(da[i] - t) <= half]
            if not ring: pts.append(self.co[a].lerp(self.co[b], k / bones)); continue
            lo = Vector((min(p.x for p in ring), min(p.y for p in ring), min(p.z for p in ring)))
            hi = Vector((max(p.x for p in ring), max(p.y for p in ring), max(p.z for p in ring)))
            mean = sum(ring, Vector()) / len(ring)
            pts.append((lo + hi) * 0.25 + mean * 0.5)
        pts[-1] = pts[-1].lerp(self.co[b], 0.7)  # the last ring is a cap: end at the tip itself
        if first is not None: pts[0] = Vector(first)
        return pts

    def junction(self, tip, body, samples=32, jump=1.8):
        """Where a limb leaves the body, walking in from its tip.

        Follows the limb's centreline (tube() from the body to the tip) and measures, at each station, how far it
        is to the nearest surface: the limb's radius while inside the limb, the body's once it gets there. The limb
        starts at the last station before that radius jumps. This is a volume test, so it is not fooled by a limb
        that is a loose piece pushed into the body (an insect's legs, often), which is where surface-distance rings leak.
        Returns (point, fraction of the way from the tip, the limb's radius), or None when there is no clear jump:
        a tail thickening smoothly into the rump.
        """
        pts = self.tube(Vector(body), Vector(tip), samples)
        rad = [self.kd.find(p)[2] for p in pts]
        n = len(pts) - 1
        limb = sorted(rad[n // 2:n - 1])
        if not limb: return None
        ref = limb[len(limb) // 2]
        for k in range(n - 2, 0, -1):          # tip end first
            if rad[k] > jump * ref and k < n * 0.9:
                return pts[k + 1], (n - k - 1) / float(n), ref
        return None


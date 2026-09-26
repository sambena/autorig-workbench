# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: surface geometry helpers for building skeletons where a model came with none: distances
# measured along the mesh's surface find the tips of its limbs, and rings of equal distance between two points give
# the line down the middle of a limb, a tail or a whole serpent.
import heapq, math
try:
    import rig_geom
except ImportError:
    from autorig.core import rig_geom
try:
    from mathutils import Vector, kdtree
except ImportError:
    class Vector(tuple):
        def __new__(cls, coords):
            return super().__new__(cls, tuple(float(c) for c in coords))
        @property
        def x(self): return self[0]
        @property
        def y(self): return self[1]
        @property
        def z(self): return self[2]
        def __add__(self, other):
            return Vector((self[0] + other[0], self[1] + other[1], self[2] + other[2]))
        def __sub__(self, other):
            return Vector((self[0] - other[0], self[1] - other[1], self[2] - other[2]))
        def __mul__(self, scalar):
            return Vector((self[0] * scalar, self[1] * scalar, self[2] * scalar))
        def __rmul__(self, scalar):
            return self.__mul__(scalar)
        def __truediv__(self, scalar):
            return Vector((self[0] / scalar, self[1] / scalar, self[2] / scalar))
        @property
        def length(self):
            return math.sqrt(self[0]**2 + self[1]**2 + self[2]**2)
        def dot(self, other):
            return self[0]*other[0] + self[1]*other[1] + self[2]*other[2]
        def cross(self, other):
            return Vector((
                self[1] * other[2] - self[2] * other[1],
                self[2] * other[0] - self[0] * other[2],
                self[0] * other[1] - self[1] * other[0]
            ))
        def normalized(self):
            l = self.length
            return Vector((0, 0, 0)) if l < 1e-12 else Vector((self[0] / l, self[1] / l, self[2] / l))
        def lerp(self, other, factor):
            return Vector((self[0] + (other[0] - self[0]) * factor,
                           self[1] + (other[1] - self[1]) * factor,
                           self[2] + (other[2] - self[2]) * factor))
        def copy(self):
            return Vector(self)
    kdtree = None

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


    def medial_axis(self, start, end, bones, slack=0.3, first=None, samples=16):
        """Curved volumetric medial axis: traces bone stations inside the mesh volume,
        balancing distances to boundary walls so bones stay centered inside the volume."""
        a, b = self.nearest(start), self.nearest(end)
        da, db = self.distances([a]), self.distances([b])
        length = da[b]
        if length == float("inf"): return [Vector(start), Vector(end)]
        on = [i for i in range(len(self.co)) if da[i] + db[i] <= length * (1.0 + slack)]
        rings, c0s = [], []
        for k in range(bones + 1):
            t = length * k / bones
            half = length / bones * 0.5
            ring = [self.co[i] for i in on if abs(da[i] - t) <= half]
            if not ring or len(ring) < 4:
                rings.append(None); c0s.append(self.co[a].lerp(self.co[b], k / bones))
                continue
            lo = Vector((min(p.x for p in ring), min(p.y for p in ring), min(p.z for p in ring)))
            hi = Vector((max(p.x for p in ring), max(p.y for p in ring), max(p.z for p in ring)))
            mean = sum(ring, Vector()) / len(ring)
            rings.append([tuple(p) for p in ring]); c0s.append((lo + hi) * 0.25 + mean * 0.5)

        # each station is centred across the limb's own local direction (from its neighbours), not the straight line
        # from end to end, which tilts every cross-section of a bent limb or a curled tail
        chord = tuple(self.co[b] - self.co[a])
        first_pass = [tuple(p) for p in c0s]
        pts = []
        for k, (ring, c0) in enumerate(zip(rings, c0s)):
            if ring is None:
                pts.append(c0); continue
            tangent = rig_geom.station_tangent(first_pass, k, chord)
            pts.append(Vector(rig_geom.centre_ring(ring, tuple(c0), tangent)))

        pts[-1] = pts[-1].lerp(self.co[b], 0.7)
        if first is not None: pts[0] = Vector(first)
        # eased across the chain only, and not at all for three bones or fewer: a knee or an elbow is kept
        return [Vector(p) for p in rig_geom.smooth_stations(pts)]

    def find_pinches(self, start, end, slices=40, span_radius=None, min_t=0.2, max_t=0.8):
        """Finds anatomical hinge creases / local cross-section minima between start and end on this surface."""
        s = Vector(start) if not isinstance(start, Vector) else start
        e = Vector(end) if not isinstance(end, Vector) else end
        return find_pinches(self.co, s, e, slices=slices, span_radius=span_radius, min_t=min_t, max_t=max_t)


def find_pinches(coords, start, end, slices=40, span_radius=None, min_t=0.2, max_t=0.8):
    """Finds pinch points (anatomical hinge creases / local cross-section minima) between start and end.
    coords: iterable of (x, y, z) or Vector.
    start, end: (x, y, z) or Vector defining limb or torso axis.
    slices: number of cross-sectional evaluation bins along the axis.
    span_radius: optional maximum radial distance from axis to include.
    min_t, max_t: search interval along the axis (fraction 0..1).
    Returns:
      list of dicts sorted by prominence (most prominent pinch first):
      [{"t": float, "pos": Vector, "radius": float, "prominence": float}, ...]
    """
    s = Vector(start) if not isinstance(start, Vector) else start
    e = Vector(end) if not isinstance(end, Vector) else end
    ab = e - s
    length = ab.length
    if length < 1e-6:
        return []
    d = ab.normalized()
    # coords and start/end must be in the same units (world, or both 0..1). Pass span_radius near a torso, so the
    # body beside a limb does not join its slices
    t3 = tuple(d)
    ref = (0.0, 0.0, 1.0) if abs(t3[2]) < 0.85 else (1.0, 0.0, 0.0)
    u = rig_geom._norm(rig_geom._cross(t3, ref))
    v = rig_geom._norm(rig_geom._cross(t3, u))
    SECTORS = 12

    # Bin vertices into slices, and within a slice by direction around the axis
    bins = [[] for _ in range(slices)]
    for pt in coords:
        p = Vector(pt) if not isinstance(pt, Vector) else pt
        ap = p - s
        proj = ap.dot(d)
        t = proj / length
        if t < min_t or t > max_t:
            continue
        perp = ap - d * proj
        r = perp.length
        if span_radius is not None and r > span_radius:
            continue
        idx = int((t - min_t) / max(1e-9, (max_t - min_t)) * (slices - 1))
        idx = max(0, min(slices - 1, idx))
        ang = math.atan2(rig_geom._dot(tuple(perp), v), rig_geom._dot(tuple(perp), u))
        bins[idx].append((p, r, t, int((ang + math.pi) / (2 * math.pi) * SECTORS) % SECTORS))

    # Slice metrics: the cross-section's radius is the mean distance to its wall over the directions it has (the
    # farthest point each way), so it does not depend on how densely the mesh is cut; the joint is the middle of the
    # wall points, not the centroid of every vertex (a one-sided slice no longer puts the joint on the surface)
    slice_data = []
    for k in range(slices):
        pts = bins[k]
        if not pts:
            slice_data.append(None)
            continue
        avg_t = sum(item[2] for item in pts) / len(pts)
        wall = {}
        for p, r, t, sec in pts:
            if sec not in wall or r > wall[sec][1]:
                wall[sec] = (p, r)
        radius = sum(r for _, r in wall.values()) / len(wall)
        axis_pt = s + d * (avg_t * length)
        if len(wall) >= SECTORS // 2:
            centre = sum((p for p, _ in wall.values()), Vector((0, 0, 0))) / len(wall)
            centre = centre - d * (centre - axis_pt).dot(d)          # on the slice's own plane
        else:
            centre = axis_pt                                         # too one-sided to say: the axis itself
        slice_data.append({"t": avg_t, "pos": centre, "radius": radius})

    # Fill gaps by linear interpolation
    known = [i for i, data in enumerate(slice_data) if data is not None]
    if len(known) < 3:
        return []
    for i in range(slices):
        if slice_data[i] is None:
            left = max([k for k in known if k < i], default=None)
            right = min([k for k in known if k > i], default=None)
            if left is not None and right is not None:
                alpha = (i - left) / (right - left)
                dl = slice_data[left]; dr = slice_data[right]
                slice_data[i] = {
                    "t": dl["t"] + (dr["t"] - dl["t"]) * alpha,
                    "pos": dl["pos"].lerp(dr["pos"], alpha),
                    "radius": dl["radius"] + (dr["radius"] - dl["radius"]) * alpha
                }
            elif left is not None:
                slice_data[i] = dict(slice_data[left])
            elif right is not None:
                slice_data[i] = dict(slice_data[right])

    radii = [data["radius"] for data in slice_data]
    smoothed = [radii[0]] + [
        0.25 * radii[i - 1] + 0.5 * radii[i] + 0.25 * radii[i + 1]
        for i in range(1, slices - 1)
    ] + [radii[-1]]

    pinches = []
    for i in range(1, slices - 1):
        if smoothed[i] < smoothed[i - 1] and smoothed[i] < smoothed[i + 1]:
            left_peak = max(smoothed[:i])
            right_peak = max(smoothed[i + 1:])
            prominence = min(left_peak, right_peak) - smoothed[i]
            if prominence > 0:
                data = slice_data[i]
                pinches.append({
                    "t": round(float(data["t"]), 4),
                    "pos": data["pos"],
                    "radius": round(float(data["radius"]), 4),
                    "prominence": round(float(prominence), 4)
                })

    pinches.sort(key=lambda item: item["prominence"], reverse=True)
    return pinches


def trace_medial_axis(coords, start, end, bones=4, slack=0.3):
    """Traces bone station points along the volumetric medial axis between start and end.
    Centers each station by balancing radial boundary distances within its orthogonal cross-section."""
    s = Vector(start) if not isinstance(start, Vector) else start
    e = Vector(end) if not isinstance(end, Vector) else end
    ab = e - s
    length = ab.length
    if length < 1e-6 or bones < 1:
        return [s, e]

    pts = []
    d = ab.normalized()

    for k in range(bones + 1):
        frac = k / bones
        nominal = s.lerp(e, frac)
        half_step = length / bones * 0.5
        slice_pts = []
        for pt in coords:
            p = Vector(pt) if not isinstance(pt, Vector) else pt
            proj = (p - nominal).dot(d)
            if abs(proj) <= half_step:
                slice_pts.append(p)

        if not slice_pts or len(slice_pts) < 4:
            pts.append(nominal)
            continue

        # the slices are planes across the straight line, so the balance is taken in that plane (rig_geom)
        c0 = sum(slice_pts, Vector((0, 0, 0))) / len(slice_pts)
        pts.append(Vector(rig_geom.centre_ring([tuple(p) for p in slice_pts], tuple(c0), tuple(d))))

    pts[0] = s
    pts[-1] = e
    return [Vector(p) for p in rig_geom.smooth_stations(pts)]


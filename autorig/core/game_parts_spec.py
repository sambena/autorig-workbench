# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the plain-Python half of steps/game_parts.py (no Blender needed, so the tests run it). A game
# model in one piece (Rodin's and Tripo's are thousands of loose fragments) has its moving parts cut out by a JSON spec:
#
#   {"Pumpjack": {"parts": [
#       {"name": "Beam", "box": {"min": [-1, -3.8, 3.9], "max": [1, 3.8, 7.8]},
#        "exclude": [{"box": {"min": [-1, -0.6, 5.3], "max": [1, 0.6, 5.9]}}],
#        "pivot": [0, -0.05, 5.75]},
#       ...]},
#    "Windpump": {"parts": [
#       {"name": "Fan", "disc": {"centre": [0, -1.4, 7.75], "axis": "y", "radius": 2.4, "slab": [-0.5, 0.5]},
#        "pivot": "centre"}]}}
#
# A part takes the fragments (connected components) whose centre lies in any of its regions ("box", "disc" or a
# list under "regions") and in none of its "exclude" regions; the first part in the list that takes a fragment keeps
# it, and the rest stay in the model. Faces are never split. Its pivot is a point, or "centre": the middle of the
# fragments it took (for a disc, the middle across its axis; along the axis the disc's own centre stays unless
# "recentre_axis" is true). "max_size": [dx, dy, dz] leaves out any fragment bigger than that along an axis (a tower's
# legs run up behind a windpump's fan, long and thin where the fan's blades are short). Every coordinate is in metres
# in Blender's frame: Z up, the model's front towards -Y.
#
# "cut": true carves a part instead: it takes everything inside its one region (a box or a disc, no "exclude"), and a
# fragment the region crosses is cut through along the region's surface, closed where it is cut (manifold3d, when the
# fragment is a closed surface; its faces sorted by their centres otherwise). For a part fused into the body in one
# piece, as Tripo makes them. Cut parts are cut first, in order; the fragment parts then share what is left.
#
#   load(path) -> {model name: [part dict, ...]} (validated; keys starting "_" are comments)
#   components(vertex count, faces) -> a component label per vertex
#   assign(parts, centres[, sizes]) -> a part index (or -1) per component (its centre, and its bounds' size)
#   pivot(part, lo, hi) -> the part's pivot given the bounds of what it took
import json

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class SpecError(ValueError):
    pass


def _vec(v, what):
    if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)):
        raise SpecError("%s must be three numbers, not %r" % (what, v))
    return tuple(float(c) for c in v)


def _axis(a, what):
    if isinstance(a, str):
        if a.lower().lstrip("+-") not in AXES:
            raise SpecError("%s: an axis is x, y, z or three numbers, not %r" % (what, a))
        return AXES[a.lower().lstrip("+-")]
    x, y, z = _vec(a, what)
    n = (x * x + y * y + z * z) ** 0.5
    if n < 1e-9:
        raise SpecError("%s: a zero axis" % what)
    return (x / n, y / n, z / n)


def region(r, what):
    """A region dict, checked and normalised: {"box": {min, max}} or {"disc": {centre, axis, radius, slab}}."""
    if not isinstance(r, dict):
        raise SpecError("%s: a region is a dict with \"box\" or \"disc\"" % what)
    if "box" in r:
        lo, hi = _vec(r["box"].get("min"), what + " box min"), _vec(r["box"].get("max"), what + " box max")
        if any(a > b for a, b in zip(lo, hi)):
            raise SpecError("%s: box min %r is above its max %r" % (what, lo, hi))
        return {"box": {"min": lo, "max": hi}}
    if "disc" in r:
        d = r["disc"]
        radius = float(d.get("radius", 0))
        slab = d.get("slab", [-0.5, 0.5])
        if radius <= 0 or not (isinstance(slab, (list, tuple)) and len(slab) == 2 and slab[0] <= slab[1]):
            raise SpecError("%s: a disc needs a radius > 0 and a slab [from, to] along its axis" % what)
        return {"disc": {"centre": _vec(d.get("centre"), what + " disc centre"), "axis": _axis(d.get("axis", "z"), what),
                         "radius": radius, "slab": (float(slab[0]), float(slab[1]))}}
    raise SpecError("%s: a region is a dict with \"box\" or \"disc\"" % what)


def part(p, what):
    """A part dict, checked: name, regions (from "box", "disc" or "regions"), exclude, pivot."""
    if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]:
        raise SpecError("%s: a part needs a \"name\"" % what)
    what = "%s %s" % (what, p["name"])
    regions = [region(r, what) for r in p.get("regions", [])]
    for key in ("box", "disc"):
        if key in p:
            regions.append(region({key: p[key]}, what))
    if not regions:
        raise SpecError("%s: no region (\"box\", \"disc\" or \"regions\")" % what)
    exclude = [region(r, what + " exclude") for r in p.get("exclude", [])]
    piv = p.get("pivot", "centre")
    if piv != "centre":
        piv = _vec(piv, what + " pivot")
    most = _vec(p["max_size"], what + " max_size") if "max_size" in p else None
    cut = bool(p.get("cut", False))
    if cut and (len(regions) != 1 or exclude or most is not None):
        raise SpecError("%s: a \"cut\" part has one region and no \"exclude\" or \"max_size\"" % what)
    return {"name": p["name"], "regions": regions, "exclude": exclude, "pivot": piv, "max_size": most,
            "recentre_axis": bool(p.get("recentre_axis", False)), "cut": cut}


def parse(spec):
    """{model: [part, ...]} from a spec dict ("_" keys are comments)."""
    out = {}
    for model, body in spec.items():
        if model.startswith("_"):
            continue
        parts = body.get("parts") if isinstance(body, dict) else None
        if not isinstance(parts, list) or not parts:
            raise SpecError("%s: needs a list of \"parts\"" % model)
        out[model] = [part(p, model) for p in parts]
        names = [p["name"] for p in out[model]]
        if len(set(names)) != len(names):
            raise SpecError("%s: two parts share a name" % model)
    return out


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse(json.load(f))


def inside(r, p):
    """Is point p in region r?"""
    if "box" in r:
        lo, hi = r["box"]["min"], r["box"]["max"]
        return all(lo[i] <= p[i] <= hi[i] for i in range(3))
    d = r["disc"]
    c, a = d["centre"], d["axis"]
    rel = [p[i] - c[i] for i in range(3)]
    along = sum(rel[i] * a[i] for i in range(3))
    if not (d["slab"][0] <= along <= d["slab"][1]):
        return False
    across = sum((rel[i] - along * a[i]) ** 2 for i in range(3)) ** 0.5
    return across <= d["radius"]


def solid_args(r):
    """A region as core/mesh_prep.Solid's arguments: ("box", lo, hi) or ("disc", centre, axis, radius, slab)."""
    if "box" in r:
        return ("box", r["box"]["min"], r["box"]["max"])
    d = r["disc"]
    return ("disc", d["centre"], d["axis"], d["radius"], d["slab"])


def takes(prt, p, size=None):
    if size is not None and prt["max_size"] is not None and any(size[i] > prt["max_size"][i] for i in range(3)):
        return False
    return any(inside(r, p) for r in prt["regions"]) and not any(inside(r, p) for r in prt["exclude"])


def assign(parts, centres, sizes=None):
    """For each component centre (and the size of its bounds, for max_size), the index of the first part that takes
    it, or -1 (it stays in the model)."""
    out = []
    for k, c in enumerate(centres):
        size = sizes[k] if sizes is not None else None
        out.append(next((i for i, prt in enumerate(parts) if not prt.get("cut") and takes(prt, c, size)), -1))
    return out


def pivot(prt, lo, hi):
    """The part's pivot: its own point, or "centre" from the bounds (lo, hi) of what it took. For a part whose first
    region is a disc, "centre" moves the disc's centre across its axis only (a fan's hub; its slab along the axis is
    lopsided with the tail of the shaft), unless recentre_axis."""
    if prt["pivot"] != "centre":
        return prt["pivot"]
    mid = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
    first = prt["regions"][0]
    if "disc" not in first or prt["recentre_axis"]:
        return mid
    c, a = first["disc"]["centre"], first["disc"]["axis"]
    # mid with its component along the axis replaced by the disc centre's
    along = sum((mid[i] - c[i]) * a[i] for i in range(3))
    return tuple(mid[i] - along * a[i] for i in range(3))


def components(n_verts, faces):
    """A connected-component label (0..k-1, in order of first vertex) per vertex, faces given as vertex index lists."""
    parent = list(range(n_verts))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for f in faces:
        r0 = find(f[0])
        for v in f[1:]:
            r = find(v)
            if r != r0:
                parent[r] = r0
    labels, out = {}, []
    for i in range(n_verts):
        r = find(i)
        if r not in labels:
            labels[r] = len(labels)
        out.append(labels[r])
    return out

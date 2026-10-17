# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: reads a binary FBX's axis system, unit scale and root transforms, as an engine importer sees them.
#
#   python autorig/core/fbx_inspect.py <file.fbx>        prints the summary as JSON
#
# Used to check the Unity FBX (steps/unity_fbx.py): Unity shows a root object's Lcl Rotation and Lcl Scaling as its
# Transform, so a Blender export written the default way shows -90 on X and a scale of 100. Reading the file itself is
# the check; re-importing it in Blender converts the axes back and hides what the engine would see.
# Plain Python (struct, zlib): binary FBX 7.x only, which is what Blender writes. ASCII FBX raises ValueError.
import json, math, struct, sys, zlib

MAGIC = b"Kaydara FBX Binary  \x00"
_ARRAY = {"f": ("f", 4), "d": ("d", 8), "l": ("q", 8), "i": ("i", 4), "b": ("?", 1)}
_SCALAR = {"Y": ("h", 2), "C": ("?", 1), "I": ("i", 4), "F": ("f", 4), "D": ("d", 8), "L": ("q", 8)}


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def all(self, name):
        return [c for c in self.children if c.name == name]


def _props(data, pos, count):
    out = []
    for _ in range(count):
        t = chr(data[pos]); pos += 1
        if t in _SCALAR:
            fmt, size = _SCALAR[t]
            out.append(struct.unpack_from("<" + fmt, data, pos)[0]); pos += size
        elif t in _ARRAY:
            n, enc, clen = struct.unpack_from("<III", data, pos); pos += 12
            raw = data[pos:pos + clen]; pos += clen
            if enc == 1: raw = zlib.decompress(raw)
            fmt, size = _ARRAY[t]
            out.append(list(struct.unpack_from("<%d%s" % (n, fmt), raw)))
        elif t in "SR":
            n = struct.unpack_from("<I", data, pos)[0]; pos += 4
            b = data[pos:pos + n]; pos += n
            out.append(b.decode("utf-8", "replace") if t == "S" else b)
        else:
            raise ValueError("unknown FBX property type %r" % t)
    return out


def _nodes(data, pos, end, wide):
    head = 25 if wide else 13
    fmt = "<QQQ" if wide else "<III"
    nodes = []
    while pos < end:
        node_end, nprops, plen = struct.unpack_from(fmt, data, pos)
        if node_end == 0: break                          # the null record closing a list
        nlen = data[pos + head - 1]
        name = data[pos + head:pos + head + nlen].decode("ascii", "replace")
        p = pos + head + nlen
        props = _props(data, p, nprops)
        p += plen
        children = _nodes(data, p, node_end, wide) if p < node_end else []
        nodes.append(Node(name, props, children))
        pos = node_end
    return nodes


def read(path):
    """The top-level nodes and the FBX version."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(MAGIC):
        raise ValueError("%s is not a binary FBX" % path)
    version = struct.unpack_from("<I", data, 23)[0]
    return _nodes(data, 27, len(data), version >= 7500), version


def _p70(node):
    """Properties70 of a node: name -> the values after (name, type, label, flags)."""
    p = node.find("Properties70") if node else None
    out = {}
    for c in (p.children if p else []):
        if c.name == "P" and c.props:
            vals = c.props[4:]
            out[c.props[0]] = vals[0] if len(vals) == 1 else vals
    return out


def _name(prop):
    s = prop if isinstance(prop, str) else prop.decode("utf-8", "replace")
    return s.split("\x00\x01")[0]


def summary(path):
    """Axis system, unit scale and every root object's transform:
    {"version", "up": "+Y", "front": "+Z", "coord": "+X", "unit_scale", "roots": [{name, type, translation, rotation,
    scaling}], "takes": [...]}. Axes are the file's own (Unity and Godot convert to theirs from these)."""
    top, version = read(path)
    by = {n.name: n for n in top}
    gs = _p70(by.get("GlobalSettings"))

    def axis(key):
        if key not in gs: return None
        return ("-" if gs.get(key + "Sign", 1) < 0 else "+") + "XYZ"[gs[key]]

    models = {}
    for m in (by["Objects"].all("Model") if "Objects" in by else []):
        p = _p70(m)
        models[m.props[0]] = {"name": _name(m.props[1]), "type": m.props[2] if len(m.props) > 2 else "",
                              "translation": list(p.get("Lcl Translation", [0.0, 0.0, 0.0])),
                              "rotation": list(p.get("Lcl Rotation", [0.0, 0.0, 0.0])),
                              "scaling": list(p.get("Lcl Scaling", [1.0, 1.0, 1.0])),
                              "pre_rotation": list(p.get("PreRotation", [0.0, 0.0, 0.0]))}
    parent = {}
    for c in (by["Connections"].all("C") if "Connections" in by else []):
        # a bone is also linked to its skin cluster, so only links to another model or to the scene (0) count
        if c.props and c.props[0] == "OO" and c.props[1] in models and (c.props[2] in models or c.props[2] == 0):
            parent[c.props[1]] = c.props[2]
    roots = [m for i, m in models.items() if parent.get(i, 0) == 0]
    takes = [_name(t.props[0]) for t in by["Takes"].all("Take")] if "Takes" in by else []
    if not takes and "Objects" in by:
        takes = [_name(s.props[1]) for s in by["Objects"].all("AnimationStack")]
    return {"version": version, "up": axis("UpAxis"), "front": axis("FrontAxis"), "coord": axis("CoordAxis"),
            "unit_scale": gs.get("UnitScaleFactor", 1.0), "roots": roots, "takes": takes}


def unity_problems(info, tol=1e-3):
    """What Unity would show wrong for this file, as sentences; empty when every root imports with rotation (0,0,0)
    and scale 1 in a Y-up file. With Convert Units on (the default), Unity reads UnitScaleFactor as the file's
    centimetres per unit and turns it into metres, so 100 (written in metres) is scale 1."""
    out = []
    if info["up"] != "+Y": out.append("the file is %s-up, not +Y-up" % info["up"])
    if abs(info["unit_scale"] - 100.0) > tol:
        out.append("UnitScaleFactor is %g, not 100 (metres): Unity scales the model by %g"
                   % (info["unit_scale"], info["unit_scale"] / 100.0))
    for r in info["roots"]:
        rot = [round(v, 3) for v in r["rotation"]]
        pre = [round(v, 3) for v in r["pre_rotation"]]
        sc = [round(v, 4) for v in r["scaling"]]
        if any(abs(v) > tol for v in rot) or any(abs(v) > tol for v in pre):
            out.append("root %s has rotation %s%s" % (r["name"], rot, " pre-rotation %s" % pre if any(pre) else ""))
        if any(abs(v - 1.0) > tol for v in sc):
            out.append("root %s has scale %s" % (r["name"], sc))
    return out


def world_points(path, mesh_name=None):
    """The mesh vertices of the first (or the named) geometry, in file axes, scaled to the file's unit and placed by
    their model's root-level transform when the model is a root: enough for a facing check."""
    top, _ = read(path)
    by = {n.name: n for n in top}
    geoms = {g.props[0]: g for g in by["Objects"].all("Geometry")} if "Objects" in by else {}
    models = {m.props[0]: m for m in by["Objects"].all("Model")} if "Objects" in by else {}
    owner = {}
    for c in by["Connections"].all("C") if "Connections" in by else []:
        if c.props[0] == "OO" and c.props[1] in geoms and c.props[2] in models:
            owner[c.props[1]] = c.props[2]
    for gid, g in geoms.items():
        m = models.get(owner.get(gid))
        if mesh_name and (not m or _name(m.props[1]) != mesh_name): continue
        v = g.find("Vertices")
        if not v: continue
        flat = v.props[0]
        pts = [flat[i:i + 3] for i in range(0, len(flat), 3)]
        p = _p70(m) if m else {}
        rx, ry, rz = [math.radians(a) for a in p.get("Lcl Rotation", [0, 0, 0])]
        sx, sy, sz = p.get("Lcl Scaling", [1, 1, 1])
        tx, ty, tz = p.get("Lcl Translation", [0, 0, 0])

        def tf(q):
            x, y, z = q[0] * sx, q[1] * sy, q[2] * sz
            y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)       # FBX: X, then Y, Z
            x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
            x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
            return [x + tx, y + ty, z + tz]
        return [tf(q) for q in pts]
    return []


if __name__ == "__main__":
    if len(sys.argv) != 2: sys.exit("usage: fbx_inspect.py <file.fbx>")
    info = summary(sys.argv[1])
    info["unity_problems"] = unity_problems(info)
    print(json.dumps(info, indent=2))

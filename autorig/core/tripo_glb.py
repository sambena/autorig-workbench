# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: mends a Tripo GLB so Blender's importer reads it right, in plain Python (no Blender needed, so
# the tests run it). Two faults Tripo's exports carry (Tornado Titans' farm animals, 2026-10-17):
#
#   - NaN numbers in the JSON (an accessor's bounds), which Blender's importer refuses, and a few texture
#     coordinates that are NaN, which the FBX exporter later refuses;
#   - a rigged animal whose joints all sit at the origin (no translation, no matrix: the horse, cat, rabbit, goat),
#     its rest pose only in the skin's inverse bind matrices. Posed by those joints the mesh is a heap (or a few
#     thousand vertices fly off to 1e30), so each joint is given its rest pose back: world = inverse(bind matrix),
#     local = inverse(parent world) x world.
#
#   mend(path) -> path of a mended copy in a temporary folder, or `path` itself when nothing needed mending.
#   The original file is never written to.
import json, os, struct, tempfile

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942


def read_glb(path):
    """(json dict, bytearray of the BIN chunk) of a .glb."""
    with open(path, "rb") as f:
        data = f.read()
    magic, _, _ = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(path + " is not a GLB")
    jl = struct.unpack_from("<I", data, 12)[0]
    j = json.loads(bytes(data[20:20 + jl]))
    binary = bytearray(data[20 + jl + 8:]) if len(data) > 20 + jl else bytearray()
    return j, binary


def write_glb(j, binary, out):
    js = json.dumps(j, separators=(",", ":"), allow_nan=False).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binary = bytes(binary) + bytes((4 - len(binary) % 4) % 4)
    with open(out, "wb") as f:
        f.write(struct.pack("<III", GLB_MAGIC, 2, 12 + 8 + len(js) + 8 + len(binary)))
        f.write(struct.pack("<II", len(js), JSON_CHUNK) + js)
        f.write(struct.pack("<II", len(binary), BIN_CHUNK) + binary)


# ------------------------------------------------------------------ 4 x 4 matrices, rows of lists

def identity():
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def det(m):
    def minor(mm, i, j):
        return [row[:j] + row[j + 1:] for k, row in enumerate(mm) if k != i]

    def d(mm):
        if len(mm) == 1:
            return mm[0][0]
        return sum(((-1) ** j) * mm[0][j] * d(minor(mm, 0, j)) for j in range(len(mm)))
    return d(m)


def inverse(m):
    """Gauss-Jordan; None when singular."""
    a = [row[:] + [1.0 if r == c else 0.0 for c in range(4)] for r, row in enumerate(m)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        f = a[c][c]
        a[c] = [x / f for x in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0.0:
                g = a[r][c]
                a[r] = [x - g * y for x, y in zip(a[r], a[c])]
    return [row[4:] for row in a]


def from_gltf(m16):
    """A glTF column-major matrix as rows."""
    return [[m16[c * 4 + r] for c in range(4)] for r in range(4)]


def to_gltf(m):
    return [m[r][c] for c in range(4) for r in range(4)]


def node_local(n):
    if "matrix" in n:
        return from_gltf(n["matrix"])
    t = n.get("translation", [0.0, 0.0, 0.0])
    x, y, z, w = n.get("rotation", [0.0, 0.0, 0.0, 1.0])
    sx, sy, sz = n.get("scale", [1.0, 1.0, 1.0])
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    s = (sx, sy, sz)
    m = [[rot[r][c] * s[c] for c in range(3)] + [t[r]] for r in range(3)]
    return m + [[0.0, 0.0, 0.0, 1.0]]


def finite(m):
    return all(x == x and abs(x) < 1e6 for row in m for x in row)


# ------------------------------------------------------------------ the mends

def fix_json_nan(node):
    """NaN numbers in the JSON itself (an accessor's min or max) set to 0. Returns (node, how many)."""
    if isinstance(node, float) and node != node:
        return 0.0, 1
    if isinstance(node, list):
        out, n = [], 0
        for x in node:
            x, k = fix_json_nan(x)
            out.append(x)
            n += k
        return out, n
    if isinstance(node, dict):
        n = 0
        for key in list(node):
            node[key], k = fix_json_nan(node[key])
            n += k
        return node, n
    return node, 0


def fix_texcoords(j, binary):
    """NaN texture coordinates set to 0. Returns how many."""
    fixed = 0
    for mesh in j.get("meshes", []):
        for prim in mesh["primitives"]:
            for key, idx in prim["attributes"].items():
                if not key.startswith("TEXCOORD"):
                    continue
                acc = j["accessors"][idx]
                if acc.get("componentType") != 5126:
                    continue
                view = j["bufferViews"][acc["bufferView"]]
                start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
                stride = view.get("byteStride", 8)
                for i in range(acc["count"]):
                    for k in range(2):
                        o = start + i * stride + k * 4
                        if struct.unpack_from("<f", binary, o)[0] != struct.unpack_from("<f", binary, o)[0]:
                            struct.pack_into("<f", binary, o, 0.0)
                            fixed += 1
    return fixed


def flat_skeleton(j, skin):
    """True when every joint of the skin sits at the origin with no matrix: the rest pose is only in the binds."""
    nodes = j["nodes"]
    return "inverseBindMatrices" in skin and all(
        "matrix" not in nodes[i] and all(abs(x) < 1e-9 for x in nodes[i].get("translation", [0.0, 0.0, 0.0]))
        for i in skin["joints"])


def rebind(j, binary, skin):
    """Gives a flat skeleton's joints their rest pose from the inverse bind matrices. Returns how many joints."""
    nodes = j["nodes"]
    acc = j["accessors"][skin["inverseBindMatrices"]]
    view = j["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    world = {}
    for k, ji in enumerate(skin["joints"]):
        ibm = from_gltf(struct.unpack_from("<16f", binary, start + k * 64))
        if not finite(ibm):
            continue
        inv = inverse(ibm)
        if inv is not None and finite(inv):
            world[ji] = inv          # a joint with no skin of its own (a singular bind) keeps its place
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    memo = {}

    def world_of(i):
        if i in world:
            return world[i]
        if i not in memo:
            p = parent.get(i)
            memo[i] = mul(world_of(p), node_local(nodes[i])) if p is not None else node_local(nodes[i])
        return memo[i]

    done = 0
    for ji in skin["joints"]:
        if ji not in world:
            continue
        p = parent.get(ji)
        pw = world_of(p) if p is not None else identity()
        pinv = inverse(pw)
        if pinv is None:
            continue
        local = mul(pinv, world[ji])
        if not finite(local):
            continue
        for key in ("translation", "rotation", "scale"):
            nodes[ji].pop(key, None)
        nodes[ji]["matrix"] = to_gltf(local)
        done += 1
    return done


def mend(path, report=print):
    """A mended copy of the GLB at `path` (see the top of this file), or `path` when nothing needed mending."""
    j, binary = read_glb(path)
    j, json_nan = fix_json_nan(j)
    texcoords = fix_texcoords(j, binary)
    joints = sum(rebind(j, binary, s) for s in j.get("skins", []) if flat_skeleton(j, s))
    if not texcoords and not joints and not json_nan:
        return path
    out = os.path.join(tempfile.mkdtemp(prefix="tripo_glb_"), os.path.basename(path))
    write_glb(j, binary, out)
    report("TRIPO_GLB %s" % json.dumps({"file": os.path.basename(path), "json_nan_fixed": json_nan, "texcoords_fixed": texcoords, "joints_rebound": joints}))
    return out

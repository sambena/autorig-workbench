# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: checks autorig/core/m2m_skin.py (our NumPy port of Mesh2Motion's skin solver) against the
# original TypeScript, run under Node, on Mesh2Motion's own sample models and rigs (static/models/model-*.glb on
# static/rigs/rig-*.glb). Every vertex's weights must match within 1e-4.
#
#   python scripts/m2m_skin_parity.py [--templates human,fox] [--out <dir>]
#
# Needs the submodule and its npm packages (git submodule update --init third_party/mesh2motion-app, then npm ci
# there; scripts/build_m2m.py does both). Writes the cases (the baked mesh and the joints' world heads, the same
# numbers to both sides) to <out>/cases.json, runs scripts/m2m_skin_parity/solvers.test.ts under the fork's vitest,
# then runs the port and compares. Nothing is committed: the fixtures are made fresh each run. Exit status 1 on any
# mismatch. Each template runs three times: as M2M's defaults leave it (no head or arm plane), with both planes at
# M2M's defaults (head 1.4, arm offset 0), and with both moved (head 0.9, arm 0.08 outboard) so they catch more.
import argparse, json, os, shutil, struct, subprocess, sys, tempfile, time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M2M = os.path.join(REPO, "third_party", "mesh2motion-app")
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import m2m_skin  # noqa: E402
from tripo_glb import read_glb  # noqa: E402

MODELS = {"human": "human", "fox": "fox", "bird": "bird", "dragon": "dragon", "kaiju": "kaiju", "spider": "spider",
          "snake": "snake", "shark": "shark", "horse": "horse"}
TOLERANCE = 1e-4
_COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def accessor(j, binary, i):
    a = j["accessors"][i]
    if "bufferView" not in a or "sparse" in a:
        raise ValueError("m2m_skin_parity: accessor %d is sparse or has no buffer view" % i)
    v = j["bufferViews"][a["bufferView"]]
    dt = np.dtype(_COMPONENTS[a["componentType"]])
    width = _WIDTH[a["type"]]
    start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = v.get("byteStride", 0) or dt.itemsize * width
    rows = np.ndarray((a["count"], width), dtype=dt, buffer=binary, offset=start, strides=(stride, dt.itemsize))
    return rows.copy()


def local_matrix(n):
    if "matrix" in n: return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
    x, y, z, w = n.get("rotation", [0.0, 0.0, 0.0, 1.0])
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R * np.array(n.get("scale", [1.0, 1.0, 1.0]))[None, :]
    M[:3, 3] = n.get("translation", [0.0, 0.0, 0.0])
    return M


def walk(j, roots):
    """(node index, parent node index or None, world matrix), depth first, children in glTF order."""
    out, stack = [], [(r, None, np.eye(4)) for r in reversed(roots)]
    while stack:
        i, p, W = stack.pop()
        M = W @ local_matrix(j["nodes"][i])
        out.append((i, p, M))
        stack.extend((c, i, M) for c in reversed(j["nodes"][i].get("children", [])))
    return out


def rig_bones(path):
    """The rig GLB's bones as M2M uses them: the armature's first child and everything under it."""
    j, _ = read_glb(path)
    joints = set(j_ for s in j.get("skins", []) for j_ in s["joints"])
    armature = next(i for i, _, _ in walk(j, j["scenes"][j.get("scene", 0)]["nodes"])
                    if any(c in joints for c in j["nodes"][i].get("children", [])))
    root = j["nodes"][armature]["children"][0]
    nodes = walk(j, [root])
    at = {i: k for k, (i, _, _) in enumerate(nodes)}
    return [{"name": j["nodes"][i]["name"], "parent": -1 if p is None else at[p],
             "head": [float(c) for c in M[:3, 3]]} for i, p, M in nodes]


def model_meshes(path):
    """Each triangle primitive of the model, baked to world space and stored as float32, as M2M's model cleanup
    leaves it: (positions (n, 3) float32, triangle indices (m, 3) or None)."""
    j, binary = read_glb(path)
    out = []
    for i, _, M in walk(j, j["scenes"][j.get("scene", 0)]["nodes"]):
        n = j["nodes"][i]
        if "mesh" not in n: continue
        for prim in j["meshes"][n["mesh"]]["primitives"]:
            if prim.get("mode", 4) != 4: continue
            P = accessor(j, binary, prim["attributes"]["POSITION"]).astype(np.float64)
            P = (np.c_[P, np.ones(len(P))] @ M.T)[:, :3].astype(np.float32)
            F = accessor(j, binary, prim["indices"]).reshape(-1, 3).astype(np.int64) if "indices" in prim else None
            out.append((P, F))
    return out


def cases(templates):
    out = []
    for t in templates:
        bones = rig_bones(os.path.join(M2M, "static", "rigs", "rig-%s.glb" % t))
        for k, (P, F) in enumerate(model_meshes(os.path.join(M2M, "static", "models", "model-%s.glb" % MODELS[t]))):
            for variant, head, arm in (("defaults", None, None), ("planes", 1.4, 0.0), ("shifted", 0.9, 0.08)):
                out.append({"name": "%s.%d.%s" % (t, k, variant), "template": t, "bones": bones,
                            "positions": [float(x) for x in P.reshape(-1)],
                            "index": None if F is None else [int(x) for x in F.reshape(-1)],
                            "head_height": head, "arm_offset": arm})
    return out


def node_reference(case_file, out_file):
    vitest = os.path.join(M2M, "node_modules", "vitest", "vitest.mjs")
    if not os.path.exists(vitest):
        sys.exit("m2m_skin_parity: no vitest in %s; run npm ci there (or scripts/build_m2m.py)" % M2M)
    node = shutil.which("node") or shutil.which("node.exe")
    if not node: sys.exit("m2m_skin_parity: node not found; install Node 24")
    env = dict(os.environ, M2M_PARITY_IN=case_file, M2M_PARITY_OUT=out_file)
    r = subprocess.run([node, vitest, "run", "--config", os.path.join("scripts", "m2m_skin_parity", "vitest.config.mjs")],
                       cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(out_file):
        sys.stderr.write(r.stdout[-4000:] + r.stderr[-4000:])
        sys.exit("m2m_skin_parity: the TypeScript solvers failed under vitest")
    with open(out_file) as f:
        return json.load(f)


def dense(idx, w, bones):
    D = np.zeros((len(idx), bones))
    np.add.at(D, (np.repeat(np.arange(len(idx)), 4), idx.reshape(-1)), w.reshape(-1))
    return D


def compare(c, ref):
    rig = m2m_skin.Rig([(b["name"], b["head"], None if b["parent"] < 0 else c["bones"][b["parent"]]["name"])
                        for b in c["bones"]])
    assert rig.names == [b["name"] for b in c["bones"]], "Rig() reordered the bones"
    V = np.array(c["positions"], dtype=np.float64).reshape(-1, 3)
    F = None if c["index"] is None else np.array(c["index"], dtype=np.int64).reshape(-1, 3)
    t0 = time.time()
    idx, w = m2m_skin.solve(rig, V, F, c["template"], c["head_height"], c["arm_offset"])
    secs = time.time() - t0
    ridx = np.array(ref["indices"], dtype=np.int64).reshape(-1, 4)
    rw = np.array(ref["weights"], dtype=np.float64).reshape(-1, 4)
    diff = float(np.abs(dense(idx, w, len(rig)) - dense(ridx, rw, len(rig))).max()) if len(V) else 0.0
    slots = int(np.count_nonzero(np.any((idx != ridx) | (np.abs(w - rw) > TOLERANCE), axis=1)))
    blended = int(np.count_nonzero(w[:, 0] < 1.0))
    return {"case": c["name"], "vertices": len(V), "blended": blended, "max_diff": diff, "slot_mismatch": slots,
            "seconds": round(secs, 2), "ok": diff <= TOLERANCE}


def run(templates, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    cs = cases(templates)
    case_file, out_file = os.path.join(out_dir, "cases.json"), os.path.join(out_dir, "reference.json")
    with open(case_file, "w") as f:
        json.dump(cs, f)
    if os.path.exists(out_file): os.remove(out_file)
    ref = node_reference(case_file, out_file)
    return [compare(c, ref[c["name"]]) for c in cs]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--templates", default=",".join(MODELS))
    p.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "m2m_skin_parity"))
    a = p.parse_args()
    rows = run([t.strip() for t in a.templates.split(",") if t.strip()], a.out)
    print("%-22s %8s %8s %10s %6s %7s" % ("case", "vertices", "blended", "max diff", "slots", "secs"))
    for r in rows:
        print("%-22s %8d %8d %10.2e %6d %7.2f %s" % (r["case"], r["vertices"], r["blended"], r["max_diff"],
                                                     r["slot_mismatch"], r["seconds"], "ok" if r["ok"] else "MISMATCH"))
    bad = [r for r in rows if not r["ok"]]
    print("M2M_SKIN_PARITY " + json.dumps({"cases": len(rows), "mismatch": len(bad), "tolerance": TOLERANCE}))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

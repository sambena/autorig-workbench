# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: tests for core/tripo_glb.py, which mends a Tripo GLB before Blender imports it (no Blender needed).
import json
import math
import os
import struct
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import tripo_glb


def translation(x, y, z):
    m = tripo_glb.identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def make_glb(path, joints_at, flat=True, nan_uv=False):
    """A tiny skinned GLB: a chain of joints at `joints_at` (world positions), each child of the one before, with
    inverse bind matrices for those places; `flat` leaves every joint node at the origin, as Tripo sometimes does."""
    binary = bytearray()
    nodes = []
    for k, p in enumerate(joints_at):
        n = {"name": "bone_%d" % k}
        if not flat:
            prev = joints_at[k - 1] if k else (0.0, 0.0, 0.0)
            n["translation"] = [p[i] - prev[i] for i in range(3)]
        if k + 1 < len(joints_at):
            n["children"] = [k + 1]
        nodes.append(n)
    ibm_offset = len(binary)
    for p in joints_at:
        inv = translation(-p[0], -p[1], -p[2])
        binary += struct.pack("<16f", *tripo_glb.to_gltf(inv))
    uv_offset = len(binary)
    binary += struct.pack("<6f", 0.5, 0.5, float("nan") if nan_uv else 0.1, 0.2, 0.3, 0.4)
    j = {
        "asset": {"version": "2.0"},
        "nodes": nodes,
        "skins": [{"joints": list(range(len(joints_at))), "inverseBindMatrices": 0}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(joints_at), "type": "MAT4"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": ibm_offset, "byteLength": 64 * len(joints_at)},
            {"buffer": 0, "byteOffset": uv_offset, "byteLength": 24},
        ],
        "buffers": [{"byteLength": len(binary)}],
        "meshes": [{"primitives": [{"attributes": {"TEXCOORD_0": 1}}]}],
    }
    tripo_glb.write_glb(j, binary, path)


class TestTripoGlb(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_a_flat_skeleton_gets_its_rest_pose_back(self):
        src = os.path.join(self.dir, "flat.glb")
        places = [(0.0, 1.0, 0.0), (0.0, 1.5, 0.2), (0.0, 1.9, 0.5)]
        make_glb(src, places, flat=True)
        out = tripo_glb.mend(src, report=lambda s: None)
        self.assertNotEqual(out, src, "a flat skeleton is mended into a copy")
        j, _ = tripo_glb.read_glb(out)
        world = tripo_glb.identity()
        for k, p in enumerate(places):
            world = tripo_glb.mul(world, tripo_glb.from_gltf(j["nodes"][k]["matrix"]))
            for i in range(3):
                self.assertAlmostEqual(world[i][3], p[i], places=5, msg="joint %d sits where its bind says" % k)

    def test_a_posed_skeleton_is_left_alone(self):
        src = os.path.join(self.dir, "posed.glb")
        make_glb(src, [(0.0, 1.0, 0.0), (0.0, 1.5, 0.0)], flat=False)
        self.assertEqual(tripo_glb.mend(src, report=lambda s: None), src)

    def test_nan_texture_coordinates_become_zero(self):
        src = os.path.join(self.dir, "nan.glb")
        make_glb(src, [(0.0, 1.0, 0.0)], flat=False, nan_uv=True)
        out = tripo_glb.mend(src, report=lambda s: None)
        j, binary = tripo_glb.read_glb(out)
        uvs = struct.unpack_from("<6f", binary, j["bufferViews"][1]["byteOffset"])
        self.assertFalse(any(math.isnan(u) for u in uvs))
        self.assertEqual(uvs[2], 0.0)

    def test_a_singular_bind_keeps_its_joint_and_nothing_is_nan(self):
        src = os.path.join(self.dir, "singular.glb")
        make_glb(src, [(0.0, 1.0, 0.0), (0.0, 2.0, 0.0)], flat=True)
        j, binary = tripo_glb.read_glb(src)
        struct.pack_into("<16f", binary, 64, *([0.0] * 16))   # the second joint's bind cannot be undone
        tripo_glb.write_glb(j, binary, src)
        out = tripo_glb.mend(src, report=lambda s: None)
        j2, _ = tripo_glb.read_glb(out)
        self.assertIn("matrix", j2["nodes"][0])
        self.assertNotIn("matrix", j2["nodes"][1])
        json.dumps(j2, allow_nan=False)   # raises on a NaN

    def test_matrix_inverse(self):
        m = tripo_glb.mul(translation(1.0, 2.0, 3.0), [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        inv = tripo_glb.inverse(m)
        one = tripo_glb.mul(m, inv)
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(one[r][c], 1.0 if r == c else 0.0, places=9)
        self.assertIsNone(tripo_glb.inverse([[0.0] * 4 for _ in range(4)]))


if __name__ == "__main__":
    unittest.main()

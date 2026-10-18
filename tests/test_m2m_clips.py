# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's human clips on Mixamo rigs (steps/m2m_clips.py, the mesh2motion convention).
#
# The mapping is checked against Mesh2Motion's own Mixamo table, src/retarget/bone-automap/MixamoMapper.ts in the
# submodule (MIT, (c) 2025 Scott Petrovic: https://github.com/Mesh2Motion/mesh2motion-app).
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import blender  # noqa: E402
import retargeter  # noqa: E402
import skeletons  # noqa: E402
import m2m_clips  # noqa: E402

HAVE_BLENDER = blender.find(required=False) is not None
M2M = os.path.join(REPO, "third_party", "mesh2motion-app")
MAPPER = os.path.join(M2M, "src", "retarget", "bone-automap", "MixamoMapper.ts")
RIG = os.path.join(M2M, "static", "rigs", "rig-human.glb")
try:
    m2m_clips.animations_dir()
    HAVE_CLIPS = True
except FileNotFoundError:
    HAVE_CLIPS = False

UE4 = ["root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head", "clavicle_l", "clavicle_r",
       "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r", "hand_l", "hand_r", "thigh_l", "thigh_r", "calf_l",
       "calf_r", "foot_l", "foot_r", "ball_l", "ball_r"]
MIXAMO = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "HeadTop_End"] + [
    s + b for s in ("Left", "Right") for b in
    ("Shoulder", "Arm", "ForeArm", "Hand", "UpLeg", "Leg", "Foot", "ToeBase", "Toe_End") +
    tuple("Hand%s%d" % (f, i) for f in ("Thumb", "Index", "Middle", "Ring", "Pinky") for i in (1, 2, 3, 4))]


def m2m_human_bones():
    return [n["name"] for n in (lambda j: [j["nodes"][i] for i in j["skins"][0]["joints"]])(m2m_clips.glb_json(RIG))]


class Convention(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(RIG), "Mesh2Motion submodule not checked out")
    def test_detects_the_human_rig(self):
        bones = m2m_human_bones()
        self.assertEqual(skeletons.detect_convention(bones), ("mesh2motion", 1.0))
        roles = skeletons.canonical_roles(bones)
        self.assertEqual([b for b, r in roles.items() if r is None], ["root"])   # travel only, never the hips
        self.assertEqual(roles["pelvis"], "hips")
        self.assertEqual(roles["spine_03"], "spine2")
        self.assertEqual(roles["ball_r"], "toe.R")
        self.assertEqual(roles["index_04_leaf_l"], "index4.L")

    def test_a_ue4_rig_is_not_mesh2motion(self):
        self.assertNotEqual(skeletons.detect_convention(UE4)[0], "mesh2motion")
        self.assertNotEqual(skeletons.detect_convention(UE4 + ["neck_02", "head_leaf"])[0], "mesh2motion")

    @unittest.skipUnless(os.path.isfile(MAPPER) and os.path.isfile(RIG), "Mesh2Motion submodule not checked out")
    def test_matches_mesh2motions_own_mixamo_table(self):
        """Every pair in MixamoMapper.ts comes out of build_retarget_mapping, with and without the namespace."""
        with open(MAPPER, encoding="utf-8") as fh:
            table = dict(re.findall(r"^\s+(\w+): 'mixamorig(\w+)'", fh.read(), re.M))
        self.assertGreater(len(table), 60)
        for prefix in ("mixamorig:", ""):
            target = [prefix + b for b in MIXAMO]
            got = retargeter.build_retarget_mapping(m2m_human_bones(), target)["mapping"]
            for src, tgt in table.items():
                self.assertEqual(got.get(src), prefix + tgt, (prefix, src))
            self.assertNotIn("root", got)


class Clips(unittest.TestCase):
    def test_root_motion_names(self):
        for n in ("Roll_RM", "Crawl RM", "Sword_Regular_C_RM"):
            self.assertTrue(m2m_clips.is_root_motion(n), n)
        for n in ("Roll", "Storm", "Walk_Rm", "RM_Walk", "Crawl_RM_Loop", "FormRM"):
            self.assertFalse(m2m_clips.is_root_motion(n), n)
        self.assertEqual(m2m_clips.in_place_name("Crawl RM"), "Crawl")

    def test_plan_keeps_travel_only_when_asked(self):
        found = [{"name": "Walk", "glb": "b.glb", "root_motion": False},
                 {"name": "Roll_RM", "glb": "b.glb", "root_motion": True},
                 {"name": "Crawl RM", "glb": "a.glb", "root_motion": True}]
        still = m2m_clips.plan_takes(found, root_motion=False)
        self.assertEqual([t["take"] for t in still], ["Walk", "Roll", "Crawl"])
        self.assertEqual([t["strip_travel"] for t in still], [False, True, True])
        moving = m2m_clips.plan_takes(found, root_motion=True)
        self.assertEqual([t["take"] for t in moving], ["Walk", "Roll_RM", "Crawl_RM"])
        self.assertFalse(any(t["strip_travel"] for t in moving))

    def test_shipped_files_are_not_overwritten(self):
        fbx, fbm = m2m_clips.output_names("/p", "IceAdult", replace=False)
        self.assertEqual(os.path.basename(fbx), "IceAdult_m2m.fbx")
        self.assertEqual(os.path.basename(fbm), "IceAdult.fbm")
        self.assertEqual(os.path.basename(m2m_clips.output_names("/p", "IceAdult", replace=True)[0]), "IceAdult.fbx")

    def test_parse_checks(self):
        line = "CLIP_CHECK " + json.dumps({"name": "Walk", "tear_edges": 3, "worst_gap_pct": 1.234,
                                           "floor_penetration": 0.01234, "errors": ["stretched edges"],
                                           "foot_skate": {"score": 2.1, "ratio": 0.3, "ground_speed": 0.9}})
        q = m2m_clips.parse_checks("noise\n" + line + "\n")
        self.assertEqual(q["Walk"]["skate"], 2.1)
        self.assertEqual(q["Walk"]["worst_gap_pct"], 1.23)

    @unittest.skipUnless(HAVE_CLIPS, "Mesh2Motion submodule not checked out")
    def test_catalogue_and_names(self):
        clips = m2m_clips.catalogue()
        self.assertEqual(len(clips), 178)
        self.assertEqual(len({c["name"] for c in clips}), 178)
        names = [c["name"] for c in m2m_clips.resolve(["Idle", "walk", "Run", "crawl rm", "Rest Pose"], clips)]
        self.assertEqual(names, ["Idle_A", "Walk", "Jog", "Crawl RM", "Rest Pose"])
        with self.assertRaises(ValueError) as e:
            m2m_clips.resolve(["Moonwalk", "Idle_Sword"], clips)
        self.assertIn("Moonwalk", str(e.exception))
        self.assertNotIn("Idle_Sword (", str(e.exception))
        walk = next(c for c in clips if c["name"] == "Walk")
        self.assertAlmostEqual(walk["seconds"], 1.667, places=2)


# A small person on a Mixamo skeleton (namespaced, -Y forward, A-posed arms, 1.75 m), skinned to a box per bone
MAKE_RIG = r'''
import bpy, sys
out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
B = {"Hips": (None, (0, 0, .95), (0, 0, 1.05)), "Spine": ("Hips", (0, 0, 1.05), (0, 0, 1.17)),
     "Spine1": ("Spine", (0, 0, 1.17), (0, 0, 1.29)), "Spine2": ("Spine1", (0, 0, 1.29), (0, 0, 1.42)),
     "Neck": ("Spine2", (0, 0, 1.47), (0, 0, 1.55)), "Head": ("Neck", (0, 0, 1.55), (0, 0, 1.75))}
for s, x in (("Left", 1), ("Right", -1)):
    B[s + "Shoulder"] = ("Spine2", (x * .04, 0, 1.42), (x * .17, 0, 1.43))
    B[s + "Arm"] = (s + "Shoulder", (x * .17, 0, 1.43), (x * .37, 0, 1.23))
    B[s + "ForeArm"] = (s + "Arm", (x * .37, 0, 1.23), (x * .55, 0, 1.05))
    B[s + "Hand"] = (s + "ForeArm", (x * .55, 0, 1.05), (x * .61, 0, .99))
    B[s + "UpLeg"] = ("Hips", (x * .09, 0, .93), (x * .1, 0, .5))
    B[s + "Leg"] = (s + "UpLeg", (x * .1, 0, .5), (x * .1, .02, .09))
    B[s + "Foot"] = (s + "Leg", (x * .1, .02, .09), (x * .1, -.1, .02))
    B[s + "ToeBase"] = (s + "Foot", (x * .1, -.1, .02), (x * .1, -.17, .02))
arm = bpy.data.objects.new("Armature", bpy.data.armatures.new("Armature"))
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
for n, (p, h, t) in B.items():
    e = arm.data.edit_bones.new("mixamorig:" + n); e.head, e.tail = h, t
for n, (p, h, t) in B.items():
    if p: arm.data.edit_bones["mixamorig:" + n].parent = arm.data.edit_bones["mixamorig:" + p]
bpy.ops.object.mode_set(mode="OBJECT")
import bmesh
from mathutils import Vector
mesh = bpy.data.meshes.new("Body"); bm = bmesh.new(); groups = []
for n, (p, h, t) in B.items():
    h, t = Vector(h), Vector(t)
    r = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    for v in r:
        v.co = h + (t - h) * (v.co.z + .5) + Vector((v.co.x, v.co.y, 0)) * .06
    groups.append((n, [v.index for v in r]))
bm.to_mesh(mesh); body = bpy.data.objects.new("Body", mesh); bpy.context.scene.collection.objects.link(body)
for n, idx in groups:
    g = body.vertex_groups.new(name="mixamorig:" + n); g.add(idx, 1.0, "REPLACE")
body.parent = arm; body.modifiers.new("Armature", "ARMATURE").object = arm
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.fbx(filepath=out, use_selection=True, object_types={"ARMATURE", "MESH"}, add_leaf_bones=False,
                         axis_forward="-Z", axis_up="Y", bake_anim=False)
'''

# Hips's world travel and height range in each take of a GLB
MEASURE = r'''
import bpy, sys, json
src = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=src)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE"); ad = arm.animation_data
for t in ad.nla_tracks: t.mute = True
out = {}
for t in ad.nla_tracks:
    a = t.strips[0].action; ad.action = a
    lo, hi = (int(x) for x in a.frame_range); ps = []
    for f in range(lo, hi + 1):
        bpy.context.scene.frame_set(f)
        ps.append(arm.matrix_world @ arm.pose.bones["mixamorig:Hips"].head)
    out[t.name] = {"travel": (ps[-1] - ps[0]).xy.length, "bob": max(p.z for p in ps) - min(p.z for p in ps)}
print("MEASURE " + json.dumps(out))
'''


@unittest.skipUnless(HAVE_BLENDER and HAVE_CLIPS, "needs Blender and the Mesh2Motion submodule")
class Retarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="m2m-clips-test-")
        cls.rig = os.path.join(cls.tmp, "Tiny_mixamo.fbx")
        r = subprocess.run([blender.find(), "-b", "--factory-startup", "--python-expr", MAKE_RIG, "--", cls.rig],
                           capture_output=True, text=True)
        assert os.path.isfile(cls.rig), r.stdout[-800:] + r.stderr[-800:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def measure(self, glb):
        r = subprocess.run([blender.find(), "-b", "--factory-startup", "--python-expr", MEASURE, "--", glb],
                           capture_output=True, text=True)
        line = next(l for l in r.stdout.splitlines() if l.startswith("MEASURE "))
        return json.loads(line[len("MEASURE "):])

    def test_walk_and_a_root_motion_clip(self):
        out = os.path.join(self.tmp, "out")
        shipped = os.path.join(out, "Tiny.fbx")
        os.makedirs(out)
        with open(shipped, "wb") as fh:
            fh.write(b"shipped")
        preview = os.path.join(self.tmp, "still.glb")
        res = m2m_clips.run(self.rig, None, "Tiny", out, ["Walk", "Roll_RM"], preview=preview, check=False)
        self.assertTrue(res.get("ok"), res)
        with open(shipped, "rb") as fh:
            self.assertEqual(fh.read(), b"shipped")                             # the shipped file is untouched
        self.assertTrue(os.path.isfile(os.path.join(out, "Tiny_m2m.fbx")))
        self.assertEqual(sorted(res["fbx_takes"]), ["Roll", "Walk"])
        self.assertTrue(all(t["mapped_bones"] >= 20 for t in res["baked"]), res["baked"])
        still = self.measure(preview)
        self.assertGreater(still["Walk"]["bob"], 0.01)                        # the pelvis bob came across
        self.assertLess(still["Walk"]["travel"], 0.1)
        self.assertLess(still["Roll"]["travel"], 0.3)                         # its travel taken out

        moving_glb = os.path.join(self.tmp, "moving.glb")
        res = m2m_clips.run(self.rig, None, "Tiny", out, ["Roll_RM"], root_motion=True, preview=moving_glb,
                            check=False)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["fbx_takes"], ["Roll_RM"])
        self.assertGreater(self.measure(moving_glb)["Roll_RM"]["travel"], 1.0)  # ~2.5 m at this leg length


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what is actually inside a batch of source exports, before any of them has a rig spec.
#
# probe_tips.py answers "where do this model's limbs end", but it needs a spec to run. This answers the question
# that comes first: what did the generator give us? The three cases - a Tripo-style animal skeleton (bone_0,
# bone_1...), a Mixamo humanoid skeleton, or no skeleton at all - each need a different spec kind, and guessing which is which from a thumbnail is how you
# write a spec for a rig that is not there.
#
#   blender -b --python autorig/steps/survey.py -- [<group>]
#   blender -b --python autorig/steps/survey.py -- -only wolf,moth
#
# Each model's facts are printed and written to <AUTORIG_WORK>/survey/<model>.json.

import bpy, sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from layout import ROOT, source_fbx, models_in, work_dir
import source_io


def skeleton_kind(names):
    if not names:
        return "none"
    try:
        import skeletons
        conv, conf = skeletons.detect_convention(names)
        if conf >= 0.25:
            return conv
    except Exception:
        pass
    if any(n.startswith("mixamorig") for n in names):
        return "mixamo"
    if all(n.startswith("bone_") for n in names):
        return "tripo"
    return "other"


def survey(key):
    path = source_fbx(key)
    if not path:
        return {"model": key, "error": "no source export (FBX, GLB, glTF or OBJ)"}

    bpy.ops.wm.read_factory_settings(use_empty=True)
    source_io.import_source(path)

    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    bones = [b.name for b in arms[0].data.bones] if arms else []

    verts = sum(len(m.data.vertices) for m in meshes)
    tris = sum(len(m.data.loop_triangles) for m in meshes for _ in [m.data.calc_loop_triangles()])

    # World-space extent of everything, which is what tells you the import scale.
    lo = [min((m.matrix_world @ v.co)[i] for m in meshes for v in m.data.vertices) for i in range(3)]
    hi = [max((m.matrix_world @ v.co)[i] for m in meshes for v in m.data.vertices) for i in range(3)]
    size = [round(hi[i] - lo[i], 3) for i in range(3)]

    # How much of the skin is actually weighted: generated animal rigs often bind almost nothing.
    weighted = 0
    if arms:
        groups = {g.name for m in meshes for g in m.vertex_groups}
        bound = groups & set(bones)
        for m in meshes:
            idx = {g.index for g in m.vertex_groups if g.name in bound}
            weighted += sum(1 for v in m.data.vertices if any(ge.group in idx and ge.weight > 0 for ge in v.groups))

    return {
        "model": key,
        "skeleton": skeleton_kind(bones),
        "bones": len(bones),
        "meshes": len(meshes),
        "verts": verts,
        "tris": tris,
        "size": size,
        "tallest_axis": "xyz"[size.index(max(size))],
        "skinned_pct": round(100.0 * weighted / verts, 1) if (arms and verts) else 0.0,
        "file": os.path.relpath(path, ROOT),
    }


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "-only" in a:
        keys = a[a.index("-only") + 1].split(",")
    else:
        keys = models_in(a[0] if a else None)

    out = work_dir("survey")
    for k in keys:
        try:
            r = survey(k)
        except Exception as e:
            r = {"model": k, "error": repr(e)}
        with open(os.path.join(out, k + ".json"), "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1)
        print("SURVEY " + json.dumps(r))
    print("SURVEY_DONE %d" % len(keys))


main()

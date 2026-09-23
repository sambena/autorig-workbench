# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the source model as it came, for the spec editor (gui/spec_editor.html). Runs inside Blender.
#
#   blender -b --python autorig/steps/source_preview.py -- -only wolf[,other] [-out <dir>]
#
# Writes, for each model, into <AUTORIG_WORK>/source/ (or -out):
#   <model>.glb    the source export's meshes with their materials and textures, exactly where the source puts them
#                  (world space, glTF's Y up). No armature and no skin: the editor draws the skeleton itself, from
#   <model>.json   the source skeleton's joints (name, head and tail, parent, depth, children), in Blender's own
#                  Z-up world coordinates, with the bounds, the counts, and which joints the rig step folds into
#                  their parent (a bone of no length; rerig.repair). A model with no skeleton has no joints: the
#                  editor then shows the mesh and lets points be placed on it.
#
# Nothing is turned or moved: the editor applies the spec's own turn (forward, straighten) itself, so it can follow
# an edit without running this again. The source file is only read.
import bpy, sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from layout import ROOT, source_model, work_dir
import source_io

FORMAT = "autorig-source/1"
MERGE = 0.004            # rerig.repair: a joint this close to its parent (share of the model's size) folds into it


def skeleton_kind(names):
    if not names: return "none"
    try:
        import skeletons
        conv, conf = skeletons.detect_convention(names)
        if conf >= 0.25:
            return conv
    except Exception:
        pass
    if any(n.startswith("mixamorig") for n in names): return "mixamo"
    if all(n.startswith("bone_") for n in names): return "tripo"
    return "other"


def r(v, n=5):
    return [round(float(x), n) for x in v]


def preview(key, out):
    path = source_model(key)
    if not path:
        return {"model": key, "error": "no source export (FBX, GLB, glTF or OBJ)"}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    source_io.import_source(path)

    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    joints = []
    if arms:
        a = arms[0]
        M = a.matrix_world
        depth = {}
        for b in a.data.bones:                      # parents come before their children in Blender's list
            depth[b.name] = depth[b.parent.name] + 1 if b.parent else 0
        for b in a.data.bones:
            joints.append({"name": b.name, "head": r(M @ b.head_local), "tail": r(M @ b.tail_local),
                           "parent": b.parent.name if b.parent else None, "depth": depth[b.name],
                           "children": [c.name for c in b.children]})

    # the meshes as they stand, without their armature: world transforms kept, skin and parenting dropped
    for m in meshes:
        mw = m.matrix_world.copy()
        for md in list(m.modifiers):
            if md.type == 'ARMATURE': m.modifiers.remove(md)
        m.parent = None
        m.matrix_world = mw
        m.vertex_groups.clear()
    for o in list(bpy.data.objects):
        if o.type != 'MESH': bpy.data.objects.remove(o, do_unlink=True)
    for m in meshes: m.data.shape_keys and m.shape_key_clear()

    co = [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]
    lo = [min(p[i] for p in co) for i in range(3)] if co else [0, 0, 0]
    hi = [max(p[i] for p in co) for i in range(3)] if co else [0, 0, 0]
    size = max(hi[i] - lo[i] for i in range(3)) or 1.0

    # joints the rig step folds into their parent (a bone of no length), as rerig.repair decides it
    by = {j["name"]: j for j in joints}
    for j in joints:
        p = by.get(j["parent"])
        if p and p["name"] != "bone_0":
            d = sum((j["head"][i] - p["head"][i]) ** 2 for i in range(3)) ** 0.5
            if d < size * MERGE: j["folds_into"] = p["name"]

    os.makedirs(out, exist_ok=True)
    glb = os.path.join(out, key + ".glb")
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=glb, export_format='GLB', use_selection=False, export_yup=True,
                              export_apply=True, export_animations=False, export_skins=False, export_morph=False,
                              export_materials='EXPORT', export_image_format='AUTO')
    tris = 0
    for m in meshes:
        m.data.calc_loop_triangles(); tris += len(m.data.loop_triangles)
    info = {"format": FORMAT, "model": key, "source": os.path.relpath(path, ROOT).replace("\\", "/"),
            "source_mtime": os.path.getmtime(path), "glb": os.path.basename(glb),
            "axes": "joints are Blender world coordinates (Z up); the GLB is glTF's Y up: (x, y, z) -> (x, z, -y)",
            "skeleton": skeleton_kind([j["name"] for j in joints]), "joints": joints,
            "bounds": {"lo": r(lo), "hi": r(hi)}, "meshes": len(meshes), "verts": len(co), "tris": tris}
    with open(os.path.join(out, key + ".json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return {"model": key, "glb": glb, "skeleton": info["skeleton"], "joints": len(joints), "verts": len(co)}


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    val = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    keys = (val("-only") or "").split(",")
    out = os.path.abspath(val("-out") or work_dir("source"))
    done = 0
    for k in [k for k in keys if k]:
        try:
            res = preview(k, out)
        except Exception as e:
            import traceback; traceback.print_exc()
            res = {"model": k, "error": repr(e)}
        print("SOURCE_PREVIEW " + json.dumps(res))
        done += 0 if res.get("error") else 1
    print("SOURCE_PREVIEW_DONE %d" % done)


main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: light cleanup in Blender, before the rig step.
#
#   blender -b --python autorig/steps/prepare.py -- -only wolf[,moth] [--unwrap] [--loops] [--format glb,fbx,obj]
#
# Imports the raw source (never the copy this step wrote last time). Applies object transforms.
# Heals loose and degenerate geometry the way Mesh Doctor does. Turns the mesh onto -Y and stands
# it on z=0, centred, and turns its armature with it when it has one. Checks UV stretch and the
# edge count across the knee, hip, elbow and shoulder.
#
# Writes <model>/prepared/<model>.glb (and prepare.json). The raw file is not modified. While that
# copy is current, the other steps read it. --unwrap runs Smart UV Project only when the UV check
# fails, and says so: a baked texture will not follow. --loops cuts one extra loop through each
# thin band. Neither is the default. This step does not remesh.
import argparse
import json
import math
import os
import shutil
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
# Core goes first. steps/mesh_doctor.py and this file are the command-line wrappers; importing
# those names must reach autorig/core, or heal_mesh_object and the prepare decisions are missing.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import layout
import mesh_doctor
import prepare
import source_io
import spec_store
import suggest


FORMATS = ("glb", "fbx", "obj")


def args(argv):
    raw = list(argv) if argv is not None else sys.argv[1:]
    if "--" in raw:
        raw = raw[raw.index("--") + 1:]
    parser = argparse.ArgumentParser(description="Prepare a source mesh before rigging.")
    parser.add_argument("-only", required=True, help="Model name, or several separated by commas.")
    parser.add_argument("--unwrap", action="store_true",
                        help="Smart UV Project when the UV check fails. A baked texture will not follow.")
    parser.add_argument("--loops", action="store_true",
                        help="Cut one extra loop through each thin joint band on an upright body.")
    parser.add_argument("--format", default="glb",
                        help="Comma list of glb, fbx, obj. Default glb, which is what the later steps read.")
    ns = parser.parse_args(raw)
    formats = []
    for part in ns.format.split(","):
        ext = part.strip().lower().lstrip(".")
        if ext and ext not in formats:
            if ext not in FORMATS:
                raise ValueError("unknown format %r (glb, fbx, obj)" % ext)
            formats.append(ext)
    if not formats:
        formats = ["glb"]
    return ns.only.split(","), ns.unwrap, ns.loops, formats


def bounds_of(meshes):
    lo = [math.inf, math.inf, math.inf]
    hi = [-math.inf, -math.inf, -math.inf]
    n = 0
    for obj in meshes:
        for v in obj.data.vertices:
            n += 1
            for i in range(3):
                lo[i] = min(lo[i], v.co[i])
                hi[i] = max(hi[i], v.co[i])
    if not n:
        return None, None
    return lo, hi


def bake(obj):
    """Object transform into the data, identity on the object. Modifiers are dropped: the rig rebuilds them."""
    mw = obj.matrix_world.copy()
    obj.parent = None
    if obj.type == "MESH":
        for md in list(obj.modifiers):
            obj.modifiers.remove(md)
        obj.data.transform(mw)
    elif obj.type == "ARMATURE":
        obj.data.transform(mw)
    obj.matrix_world = Matrix.Identity(4)


def apply_matrix(objs, mat):
    for obj in objs:
        obj.data.transform(mat)
        obj.data.update()


def face_stretch(me, uv, poly):
    if uv is None or len(poly.loop_indices) < 3:
        return None
    pts, uvs = [], []
    for li in poly.loop_indices:
        pts.append(me.vertices[me.loops[li].vertex_index].co)
        uvs.append(uv.data[li].uv)
    best = None
    for i in range(1, len(pts) - 1):
        r = prepare.tri_stretch(pts[0], pts[i], pts[i + 1], uvs[0], uvs[i], uvs[i + 1])
        if r is not None and (best is None or r > best):
            best = r
    return best


def collect_uv(meshes):
    ratios = []
    missing = 0
    total = 0
    for obj in meshes:
        me = obj.data
        uv = me.uv_layers.active
        for poly in me.polygons:
            total += 1
            if uv is None:
                missing += 1
                continue
            r = face_stretch(me, uv, poly)
            if r is None:
                missing += 1
            else:
                ratios.append(r)
    return prepare.uv_report(ratios, missing=missing, total=total)


def collect_joints(meshes):
    verts = []
    edges = []
    for obj in meshes:
        base = len(verts)
        verts.extend(v.co[:] for v in obj.data.vertices)
        edges.extend((base + e.vertices[0], base + e.vertices[1]) for e in obj.data.edges)
    lo, hi = bounds_of(meshes)
    if lo is None:
        return {"upright": False, "bands": [], "thin": [], "note": "empty mesh"}
    return prepare.joint_report(verts, edges, lo, hi)


def smart_unwrap(obj):
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def add_loop(obj, z):
    """One cut through the faces that cross height z. The new vertices sit on that plane."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    geom = []
    seen = set()
    for face in bm.faces:
        zs = [v.co.z for v in face.verts]
        if not zs or min(zs) >= z or max(zs) <= z:
            continue
        geom.append(face)
        for edge in face.edges:
            if edge.index not in seen:
                seen.add(edge.index)
                geom.append(edge)
        for vert in face.verts:
            if ("v", vert.index) not in seen:
                seen.add(("v", vert.index))
                geom.append(vert)
    if not geom:
        bm.free()
        return 0
    res = bmesh.ops.bisect_plane(
        bm, geom=geom, plane_co=(0.0, 0.0, z), plane_no=(0.0, 0.0, 1.0),
        clear_inner=False, clear_outer=False,
    )
    n = len(res.get("geom_cut", []))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return n


def join_meshes(meshes):
    if len(meshes) <= 1:
        return meshes[:1]
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    try:
        bpy.ops.object.join()
    except Exception as e:
        print("PREPARE join failed (%s); keeping %d meshes" % (e, len(meshes)))
        return meshes
    joined = bpy.context.view_layer.objects.active
    return [joined] if joined is not None else meshes


def export_files(out_dir, name, formats):
    paths = []
    if "glb" in formats:
        path = os.path.join(out_dir, name + ".glb")
        bpy.ops.export_scene.gltf(
            filepath=path, export_format="GLB", export_materials="EXPORT",
            export_animations=False, export_yup=True, use_selection=False,
        )
        paths.append(path)
    if "fbx" in formats:
        path = os.path.join(out_dir, name + ".fbx")
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=False, object_types={"ARMATURE", "MESH"},
            apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True,
            axis_forward="-Z", axis_up="Y", add_leaf_bones=False, bake_anim=False,
            path_mode="COPY", embed_textures=True,
        )
        paths.append(path)
    if "obj" in formats:
        path = os.path.join(out_dir, name + ".obj")
        bpy.ops.wm.obj_export(filepath=path, export_materials=True, export_uv=True,
                              export_selected_objects=False)
        paths.append(path)
    return paths


def preferred_name(paths):
    for ext in (".glb", ".fbx", ".obj"):
        for path in paths:
            if path.lower().endswith(ext):
                return os.path.basename(path)
    return os.path.basename(paths[0])


def read_report(pack):
    path = os.path.join(pack, "prepared", "prepare.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def update_spec(name, spec, before, yaw):
    """Point an existing forward at -Y after a real turn. No spec, or no forward, is left alone."""
    if abs(yaw) < 1e-3 or not spec:
        return False
    new = prepare.rewrite_forward(spec, before)
    if new is None:
        return False
    path = spec_store.spec_path(name)
    if not os.path.isfile(path):
        return False
    shutil.copy2(path, path + ".bak")
    write_json(path, new)
    spec_store.reload()
    print("PREPARE %s spec forward set to -Y (was %s); rig.json.bak kept" % (name, before))
    return True


def armature_joints(arms):
    joints = []
    for arm in arms:
        for bone in arm.data.bones:
            head = arm.matrix_world @ bone.head_local
            joints.append({"name": bone.name, "pos": [head.x, head.y, head.z]})
    return joints


def prepare_one(name, unwrap, do_loops, formats):
    pack = layout.pack_dir(name)
    raw = layout.raw_source(name)
    if not raw or not os.path.isfile(raw):
        print("PREPARE %s no source" % name)
        return 1
    old = read_report(pack)
    try:
        same = abs(os.path.getmtime(raw) - float(old.get("source_mtime"))) < 1.0 if "source_mtime" in old else False
    except (TypeError, ValueError, OSError):
        same = False
    spec = {}
    try:
        spec = spec_store.model(name) or {}
    except Exception as e:
        print("PREPARE %s spec not read (%s); facing will be measured" % (name, e))
    rig = spec.get("rig") if isinstance(spec.get("rig"), dict) else {}
    hum = spec.get("humanoid") if isinstance(spec.get("humanoid"), dict) else {}
    spec_fwd = rig.get("forward")
    if spec_fwd is None:
        spec_fwd = hum.get("forward")
    fwd = prepare.raw_forward(spec_fwd, old.get("forward_before"), same)
    how = "saved" if same and fwd is not None else ("spec" if fwd is not None else "detected")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    source_io.import_source(raw)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not meshes:
        print("PREPARE %s no mesh in %s" % (name, os.path.basename(raw)))
        return 1
    for obj in meshes + arms:
        bake(obj)
    for obj in list(bpy.data.objects):
        if obj.type not in ("MESH", "ARMATURE"):
            bpy.data.objects.remove(obj, do_unlink=True)

    healed = None
    try:
        actions = []
        for obj in meshes:
            rep = mesh_doctor.heal_mesh_object(obj)
            if rep and rep.get("actions"):
                actions.append(rep["actions"])
        if actions:
            healed = actions[0] if len(actions) == 1 else actions
            print("PREPARE %s healed %s" % (name, healed))
    except Exception as e:
        print("PREPARE %s heal skipped (%s)" % (name, e))

    meshes = join_meshes(meshes)
    if fwd is None:
        coords = [v.co[:] for obj in meshes for v in obj.data.vertices]
        found = suggest.detect_mesh_forward(coords, joints=armature_joints(arms) or None)
        fwd = list(found) if found else [0, -1, 0]
        how = "detected"
    yaw = prepare.yaw_to_neg_y(fwd)
    if not rig.get("straighten") and not hum.get("straighten"):
        yaw = prepare.snap_quarter(yaw)
    if abs(yaw) > 1e-8:
        apply_matrix(meshes + arms, Matrix.Rotation(yaw, 4, "Z"))

    origin = "center" if rig.get("origin") == "center" else "feet"
    lo, hi = bounds_of(meshes)
    if lo is None:
        print("PREPARE %s empty after cleanup" % name)
        return 1
    off = prepare.feet_offset(lo, hi, origin)
    if any(abs(c) > 1e-8 for c in off):
        apply_matrix(meshes + arms, Matrix.Translation(Vector(off)))

    uv = collect_uv(meshes)
    print("PREPARE %s uv %s" % (name, uv.get("note")))
    unwrapped = False
    if unwrap and not uv.get("usable"):
        try:
            for obj in meshes:
                smart_unwrap(obj)
            unwrapped = True
            print("PREPARE %s unwrapped with Smart UV Project; a baked texture will not follow" % name)
        except Exception as e:
            print("PREPARE %s unwrap failed (%s)" % (name, e))
    elif unwrap:
        print("PREPARE %s UVs usable, not unwrapped" % name)

    joints = collect_joints(meshes)
    print("PREPARE %s joints %s" % (name, joints.get("note")))
    cut = []
    if do_loops and joints.get("thin"):
        for band in joints.get("bands") or []:
            if not band.get("thin"):
                continue
            n = 0
            for obj in meshes:
                try:
                    n += add_loop(obj, band["z"])
                except Exception as e:
                    print("PREPARE %s loop at %s failed (%s)" % (name, band["name"], e))
            if n:
                cut.append(band["name"])
                print("PREPARE %s loop cut at %s (z=%s)" % (name, band["name"], band["z"]))

    for obj in meshes:
        obj.name = name
        obj.data.name = name
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    out_dir = os.path.join(pack, "prepared")
    os.makedirs(out_dir, exist_ok=True)
    try:
        paths = export_files(out_dir, name, formats)
    except Exception as e:
        print("PREPARE %s export failed (%s)" % (name, e))
        return 1
    if not paths:
        print("PREPARE %s nothing exported" % name)
        return 1

    got = prepare.as_forward(fwd) or (0.0, -1.0, 0.0)
    before = [round(float(c), 4) for c in got]
    report = {
        "source": os.path.relpath(raw, pack).replace("\\", "/"),
        "source_mtime": os.path.getmtime(raw),
        "output": preferred_name(paths),
        "formats": [os.path.basename(p) for p in paths],
        "turned_deg": round(math.degrees(yaw), 1),
        "forward_before": before,
        "forward_how": how,
        "origin": origin,
        "offset": [round(c, 4) for c in off],
        "healed": healed,
        "uv": uv,
        "joints": joints,
        "unwrapped": unwrapped,
        "loops_cut": cut,
    }
    write_json(os.path.join(out_dir, "prepare.json"), report)
    updated = update_spec(name, spec, before, yaw)
    report["spec_forward_updated"] = updated
    write_json(os.path.join(out_dir, "prepare.json"), report)
    print("PREPARE %s turned %.1f deg (%s %s), origin %s" % (name, report["turned_deg"], how, before, origin))
    print("PREPARE %s saved %s" % (name, os.path.relpath(os.path.join(out_dir, report["output"]), pack)))
    return 0


def main(argv=None):
    try:
        names, unwrap, do_loops, formats = args(argv)
    except ValueError as e:
        print("PREPARE_ERROR: %s" % e)
        return 1
    code = 0
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            code |= prepare_one(name, unwrap, do_loops, formats)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("PREPARE %s error %s" % (name, e))
            code = 1
    print("PREPARE_DONE")
    return code


if __name__ == "__main__":
    sys.exit(main())

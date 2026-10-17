# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Pre-flight Mesh Doctor & Healing CLI step.
#
#   python autorig/steps/mesh_doctor.py <model> [--heal] [--out <dir>]
#   blender -b --python autorig/steps/mesh_doctor.py -- <model> [--heal | --repair | --watertight [--bake 1024]]
#
# --heal        Blender's own clean-up (loose parts, zero-area faces, doubles, normals): <model>_healed.glb
# --repair      PyMeshLab (extra): duplicates, non-manifold edges and vertices, small holes; UVs and materials kept:
#               <model>_repaired.glb
# --watertight  PyMeshFix (extra) closes every shell, manifold3d joins them: one closed surface for voxel skinning, a
#               collider or a boolean; <model>_watertight.glb. UVs are lost; --bake N re-unwraps it with xatlas and
#               bakes the source's colour onto an N x N atlas (selected to active, Cycles on the GPU when there is one).
# Every copy goes to the output folder (work/doctor); the model's own source file is never overwritten.

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import layout
import mesh_doctor


def import_source(src):
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(src)[1].lower()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=src)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=src)


def fix_copy(src, name, kind, out_d, bake=0):
    """One fixed copy of the source, written to out_d as <name>_<healed|repaired|watertight>.glb. Returns its report."""
    import bpy
    import extras
    extras.activate()
    import_source(src)
    if kind == "heal":
        meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        if not meshes:
            return {"error": "no mesh"}
        obj = max(meshes, key=lambda o: len(o.data.vertices))
        res = mesh_doctor.heal_mesh_object(obj)
        print(f"HEALED {name} score: {res['before']['health_score']} -> {res['after']['health_score']}")
        print(f"  ACTIONS: {res['actions']}")
        suffix = "healed"
    else:
        obj = mesh_doctor._joined_mesh()
        if obj is None:
            return {"error": "no mesh"}
        res = mesh_doctor.repair_object(obj) if kind == "repair" else mesh_doctor.watertight_object(obj)
        if res.get("error"):
            print(f"DOCTOR_ERROR: {kind}: {res['error']}")
            return res
        print(f"{kind.upper()} {name} " + json.dumps(res))
        suffix = "repaired" if kind == "repair" else "watertight"
        if kind == "watertight" and bake:
            res["bake"] = bake_back(src, obj, out_d, f"{name}_watertight", bake)
    out = os.path.join(out_d, f"{name}_{suffix}.glb")
    for o in list(bpy.data.objects):
        o.select_set(o is obj or kind == "heal")
    bpy.ops.export_scene.gltf(filepath=out, export_format='GLB', export_materials='EXPORT', export_animations=False,
                              use_selection=True)
    print(f"  SAVED: {out}")
    res["file"] = os.path.basename(out)
    return res


def bake_back(src, obj, folder, stem, size):
    """obj re-unwrapped with xatlas and the source's colour baked onto it (selected to active)."""
    import bpy
    import extras, mesh_bridge
    if not extras.have("xatlas"):
        return {"error": "needs the xatlas extra"}
    keep = set(bpy.data.objects)
    ext = os.path.splitext(src)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=src)
    else:
        bpy.ops.wm.obj_import(filepath=src)
    sources = [o for o in bpy.data.objects if o not in keep and o.type == 'MESH']
    mesh_bridge.unwrap(obj, size, padding=max(2, size // 256))
    maps = mesh_bridge.bake_to_atlas(sources, obj, size, folder, stem, normal=False)
    for o in [o for o in bpy.data.objects if o not in keep]:
        bpy.data.objects.remove(o, do_unlink=True)
    print(f"  BAKED: {maps['basecolor']}")
    return {"image": os.path.basename(maps["basecolor"]), "size": size}


def main(argv=None):
    # Support both direct python and Blender '--' argument syntax
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if "--" in raw_args:
        raw_args = raw_args[raw_args.index("--") + 1:]

    parser = argparse.ArgumentParser(description="Mesh Doctor: pre-flight geometry diagnostics and healing.")
    parser.add_argument("model", help="Name of the model to inspect.")
    parser.add_argument("--heal", action="store_true", help="Apply auto-healing repair pass to fix defects.")
    parser.add_argument("--repair", action="store_true", help="PyMeshLab repair (extra): non-manifold edges, duplicates, holes.")
    parser.add_argument("--watertight", action="store_true", help="PyMeshFix (extra): one closed surface.")
    parser.add_argument("--bake", type=int, default=0, help="with --watertight: re-unwrap (xatlas) and bake the colour at this size.")
    parser.add_argument("--out", default=None, help="Optional output directory for diagnostic JSON or healed model.")
    args = parser.parse_args(raw_args)

    name = args.model
    pack_d = layout.pack_dir(name)
    if not pack_d:
        sys.stderr.write(f"DOCTOR_ERROR: model '{name}' not found\n")
        return 1

    src = layout.source_model(name)
    if not src or not os.path.isfile(src):
        sys.stderr.write(f"DOCTOR_ERROR: no source 3D model found for '{name}'\n")
        return 1

    try:
        diag = mesh_doctor.inspect_source_model(src)
        print(f"DOCTOR {name} file={os.path.basename(src)} grade={diag['grade']} score={diag['health_score']}/100")
        print(f"  VERTS: {diag.get('verts', '-')}  FACES: {diag.get('faces', '-')}  EDGES: {diag.get('edges', '-')}")
        if diag.get("non_manifold_edges"):
            print(f"  WARNING: {diag['non_manifold_edges']} non-manifold edge(s)")
        if diag.get("degenerate_faces"):
            print(f"  WARNING: {diag['degenerate_faces']} zero-area degenerate face(s)")
        if diag.get("loose_verts"):
            print(f"  WARNING: {diag['loose_verts']} loose floating vertex/vertices")
        if diag.get("duplicate_verts"):
            print(f"  NOTICE:  {diag['duplicate_verts']} coincident duplicate vertex/vertices")

        out_d = args.out or layout.work_dir("doctor")
        os.makedirs(out_d, exist_ok=True)
        report_file = os.path.join(out_d, f"{name}.json")
        with open(report_file, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2)

        if args.heal or args.repair or args.watertight:
            if not mesh_doctor.HAVE_BLENDER:
                print(f"NOTICE: healing and repair run under Blender (blender -b --python ...)")
            else:
                for kind in [k for k, on in (("heal", args.heal), ("repair", args.repair), ("watertight", args.watertight)) if on]:
                    res = fix_copy(src, name, kind, out_d, args.bake)
                    diag[kind] = res
                with open(report_file, "w", encoding="utf-8") as fh:
                    json.dump(diag, fh, indent=2)

        print("DOCTOR_DONE")
        return 0
    except Exception as e:
        sys.stderr.write(f"DOCTOR_ERROR: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

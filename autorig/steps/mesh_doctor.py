# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Pre-flight Mesh Doctor & Healing CLI step.
#
#   python autorig/steps/mesh_doctor.py <model> [--heal] [--out <dir>]
#   blender -b --python autorig/steps/mesh_doctor.py -- <model> [--heal]

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import layout
import mesh_doctor


def main(argv=None):
    # Support both direct python and Blender '--' argument syntax
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if "--" in raw_args:
        raw_args = raw_args[raw_args.index("--") + 1:]

    parser = argparse.ArgumentParser(description="Mesh Doctor: pre-flight geometry diagnostics and healing.")
    parser.add_argument("model", help="Name of the model to inspect.")
    parser.add_argument("--heal", action="store_true", help="Apply auto-healing repair pass to fix defects.")
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

        if args.heal:
            if mesh_doctor.HAVE_BLENDER:
                import bpy
                bpy.ops.wm.read_factory_settings(use_empty=True)
                ext = os.path.splitext(src)[1].lower()
                if ext == ".obj":
                    bpy.ops.wm.obj_import(filepath=src)
                elif ext in (".glb", ".gltf"):
                    bpy.ops.import_scene.gltf(filepath=src)
                elif ext == ".fbx":
                    bpy.ops.import_scene.fbx(filepath=src)
                meshes = [o for o in bpy.data.objects if o.type == 'MESH']
                if meshes:
                    main_obj = max(meshes, key=lambda o: len(o.data.vertices))
                    res = mesh_doctor.heal_mesh_object(main_obj)
                    print(f"HEALED {name} score: {res['before']['health_score']} -> {res['after']['health_score']}")
                    print(f"  ACTIONS: {res['actions']}")
                    # the healed mesh, beside the report; the source is never written over
                    healed = os.path.join(out_d, f"{name}_healed.obj")
                    for o in bpy.data.objects: o.select_set(o == main_obj)
                    bpy.context.view_layer.objects.active = main_obj
                    bpy.ops.wm.obj_export(filepath=healed, export_selected_objects=True)
                    print(f"  WROTE {healed}")
            else:
                print(f"NOTICE: deep healing requires running under Blender (blender -b --python ...)")

        print("DOCTOR_DONE")
        return 0
    except Exception as e:
        sys.stderr.write(f"DOCTOR_ERROR: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

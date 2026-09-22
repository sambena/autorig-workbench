# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: importing a source export into Blender, whatever its format (runs inside Blender).
#
# FBX is imported exactly as the pipeline always has (the settings every rig was tuned against). GLB/glTF and OBJ
# go through Blender's own importers; both arrive Y-up converted to Blender's Z-up, as FBX does.
import os
import bpy, bmesh


def import_source(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, ignore_leaf_bones=False, automatic_bone_orientation=False)
    elif ext in (".glb", ".gltf"):
        # glTF stores a vertex once per UV seam and hard edge; merged back, the surface is one piece again, as bone
        # heat and the surface walks need (unmerged, most of a model came out unreached)
        bpy.ops.import_scene.gltf(filepath=path, merge_vertices=True)
        weld([o for o in bpy.context.scene.objects if o.type == 'MESH'])
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError("not a source format the tool reads (FBX, GLB, glTF, OBJ): " + path)


def weld(meshes, tolerance=1e-5):
    """Vertices that sit on the same spot (within a hundred-thousandth of the mesh's size) become one: what the
    importer's own merge leaves split where normals differ."""
    for o in meshes:
        size = max(o.dimensions) or 1.0
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=tolerance * size)
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()

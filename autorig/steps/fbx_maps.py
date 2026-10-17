# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a rigged FBX given its source's colour and normal maps, saved beside it as
# <Name>.fbm/<Name>_basecolor.png and _normal.png and wired straight into its material.
#
#   blender -b --python autorig/steps/fbx_maps.py -- --fbx rigged/Ice.fbx --source Ice.glb --name IceTeen --out <folder>
#   python autorig/cli/run.py fbx-maps <the same arguments>
#
# The rig and decimate steps export the FBX with `path_mode='AUTO'` and no embedded textures. A GLB source keeps its
# images packed inside the .glb, so the FBX reaches an engine with no texture at all (Tornado Titans' story
# characters, 2026-10-17: Tripo multi-view GLBs through rerig_humanoid.py). game_prop.py already saves a GLB's maps
# for props; this does the same for anything rigged, without touching its armature, weights or mesh:
#   1. imports the source (GLB/glTF/FBX) and saves its base colour and normal images (game_prop.save_maps);
#   2. imports the rigged FBX into a fresh scene and wires those images into each of its materials;
#   3. exports it again as <out>/<Name>.fbx with decimate.py's settings (-Z forward, Y up, no leaf bones, deform
#      bones only), the maps referenced by relative path.
import argparse, os, sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
from game_prop import import_model, save_maps


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--fbx", required=True, help="the rigged FBX (rig or decimate step output)")
    p.add_argument("--source", required=True, help="the model's source file, whose images the FBX lost")
    p.add_argument("--name", required=True, help="the name to export under (the engine's resource name)")
    p.add_argument("--out", required=True, help="folder for <Name>.fbx and <Name>.fbm/")
    return p.parse_args(argv)


def wire(material, base, normal):
    """A plain Principled BSDF on `material` reading `base` (and `normal`), as game_prop.save_maps leaves it."""
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    b = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(b.outputs["BSDF"], out.inputs["Surface"])
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = base
    links.new(tex.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.75
    if normal is not None:
        nt = nodes.new("ShaderNodeTexImage")
        nt.image = normal
        nt.image.colorspace_settings.name = "Non-Color"
        nm = nodes.new("ShaderNodeNormalMap")
        links.new(nt.outputs["Color"], nm.inputs["Color"])
        links.new(nm.outputs["Normal"], b.inputs["Normal"])


def main():
    a = args()
    os.makedirs(a.out, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_model(a.source)
    saved = save_maps(a.name, a.out)
    if not saved:
        raise SystemExit("fbx_maps.py: the source has no base colour image to give the FBX")
    folder = os.path.join(a.out, a.name + ".fbm")
    paths = {s: os.path.join(folder, "%s_%s.png" % (a.name, s)) for s in ("basecolor", "normal")}

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=os.path.abspath(a.fbx))
    base = bpy.data.images.load(paths["basecolor"])
    normal = bpy.data.images.load(paths["normal"]) if os.path.exists(paths["normal"]) else None
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    materials = {m for o in meshes for m in o.data.materials if m is not None}
    if not materials:
        m = bpy.data.materials.new(a.name)
        for o in meshes:
            o.data.materials.append(m)
        materials = {m}
    for m in materials:
        wire(m, base, normal)

    bpy.ops.object.select_all(action="DESELECT")
    for o in bpy.context.scene.objects:
        o.select_set(o.type in ("MESH", "ARMATURE"))
    bpy.context.view_layer.objects.active = meshes[0]
    out = os.path.join(a.out, a.name + ".fbx")
    bpy.ops.export_scene.fbx(filepath=out, use_selection=True, object_types={"ARMATURE", "MESH"},
                             apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True,
                             axis_forward="-Z", axis_up="Y", add_leaf_bones=False,
                             use_armature_deform_only=True, bake_anim=False,
                             mesh_smooth_type="OFF", path_mode="RELATIVE", embed_textures=False)
    print("FBX_MAPS", a.name, "materials", len(materials), "maps", saved, "->", out)


if __name__ == "__main__":
    main()

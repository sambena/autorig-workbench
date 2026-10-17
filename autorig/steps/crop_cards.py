# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: picture cards of a plant for a game's foliage atlas (Tornado Titans' Tripo crops, 2026-10-17).
#
# A field draws thousands of plants as crossed cards, which no generated mesh could afford. Each plant is rendered
# flat (its own colour, no light: the game lights the card) on a transparent ground, its foot at the bottom middle of
# the picture and its top near the top. A card drawn on a quad `wide` times as wide as it is tall, but kept in a square
# atlas cell, is rendered that narrow (the atlas stretches it square; the quad squeezes it back).
#
#   blender -b --python autorig/steps/crop_cards.py -- --out <folder> \
#           --card corn_green_front=Corn_MatureGreen.glb:0:0.45 --card wheat_gold=Wheat_Golden.glb
#   python autorig/cli/run.py crop-cards <the same arguments>
#
#   --card <name>=<model>[:<view degrees, 0 front, 90 side>[:<wide>]]   writes <out>/<name>.png, 1024 px tall
#
# Prints one CARD line of JSON per card.
import argparse, json, math, os, sys

import bpy
from mathutils import Vector

SIZE = 1024


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--card", action="append", required=True, help="<name>=<model>[:<view>[:<wide>]]")
    return p.parse_args(sys.argv[sys.argv.index("--") + 1:])


def parse(card):
    name, rest = card.split("=", 1)
    parts = rest.rsplit(":", 2)
    # a Windows path has a drive colon: only numbers after the last colons are the view and the width
    nums = []
    while len(parts) > 1:
        try:
            float(parts[-1])
        except ValueError:
            break
        nums.insert(0, float(parts.pop()))
    path = ":".join(parts)
    view = nums[0] if len(nums) > 0 else 0.0
    wide = nums[1] if len(nums) > 1 else 1.0
    return name, path, view, wide


def render(src, view, wide, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(src)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=src)
    else:
        bpy.ops.wm.obj_import(filepath=src)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    height = hi.z - lo.z
    # the plant's height fills 94% of the picture, its foot 2% above the bottom, centred across
    span = height / 0.94
    centre = Vector(((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5, lo.z - span * 0.02 + span * 0.5))
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = span
    a = math.radians(view)
    cam.location = centre + Vector((math.sin(a), -math.cos(a), 0.0)) * (span * 3.0)
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y = max(8, int(round(SIZE * wide))), SIZE
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    return height


def main():
    a = args()
    os.makedirs(a.out, exist_ok=True)
    for card in a.card:
        name, path, view, wide = parse(card)
        out = os.path.abspath(os.path.join(a.out, name + ".png"))
        h = render(os.path.abspath(path), view, wide, out)
        print("CARD " + json.dumps({"name": name, "from": os.path.basename(path), "view": view, "wide": wide, "png": out, "model_height": round(h, 3)}))


if __name__ == "__main__":
    main()

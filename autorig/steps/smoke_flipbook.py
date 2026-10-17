# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a billowing puff of smoke or dust, simulated in Blender and rendered as a flipbook: one PNG of
# frames in a grid (an engine's texture-sheet animation), white with the puff's density in alpha so the game tints it.
#
#   blender -b --python autorig/steps/smoke_flipbook.py -- --out dust_puff.png [--grid 4] [--size 256] [--res 64]
#           [--frames 72] [--swirl 1.5] [--samples 32] [--seed 3]
#   python autorig/cli/run.py smoke-flipbook <the same arguments>
#
# Made for Tornado Titans (2026-10-17): the dust rolling round a tornado's foot was a soft round dot, and Phil's
# reference has a boiling cloud of dirt. Generated here, from nothing but the solver, so the picture is our own.
#   1. a smoke domain with a burst of smoke from a small sphere at its foot, pushed up and outward, stirred by
#      turbulence and a vortex (--swirl) so it billows and rolls;
#   2. baked with Blender's gas solver (Mantaflow) at --res cells across;
#   3. each of grid x grid frames (spread evenly over the puff's life, rising, swelling, thinning) rendered with Cycles
#      from the side, orthographic, on a transparent film: lit from above and to one side, so the billows have shape;
#   4. laid out left to right, top to bottom (Unity's texture-sheet order), grey shading in the colour, alpha the
#      coverage faded out over the outer eighth of each frame (no seams on a sprite).
import argparse, os, shutil, sys, tempfile

import bpy
import numpy as np


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="the flipbook PNG")
    p.add_argument("--grid", type=int, default=4, help="frames across and down")
    p.add_argument("--size", type=int, default=256, help="pixels per frame")
    p.add_argument("--res", type=int, default=96, help="the solver's cells across the domain")
    p.add_argument("--frames", type=int, default=72, help="frames simulated")
    p.add_argument("--swirl", type=float, default=1.5, help="the vortex's strength: how much it rolls round")
    p.add_argument("--samples", type=int, default=64, help="Cycles samples per frame (denoised)")
    p.add_argument("--seed", type=int, default=3, help="the turbulence's seed (a different puff)")
    return p.parse_args(argv)


def build(a, cache):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, a.frames
    # the domain: 3 m across, 3.4 m tall; the puff grows to fill most of it
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.7))
    dom = bpy.context.object
    dom.name = "Domain"
    dom.scale = (3, 3, 3.4)
    bpy.ops.object.modifier_add(type="FLUID")
    ds = dom.modifiers["Fluid"]
    ds.fluid_type = "DOMAIN"
    s = ds.domain_settings
    s.domain_type = "GAS"
    s.resolution_max = a.res
    s.cache_directory = cache
    s.cache_type = "ALL"
    s.cache_frame_start, s.cache_frame_end = 1, a.frames
    s.use_dissolve_smoke = True
    s.dissolve_speed = int(a.frames * 0.9)
    s.vorticity = 0.7
    s.alpha = 0.7          # density buoyancy: rises, a little
    s.beta = 0.6           # and heat
    # the burst: a small sphere near the floor, smoke for the first frames only, thrown up and out
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 1.0))
    em = bpy.context.object
    em.name = "Emitter"
    bpy.ops.object.modifier_add(type="FLUID")
    fs = em.modifiers["Fluid"]
    fs.fluid_type = "FLOW"
    f = fs.flow_settings
    f.flow_type = "SMOKE"
    f.flow_behavior = "INFLOW"
    f.smoke_color = (1, 1, 1)
    f.density = 1.0
    f.use_initial_velocity = True
    f.velocity_coord = (0.0, 0.0, 1.2)
    f.velocity_normal = 1.6
    f.temperature = 1.5
    # emit only at the start: keyframe the flow off after a burst
    f.use_plane_init = False
    fs.flow_settings.subframes = 1
    em.modifiers["Fluid"].flow_settings.flow_behavior = "INFLOW"
    f.keyframe_insert(data_path="density", frame=1)
    f.density = 1.0
    f.keyframe_insert(data_path="density", frame=int(a.frames * 0.18))
    f.density = 0.0
    f.keyframe_insert(data_path="density", frame=int(a.frames * 0.22))
    em.hide_render = True
    # stirred: turbulence for the billows, a vortex so it rolls
    bpy.ops.object.effector_add(type="TURBULENCE", location=(0, 0, 1.5))
    t = bpy.context.object
    t.field.strength = 6.0
    t.field.size = 0.45
    t.field.noise = 0.3
    t.field.seed = a.seed
    bpy.ops.object.effector_add(type="VORTEX", location=(0, 0, 0))
    v = bpy.context.object
    v.field.strength = a.swirl
    v.field.falloff_type = "TUBE"
    # the volume: white smoke whose density is its alpha
    mat = bpy.data.materials.new("Smoke")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Color"].default_value = (1, 1, 1, 1)
    vol.inputs["Density"].default_value = 6.0
    links.new(vol.outputs["Volume"], out.inputs["Volume"])
    dom.data.materials.append(mat)
    return sc, dom


def render(a, sc):
    sc.render.engine = "CYCLES"
    sc.cycles.samples = a.samples
    sc.cycles.device = "CPU"
    sc.cycles.use_denoising = True          # a volume at a few dozen samples is grainy; the denoiser smooths it
    sc.cycles.volume_step_rate = 0.5
    sc.render.film_transparent = True
    sc.render.resolution_x = sc.render.resolution_y = a.size
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    # the sun from above and one side; a dim sky so the shadowed side is not black
    bpy.ops.object.light_add(type="SUN", location=(3, -3, 8))
    sun = bpy.context.object
    sun.data.energy = 3.5
    sun.rotation_euler = (0.7, 0.3, 0.6)
    world = bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.5, 0.5, 0.55, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.5
    cam.location = (0, -12, 1.45)
    cam.rotation_euler = (1.5708, 0, 0)


def main():
    a = args()
    cache = tempfile.mkdtemp(prefix="smoke_flipbook_")
    try:
        sc, dom = build(a, cache)
        bpy.context.view_layer.objects.active = dom
        bpy.ops.fluid.bake_all()
        render(a, sc)
        n = a.grid * a.grid
        # from once the burst has lost the emitter's round shape, to when it has nearly thinned away
        first, last = int(a.frames * 0.22), int(a.frames * 0.95)
        frames = [round(first + (last - first) * i / (n - 1)) for i in range(n)]
        sheet = np.zeros((a.size * a.grid, a.size * a.grid, 4), dtype=np.float32)
        shot = os.path.join(cache, "frame.png")
        for i, fr in enumerate(frames):
            sc.frame_set(fr)
            sc.render.filepath = shot
            bpy.ops.render.render(write_still=True)
            img = bpy.data.images.load(shot)
            px = np.array(img.pixels[:], dtype=np.float32).reshape(a.size, a.size, 4)   # bottom row first
            bpy.data.images.remove(img)
            # the shading in the colour, as grey; the coverage in alpha (premultiplied off)
            alpha = px[..., 3:4]
            # faded out towards the frame's edges, so a puff that grew to touch them shows no hard seam on a sprite
            ramp = np.clip(np.minimum(np.arange(a.size), np.arange(a.size)[::-1]) / (a.size * 0.12), 0.0, 1.0)
            ramp = ramp * ramp * (3.0 - 2.0 * ramp)
            alpha = alpha * (ramp[:, None] * ramp[None, :])[..., None]
            grey = np.where(alpha > 1e-4, px[..., :3] / np.maximum(alpha, 1e-4), 0.0).mean(axis=2, keepdims=True)
            tile = np.concatenate([np.repeat(np.clip(grey, 0, 1), 3, axis=2), alpha], axis=2)
            row, col = divmod(i, a.grid)
            # Unity's sheet runs from the top left; Blender's pixels from the bottom: row 0 is the top band
            y0 = (a.grid - 1 - row) * a.size
            sheet[y0:y0 + a.size, col * a.size:(col + 1) * a.size] = tile
            print("SMOKE_FRAME", i, "of", n, "frame", fr, "coverage %.3f" % float(alpha.mean()))
        out = bpy.data.images.new("Flipbook", a.size * a.grid, a.size * a.grid, alpha=True)
        out.pixels = sheet.ravel()
        out.filepath_raw = os.path.abspath(a.out)
        out.file_format = "PNG"
        out.save()
        print("SMOKE_FLIPBOOK", os.path.abspath(a.out), "%d x %d frames of %d px" % (a.grid, a.grid, a.size))
    finally:
        shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    main()

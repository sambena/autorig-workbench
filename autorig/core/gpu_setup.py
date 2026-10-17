# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: rendering and baking on the GPU when there is one. (Not named gpu.py: that is Blender's own module.)
#
#   python autorig/cli/run.py gpu          which GPU Blender draws with (Workbench/EEVEE) and computes on (Cycles)
#
# Workbench renders (QA shots, facing views, audits) go through OpenGL. Headless, Blender opens it with EGL, which
# picks the NVIDIA driver when one is installed and Mesa's software renderer otherwise: nothing to switch.
# Cycles (bakes) runs on the CPU unless told otherwise, and --factory-startup forgets any preference, so a step
# that renders or bakes with Cycles calls use_cycles_gpu(scene) first. AUTORIG_GPU=0 keeps everything on the CPU.
import os

# best first: OptiX (RTX and GTX 16xx with a recent driver), then CUDA, then the other vendors
BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def wanted():
    return os.environ.get("AUTORIG_GPU", "1").strip().lower() not in ("0", "no", "off", "false", "cpu")


def use_cycles_gpu(scene=None):
    """Point Cycles at the best GPU backend there is; returns that backend's name, or "CPU". Call it inside Blender
    before a Cycles render or bake. Safe to call when there is no GPU or no Cycles."""
    import bpy
    scene = scene or bpy.context.scene
    if not wanted():
        scene.cycles.device = "CPU"
        return "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in BACKENDS:
        try:
            prefs.compute_device_type = backend
        except TypeError:                     # this build has no such backend
            continue
        prefs.refresh_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if not gpus:
            continue
        for d in prefs.devices:
            d.use = d.type == backend
        scene.cycles.device = "GPU"
        return backend
    prefs.compute_device_type = "NONE"
    scene.cycles.device = "CPU"
    return "CPU"


def report():
    """Inside Blender: the OpenGL renderer and the Cycles devices, as a dict (run.py gpu prints it)."""
    import bpy
    out = {"opengl": None, "cycles": use_cycles_gpu(), "devices": []}
    try:
        # headless, Blender opens its GPU context on the first render: draw a tiny Workbench frame first
        scene = bpy.context.scene
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = scene.render.resolution_y = 8
        if scene.camera is None:
            cam = bpy.data.objects.new("gpu_report_cam", bpy.data.cameras.new("gpu_report_cam"))
            scene.collection.objects.link(cam)
            scene.camera = cam
        bpy.ops.render.render()
        import gpu as bgpu
        out["opengl"] = "%s / %s" % (bgpu.platform.vendor_get(), bgpu.platform.renderer_get())
    except Exception as e:                     # no GPU module in this build or context
        out["opengl"] = "unknown (%s)" % e
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        out["devices"] = ["%s %s%s" % (d.type, d.name, " (used)" if d.use else "") for d in prefs.devices]
    except KeyError:
        pass
    return out

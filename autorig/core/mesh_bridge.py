# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a Blender mesh in and out of core/mesh_prep.py's arrays, and the Blender-side uses of it: the
# texture-safe decimation that trim, game-prop and the Studio LODs share, and a bake of the old texture onto a new UV
# atlas. Inside a Blender step only (bpy).
#
#   read(obj)                      -> V, F, UV (per corner, active UV map, or None), FD (material per triangle)
#   write(obj, V, F, UV, FD)       replaces obj's geometry, keeping its materials and UV map name
#   decimate(obj, target)          the PyMeshLab collapse on obj, or None when it cannot be used (no extra, vertex
#                                  groups, shape keys): the caller keeps its Blender Decimate then
#   bake_to_atlas(src_objs, dst, size, path)   dst's UV map baked from src's materials (selected to active)
import os, sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import extras
import mesh_prep


def read(obj):
    """The evaluated-free mesh data of obj, triangulated: (V, F, UV, FD, smooth). UV is per triangle corner from the
    active UV map; FD the material index per triangle; smooth whether most faces were shaded smooth."""
    me = obj.data
    me.calc_loop_triangles()
    n, m = len(me.vertices), len(me.loop_triangles)
    V = np.empty(n * 3, dtype=np.float64); me.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
    F = np.empty(m * 3, dtype=np.int64); me.loop_triangles.foreach_get("vertices", F); F = F.reshape(-1, 3)
    loops = np.empty(m * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", loops)
    FD = np.empty(m, dtype=np.int64); me.loop_triangles.foreach_get("material_index", FD)
    UV = None
    layer = me.uv_layers.active
    if layer is not None:
        uv = np.empty(len(me.loops) * 2, dtype=np.float64); layer.data.foreach_get("uv", uv)
        UV = uv.reshape(-1, 2)[loops]
    sm = np.empty(len(me.polygons), dtype=bool); me.polygons.foreach_get("use_smooth", sm)
    return V, F, UV, FD, bool(sm.mean() >= 0.5) if len(sm) else True


def write(obj, V, F, UV=None, FD=None, smooth=True, uv_name=None):
    """Replaces obj's mesh with (V, F, UV, FD), keeping its materials. A new mesh datablock is built and swapped in,
    so custom split normals and attributes that no longer match are dropped with the old one."""
    old = obj.data
    me = bpy.data.meshes.new(old.name)
    V = np.asarray(V, dtype=np.float64); F = np.asarray(F, dtype=np.int64)
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(len(F) * 3); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F))
    me.polygons.foreach_set("loop_start", (np.arange(len(F)) * 3).astype(np.int32))
    for mat in old.materials:
        me.materials.append(mat)
    if FD is not None and len(F):
        fd = np.clip(np.asarray(FD, dtype=np.int32), 0, max(len(old.materials) - 1, 0))
        me.polygons.foreach_set("material_index", fd)
    if UV is not None and len(F):
        name = uv_name or (old.uv_layers.active.name if old.uv_layers.active else "UVMap")
        layer = me.uv_layers.new(name=name)
        layer.data.foreach_set("uv", np.asarray(UV, dtype=np.float32).ravel())
    me.polygons.foreach_set("use_smooth", np.full(len(F), bool(smooth)))
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    obj.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return me


def usable(obj, why=None):
    """Whether the library path can take obj: no vertex groups (a skin) and no shape keys, which the arrays do not
    carry. `why` (a list) collects the reason it cannot."""
    reasons = []
    if obj.vertex_groups:
        reasons.append("vertex groups (a skin)")
    if getattr(obj.data, "shape_keys", None):
        reasons.append("shape keys")
    if why is not None:
        why.extend(reasons)
    return not reasons


def collapse_mode():
    """AUTORIG_COLLAPSE: "blender" never uses the library collapse, "pymeshlab" always does (textured meshes too, even
    unbaked); unset or "auto" follows each caller's textured_ok."""
    return (os.environ.get("AUTORIG_COLLAPSE") or "auto").strip().lower()


def decimate(obj, target, textured_ok=False, log=print, **options):
    """obj cut to `target` triangles with PyMeshLab's quadric collapse (core/mesh_prep.decimate). Returns {"before",
    "after", "method"}, or None when the caller should keep Blender's Decimate: the extra is missing, obj has vertex
    groups or shape keys, or obj is textured and textured_ok is False.

    Why textured_ok: on Tripo models (docs/GAME-TOOLS.md, "Decimation compared") PyMeshLab holds the shape far better
    than Blender's Decimate, but its texture-aware variant still smears the texture at UV seams and across big flat
    areas (a barn roof, a car's grille), since Tripo's atlases are not linear across them. So a textured mesh seen
    close takes it only when its texture is baked afresh afterwards (re-unwrap and bake), and distant LODs, where the
    silhouette is what shows, take it anyway (textured_ok=True)."""
    mode = collapse_mode()
    if mode == "blender":
        return None
    why = []
    if not usable(obj, why):
        log("mesh_bridge: %s kept on Blender's Decimate (%s)" % (obj.name, ", ".join(why)))
        return None
    if not extras.have("pymeshlab"):
        return None
    if obj.data.uv_layers and not textured_ok and mode != "pymeshlab":
        return None
    V, F, UV, FD, smooth = read(obj)
    if len(F) <= target:
        return {"before": len(F), "after": len(F), "method": "pymeshlab"}
    V2, F2, UV2, FD2 = mesh_prep.decimate(V, F, UV, FD, int(target), **options)
    if len(F2) > 1.1 * target + 10:
        # the texture-aware collapse refuses edges on UV seams and flips; on a mesh Blender's Decimate already cut it
        # can stall far above the budget (a barn: 10,000 -> 9,934 for 4,000). Blender's Decimate then, as before.
        log("mesh_bridge: PyMeshLab stopped at %d triangles of %d asked for %s; left to Blender's Decimate" %
            (len(F2), target, obj.name))
        return None
    write(obj, V2, F2, UV2, FD2, smooth)
    log("mesh_bridge: %s %d -> %d triangles with PyMeshLab (%s)" %
        (obj.name, len(F), len(F2), "texture-aware" if UV is not None else "no UVs"))
    return {"before": int(len(F)), "after": int(len(F2)), "method": "pymeshlab"}


def unwrap(obj, resolution=1024, padding=4, uv_name="Atlas"):
    """A fresh xatlas UV map on obj (added as `uv_name` and made active; the old maps stay for the bake to read).
    Returns the new layer, or None without xatlas."""
    if not extras.have("xatlas"):
        return None
    V, F, _, _, _ = read(obj)
    UV = mesh_prep.unwrap(V, F, resolution, padding)
    me = obj.data
    # loop_triangles map onto loops; for a triangulated mesh each triangle is one polygon in order
    me.calc_loop_triangles()
    loops = np.empty(len(me.loop_triangles) * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", loops)
    uv = np.zeros((len(me.loops), 2), dtype=np.float32)
    uv[loops] = UV
    layer = me.uv_layers.get(uv_name) or me.uv_layers.new(name=uv_name)
    layer.data.foreach_set("uv", uv.ravel())
    me.uv_layers.active = layer
    return layer


def _bake_image(name, size, colour):
    img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "sRGB" if colour else "Non-Color"
    return img


def bake_to_atlas(sources, target, size, folder, name, normal=True, margin=None, samples=1):
    """Bakes `sources` (their own materials and UVs: the full-resolution model) onto `target`'s active UV map, selected
    to active, with Cycles on the GPU when there is one (core/gpu_setup.py): base colour (no lighting) and, with normal, a tangent-space normal map that
    carries the full-resolution surface's detail onto the reduced mesh. Saves <folder>/<name>_basecolor.png (and
    _normal.png) and gives target one material using them. Returns {"basecolor": path, "normal": path or None}."""
    scene = bpy.context.scene
    engine = scene.render.engine
    scene.render.engine = "CYCLES"
    import gpu_setup
    device = gpu_setup.use_cycles_gpu(scene)        # OptiX/CUDA when there is a GPU (AUTORIG_GPU=0: the CPU)
    scene.cycles.samples = samples
    os.makedirs(folder, exist_ok=True)
    mat = bpy.data.materials.new(name + "_atlas")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.75
    target.data.materials.clear()
    target.data.materials.append(mat)
    bpy.ops.object.select_all(action="DESELECT")
    for s in sources:
        s.hide_render = False
        s.hide_set(False)
        s.select_set(True)
    target.hide_render = False
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    dims = max(target.dimensions) or 1.0
    bake = scene.render.bake
    bake.use_selected_to_active = True
    bake.cage_extrusion = dims * 0.01
    bake.max_ray_distance = dims * 0.03
    # a wide margin: the LODs collapsed from this mesh drag a few UVs over a chart's edge, into the gutter
    bake.margin = margin if margin is not None else max(8, size // 64)
    out = {"basecolor": None, "normal": None, "device": device}
    for kind in ("DIFFUSE", "NORMAL") if normal else ("DIFFUSE",):
        suffix = "basecolor" if kind == "DIFFUSE" else "normal"
        img = _bake_image("%s_%s" % (name, suffix), size, kind == "DIFFUSE")
        tex = nodes.new("ShaderNodeTexImage"); tex.image = img
        if kind == "NORMAL":
            tex.interpolation = "Linear"
        for n in nodes: n.select = False
        tex.select = True
        nodes.active = tex
        if kind == "DIFFUSE":
            bake.use_pass_direct = False; bake.use_pass_indirect = False; bake.use_pass_color = True
            bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=True)
        else:
            bake.normal_space = "TANGENT"
            bpy.ops.object.bake(type="NORMAL", use_selected_to_active=True)
        path = os.path.join(folder, "%s_%s.png" % (name, suffix))
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        if kind == "DIFFUSE":
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        else:
            nm = nodes.new("ShaderNodeNormalMap")
            links.new(tex.outputs["Color"], nm.inputs["Color"]); links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
        out[suffix] = path
    scene.render.engine = engine
    return out


def bake_atlas(obj, size, folder, name, normal=True, decimate_to=None):
    """The whole re-texture pass on obj, in place: a full-resolution copy kept aside as the bake source, obj cut to
    decimate_to triangles (PyMeshLab, textured_ok: the bake replaces its texture), a fresh xatlas UV map, and the
    source baked onto it (bake_to_atlas). Several materials become one atlas. Returns the report, or None without the
    xatlas extra (obj untouched)."""
    if not extras.have("xatlas"):
        return None
    src = obj.copy(); src.data = obj.data.copy(); src.name = obj.name + "_bake_source"
    bpy.context.scene.collection.objects.link(src)
    report = {"triangles_before": None, "triangles": None}
    obj.data.calc_loop_triangles(); report["triangles_before"] = len(obj.data.loop_triangles)
    if decimate_to:
        if decimate(obj, decimate_to, textured_ok=True) is None:
            mod = obj.modifiers.new("Decimate", "DECIMATE")
            mod.ratio = decimate_to / float(max(report["triangles_before"], 1))
            mod.use_collapse_triangulate = True
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)
    for layer in list(obj.data.uv_layers):
        obj.data.uv_layers.remove(layer)
    unwrap(obj, size, padding=max(2, size // 256))
    report.update(bake_to_atlas([src], obj, size, folder, name, normal=normal))
    data = src.data
    bpy.data.objects.remove(src, do_unlink=True)
    if data.users == 0:
        bpy.data.meshes.remove(data)
    obj.data.calc_loop_triangles(); report["triangles"] = len(obj.data.loop_triangles)
    return report

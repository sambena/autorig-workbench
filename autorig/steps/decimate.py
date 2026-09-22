# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: cuts each rigged model down to its engine budget and rewrites the FBX.
#
# Generators return about the same triangle count for everything regardless of how big the thing is on screen,
# and a game may draw a lot of them at once. The .blend beside it keeps the full-resolution mesh to animate
# against; only the FBX the engine loads is reduced.
#
# Decimating after rigging rather than before is deliberate: COLLAPSE interpolates vertex
# groups, so the skin survives, and the rig was built against the shape the artist made
# rather than against an already-simplified one.
#
#   blender -b --python autorig/steps/decimate.py -- [<group>]       every model in a group (or at the root)
#   blender -b --python autorig/steps/decimate.py -- -only wolf,moth

import bpy, sys, os, json, math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from layout import ROOT, rigged_dir, leaf, source_fbx, budget, models_in, group_of
import source_io

# Budgets: rig.json "budget", then the collection's autorig.json ("full_resolution_groups", "budget"), read through
# layout.budget().


def tri_count(mesh):
    mesh.data.calc_loop_triangles()
    return len(mesh.data.loop_triangles)


def reduce(mesh, target, planar=True):
    """Flat panels dissolved first, then a collapse to budget.

    A collapse on its own spends the budget evenly, so a model made of long flat boxes loses its thin parts first:
    a table's legs and rails, a machine's plates, all came out as spikes. Dissolving what is already flat (under a
    degree, never across a UV seam) costs no shape and leaves the collapse the triangles that carry some. On an
    organic sculpt almost nothing is that flat, so it changes little there.

    Not on a rigged model: the dissolve turns a flat plate into a few long triangles, and a long edge across a joint
    tears when the joint bends (the Dirt Creator's thorax plate reached 8x at a 40-degree head bend). A bending mesh
    needs its vertices where it bends, so a skinned model gets the collapse alone."""
    bpy.context.view_layer.objects.active = mesh
    for o in bpy.context.selected_objects: o.select_set(False)
    mesh.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=1e-5)
    bpy.ops.object.mode_set(mode='OBJECT')
    for kind, setup in (() if not planar else (('DECIMATE', lambda m: (setattr(m, "decimate_type", 'DISSOLVE'),
                                                 setattr(m, "angle_limit", math.radians(1.0)),
                                                 setattr(m, "delimit", {'UV'}))),
                        ('TRIANGULATE', lambda m: None))):
        mod = mesh.modifiers.new("step", kind); setup(mod)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    now = tri_count(mesh)
    if now > target:
        mod = mesh.modifiers.new("Collapse", 'DECIMATE')
        mod.decimate_type = 'COLLAPSE'
        mod.use_collapse_triangulate = True
        mod.ratio = target / float(now)
        bpy.ops.object.modifier_apply(modifier=mod.name)


def limit_influences(mesh, most=4):
    """Rule E (docs/PIPELINE.md): at most four bones per vertex, normalised, after the cut.

    rerig.py limits to four, but a collapse interpolates the groups of the vertices it merges, and the shipped files
    came back with up to 13 where engines take four and drop the rest unrenormalised or with a pop."""
    if not mesh.vertex_groups: return 0
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.vertex_group_clean(group_select_mode='ALL', limit=0.001)
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=most)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    return max((sum(1 for g in v.groups if g.weight > 0) for v in mesh.data.vertices), default=0)


def run(key):
    # A rigged model decimates from its .blend, which holds the rig and the full-resolution
    # mesh. A static prop - a table, a crate - is never rigged, so there is no .blend
    # and the source to cut down is its own export. Both still owe the engine an
    # `<model>.fbx` at budget; skipping props entirely would leave them unusable.
    path = os.path.join(rigged_dir(key), leaf(key) + ".blend")
    rigged = os.path.exists(path)
    # A group listed in full_resolution_groups ships its rigged FBX at full resolution (rerig.py's export) and each
    # consumer cuts its own copy; only a model with a budget of its own is cut here.
    target = budget(key)
    if target is None:
        return {"model": key, "skipped": "kept at full resolution (no budget for %s)" % (group_of(key) or "the root")}

    if rigged:
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        source = source_fbx(key)
        if source is None:
            return {"model": key, "error": "no rigged blend and no source fbx"}
        bpy.ops.wm.read_factory_settings(use_empty=True)
        source_io.import_source(source)
        os.makedirs(rigged_dir(key), exist_ok=True)
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    if not meshes:
        return {"model": key, "error": "no mesh"}

    mesh = meshes[0]
    before = tri_count(mesh)

    if before > target:
        reduce(mesh, target, planar=not arms)

    after = tri_count(mesh)
    influences = limit_influences(mesh)

    # Vertex groups whose weights all collapsed away leave a bone driving nothing.
    groups = {g.name for g in mesh.vertex_groups}
    live = set()
    for v in mesh.data.vertices:
        for ge in v.groups:
            if ge.weight > 0:
                live.add(mesh.vertex_groups[ge.group].name)
    lost = sorted(groups - live)

    out = os.path.join(rigged_dir(key), leaf(key) + ".fbx")
    for o in bpy.data.objects:
        o.select_set(o.type in ('MESH', 'ARMATURE'))
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.export_scene.fbx(filepath=out, use_selection=True, object_types={'ARMATURE', 'MESH'},
                             apply_scale_options='FBX_SCALE_UNITS', bake_space_transform=True,
                             axis_forward='-Z', axis_up='Y', add_leaf_bones=False,
                             use_armature_deform_only=True, bake_anim=False,
                             mesh_smooth_type='OFF', path_mode='AUTO', embed_textures=False)

    return {"model": key, "before": before, "after": after, "target": target,
            "static": not rigged,
            "bones": len(arms[0].data.bones) if arms else 0,
            "bones_left_empty": lost, "max_influences": influences,
            "fbx": os.path.relpath(out, ROOT)}


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "-only" in a:
        keys = a[a.index("-only") + 1].split(",")
    else:
        group = a[0] if a and not a[0].startswith("-") else None
        # Anything with a source export, not just what has already been rigged: props
        # never get a rigged/ folder and would otherwise never be cut down.
        keys = [k for k in models_in(group) if source_fbx(k)]

    for k in keys:
        try:
            print("DECIMATE " + json.dumps(run(k)))
        except Exception as e:
            print("DECIMATE " + json.dumps({"model": k, "error": repr(e)}))
    print("DECIMATE_DONE %d" % len(keys))


if __name__ == "__main__":
    main()

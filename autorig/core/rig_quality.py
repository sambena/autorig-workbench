# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender deformation probes, preserving all pose and constraint state."""
import contextlib
import numpy as np
from deformation import metrics, seam_pairs


def coords(obj):
    import bpy
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh = ev.to_mesh()
    try:
        p = np.empty(len(mesh.vertices)*3); mesh.vertices.foreach_get("co", p)
        m = np.asarray(ev.matrix_world)
        return p.reshape(-1,3) @ m[:3,:3].T + m[:3,3]
    finally: ev.to_mesh_clear()


@contextlib.contextmanager
def probe_pose(arm):
    import bpy
    saved = [(b, b.matrix_basis.copy(), b.rotation_mode) for b in arm.pose.bones]
    constraints = [(c, c.mute) for b in arm.pose.bones for c in b.constraints]
    old_position = arm.data.pose_position
    arm.data.pose_position = "POSE"
    try:
        for c, _ in constraints: c.mute = True
        for b, _, _ in saved: b.matrix_basis.identity(); b.rotation_mode = "XYZ"
        bpy.context.view_layer.update()
        yield
    finally:
        for b, matrix, mode in saved: b.rotation_mode = mode; b.matrix_basis = matrix
        for c, mute in constraints: c.mute = mute
        arm.data.pose_position = old_position
        bpy.context.view_layer.update()


def sample_skin(mesh, arm, limit=12):
    import bpy
    import math
    with probe_pose(arm):
        rest = coords(mesh)
        edges = np.array([e.vertices[:] for e in mesh.data.edges], dtype=int).reshape(-1,2)
        mesh.data.calc_loop_triangles()
        triangles = np.array([t.vertices[:] for t in mesh.data.loop_triangles], dtype=int).reshape(-1,3)
        bones = [b for b in arm.pose.bones if b.bone.use_deform and b.parent]
        cut = mesh.data.attributes.get('_AUTORIG_CUT')
        ignored = np.array([v.value > .5 for v in cut.data]) if cut else None
        pairs = seam_pairs(rest, ignored=ignored)
        # Long bones first, with a deterministic spread over the rest of the skeleton.
        bones.sort(key=lambda b:(-b.length,b.name))
        selected = bones[:limit//2] + bones[limit//2::max(1,len(bones)//max(1,limit//2))][:limit-limit//2]
        results = []
        for b in selected:
            for axis, angle in ((0,40),(1,60)):
                b.rotation_euler[axis] = math.radians(angle); bpy.context.view_layer.update()
                results.append(metrics(rest, coords(mesh), edges, triangles, pairs))
                b.rotation_euler[axis] = 0
        return results


def protect_joints(mesh):
    """Protect mixed influences and vertices near deform-bone endpoints during collapse."""
    import bpy
    arm = mesh.find_armature()
    if not arm: return None
    group = mesh.vertex_groups.new(name="_studio_joint_protection")
    deform = {b.name for b in arm.data.bones if b.use_deform}
    names = {g.index:g.name for g in mesh.vertex_groups}
    inv = mesh.matrix_world.inverted() @ arm.matrix_world
    joints = [(inv @ b.head_local, max(b.length*.22,1e-6)) for b in arm.data.bones if b.use_deform and b.parent]
    buckets = {}
    for v in mesh.data.vertices:
        weights = [g.weight for g in v.groups if names.get(g.group) in deform and g.weight > .01]
        mixed = min(1.0, 4*(1-max(weights))) if weights else 0
        proximity = max((max(0,1-(v.co-p).length/r) for p,r in joints), default=0)
        value = round(max(mixed,proximity),2)
        if value: buckets.setdefault(value,[]).append(v.index)
    for w, ids in buckets.items(): group.add(ids,w,'REPLACE')
    return group

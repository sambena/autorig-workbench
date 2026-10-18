# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's skinning (core/m2m_skin.py, ported from Mesh2Motion by Scott Petrovic and
# contributors, MIT, https://github.com/Mesh2Motion/mesh2motion-app) applied to a Blender mesh and armature, as vertex
# groups and an Armature modifier, so steps/audit.py grades it exactly as it grades our own skinning. Inside a Blender
# step only (bpy).
#
#   unreached, bare, off = skin(mesh, arm, template, head_height=None, arm_offset=None)
#       the same return as m2m_rig.skin() (unreached is always 0: there is no voxel proxy), so the M2 step's
#       `--skin m2m` can call this in its place:
#           if a.skin == "m2m": unreached, bare, off = m2m_skin_blender.skin(mesh, arm, template)
#           else:               unreached, bare, off = skin(mesh, arm, voxel, after)
#       template is the Mesh2Motion template the skeleton was fitted from (human, fox, horse, ...; m2m_skin.TEMPLATES).
#       head_height / arm_offset turn on M2M's head and arm planes (metres, M2M's axes: height above the origin, and
#       offset outboard of the shoulder joint); both off by default, as in M2M.
#   weights(mesh, arm, template, ...) -> (names, idx, w)   the solve alone, nothing written
# The mesh's existing vertex groups are replaced. The root and the leaf/tip bones (which M2M never weights) are set not
# to deform, as m2m_rig.skin() does, and returned in `off`.
import os, sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import m2m_skin


def _to_gltf(P):
    """Blender's Z-up world to Mesh2Motion's Y-up: (x, y, z) -> (x, z, -y)."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    return np.stack([P[:, 0], P[:, 2], -P[:, 1]], axis=1)


def rig_of(arm):
    """m2m_skin.Rig from the armature's bones, each at its world head."""
    mw = arm.matrix_world
    bones = [(b.name, tuple(mw @ b.head_local), b.parent.name if b.parent else None) for b in arm.data.bones]
    heads = _to_gltf([b[1] for b in bones])
    return m2m_skin.Rig([(n, tuple(h), p) for (n, _, p), h in zip(bones, heads)])


def mesh_arrays(mesh):
    """The mesh's world vertices in M2M's axes and its triangles (vertex indices)."""
    me = mesh.data
    me.calc_loop_triangles()
    n, m = len(me.vertices), len(me.loop_triangles)
    co = np.empty(n * 3, dtype=np.float64); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    mw = np.array(mesh.matrix_world, dtype=np.float64)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    F = np.empty(m * 3, dtype=np.int64); me.loop_triangles.foreach_get("vertices", F)
    return _to_gltf(co), F.reshape(-1, 3)


def weights(mesh, arm, template, head_height=None, arm_offset=None):
    rig = rig_of(arm)
    V, F = mesh_arrays(mesh)
    idx, w = m2m_skin.solve(rig, V, F, template, head_height, arm_offset)
    return rig.names, idx, w


def skin(mesh, arm, template, head_height=None, arm_offset=None):
    names, idx, w = weights(mesh, arm, template, head_height, arm_offset)
    off = []
    for b in arm.data.bones:
        skipped = b.name == 'root' or (not b.children and ('leaf' in b.name.lower() or 'tip' in b.name.lower()))
        if skipped:
            if b.use_deform: off.append(b.name)
            b.use_deform = False
    for vg in list(mesh.vertex_groups): mesh.vertex_groups.remove(vg)
    # gather each bone's vertices by weight, so each vertex group gets a few add() calls rather than one per vertex
    groups = {}
    for slot in range(4):
        live = w[:, slot] > 0
        for v, b, x in zip(np.nonzero(live)[0].tolist(), idx[live, slot].tolist(), w[live, slot].tolist()):
            groups.setdefault(b, {}).setdefault(v, 0.0)
            groups[b][v] += x
    for b, members in groups.items():
        vg = mesh.vertex_groups.new(name=names[b])
        by_weight = {}
        for v, x in members.items(): by_weight.setdefault(x, []).append(v)
        for x, vs in by_weight.items(): vg.add(vs, x, 'REPLACE')
    mw = mesh.matrix_world.copy()
    mesh.parent = arm
    mesh.matrix_world = mw
    mod = next((m for m in mesh.modifiers if m.type == 'ARMATURE'), None) or mesh.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm
    bare = int(np.count_nonzero(~np.any(w > 0, axis=1)))
    return 0, bare, off

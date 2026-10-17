# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: passes over baked clips, inside Blender (make_clips.py runs them after it keys a clip).
#
#   anti_slide   planted feet stay planted: contacts found on the evaluated feet, pinned (core/foot_contact.py, after
#                MoMask's remove_fs.py and GenMM's fix_contact.py), and each leg re-solved onto its pin with FABRIK
#                (core/limb_ik.py). The leg bones' rotation keys are rewritten; nothing else is touched.
#   place_feet   the same re-solve onto targets the caller gives (leg_planting.py's stepping feet).
#
# Both work on any keyed action (an authored clip, a GenMM variant), so they run after the clip is made rather than
# inside each clip's pose function. Legs driven by a live IK constraint are left to their IK.
import bpy
from mathutils import Matrix, Quaternion, Vector

import foot_contact
import limb_ik


def foot_point(arm, mesh, chain):
    """Where a leg meets the ground, in its last bone's rest space: the lowest tenth (at rest) of the vertices skinned
    mostly to that bone, or to the nearest bone up the leg that has any (clip-check measures the same point). A
    placed rig's bones need not run down the middle of the mesh's legs (the beetle's splay out while its legs hang
    straight down), so a bone's tail can be well off the foot. None without a mesh or skinned vertices."""
    if mesh is None: return None
    groups = {g.index: g.name for g in mesh.vertex_groups}
    to_arm = arm.matrix_world.inverted() @ mesh.matrix_world
    for bone in reversed(chain):
        pts = [to_arm @ v.co for v in mesh.data.vertices
               if v.groups and groups.get(max(v.groups, key=lambda g: g.weight).group) == bone]
        if pts:
            pts.sort(key=lambda p: p.z)
            low = pts[:max(3, len(pts) // 10)]
            centre = sum(low, Vector()) / len(low)
            return arm.data.bones[chain[-1]].matrix_local.inverted() @ centre
    return None


def leg_chains(arm, skeleton=None, humanoid=None, mesh=None):
    """The legs a pass can re-solve: [{name, bones (hip first), keep (bones whose orientation is held: a humanoid's
    foot), foot (where it meets the ground, in the last bone's rest space; None: its tail)}]. From the card's skeleton
    legs (rig pipeline rigs), else a Mixamo humanoid's thigh and shin."""
    bones = arm.data.bones
    out = []
    for leg in (skeleton or {}).get("legs", []):
        chain = [b for b in leg.get("bones", []) if b in bones]
        if not chain or (leg.get("ik") or "") in bones: continue
        if any(bones[chain[i + 1]].parent is None or bones[chain[i + 1]].parent.name != chain[i]
               for i in range(len(chain) - 1)): continue
        if any(c.type == 'IK' and not c.mute for b in chain for c in arm.pose.bones[b].constraints): continue
        out.append({"name": leg.get("name") or chain[0], "bones": chain, "keep": [], "foot": foot_point(arm, mesh, chain)})
    if not out and humanoid:
        for s in "LR":
            th, sh, ft = humanoid.get("thigh." + s), humanoid.get("shin." + s), humanoid.get("foot." + s)
            if th and sh and bones[sh].parent and bones[sh].parent.name == th:
                out.append({"name": "leg." + s, "bones": [th, sh], "keep": [ft] if ft else [], "foot": None})
    return out


def rest_floor(arm, chains):
    """The floor: the lowest foot at rest (armature space). A clip's own lowest feet are not it: a flier hovers."""
    return min(rest_foot(arm, c).z for c in chains)


def rest_foot(arm, chain):
    """A chain's foot at rest, armature space."""
    b = arm.data.bones[chain["bones"][-1]]
    return b.matrix_local @ chain["foot"] if chain.get("foot") is not None else b.tail_local.copy()


def action_curves(action):
    """{(bone, property): {index: F-curve}} for an action's pose-bone channels (slotted actions keep them in
    channelbags, older ones on the action)."""
    curves = []
    if hasattr(action, "layers") and len(getattr(action, "layers", [])):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags: curves += list(bag.fcurves)
    else:
        curves = list(action.fcurves)
    out = {}
    for fc in curves:
        if not fc.data_path.startswith('pose.bones["'): continue
        bone, prop = fc.data_path[len('pose.bones["'):].split('"].', 1)
        out.setdefault((bpy.utils.unescape_identifier(bone), prop), {})[fc.array_index] = fc
    return out


class Posed:
    """Poses the armature from an action's curves, frame by frame, with nothing else animating it. Scene frame
    changes do not re-run an action whose keys were just rewritten (the depsgraph keeps its copy), so the passes
    set every keyed channel themselves."""

    def __init__(self, arm, action):
        self.arm, self.curves = arm, action_curves(action)

    def __enter__(self):
        ad = self.arm.animation_data or self.arm.animation_data_create()
        self.saved = (ad.action, ad.use_nla)
        ad.action = None; ad.use_nla = False
        return self

    def __exit__(self, *exc):
        ad = self.arm.animation_data
        ad.action, ad.use_nla = self.saved

    def at(self, f):
        pbs = self.arm.pose.bones
        for (bone, prop), by in self.curves.items():
            pb = pbs.get(bone)
            if pb is None or not hasattr(pb, prop): continue
            if prop == "rotation_quaternion": pb.rotation_mode = 'QUATERNION'
            value = list(getattr(pb, prop))
            for i, fc in by.items():
                if i < len(value): value[i] = fc.evaluate(f)
            setattr(pb, prop, value)
        bpy.context.view_layer.update()


def _joints(arm, chain):
    pb = arm.pose.bones
    pts = [tuple(pb[b].head) for b in chain["bones"]]
    last = pb[chain["bones"][-1]]
    pts.append(tuple(last.matrix @ chain["foot"]) if chain.get("foot") is not None else tuple(last.tail))
    return pts


def sample(arm, action, last, chains):
    """Every chain's joints (armature space) at frames 0..last of the action."""
    with Posed(arm, action) as posed:
        out = []
        for f in range(last + 1):
            posed.at(f)
            out.append({c["name"]: _joints(arm, c) for c in chains})
    return out


FORWARD = Vector((0.0, -1.0, 0.0))   # the rig convention: a straight leg's knee bends forward when re-solved
MAX_TURN = 0.6     # radians (about 35 degrees): the most a re-solve turns one bone from its authored pose; a foot the
                   # leg cannot reach without more is left a little short. A rig with a weak joint can still tear
                   # inside that (the beetle's hip): the mesh check below keeps the authored clip then.


STEP_TURN = 1.2    # radians: a stepping leg (place_feet from leg_planting) swings much further than a fix needs


def _solve_frame(arm, chains, joints, targets, tolerance, max_turn=MAX_TURN):
    """Re-solves the chains at the current frame onto their targets. Returns {bone: quaternion} for the bones turned."""
    pbs = arm.pose.bones
    layer = bpy.context.view_layer
    turned = {}
    for c in chains:
        tgt = targets.get(c["name"])
        if tgt is None: continue
        cur = joints[c["name"]]
        if (Vector(cur[-1]) - Vector(tgt)).length < tolerance: continue
        held = {b: pbs[b].matrix.copy() for b in c["keep"]}
        new = limb_ik.swing_then_fabrik(cur, tgt, pole=tuple(FORWARD))
        for i, bone in enumerate(c["bones"]):
            pb = pbs[bone]
            m = pb.matrix.copy()
            head = m.to_translation()
            nxt = pbs[c["bones"][i + 1]].head if i + 1 < len(c["bones"]) else                 (pb.matrix @ c["foot"] if c.get("foot") is not None else pb.tail)
            old_dir = Vector(nxt) - head                   # to the next joint, as FABRIK measured the bone
            new_dir = Vector(new[i + 1]) - head
            if old_dir.length < 1e-9 or new_dir.length < 1e-9: continue
            r = old_dir.rotation_difference(new_dir)
            if r.angle > max_turn: r = Quaternion().slerp(r, max_turn / r.angle)
            pb.matrix = Matrix.Translation(head) @ (r.to_matrix() @ m.to_3x3()).to_4x4()
            layer.update()
            turned[bone] = pb.rotation_quaternion.copy()
        for b, m in held.items():
            pb = pbs[b]
            pb.matrix = Matrix.Translation(pb.matrix.to_translation()) @ m.to_3x3().to_4x4()
            layer.update()
            turned[b] = pb.rotation_quaternion.copy()
    return turned


def place_feet(arm, action, last, loops, chains, targets, size, max_turn=MAX_TURN):
    """Re-solves the legs so each chain's foot (its last bone's tail) is on targets[frame][chain]. Rewrites the leg
    bones' rotation keys. Returns the largest move of a foot it made (armature units)."""
    if not chains: return 0.0, ({}, {})
    tol = 1e-4 * size
    rows, moved = {}, 0.0
    with Posed(arm, action) as posed:
        for f in range(last + 1):
            src = 0 if (loops and f == last) else f      # a loop's last key is its first
            posed.at(src)
            joints = {c["name"]: _joints(arm, c) for c in chains}
            tg = targets[src]
            for c in chains:
                if c["name"] in tg:
                    moved = max(moved, (Vector(joints[c["name"]][-1]) - Vector(tg[c["name"]])).length)
            for bone, q in _solve_frame(arm, chains, joints, tg, tol, max_turn).items():
                rows.setdefault(bone, {})[f] = q
    original = {}
    for bone, by_frame in rows.items():
        curves = posed.curves.get((bone, "rotation_quaternion"), {})
        if len(curves) == 4:
            original[bone] = [[tuple(kp.co) + tuple(kp.handle_left) + tuple(kp.handle_right)
                               for kp in curves[i].keyframe_points] for i in range(4)]
        if len(curves) < 4: continue
        pts = [curves[i].keyframe_points for i in range(4)]
        prev = None
        for f in range(last + 1):
            k = next((j for j, kp in enumerate(pts[0]) if abs(kp.co[0] - f) < 1e-3), None)
            if k is None: continue
            q = by_frame.get(f)
            if q is None:
                prev = [pts[i][k].co[1] for i in range(4)]
                continue
            q = list(q)
            if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0: q = [-v for v in q]
            for i in range(4):
                kp = pts[i][k]
                d = q[i] - kp.co[1]
                kp.co[1] = q[i]; kp.handle_left[1] += d; kp.handle_right[1] += d
            prev = q
        for i in range(4): curves[i].update()
    return moved, (posed.curves, original)


def restore(saved):
    """Puts back the keys place_feet rewrote."""
    curves, original = saved
    for bone, rows in original.items():
        by = curves[(bone, "rotation_quaternion")]
        for i in range(4):
            for kp, v in zip(by[i].keyframe_points, rows[i]):
                kp.co = v[0:2]; kp.handle_left = v[2:4]; kp.handle_right = v[4:6]
            by[i].update()


def mesh_strain(arm, action, last, mesh):
    """The worst a clip strains its mesh: (edges torn, largest stretch), over frames 0..last (deformation.metrics)."""
    import numpy as np
    from deformation import metrics
    from rig_quality import coords
    with Posed(arm, action) as posed:
        arm.data.pose_position = 'REST'; bpy.context.view_layer.update()
        rest = coords(mesh)
        arm.data.pose_position = 'POSE'
        edges = [tuple(e.vertices) for e in mesh.data.edges]
        torn, stretch = 0, 1.0
        for f in range(last + 1):
            posed.at(f)
            m = metrics(rest, coords(mesh), edges, scale=float(np.ptp(rest, axis=0).max()))
            torn, stretch = max(torn, m["tear_edges"]), max(stretch, m["max_stretch"])
    return torn, stretch


def anti_slide(arm, action, last, loops, chains, size, velocity=None, fps=24, mesh=None):
    """Pins planted feet where they land and re-solves the legs onto the pins. Returns
    {before, after (foot_skate scores), moved, ground_speed}."""
    if not chains: return None
    frames = sample(arm, action, last, chains)
    n = last if loops else last + 1
    tracks = {c["name"]: [frames[f][c["name"]][-1] for f in range(n)] for c in chains}
    floor = rest_floor(arm, chains)
    before = foot_contact.foot_skate(tracks, size, fps=fps, loop=loops, velocity=velocity, floor=floor)
    pins, contacts, v = foot_contact.plan_pins(tracks, size, loop=loops, velocity=velocity, floor=floor)
    if not any(any(c) for c in contacts.values()):
        return {"before": before["score"], "after": before["score"], "moved": 0.0, "ground_speed": before["ground_speed"]}
    targets = [{k: pins[k][f] for k in pins if contacts[k][f] or pins[k][f] != tracks[k][f]} for f in range(n)]
    strain = mesh_strain(arm, action, last, mesh) if mesh is not None else None
    moved, saved = place_feet(arm, action, last, loops, chains, targets, size)
    frames = sample(arm, action, last, chains)
    tracks = {c["name"]: [frames[f][c["name"]][-1] for f in range(n)] for c in chains}
    after = foot_contact.foot_skate(tracks, size, fps=fps, loop=loops, velocity=v, floor=floor)
    kept = after["score"] < 0.9 * before["score"]
    if kept and strain is not None:
        torn, stretch = mesh_strain(arm, action, last, mesh)
        kept = torn <= strain[0] and stretch <= strain[1] + 0.25
    if not kept:
        # the legs could not hold their feet (a body that rises out of reach, a leg at its turn limit), or holding
        # them strains the mesh more than the clip did: the clip as authored is the better one
        restore(saved)
    return {"before": before["score"], "after": after["score"] if kept else before["score"], "kept": kept,
            "moved": round(moved / size, 5) if kept else 0.0, "ground_speed": before["ground_speed"]}

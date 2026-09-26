# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: authors a rigged creature's animation clips in Blender, keyed on the rig by its documented bone
# names (SKELETONS.md), and writes them out in a form any engine can use. The model's rig.json "clips" section picks
# the archetype and its settings (docs/SPEC.md).
#
#   blender -b --python autorig/steps/make_clips.py -- wolf                          a creature archetype: writes clips/
#   blender -b --python autorig/steps/make_clips.py -- wolf --preview <dir>          also render check frames per clip
#   blender -b --python autorig/steps/make_clips.py -- wyvern --split-clips <dir>    winged: also write the split pair
#
# A creature archetype (walker, flyer, exploder, swimmer, turret) writes clips/ in the model folder (see below).
# The winged archetype writes, for a model <key> with display name <Name>, beside the rig it animated
# (<model>/<rig folder>/):
#   <key>.blend                    the rig with every clip as a named action (the source for any further animation)
#   <Name>/<Name>.fbx              mesh (decimated to the model's budget), rig and every clip as a named take, in metres
#   <Name>/<Name>.jpg              the base colour texture
#   <Name>/<Name>.json             the manifest: clips (length, loop, speed, events), bone map, size, facing, licence.
#                                  Format "autorig-export/1" (docs/FORMATS.md).
# and with --split-clips <dir>, the split pair: <dir>/<Name>.fbx (armature and takes, the rig's own units),
# <dir>/<Name>_model.fbx (the mesh bound to the same armature) and <dir>/<Name>_clips.json (lengths, loops, fps,
# walkSpeed from the spec's splitWalkSpeed, windUpEnd).
#
# Why here and not in an engine: the clips belong to the rig, and any number of projects use the rig. An engine
# imports the result; it does not re-derive the motion.
#
# How a clip is authored: every frame is a Pose, built by a function of the frame. A Pose holds, per bone, either
#   - turns: rotations about armature axes, applied in the bone's parent's posed frame (so "pitch the neck back" is
#     the same instruction however the body above it is turned), or
#   - an aim: the direction the bone should point in the world (armature space), whatever its parents do. Wings
#     are posed this way: a spread wing is a set of directions, and a wingbeat turns those directions about the
#     body's long axis.
# The root carries the body's pitch and position. IK and copy-rotation constraints are muted: the clips are FK, and
# the exporter bakes exactly the keyed pose.
import bpy, sys, os, json, math, shutil, re
from mathutils import Vector, Quaternion
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import layout
import gait
import digits
import morph_generator
import clip_contract
# Per-model clip settings (archetype, display name, category, attack...): rig.json "clips"; the licence line:
# the collection's autorig.json.
from spec_store import CLIPS as MODELS, LICENCE

FPS = 30

# The rig convention: faces -Y, up +Z, its left is +X (.L).
FORWARD = Vector((0.0, -1.0, 0.0))
UP = Vector((0.0, 0.0, 1.0))
LATERAL = UP.cross(FORWARD)       # +X. A positive turn about it dips the nose and lifts whatever is behind the pivot.


# ---------------------------------------------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------------------------------------------

class Rig:
    def __init__(self, arm, mesh):
        self.arm, self.mesh = arm, mesh
        self.pose = arm.pose.bones
        self.bones = arm.data.bones
        self.names = [b.name for b in self.bones]
        self.rest3 = {b.name: b.matrix_local.to_3x3() for b in self.bones}
        self.dir = {b.name: (b.tail_local - b.head_local).normalized() for b in self.bones}
        self.head = {b.name: b.head_local.copy() for b in self.bones}
        order, todo = [], [b for b in self.bones if b.parent is None]
        while todo:
            b = todo.pop(0); order.append(b.name); todo.extend(b.children)
        self.order = order
        if mesh and len(mesh.data.vertices) > 0:
            co = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
            self.lo = Vector([min(p[k] for p in co) for k in range(3)])
            self.hi = Vector([max(p[k] for p in co) for k in range(3)])
        else:
            self.lo = Vector((0.0, 0.0, 0.0))
            self.hi = Vector((1.0, 1.0, 1.0))
        self.centre = (self.lo + self.hi) * 0.5
        self.size = max(max(self.hi - self.lo), 1e-4)

    def has(self, n): return n in self.rest3

    def chain(self, stem, side=""):
        """stem_1, stem_2, ... (with the side suffix) for as long as they exist."""
        out, i = [], 1
        while self.has("%s_%d%s" % (stem, i, side)):
            out.append("%s_%d%s" % (stem, i, side)); i += 1
        return out


KEY = {"f": 0}    # the key build() is making, not wrapped: a loop's last key is `frames` here, 0 in the pose phase


class Pose:
    def __init__(self):
        self.turns, self.aims, self.moves, self.scales, self.morphs = {}, {}, {}, {}, {}

    def morph(self, name, value):
        """Sets a target blendshape/morph value in [0, 1]."""
        self.morphs[name] = float(value)

    def grow(self, bone, factors):
        """A scale in the bone's own axes (Y along the bone): a bell pulsing, a body swelling. Children inherit it."""
        if bone:
            s = self.scales.get(bone, Vector((1, 1, 1)))
            self.scales[bone] = Vector((s.x * factors[0], s.y * factors[1], s.z * factors[2]))

    def orient(self, bone, q):
        """An arbitrary armature-space rotation (a limb aimed at a direction), applied in order with the turns."""
        if bone: self.turns.setdefault(bone, []).append(q)

    def turn(self, bone, axis, degrees):
        if bone and abs(degrees) > 1e-5:
            self.turns.setdefault(bone, []).append(Quaternion(axis, math.radians(degrees)))

    def rotate(self, bone, q):
        self.turns.setdefault(bone, []).append(q)

    def aim(self, bone, direction):
        self.aims[bone] = Vector(direction).normalized()

    def move(self, bone, delta):
        self.moves[bone] = self.moves.get(bone, Vector()) + Vector(delta)


_LAST_Q = {}
_KEYS = None      # (bone, property) -> [(frame, values)] while build() makes a clip; None keys each frame directly


def _fcurve_bag(action, obj):
    """Where the action's F-curves live: Blender 4.4+ keeps them in a channelbag per slot (made here the way
    keyframe_insert makes one), older ones on the action itself."""
    ad = obj.animation_data
    if hasattr(action, "slots") and hasattr(ad, "action_slot"):
        from bpy_extras import anim_utils
        slot = ad.action_slot
        if slot is None:
            slot = action.slots.new(id_type='OBJECT', name=obj.name)
            ad.action_slot = slot
        return anim_utils.action_ensure_channelbag_for_slot(action, slot), True
    return action, False


def flush_keys(action, obj, keys):
    """Writes a clip's gathered bone keys: one F-curve per channel filled in a single foreach_set, handles worked
    out once at the end, instead of three keyframe_insert calls per bone per frame (each one sorting the curve and
    redoing its handles). Same curves: Bezier keys, auto-clamped handles, grouped by bone. Anything unexpected
    (an F-curve that is already there, an API this Blender lacks) falls back to keyframe_insert, key by key."""
    made = []
    try:
        bag, slotted = _fcurve_bag(action, obj)
        groups = {}
        for (bone, prop), rows in keys.items():
            path = 'pose.bones["%s"].%s' % (bpy.utils.escape_identifier(bone), prop)
            for i in range(len(rows[0][1])):
                if bag.fcurves.find(path, index=i) is not None:
                    raise RuntimeError("curve exists: %s[%d]" % (path, i))
                if slotted:
                    fc = bag.fcurves.new(path, index=i)
                    if bone not in groups: groups[bone] = bag.groups.get(bone) or bag.groups.new(bone)
                    fc.group = groups[bone]
                else:
                    fc = bag.fcurves.new(path, index=i, action_group=bone)
                made.append(fc)
                fc.keyframe_points.add(len(rows))
                co = np.empty(len(rows) * 2, dtype=np.float32)
                co[0::2] = [f for f, _ in rows]; co[1::2] = [v[i] for _, v in rows]
                fc.keyframe_points.foreach_set("co", co)
                fc.update()
        return True
    except Exception as e:
        print("MAKE_CLIPS note: fast keying unavailable (%s): keying frame by frame" % e)
        for fc in made:                       # half-made curves (keys added, not yet filled) would keep (0, 0) keys
            try: bag.fcurves.remove(fc)
            except Exception: pass
        pbs = obj.pose.bones
        for (bone, prop), rows in keys.items():
            path = 'pose.bones["%s"].%s' % (bpy.utils.escape_identifier(bone), prop)
            for f, v in rows:
                setattr(pbs[bone], prop, v)
                pbs[bone].keyframe_insert(prop, frame=f, group=bone)
        return False


def apply(rig, pose, frame):
    """Poses every bone and keys it at this frame. Every bone is keyed on every frame: a channel an action does not
    key keeps whatever the last action left there, in Blender and in the exporter."""
    delta = {}
    for name in rig.order:
        b = rig.bones[name]
        dp = delta[b.parent.name] if b.parent else Quaternion()
        if name in pose.aims:
            total = dp.inverted() @ rig.dir[name].rotation_difference(pose.aims[name])
        else:
            total = Quaternion()
            for q in pose.turns.get(name, []): total = q @ total
        delta[name] = dp @ total
        m = rig.rest3[name]
        pb = rig.pose[name]
        pb.rotation_mode = 'QUATERNION'
        q = (m.inverted() @ total.to_matrix() @ m).to_quaternion()
        # keep each bone's keys on one side of the quaternion sphere: a part spinning whole turns would otherwise
        # flip sign at every half turn, and anything interpolating between the keys would spin it back
        prev = _LAST_Q.get(name)
        if prev is not None and prev.dot(q) < 0: q = -q
        _LAST_Q[name] = q.copy()
        pb.rotation_quaternion = q
        pb.location = m.inverted() @ pose.moves[name] if name in pose.moves else Vector()
        pb.scale = pose.scales.get(name, Vector((1, 1, 1)))
    if _KEYS is not None:                        # build(): gathered here, written a curve at a time by flush_keys
        for pb in rig.pose:
            _KEYS.setdefault((pb.name, "location"), []).append((frame, tuple(pb.location)))
            _KEYS.setdefault((pb.name, "rotation_quaternion"), []).append((frame, tuple(pb.rotation_quaternion)))
            _KEYS.setdefault((pb.name, "scale"), []).append((frame, tuple(pb.scale)))
    else:
        for pb in rig.pose:
            pb.keyframe_insert("location", frame=frame, group=pb.name)
            pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
            pb.keyframe_insert("scale", frame=frame, group=pb.name)

    # Key morph targets on mesh shape keys if present
    mesh = getattr(rig, "mesh", None)
    if mesh and getattr(mesh.data, "shape_keys", None):
        kb = mesh.data.shape_keys.key_blocks
        for name in kb.keys():
            if name == "Basis":
                continue
            val = pose.morphs.get(name, 0.0) if hasattr(pose, "morphs") else 0.0
            kb[name].value = float(val)
            kb[name].keyframe_insert("value", frame=frame)


def ease(t):
    t = max(0.0, min(1.0, t)); return t * t * (3 - 2 * t)


def over(f, a, b):
    return ease((f - a) / float(max(1e-6, b - a)))


def since(f, onset, n):
    """Time since an impact at frame `onset`, as a share of the clip (0 before it): what a damped secondary ripple
    (gait.evaluate_secondary_chain's impulse_time) runs on. An envelope that rises and falls ran the ripple
    backwards on its way down."""
    return max(0.0, (f - onset) / float(max(1, n)))


def lerp(a, b, t): return a + (b - a) * t


# ---------------------------------------------------------------------------------------------------------------
# The winged archetype (SKELETONS.md, "Winged creature")
# ---------------------------------------------------------------------------------------------------------------

# A spread wing and a wing folded on the upstroke, as directions per bone in the flight frame (the creature
# flying level towards -Y, up +Z), for the left wing; the right is mirrored. The humerus (wing_2) goes out and a
# little up and back, the forearm (wing_3) straight out, then the three spars fan back from the wrist: the leading
# spar out along the span, the middle spar back through the membrane, the inner spar back towards the tail.
SPREAD = {"wing_2": (1, 0.25, 0.35), "wing_3": (1, -0.15, 0.05),
          "wing_finger1_1": (0.85, 0.45, 0.0), "wing_finger1_2": (0.7, 0.7, -0.05), "wing_finger1_3": (0.5, 0.85, -0.1),
          "wing_finger2_1": (0.45, 0.9, -0.05), "wing_finger2_2": (0.3, 0.95, -0.1),
          "wing_finger3_1": (0.12, 1.0, -0.05), "wing_finger3_2": (0.0, 1.0, -0.1)}
FOLDED = {"wing_2": (0.85, 0.35, 0.45), "wing_3": (0.55, 0.8, 0.25),
          "wing_finger1_1": (0.25, 1.0, 0.05), "wing_finger1_2": (0.1, 1.0, -0.05), "wing_finger1_3": (0.0, 1.0, -0.1),
          "wing_finger2_1": (0.1, 1.0, -0.05), "wing_finger2_2": (0.0, 1.0, -0.1),
          "wing_finger3_1": (-0.05, 1.0, -0.1), "wing_finger3_2": (-0.1, 1.0, -0.15)}


class Winged:
    """Clips for a winged creature: fly, idle (hover), perch, the attacks with their wind-ups, hit and death."""

    FLY_PITCH = 70.0     # degrees the upright-built body tips nose-down to fly level

    def __init__(self, rig, spec):
        r = self.r = rig
        self.L = rig.size
        self.spine = [n for n in ("hips",) if r.has(n)] + r.chain("spine")
        self.neck = r.chain("neck")
        self.head = "head" if r.has("head") else None
        self.jaw_b = "jaw" if r.has("jaw") else None
        self.tails = r.chain("tail")
        self.sides = [(".L", 1.0), (".R", -1.0)]
        self.legs = {s: r.chain("leg", s) for s, _ in self.sides}
        self.arms = {s: r.chain("arm", s) for s, _ in self.sides}
        self.root = "root"
        self.pitch = 0.0
        self.spec = spec

    # ---- the body ----

    def body(self, p, pitch, lift=0.0, fwd=0.0, roll=0.0):
        """The whole creature turned about the middle of its body: pitch nose-down, roll about its length."""
        R = Quaternion(FORWARD, math.radians(roll)) @ Quaternion(LATERAL, math.radians(pitch))
        p.rotate(self.root, R)
        C = self.r.centre
        p.move(self.root, C - R @ C + UP * (lift * self.L) + FORWARD * (fwd * self.L))
        self.pitch, self.R = pitch, R

    def neck_level(self, p, extra=0.0, head=0.0, nod=0.0):
        """The neck counter-turned against the body's pitch so the head leads level; `extra` bends each link
        (negative draws the head back and up), `head` pitches the head alone."""
        k = [0.3, 0.28, 0.2]
        for b, kk in zip(self.neck, k): p.turn(b, LATERAL, -kk * self.pitch + extra)
        p.turn(self.head, LATERAL, -0.12 * self.pitch + head + nod)

    def jaw(self, p, deg):
        p.turn(self.jaw_b, LATERAL, deg)

    def tail(self, p, base, t=0.0, wave=0.0, per_link=0.0, lag=0.8):
        """Trailing: `base` on the root link against the body's pitch, `per_link` more at each, and a travelling
        wave down it that lags a little more at every link, so the tip whips."""
        for i, b in enumerate(self.tails):
            a = (base if i == 0 else 0.0) + per_link * i
            a += wave * (0.5 + 0.18 * i) * math.sin(t - lag * (i + 1))
            p.turn(b, LATERAL, a)

    def legs_trail(self, p, amount=1.0, dangle=0.0):
        """Hind legs drawn up and back in flight (`amount` 1), or hanging (`dangle` 1)."""
        for s, sign in self.sides:
            lg = self.legs[s]
            if len(lg) < 4: continue
            p.turn(lg[0], LATERAL, lerp(22, -10, dangle) * amount)
            p.turn(lg[1], LATERAL, lerp(-35, -15, dangle) * amount)
            p.turn(lg[2], LATERAL, lerp(45, 20, dangle) * amount)
            p.turn(lg[3], LATERAL, lerp(30, 10, dangle) * amount)

    def arms_tucked(self, p, amount=1.0):
        """The small forelimbs folded against the chest, and held there: they never move with the wings."""
        for s, sign in self.sides:
            a = self.arms[s]
            if len(a) < 3: continue
            p.turn(a[0], LATERAL, 15 * amount)
            p.turn(a[1], LATERAL, -70 * amount)
            p.turn(a[2], LATERAL, -35 * amount)

    def wings(self, p, phi, fold=0.0, sweep=0.0, finger_phi=None, spread=1.0, fly_frame=True):
        """Both wings: `phi` degrees of elevation about the body's long axis (positive raises the tips), `fold`
        0 spread .. 1 folded, `sweep` degrees forward about the vertical, `finger_phi` the spars' own elevation
        (a beat behind the arm, so the tips whip), `spread` 0 = the rest pose's fold .. 1 = flight. The directions
        are in the flight frame, turned with the body when it pitches away from level flight."""
        if finger_phi is None: finger_phi = phi
        body = Quaternion(LATERAL, math.radians(self.pitch - self.FLY_PITCH)) if fly_frame else Quaternion()
        for s, side in self.sides:
            up_arm = Quaternion((0, -side, 0), math.radians(phi))
            up_fin = Quaternion((0, -side, 0), math.radians(finger_phi))
            fw = Quaternion((0, 0, -side), math.radians(sweep))
            for stem, a in SPREAD.items():
                b = stem + s
                if not self.r.has(b): continue
                v = Vector(a).lerp(Vector(FOLDED[stem]), fold).normalized()
                v = Vector((v.x * side, v.y, v.z))
                v = fw @ ((up_arm if stem in ("wing_2", "wing_3") else up_fin) @ v)
                v = body @ v
                if spread < 1.0:
                    rest = self.r.dir[b]
                    v = rest.slerp(v, spread) if hasattr(rest, "slerp") else rest.lerp(v, spread).normalized()
                p.aim(b, v)

    # ---- the stroke ----

    @staticmethod
    def stroke(s, top=62.0, bottom=-55.0, down=0.42):
        """Wing elevation and fold through one beat, s 0..1. The downstroke is the first `down` of the beat, fast
        through the middle; the upstroke is slower, and the wing folds through it and opens again at the top."""
        s %= 1.0
        if s < down:
            u = s / down
            return lerp(top, bottom, ease(u)), 0.0
        u = (s - down) / (1 - down)
        return lerp(bottom, top, ease(u)), 0.85 * math.sin(math.pi * u) ** 1.5

    def flight(self, p, s, pitch, beat=1.0, t_tail=None, lift_amp=0.03, dangle=0.0):
        """Flying, s through the beat: body, neck, tail, legs, arms and wings together."""
        phi, fold = self.stroke(s)
        phi_f, _ = self.stroke(s - 0.06)
        # the body rises on the downstroke, a quarter beat behind the wing, and sinks on the upstroke
        lift = lift_amp * math.sin(2 * math.pi * (s - 0.2)) * beat
        self.body(p, pitch + 3.0 * math.sin(2 * math.pi * (s + 0.1)) * beat, lift=lift)
        self.neck_level(p, head=-2.0 * math.sin(2 * math.pi * (s + 0.3)) * beat)
        tt = 2 * math.pi * s if t_tail is None else t_tail
        self.tail(p, base=-38 + 0.35 * (self.FLY_PITCH - pitch) * -1, t=tt, wave=6 * beat, per_link=-1.5)
        self.legs_trail(p, dangle=dangle)
        self.arms_tucked(p)
        self.wings(p, phi * beat + 5 * (1 - beat), fold * beat, sweep=10 * math.cos(2 * math.pi * s) * beat,
                   finger_phi=phi_f * beat + 5 * (1 - beat))
        return phi

    # ---- the clips ----

    def fly(self, f, n):
        p = Pose()
        self.flight(p, f / float(n), self.FLY_PITCH)
        return p

    def idle(self, f, n):
        """Hovering: more upright, slower beats with a smaller stroke, legs hanging, head looking about."""
        p = Pose()
        s = f / float(n)
        phi, fold = self.stroke(s, top=52, bottom=-40, down=0.45)
        phi_f, _ = self.stroke(s - 0.07, top=52, bottom=-40, down=0.45)
        self.body(p, 38 + 2.5 * math.sin(2 * math.pi * (s + 0.1)), lift=0.035 * math.sin(2 * math.pi * (s - 0.2)))
        self.neck_level(p, nod=4 * math.sin(2 * math.pi * s * 0.5 + 1.0))
        self.tail(p, base=-18, t=2 * math.pi * s, wave=7, per_link=-2.5)
        self.legs_trail(p, dangle=1.0)
        self.arms_tucked(p)
        self.wings(p, phi, fold, sweep=14 * math.cos(2 * math.pi * s), finger_phi=phi_f)
        return p

    def perch(self, f, n):
        """Standing on a ledge, wings folded as sculpted: breathing, a slow look about, the tail swaying."""
        p = Pose()
        t = 2 * math.pi * f / float(n)
        self.body(p, 0.0, lift=0.004 * math.sin(2 * t))
        for b in self.spine[1:]: p.turn(b, LATERAL, -1.2 * math.sin(2 * t))
        for i, b in enumerate(self.neck): p.turn(b, UP, 5 * math.sin(t + 0.4 * i))
        p.turn(self.head, LATERAL, 4 * math.sin(t * 2 + 1.0))
        self.jaw(p, -4 + 4 * math.sin(2 * t))
        for i, b in enumerate(self.tails): p.turn(b, UP, (2 + 1.2 * i) * math.sin(t - 0.6 * (i + 1)))
        for s, side in self.sides:
            p.turn("wing_2" + s, FORWARD, side * 2.0 * math.sin(2 * t + 0.5))   # the folded wing settling
        return p

    # attacks: the wind-up is the first WINDUP frames (a game may scrub it with its own telegraph), then the strike
    WINDUP, IMPACT, LENGTH = 20, 24, 36

    def _attack_base(self, p, f, kind):
        n = self.LENGTH
        w = over(f, 0, self.WINDUP - 2)                     # into the wind-up pose
        shiver = math.sin(f * 2.4) * over(f, self.WINDUP - 7, self.WINDUP) * (1 - over(f, self.WINDUP, self.WINDUP + 1))
        k = over(f, self.WINDUP, self.IMPACT)               # the strike
        r = over(f, self.IMPACT + 3, n - 1)                 # the recovery back to the hover
        hold = w * (1 - k)
        hit = k * (1 - r)
        return w, k, r, hold, hit, shiver

    def _hover_under(self, p, pitch, phi, fold=0.0, sweep=0.0, finger_lag=6.0, lift=0.0, fwd=0.0, tail=None,
                     legs=1.0):
        self.body(p, pitch, lift=lift, fwd=fwd)
        self.arms_tucked(p)
        self.legs_trail(p, dangle=legs)
        self.wings(p, phi, fold, sweep=sweep, finger_phi=phi - finger_lag)

    def attack(self, f, n):
        """The bite: coils the neck back with the jaw opening and the wings up, then snaps forward and down."""
        p = Pose()
        w, k, r, hold, hit, sh = self._attack_base(p, f, "bite")
        pitch = 38 - 16 * hold + 30 * hit
        self._hover_under(p, pitch, phi=lerp(5, 45, hold) - 50 * hit + 3 * sh, lift=0.05 * hold - 0.04 * hit,
                          fwd=-0.08 * hold + 0.16 * hit)
        self.neck_level(p, extra=-14 * hold + 18 * hit, head=-10 * hold + 14 * hit + sh)
        self.jaw(p, 22 * hold + 30 * over(f, self.WINDUP, self.WINDUP + 2) * (1 - over(f, self.IMPACT - 1, self.IMPACT)))
        self.tail(p, base=-18 + 6 * hold - 6 * hit, per_link=-2 + 0.8 * hold - 1 * hit)   # the coil is the rear's
        return p

    def attack_inhale(self, f, n):
        """Fire breath. Wind-up: neck drawn back and up, chest lifted, jaw opening wide, wings spread high and held.
        Strike: the neck throws forward and down, the jaw at its widest and held, the body recoiling back."""
        p = Pose()
        w, k, r, hold, hit, sh = self._attack_base(p, f, "inhale")
        pitch = 38 - 18 * hold + 22 * hit
        self._hover_under(p, pitch, phi=lerp(5, 40, hold) - 30 * hit + 2 * sh, sweep=-12 * hit,
                          lift=0.03 * hold, fwd=-0.06 * hold - 0.05 * hit)
        for b in self.spine[1:]: p.turn(b, LATERAL, -8 * hold)
        self.neck_level(p, extra=-12 * hold + 24 * hit, head=-22 * hold + 18 * hit + 1.5 * sh)
        self.jaw(p, 28 * hold + 36 * hit)
        self.tail(p, base=-18 + 5 * hold - 4 * hit, per_link=-2 + 0.6 * hold)
        return p

    def attack_rise(self, f, n):
        """The dive. Wind-up: wings swept up high overhead, body reared up, legs drawn in, head down at the target.
        Strike: the wings slam down, it pitches into a steep dive and throws its talons forward."""
        p = Pose()
        w, k, r, hold, hit, sh = self._attack_base(p, f, "rise")
        pitch = 38 - 26 * hold + 60 * hit
        self._hover_under(p, pitch, phi=lerp(5, 82, hold) - 130 * hit * 0.95 + 2 * sh, fold=0.0,
                          sweep=-8 * hold + 10 * hit, lift=0.12 * hold - 0.12 * hit, fwd=0.2 * hit, legs=1 - hold)
        self.neck_level(p, extra=4 * hold, head=16 * hold + 10 * hit)
        for s, _ in self.sides:
            lg = self.legs[s]
            if lg:
                p.turn(lg[0], LATERAL, 25 * hold - 70 * hit)     # knees up, then talons thrown forward
                p.turn(lg[1], LATERAL, -30 * hold + 20 * hit)
        # in the dive the body pitches past vertical, which stands the tail up like a mast: it is held down
        # against the pitch so it streams out behind
        self.tail(p, base=-18 - 14 * hold - 38 * hit, per_link=-2 - 3 * hold - 1 * hit)
        return p

    def attack_rear(self, f, n):
        """The tail sweep. Wind-up: reared back with the tail curled up high over its back. Strike: it pitches
        forward and the tail whips down and through in an arc, the tip last."""
        p = Pose()
        w, k, r, hold, hit, sh = self._attack_base(p, f, "rear")
        pitch = 38 - 20 * hold + 30 * hit
        self._hover_under(p, pitch, phi=lerp(5, 25, hold) - 20 * hit, lift=0.04 * hold, fwd=-0.05 * hold + 0.08 * hit)
        self.neck_level(p, extra=-6 * hold + 6 * hit, head=-8 * hold + sh)
        for i, b in enumerate(self.tails):
            late = over(f, self.WINDUP + 0.6 * i, self.IMPACT + 0.8 * i) * (1 - r)
            p.turn(b, LATERAL, (-18 if i == 0 else -2) * (1 - hold) + (14 + 5 * i) * hold * (1 - late)
                   - (18 + 4 * i) * late + sh * 0.6)
        return p

    def strike(self, f, n):
        """The bite's strike alone, from the full wind-up: for engines that play wind-up and strike separately."""
        return self.attack(self.WINDUP - 2 + f, self.LENGTH)

    def attack_windup(self, f, n):
        """The bite's wind-up, ramping to full and held."""
        return self.attack(min(f, self.WINDUP - 1), self.LENGTH)

    def hit(self, f, n):
        """Knocked back: head snapped back, jaw open, wings jolted up, tail flicked; a third of a second."""
        p = Pose()
        e = math.sin(math.pi * min(1.0, f / float(n - 1)))
        self._hover_under(p, 38 - 22 * e, phi=5 + 42 * e, fwd=-0.07 * e, lift=0.03 * e)
        self.neck_level(p, extra=-12 * e, head=-24 * e)
        self.jaw(p, 20 * e)
        self.tail(p, base=-18 + 14 * e, per_link=-2 + 2 * e)
        return p

    def death(self, f, n):
        """Shot down: thrown back, the wings go limp, it pitches over and comes down on its belly, wings splayed on
        the floor either side, neck and head down, tail flat. In place: the game drops the body to the floor, and
        the last frame is the corpse, belly lowest."""
        p = Pose()
        a = over(f, 0, 5) * (1 - over(f, 5, 12))            # the hit that kills it
        fall = over(f, 5, 28)
        land = over(f, 28, 32)
        bounce = math.sin(math.pi * over(f, 31, 38)) * (1 - over(f, 38, 44))
        slump = over(f, 30, 42)
        pitch = lerp(38, 90, fall) - 18 * a + 3 * bounce
        roll = 10 * math.sin(math.pi * fall)
        self.body(p, pitch, lift=0.03 * a + 0.01 * bounce, roll=roll)
        # The flight tuck swings the forearms forward against the chest, which belly-down is into the floor: they
        # propped the corpse up. So they untuck as it falls and lie back along the belly.
        self.arms_tucked(p, 1 - fall)
        for s, _ in self.sides:
            fore = self.arms[s]
            if len(fore) >= 2:
                p.turn(fore[0], LATERAL, 25 * fall)
                p.turn(fore[1], LATERAL, 15 * fall)
        self.legs_trail(p, amount=1 - 0.3 * slump, dangle=lerp(1, 0.2, fall))
        # the wings crumple half-folded along the flanks rather than splaying down: spread and drooped, their spars
        # reached below the belly and propped the corpse up off the floor (QA sheet, 2026-09-21)
        phi = 5 + 45 * a - lerp(0, 12, fall) + 8 * bounce
        self.wings(p, phi, fold=lerp(0.0, 0.6, fall), finger_phi=phi - 4 * fall, sweep=-10 * fall)
        self.neck_level(p, extra=-10 * a + 6 * slump + 4 * bounce, head=-15 * a + 10 * slump)
        self.jaw(p, 14 * a + 10 * slump)
        self.tail(p, base=lerp(-18, -62, fall), per_link=lerp(-2, -1.5, slump), wave=8 * bounce, t=f * 0.6)
        return p

    def clips(self):
        L = self.LENGTH
        return {
            # name: (frames, function, loops)
            "fly": (30, self.fly, True),
            "idle": (45, self.idle, True),
            "perch": (90, self.perch, True),
            "attack": (L, self.attack, False),
            "attack_inhale": (L, self.attack_inhale, False),
            "attack_rise": (L, self.attack_rise, False),
            "attack_rear": (L, self.attack_rear, False),
            "attack_windup": (30, self.attack_windup, False),
            "strike": (L - self.WINDUP + 2, self.strike, False),
            "hit": (10, self.hit, False),
            "death": (45, self.death, False),
        }

    def events(self):
        s = 1.0 / FPS
        attack = [{"name": "windup_full", "time": round((self.WINDUP - 2) * s, 4),
                   "detail": "the wind-up pose is complete; a game holding the telegraph holds here"},
                  {"name": "strike_release", "time": round(self.WINDUP * s, 4), "detail": "the strike starts"},
                  {"name": "strike_impact", "time": round(self.IMPACT * s, 4), "detail": "the strike's furthest pose"}]
        return {
            "fly": [{"name": "wingbeat_down", "time": 0.0, "detail": "the downstroke starts (a whoosh)"}],
            "idle": [{"name": "wingbeat_down", "time": 0.0, "detail": "the downstroke starts"}],
            "attack": attack, "attack_inhale": attack, "attack_rise": attack, "attack_rear": attack,
            "attack_windup": [attack[0]],
            "strike": [{"name": "strike_impact", "time": round((self.IMPACT - self.WINDUP + 2) * s, 4),
                        "detail": "the strike's furthest pose"}],
            "hit": [{"name": "hit", "time": 0.0, "detail": "the flinch starts on the hit"}],
            "death": [{"name": "death_land", "time": round(30 * s, 4), "detail": "the belly hits the ground"},
                      {"name": "death_rest", "time": round(42 * s, 4), "detail": "the body lies still"}],
        }

    def windup_end(self):
        return self.WINDUP / float(self.LENGTH)

    def notes(self):
        return {
            "attack kinds": "attack_inhale (fire breath), attack_rise (wings up, then a dive), attack_rear (tail sweep); "
                            "attack is the bite and the fallback. All four are the same length with the same wind-up "
                            "end, so one windUpEnd serves them all.",
            "idle": "a hover: the creature is a flyer. perch is standing on a ledge, wings folded.",
        }


ARCHETYPES = {"winged": Winged}



# ---------------------------------------------------------------------------------------------------------------
# The creature archetypes: walker, flyer, exploder, swimmer, turret (SKELETONS.md: quadruped, hexapod, floater,
# serpent, rigid). A centred jaw opens (pitches) where a pair of mandibles spreads (yaws); the rig they read is built
# from the card.
#
# Output, per model, in its folder (the portable form any engine reads; SKELETONS.md, docs/FORMATS.md):
#   clips/<key>.fbx           the rig, the mesh at its engine budget, and every clip as a take named for it, in the
#                             same units and bone space as rigged/<key>.fbx (so either file's takes bind to the other)
#   clips/<key>_clips.blend   the rig with every clip as an action (the source to animate further from)
#   clips/<key>_clips.json    clip lengths, loops, events, speeds, the wind-up point, and the card's bone roles
# ---------------------------------------------------------------------------------------------------------------

CREATURE_FPS = 24              # these clips were timed in frames at 24; the file says so
WINDUP_END = 14
STRIKE_END = 18
# Parts that trail behind a flyer and follow its motion a beat late. Pods and tendrils are deliberately left out:
# they hold still on a flyer (adding them would change every flyer's clips that has them).
TRAILING = ("tail", "tentacle", "streamer", "oral_arm", "flame", "wisp", "ear", "abdomen")
CHAIN_NAME = re.compile(r"^(?P<base>[a-z]+(?:_[a-z]+)*\d*)(?:_(?P<i>\d+))?(?:\.(?P<side>[LR]))?$")


def first(names, prefixes):
    for p in prefixes:
        for n in names:
            if n == p or n.startswith(p + "_"):
                return n
    return None


def trailing_number(name):
    digits = ""
    for ch in reversed(name):
        if ch.isdigit(): digits = ch + digits
        elif digits: break
    return int(digits) if digits else 0


# Mixamo-style humanoid Walkers (the kept Mixamo rigs and the standard humanoid): roles by bone name, matched on the
# part after any "namespace:".
HUMANOID_ROLES = {
    "hips": "Hips", "spine": "Spine", "spine1": "Spine1", "spine2": "Spine2",
    "neck": "Neck", "head": "Head",
    "thigh.L": "LeftUpLeg", "shin.L": "LeftLeg", "foot.L": "LeftFoot",
    "thigh.R": "RightUpLeg", "shin.R": "RightLeg", "foot.R": "RightFoot",
    "arm.L": "LeftArm", "forearm.L": "LeftForeArm",
    "arm.R": "RightArm", "forearm.R": "RightForeArm",
}


def humanoid_roles(names):
    short = {n.split(":")[-1]: n for n in names}
    roles = {role: short.get(mixamo) for role, mixamo in HUMANOID_ROLES.items()}
    needed = ("hips", "thigh.L", "thigh.R", "shin.L", "shin.R", "arm.L", "arm.R")
    if not all(roles[r] for r in needed):
        return None
    roles["root"] = short.get("root") or short.get("Root") or ("root" if "root" in names else None)
    return roles


class CreatureRig:
    """The rig as the creature archetypes read it. Roles come from the card (model.json rig.skeleton, written by the
    rig pipeline from the chains it built); a rig with no role map yet (an older lift) falls back to the documented
    names in SKELETONS.md. Also carries what apply() needs."""

    def __init__(self, arm, card, body=None, mesh=None):
        self.arm = arm
        self.mesh = mesh or (next((o for o in bpy.data.objects if o.type == 'MESH' and o.find_armature() == arm), None) if 'bpy' in globals() else None)
        self.pose = arm.pose.bones
        self.bones = arm.data.bones
        self.rest = {b.name: b.matrix_local.copy() for b in arm.data.bones}
        self.rest3 = {b.name: b.matrix_local.to_3x3() for b in arm.data.bones}
        self.dir = {b.name: (b.tail_local - b.head_local).normalized() for b in arm.data.bones}
        order, todo = [], [b for b in arm.data.bones if b.parent is None]
        while todo:
            b = todo.pop(0); order.append(b.name); todo.extend(b.children)
        self.order = order
        names = [b.name for b in arm.data.bones]
        self.names = names
        sk = (card.get("rig") or {}).get("skeleton") or {}
        self.skeleton = sk
        has = lambda n: isinstance(n, str) and n in names
        self.body = body if has(body) else sk.get("body") if has(sk.get("body")) else first(names, ("body", "spine", "hips", "chest"))
        self.head = sk.get("head") if has(sk.get("head")) else first(names, ("head",))
        self.neck = [b for b in sk.get("neck", []) if has(b)]
        self.root = "root" if "root" in names else None
        self.abdomen = [b for b in sk.get("abdomen", []) if has(b)] or sorted(n for n in names if n.startswith("abdomen"))
        # mandibles spread (yaw, by side); a jaw opens (pitch). Both are "jaws" to the clips.
        jaws = [e["bones"][0] for e in sk.get("mandibles", []) if e.get("bones") and has(e["bones"][0])]
        if has(sk.get("jaw")): jaws.append(sk["jaw"])
        self.jaws = jaws or [n for n in names if n.startswith(("mandible", "jaw"))]
        self.tail = [b for b in sk.get("tail", []) if has(b)] or sorted((n for n in names if n.startswith("tail")), key=trailing_number)
        self.humanoid = humanoid_roles(names)
        iks = [e["ik"] for e in sk.get("legs", []) if has(e.get("ik"))] or [n for n in names if n.startswith("ik_leg")]
        self.feet = []
        for n in iks:
            head = self.rest[n].to_translation()
            self.feet.append({"name": n, "rest": head, "side": 1.0 if n.endswith(".L") else -1.0, "along": head.dot(FORWARD)})
        # alternate groups front to back, the two sides opposite: a tripod gait on six legs, a trot on four
        for side in (1.0, -1.0):
            row = sorted((f for f in self.feet if f["side"] == side), key=lambda f: -f["along"])
            for i, f in enumerate(row): f["group"] = (i + (0 if side > 0 else 1)) % 2
        # True 4-beat lateral sequence walk for quadrupeds: LH -> LF -> RH -> RF
        if len(self.feet) == 4:
            left_feet = sorted([f for f in self.feet if f["side"] == 1.0], key=lambda f: -f["along"])
            right_feet = sorted([f for f in self.feet if f["side"] == -1.0], key=lambda f: -f["along"])
            if len(left_feet) == 2 and len(right_feet) == 2:
                left_feet[1]["lateral_phase"] = 0.00   # Left Hind
                left_feet[0]["lateral_phase"] = 0.25   # Left Front
                right_feet[1]["lateral_phase"] = 0.50  # Right Hind
                right_feet[0]["lateral_phase"] = 0.75  # Right Front
        along = [v.dot(FORWARD) for b in arm.data.bones for v in (b.head_local, b.tail_local)]
        self.length = (max(along) - min(along)) if along else 1.0
        ups = [v.dot(UP) for b in arm.data.bones for v in (b.head_local, b.tail_local)]
        self.height = max(ups) if ups else 1.0
        self.size = max(self.length, self.height)
        self.front = set()
        for side in (1.0, -1.0):
            row = [f for f in self.feet if f["side"] == side]
            if row: self.front.add(max(row, key=lambda f: f["along"])["name"])
        # gait's gallop and quadruped walk read which feet are the front ones: without this every foot was a hind
        # foot, LF moved with LH and RF with RH (a bound, not a rotary gallop)
        for f in self.feet:
            f["is_front"] = f["name"] in self.front
        # each foot's leg: the length of the IK chain that plants it (the gallop keeps its sweep inside it)
        for f in self.feet:
            pb = next((p for p in arm.pose.bones for c in p.constraints
                       if c.type == 'IK' and c.subtarget == f["name"]), None)
            if pb is None:
                f["leg_length"] = None; continue
            ik = next(c for c in pb.constraints if c.type == 'IK' and c.subtarget == f["name"])
            chain = [pb]
            while len(chain) < (ik.chain_count or 99) and chain[-1].parent is not None:
                chain.append(chain[-1].parent)
            f["leg_length"] = sum(self.bones[p.name].length for p in chain)

    def direction(self, bone):
        b = self.arm.data.bones[bone]
        return (b.tail_local - b.head_local).normalized()

    def aim(self, bone, target):
        return self.direction(bone).rotation_difference(target.normalized())

    def chains(self, prefixes):
        groups = {}
        for n in self.names:
            if not n.startswith(prefixes): continue
            m = CHAIN_NAME.match(n)
            if not m: continue
            groups.setdefault((m.group("base"), m.group("side") or ""), []).append((int(m.group("i") or 0), n))
        return [([n for _, n in sorted(links)], 1.0 if side == "L" else (-1.0 if side == "R" else 0.0))
                for (base, side), links in sorted(groups.items())]

    def chain_from(self, bone):
        out = []
        b = self.arm.data.bones.get(bone) if bone else None
        while b is not None and len(b.children) == 1:
            b = b.children[0]; out.append(b.name)
        return out


def turn_jaw(p, j, degrees):
    """A mandible (sided) spreads about the up axis; a centred jaw opens about the lateral axis, chin down."""
    if j.endswith((".L", ".R")): p.turn(j, UP, degrees)
    else: p.turn(j, LATERAL, abs(degrees))

def walker_clips(rig, spec):
    """The Walker set, by the kind of skeleton: a Mixamo humanoid, or anything the pipeline
    rigged to its own convention (legged or not)."""
    style = spec.get("attack", "bite")
    if rig.humanoid:
        return humanoid_walker(rig, style, spec)
    return creature_walker(rig, style, spec)


def creature_walker(rig, style, spec=None):
    L = rig.size
    spec = spec or {}
    walk_spec = spec.get("walk") if isinstance(spec.get("walk"), dict) else {}
    preset_name = walk_spec.get("preset") or spec.get("gait") or ("quadruped_trot" if spec.get("gait") == "trot" else "quadruped_walk")
    overrides = dict(walk_spec)
    for k in ("stride", "cadence", "sway", "bob", "foot_lift", "duty_factor", "tail_wave"):
        if k in spec and k not in overrides:
            overrides[k] = spec[k]
    gait_params = gait.merge_gait_params(preset_name, overrides)

    stride = 0.26 * L * gait_params.get("stride", 1.0)
    lift = 0.06 * L * gait_params.get("foot_lift", 1.0)
    duty = gait_params.get("duty_factor", 0.65)
    legless = not rig.feet
    discharge = style == "discharge"
    # windup: "rear" (up on the hind legs, forelegs raised: the default bite) or "head_down" (a big beast bracing to
    # charge: front end dropped, neck and head lowered, then the lunge); hit_rear scales how far a hit rocks it
    # back and up (a heavy animal barely rears)
    head_down = spec.get("windup") == "head_down"
    hit_rear = float(spec.get("hit_rear", 1.0))

    clips = {}

    def tail_wave(p, t, amount, axis=UP, impulse_time=None):
        """Analytical 2nd-order spring-damper wave propagation down the tail."""
        if not rig.tail:
            return
        angles = gait.evaluate_secondary_chain(
            len(rig.tail),
            # every caller passes an angle (radians); the old "t > 1: radians, else a 0..1 phase" read the first
            # radian of every loop as a whole cycle, a jump each time t crossed 1
            phase=t / (2.0 * math.pi),
            frequency=1.0,
            base_amplitude=amount * 0.6,
            amplitude_growth=1.22,
            phase_lag=0.45,
            damping=1.8 if impulse_time is not None else 0.0,
            impulse_time=impulse_time,
        )
        for bone, ang in zip(rig.tail, angles):
            p.turn(bone, axis, ang)

    # ---- idle: 2 s loop. Breathing, jaws working, feet planted -------------------------------
    # A creature standing still has to look alive rather than paused, but anything bigger than
    # a breath reads as a twitch at distance.
    def idle(f, n):
        p = Pose()
        t = 2 * math.pi * f / n
        p.move(rig.body, UP * (0.012 * L * math.sin(t)))
        p.turn(rig.body, LATERAL, 1.5 * math.sin(t))
        for i, a in enumerate(rig.abdomen):
            p.turn(a, LATERAL, 2.0 * math.sin(t - 0.8 * (i + 1)))
        p.turn(rig.head, UP, 4.0 * math.sin(t * 0.5))
        for j in rig.jaws:
            side = 1.0 if j.endswith(".L") else -1.0
            turn_jaw(p, j, side * 6.0 * max(0.0, math.sin(t * 2.0)))
        tail_wave(p, t, 5.0)
        # Periodic blink
        blink_u = (f % 24) / 4.0
        blink_val = math.sin(math.pi * blink_u) if blink_u <= 1.0 else 0.0
        p.morph("eyeBlink_L", blink_val)
        p.morph("eyeBlink_R", blink_val)
        return p

    clips["idle"] = (48, idle, True)

    is_quad = len(rig.feet) == 4 and any("lateral_phase" in f for f in rig.feet) and spec.get("gait") != "trot" and preset_name != "quadruped_trot"

    # ---- walk: 4-beat lateral sequence for quadrupeds, 2-group trot/scuttle otherwise -------
    def walk(f, n):
        p = Pose()
        phase = f / float(n)
        if is_quad:
            for foot in rig.feet:
                offset = foot.get("lateral_phase", 0.0)
                local = (phase - offset) % 1.0
                if local < duty:
                    s = local / duty
                    along = stride * (0.5 - s)
                    up = 0.0
                else:
                    s = (local - duty) / (1.0 - duty)
                    along = stride * (-0.5 + s)
                    up = lift * math.sin(math.pi * s)
                p.move(foot["name"], FORWARD * along + UP * up)
            t = 2 * math.pi * phase
            p.move(rig.body, UP * (0.010 * L * gait_params.get("bob", 1.0) * math.cos(4 * t)))
            p.turn(rig.body, UP, 2.5 * gait_params.get("sway", 1.0) * math.sin(t))             # horizontal S-curve spine sway
            p.turn(rig.body, FORWARD, 1.8 * gait_params.get("sway", 1.0) * math.cos(t))         # roll with the lateral gait
            p.turn(rig.head, UP, -2.5 * gait_params.get("sway", 1.0) * math.sin(t))
            for i, a in enumerate(rig.abdomen):
                p.turn(a, UP, 3.0 * gait_params.get("sway", 1.0) * math.sin(t - 0.9 * (i + 1)))
            tail_wave(p, t, 9.0 * (gait_params.get("tail_wave", 1.0) if "tail_wave" in gait_params else 1.0))
            return p

        for foot in rig.feet:
            local = (phase + 0.5 * foot["group"]) % 1.0
            if local < 0.5:                       # stance: planted, moving back
                s = local / 0.5
                along = stride * (0.5 - s)
                up = 0.0
            else:                                 # swing: lifted, moving forward
                s = (local - 0.5) / 0.5
                along = stride * (-0.5 + s)
                up = lift * math.sin(math.pi * s)
            p.move(foot["name"], FORWARD * along + UP * up)
        t = 2 * math.pi * phase
        p.move(rig.body, UP * (0.015 * L * math.cos(2 * t)))
        p.turn(rig.body, FORWARD, 2.5 * math.sin(t))          # roll with the gait
        for i, a in enumerate(rig.abdomen):
            p.turn(a, UP, 3.0 * math.sin(t - 0.9 * (i + 1)))  # abdomen trails, side to side
        p.turn(rig.head, UP, -2.0 * math.sin(t))
        tail_wave(p, t, 8.0)

        # Nothing to stride with: a legless body travels by rocking forward onto its leading edge and
        # back, twice a cycle, with a hop at each rock. Side-on that reads as a creature
        # heaving itself along, where a plain bob would read as a thing being slid.
        if legless:
            p.turn(rig.body, LATERAL, 6.0 * math.sin(2 * t))
            p.move(rig.body, UP * (0.03 * L * abs(math.sin(2 * t))))
        return p

    def trot(f, n):
        p = Pose()
        phase = f / float(n)
        for foot in rig.feet:
            local = (phase + 0.5 * foot["group"]) % 1.0
            if local < 0.5:
                s = local / 0.5
                along = stride * (0.5 - s)
                up = 0.0
            else:
                s = (local - 0.5) / 0.5
                along = stride * (-0.5 + s)
                up = lift * math.sin(math.pi * s)
            p.move(foot["name"], FORWARD * along + UP * up)
        t = 2 * math.pi * phase
        p.move(rig.body, UP * (0.018 * L * math.cos(2 * t)))
        p.turn(rig.body, FORWARD, 3.0 * math.sin(t))
        p.turn(rig.head, UP, -2.0 * math.sin(t))
        tail_wave(p, t, 12.0)
        return p

    # The gallop sweeps each foot 0.45 x the stride length in a quarter of the cycle; on the canine that was twice
    # its legs' length, the IK could not follow, and the hind knees flipped over (a 180-degree pop, the clip audit).
    # Its sweep stays within 1.1 x the shortest leg.
    legs = [f["leg_length"] for f in rig.feet if f.get("leg_length")]
    gallop_len = min(L, 1.1 * min(legs) / 0.45) if legs else L

    def gallop(f, n):
        p = Pose()
        phase = f / float(n)
        st = gait.evaluate_quadruped_gallop(phase, gallop_len, L, feet_info=rig.feet)
        feet_st = st["feet"]
        body_st = st["body"]
        for foot in rig.feet:
            fname = foot["name"]
            if fname in feet_st:
                pos = feet_st[fname]
                p.move(fname, FORWARD * pos["along"] + UP * pos["up"])
        t = 2 * math.pi * phase
        p.move(rig.body, UP * (0.025 * L * math.sin(2 * t)))
        p.turn(rig.body, LATERAL, body_st["rot"][0])
        p.turn(rig.body, FORWARD, body_st["rot"][1])
        p.turn(rig.body, UP, body_st["rot"][2])
        p.turn(rig.head, LATERAL, st["head"]["pitch"])
        p.turn(rig.head, UP, st["head"]["yaw"])
        tail_wave(p, t, 16.0)
        return p

    clips["walk"] = (24 if is_quad else 16, walk, True)
    if is_quad:
        clips["trot"] = (16, trot, True)
        clips["gallop"] = (14, gallop, True)

    # ---- attack: rear, hold, strike, recover --------------------------------------------------
    # The wind-up is most of the clip and the strike is four frames, because the wind-up is the
    # part a player reads. A game can scrub the first WINDUP_END of this clip from the attacker's
    # own telegraph timer, then play the strike when it lands.
    windup_end = 14
    strike_end = 18

    def attack(f, n):
        if discharge:
            return discharge_attack(f, n)
        if head_down:
            return charge_attack(f, n)

        p = Pose()
        rear = over(f, 0, windup_end - 2)
        strike = over(f, windup_end, strike_end)
        recover = over(f, strike_end, n)

        pitch = -22.0 * rear * (1 - strike) + 12.0 * strike * (1 - recover)
        back = 0.08 * L * rear * (1 - strike)
        lunge = 0.14 * L * strike * (1 - recover)

        p.move(rig.body, UP * (0.06 * L * rear * (1 - strike)) - FORWARD * back + FORWARD * lunge)
        p.turn(rig.body, LATERAL, pitch)
        # The head adds only a little of its own: the neck is a weighting seam, and at full
        # rear it shears if the head pitches much further than the body carrying it.
        p.turn(rig.head, LATERAL, pitch * 0.3)
        # The abdomen barely counters. Generated meshes' weighting at the tail can have islands that
        # tear loose past a few degrees, so a tail that curls to balance the rear looks shattered.
        for a in rig.abdomen:
            p.turn(a, LATERAL, -pitch * 0.12)

        # Front legs come up off the ground in threat: the silhouette changes, which survives a
        # dark cave where a detail would not.
        for foot in rig.feet:
            if foot["name"] in rig.front:
                raise_ = 0.10 * L * rear * (1 - strike)
                p.move(foot["name"], UP * raise_ + FORWARD * (0.05 * L * rear) + FORWARD * lunge)

        for j in rig.jaws:
            side = 1.0 if j.endswith(".L") else -1.0
            spread = 18.0 * rear * (1 - strike) - 8.0 * strike * (1 - recover)
            turn_jaw(p, j, side * spread)
        return p

    def charge_attack(f, n):
        """Brace head-down, then lunge. The front end drops and the neck and head go low, the whole neck, link by
        link, not just the face; the feet stay planted, so the legs fold under the lowered chest. On the strike it
        drives forward low, the head coming up only into the bite: rearing on the strike read as a flinch."""
        p = Pose()
        rear = over(f, 0, windup_end - 2)
        strike = over(f, windup_end, strike_end)
        recover = over(f, strike_end, n)
        hold = rear * (1 - strike)
        lunge = strike * (1 - recover)
        shiver = math.sin(f * 2.4) * over(f, windup_end - 5, windup_end) * (1 - strike)
        p.move(rig.body, -FORWARD * (0.05 * L * hold) - UP * (0.04 * L * hold) + FORWARD * (0.14 * L * lunge))
        p.turn(rig.body, LATERAL, 7.0 * hold - 1.5 * lunge + 0.8 * shiver)
        necks = rig.neck or []
        for i, b in enumerate(necks):
            p.turn(b, LATERAL, (12.0 + 3.0 * i) * hold - 1.5 * lunge)
        p.turn(rig.head, LATERAL, 10.0 * hold - 5.0 * lunge)
        for a in rig.abdomen:
            p.turn(a, LATERAL, -3.0 * hold)
        for i, bone in enumerate(rig.tail):
            p.turn(bone, LATERAL, (4.0 + 2.0 * i) * hold - 3.0 * lunge)
        for j in rig.jaws:
            side = 1.0 if j.endswith(".L") else -1.0
            turn_jaw(p, j, side * (6.0 * hold + 20.0 * lunge))
        return p

    def discharge_attack(f, n):
        """The ranged beast's attack: a charge you can see coming, then a crack forward.

        A ranged creature's wind-up is the one the player actually gets to read -- a game
        can scrub it from its telegraph timer -- and the player is usually well away when
        it plays. So every part of it changes the outline rather than a detail: the body
        sits back on its haunches, the head comes up, and the tail arches right over the back.
        The last frames of the charge crackle, a fast small shiver, so "about to fire" is
        distinct from "rearing" even from across a cave.
        """
        p = Pose()
        rear = over(f, 0, windup_end - 2)
        crackle = over(f, windup_end - 6, windup_end) * (1 - over(f, windup_end, windup_end + 1))
        strike = over(f, windup_end, strike_end)
        recover = over(f, strike_end, n)

        shiver = 2.5 * crackle * math.sin(f * 2.6)
        pitch = -18.0 * rear * (1 - strike) + 10.0 * strike * (1 - recover) + shiver

        # Back and down onto the hind legs; feet stay planted, so the legs fold under it.
        p.move(rig.body, -FORWARD * (0.07 * L * rear * (1 - strike))
               - UP * (0.03 * L * rear * (1 - strike))
               + FORWARD * (0.10 * L * strike * (1 - recover)))
        p.turn(rig.body, LATERAL, pitch)
        p.turn(rig.head, LATERAL, pitch * 0.4 - 8.0 * rear * (1 - strike))

        # Tail up and over the back, more at each link, then flung forward on the strike.
        # A positive turn about the lateral axis lifts anything behind the pivot.
        for i, bone in enumerate(rig.tail):
            arch = (14.0 + 6.0 * i) * rear * (1 - strike)
            whip = -(10.0 + 4.0 * i) * strike * (1 - recover)
            p.turn(bone, LATERAL, arch + whip + shiver * 0.5)
        return p

    clips["attack"] = (24, attack, False)

    # ---- hit: a sharp jolt back and up, over in a third of a second ---------------------------
    # Short on purpose: being hit has to register without hiding the next wind-up.
    def hit(f, n):
        p = Pose()
        k = math.sin(math.pi * min(1.0, f / float(n))) * (1.0 - 0.3 * f / float(n))
        p.move(rig.body, -FORWARD * (0.05 * L * k) + UP * (0.02 * L * k * hit_rear))
        p.turn(rig.body, LATERAL, -12.0 * k * hit_rear)
        p.turn(rig.head, LATERAL, -14.0 * k * hit_rear)
        for a in rig.abdomen:
            p.turn(a, LATERAL, 3.0 * k)
        for i, bone in enumerate(rig.tail):
            p.turn(bone, LATERAL, (6.0 + 3.0 * i) * k)
        return p

    clips["hit"] = (8, hit, False)

    # ---- die: flip onto its back, legs curl, and stay there ------------------------------------
    # A beetle dead on its back is readable in silhouette from the side, and it is the one pose
    # nobody mistakes for resting. The root lifts as it rolls so the shell ends on the ground
    # rather than half under it.
    def die(f, n):
        if legless:
            return topple(f, n)

        p = Pose()
        roll = over(f, 2, 12)
        settle = math.sin(math.pi * over(f, 12, 16)) * 0.15
        curl = over(f, 4, 14)

        body_height = rig.rest[rig.body].to_translation().dot(UP) if rig.body else 0.2 * L
        if rig.root:
            p.turn(rig.root, FORWARD, 180.0 * roll)
            # Rolling over, a body pivots on its side edge: the root rises by its half-width x sin(the roll) on the
            # way over. Lifting only with the roll (2 x body height x roll) swung the near side, legs and all,
            # through the floor mid-roll (the clip audit: a leg 21 cm under the floor on the bundled beetle).
            half_w = max([abs(f["rest"].x) for f in rig.feet] + [0.0])
            lift = max(2.0 * body_height * roll, half_w * math.sin(math.pi * roll) + body_height * roll)
            p.move(rig.root, UP * (lift + 0.02 * L * settle))

        # IK targets hang off the root, so in root space "curl" is toward the centre line and
        # up toward the belly -- which, once flipped, is into the air. Only part of the way:
        # pulled all the way in, the legs fold flat against the shell and the silhouette
        # stops saying "legs in the air", which is the entire read of the pose.
        for foot in rig.feet:
            rest = foot["rest"]
            inward = Vector((-rest.x * 0.3, 0.0, 0.0))
            up = UP * (body_height * 0.45)
            p.move(foot["name"], (inward + up) * curl)

        p.turn(rig.head, LATERAL, 18.0 * curl)
        for j in rig.jaws:
            side = 1.0 if j.endswith(".L") else -1.0
            turn_jaw(p, j, side * 20.0 * curl)
        return p

    def topple(f, n):
        """A legless body's death: over backwards onto its side, a small bounce, still.

        The flip above lifts the root by the body's height so the shell lands on the ground,
        and a rig that is only a root and a body has no body height to lift by: when the
        body bone starts at the root, the flip would leave it upside down and half
        underground. Tipping about the base in the visible plane needs no height at all.
        Negative about the lateral axis sends the top backwards, away from the way it faced.
        """
        p = Pose()
        fall = over(f, 1, 11)
        bounce = math.sin(math.pi * over(f, 11, 15)) * 6.0
        if rig.root:
            p.turn(rig.root, LATERAL, -(88.0 * fall - bounce))
            # Pivoting on the base point swings the near edge of a wide body below the floor;
            # lift it as it goes so it lands on its side rather than half buried.
            p.move(rig.root, UP * (0.2 * rig.size * fall))
        return p

    clips["die"] = (18, die, False)

    # ---- creature agility: jump, dodge, block ------------------------------------------------
    def creature_jump_start(f, n):
        p = Pose()
        tau = f / float(n)
        crouch = gait._smooth_step(0.0, 0.5, tau)
        launch = gait._smooth_step(0.5, 1.0, tau)
        p.move(rig.body, -UP * (0.08 * L * crouch * (1.0 - launch)) + UP * (0.10 * L * launch))
        p.turn(rig.body, LATERAL, 10.0 * crouch * (1.0 - launch) - 8.0 * launch)
        for foot in rig.feet:
            p.move(foot["name"], -UP * (0.04 * L * crouch * (1.0 - launch)) + UP * (0.06 * L * launch))
        tail_wave(p, tau * 2.0 * math.pi, 6.0)
        return p

    clips["jump_start"] = (12, creature_jump_start, False)

    def creature_jump_loop(f, n):
        p = Pose()
        phase = f / float(n)
        w = 2.0 * math.pi * phase
        p.move(rig.body, UP * (0.10 * L + 0.015 * L * math.sin(w)))
        p.turn(rig.body, LATERAL, 2.0 * math.cos(w))
        for foot in rig.feet:
            p.move(foot["name"], -FORWARD * (0.02 * L) + UP * (0.03 * L * math.sin(w)))
        tail_wave(p, phase * 2.0 * math.pi, 8.0)
        return p

    clips["jump_loop"] = (16, creature_jump_loop, True)

    def creature_jump_land(f, n):
        p = Pose()
        tau = f / float(n)
        impact = 1.0 - abs(tau - 0.3) / 0.7 if tau > 0.3 else tau / 0.3
        impact = max(0.0, min(1.0, impact))
        p.move(rig.body, -UP * (0.09 * L * impact))
        p.turn(rig.body, LATERAL, 12.0 * impact)
        tail_wave(p, tau * 2.0 * math.pi, 10.0, impulse_time=max(0.0, tau - 0.3))
        return p

    clips["jump_land"] = (14, creature_jump_land, False)

    def creature_dodge(f, n):
        p = Pose()
        tau = f / float(n)
        evade = math.sin(math.pi * tau)
        p.move(rig.body, LATERAL * (0.12 * L * evade) - FORWARD * (0.05 * L * evade))
        p.turn(rig.body, UP, 18.0 * evade)
        tail_wave(p, tau * 2.0 * math.pi, 14.0)
        return p

    clips["dodge"] = (16, creature_dodge, False)

    def creature_block(f, n):
        p = Pose()
        phase = f / float(n)
        w = 2.0 * math.pi * phase
        brace = 0.8 + 0.2 * math.sin(w)
        p.move(rig.body, -UP * (0.05 * L * brace))
        p.turn(rig.body, LATERAL, 8.0 * brace)
        for j in rig.jaws:
            side = 1.0 if j.endswith(".L") else -1.0
            turn_jaw(p, j, side * -12.0 * brace)
        return p

    clips["block"] = (16, creature_block, True)

    # the walk as built: a quadruped's 4-beat walk is 24 frames with each foot planted `duty` of the cycle, the
    # 2-group gait 16 frames at half and half (the facts used to say 16 frames for both, so the quad walk's speed
    # was a third too fast for its feet)
    return clips, {"windUpEnd": windup_end / 24.0, "stride": stride, "walkFrames": 24 if is_quad else 16,
                   "duty": duty if is_quad else 0.5}


def humanoid_walker(rig, style, spec=None):
    """The Walker set for a Mixamo skeleton: forward kinematics, by role.

    These rigs were kept from Mixamo rather than rebuilt, so there are no IK targets to plant
    and no `leg`/`body` names to find. A role map (see HUMANOID_ROLES) finds the hips, legs and
    arms, and the limbs are rotated directly. Signs follow the rule the creature clips use: a
    negative turn about the lateral axis swings a hanging limb forward.

    Mixamo rigs rest in a T-pose, arms straight out. Every clip first brings the arms down to
    the sides -- computed from where each arm actually points at rest, not assumed -- and
    swings them from there. A Walker marching with its arms held out like a scarecrow is the
    first thing anyone would notice.
    """
    spec = spec or {}
    walk_spec = spec.get("walk") if isinstance(spec.get("walk"), dict) else {}
    preset_name = walk_spec.get("preset") or spec.get("preset") or ("soldier" if style == "shoot" else "natural")
    overrides = dict(walk_spec)
    for k in ("stride", "cadence", "sway", "bob", "lean", "hip_drop", "counter_twist", "arm_swing", "foot_lift"):
        if k in spec and k not in overrides:
            overrides[k] = spec[k]
    gait_params = gait.merge_gait_params(preset_name, overrides)

    r = rig.humanoid
    H = rig.rest[r["hips"]].to_translation().dot(UP)       # hip height: the leg's length, near enough
    spine = [b for b in (r["spine"], r["spine1"], r["spine2"]) if b]

    def side_of(bone):
        return 1.0 if rig.direction(bone).x >= 0 else -1.0

    arms = [(r["arm.L"], r["forearm.L"]), (r["arm.R"], r["forearm.R"])]
    at_side = {a: rig.aim(a, Vector((side_of(a) * 0.28, 0.0, -1.0))) for a, _ in arms}
    level = {a: rig.aim(a, Vector((side_of(a) * 0.12, -1.0, 0.02))) for a, _ in arms}
    overhead = {a: rig.aim(a, Vector((side_of(a) * 0.15, 0.15, 1.0))) for a, _ in arms}
    slam = {a: rig.aim(a, Vector((side_of(a) * 0.15, -1.0, -0.7))) for a, _ in arms}

    shooter = style == "shoot"
    cadence = gait_params.get("cadence", 1.0)
    walk_frames = max(12, int(round(24.0 / cadence)))
    thigh_swing = 22.0 * gait_params.get("stride", 1.0)
    stride = 2.0 * H * math.sin(math.radians(thigh_swing))
    # the duty factors and run stride gait's evaluators use, so root motion moves the body at the planted foot's speed
    walk_duty = gait.merge_gait_params("natural", gait_params)["duty_factor"]
    run_p = gait.merge_gait_params("run", gait_params)
    run_stride = 2.4 * H * math.sin(math.radians(26.0 * run_p["stride"]))
    root_bone = r.get("root") or getattr(rig, "root", None) or ("root" if "root" in rig.names else None)
    root_motion = bool(spec.get("root_motion", False)) or ("--root-motion" in sys.argv)
    # the travel goes on the root bone, or, on a rig with none (a Mixamo humanoid), on the hips
    rm_bone = root_bone or r.get("hips")

    def root_t(n):
        """How far through a looping clip this key is, 0..1 including the last key (build() evaluates a loop's last
        key at phase 0 so the pose closes the loop, but the travel must not snap back to the start there)."""
        return KEY["f"] / float(n)

    # Automated finger / digit articulation (Pillar 2)
    fingers = []
    short_names = {n.split(":")[-1]: n for n in rig.names}      # a rig that kept "mixamorig:" still has fingers
    for side in ("Left", "Right"):
        side_sign = 1.0 if side == "Left" else -1.0
        for fname in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
            f_bones = [short_names[b] for b in (f"{side}Hand{fname}1", f"{side}Hand{fname}2", f"{side}Hand{fname}3")
                       if b in short_names]
            if f_bones:
                fingers.append((side, fname, side_sign, f_bones))

    def curl_fingers(p, state="relax", intensity=1.0):
        # T-pose, palms down, fingers along +-X: a curl folds them down toward the palm (about the forward axis),
        # a spread fans them within the palm (about the up axis). Curl used to turn about up (a fan) and spread about
        # the lateral axis (a twist of each finger about its own length).
        if not fingers:
            return
        angles = digits.compute_finger_curl_angles(state, intensity)
        for side, fname, side_sign, f_bones in fingers:
            pitch_curl, spread = angles.get(fname, (18.0, 0.0))
            per_seg = pitch_curl / float(len(f_bones))
            for i, fb in enumerate(f_bones):
                p.turn(fb, FORWARD, -side_sign * per_seg)
                if i == 0 and abs(spread) > 1e-4:
                    p.turn(fb, UP, side_sign * spread)

    # Dynamic secondary physics (Pillar 3: Tails, Capes, Hair, Ears)
    secondary_chains = []
    tail_bones = sorted([n for n in rig.names if n.lower().startswith("tail")], key=trailing_number)
    if tail_bones:
        secondary_chains.append(("tail", UP, tail_bones, 10.0, 1.25, 0.45))
    cape_bones = sorted([n for n in rig.names if any(k in n.lower() for k in ("cape", "coat", "skirt"))], key=trailing_number)
    if cape_bones:
        secondary_chains.append(("cape", LATERAL, cape_bones, 8.0, 1.15, 0.35))
    hair_bones = sorted([n for n in rig.names if "hair" in n.lower() or "ponytail" in n.lower()], key=trailing_number)
    if hair_bones:
        secondary_chains.append(("hair", LATERAL, hair_bones, 6.0, 1.20, 0.40))

    def animate_secondary(p, phase_or_t, impulse_time=None):
        for kind, axis, bones, base_amp, growth, lag in secondary_chains:
            u = phase_or_t if phase_or_t <= 1.0 else phase_or_t / (2.0 * math.pi)
            angles = gait.evaluate_secondary_chain(
                len(bones), phase=u, frequency=1.0, base_amplitude=base_amp,
                amplitude_growth=growth, phase_lag=lag, damping=2.0 if impulse_time is not None else 0.0,
                impulse_time=impulse_time
            )
            for b, ang in zip(bones, angles):
                p.turn(b, axis, ang)

    def arms_down(p, bend=None):
        for a, fa in arms:
            p.orient(a, at_side[a])
            if bend is not None and abs(bend) > 1e-5:
                side_sign = 1.0 if side_of(a) > 0 else -1.0
                p.turn(fa, UP, -side_sign * bend)

    def lean(p, degrees):
        """Spread a pitch across the spine, so the back curves rather than hinging at one joint."""
        for s in spine:
            p.turn(s, LATERAL, degrees / max(1, len(spine)))

    clips = {}

    def idle(f, n):
        p = Pose()
        t = 2 * math.pi * f / n
        arms_down(p, 12.0 if not shooter else 30.0)
        p.move(r["hips"], UP * (0.006 * H * math.sin(t)))
        lean(p, 1.2 * math.sin(t))
        p.turn(r["head"], UP, 3.0 * math.sin(t * 0.5))
        for a, _ in arms:
            p.turn(a, LATERAL, 2.0 * math.sin(t + (0 if side_of(a) > 0 else math.pi)))
        curl_fingers(p, "relax")
        animate_secondary(p, f / float(n))
        blink_u = (f % 24) / 4.0
        blink_val = math.sin(math.pi * blink_u) if blink_u <= 1.0 else 0.0
        p.morph("eyeBlink_L", blink_val)
        p.morph("eyeBlink_R", blink_val)
        return p

    clips["idle"] = (48, idle, True)

    # ---- walk: analytical biomechanical gait synthesis ---------------------------------------
    def walk(f, n):
        p = Pose()
        phase = f / float(n)
        st = gait.evaluate_biped_walk(phase, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]
        head_st = st["head"]

        # Base arm orientation (arms down at sides, no static bend so dynamic bend keys cleanly)
        arms_down(p, bend=None)

        # Pelvis translation and 6-DoF rotation
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])
        p.turn(r["hips"], FORWARD, pelvis["rot"][1])
        p.turn(r["hips"], UP, pelvis["rot"][2])

        # Spine and thoracic counter-rotation
        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)
                p.turn(s, FORWARD, spine_st["roll"] / num_spine)
                p.turn(s, UP, spine_st["yaw"] / num_spine)

        # Head stabilization
        if r.get("head"):
            p.turn(r["head"], LATERAL, head_st["pitch"])
            p.turn(r["head"], FORWARD, head_st["roll"])
            p.turn(r["head"], UP, head_st["yaw"])

        # Legs: thigh, shin, foot roll
        for side in ("L", "R"):
            leg = legs_st[side]
            thigh_p = leg["thigh_pitch"]
            knee_p = leg["knee_pitch"]
            foot_p = leg["foot_pitch"]
            p.turn(r["thigh." + side], LATERAL, thigh_p)
            p.turn(r["shin." + side], LATERAL, knee_p)
            p.turn(r["foot." + side], LATERAL, -foot_p - (thigh_p + knee_p))

        # Arms: contralateral reciprocal swing and forward elbow flexion
        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "relax")
        animate_secondary(p, phase)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("walk", root_t(n), stride, H, duty=walk_duty)
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["walk"] = (walk_frames, walk, True)

    # ---- run: ballistic flight, spring-mass bounce, high knee drive -------------------------
    run_params = gait.merge_gait_params("run", walk_spec.get("run") if isinstance(walk_spec.get("run"), dict) else {})
    run_frames = max(10, int(round(24.0 / run_params.get("cadence", 1.5))))

    def run(f, n):
        p = Pose()
        phase = f / float(n)
        st = gait.evaluate_biped_run(phase, H, H, run_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]
        head_st = st["head"]

        arms_down(p, bend=None)

        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])
        p.turn(r["hips"], FORWARD, pelvis["rot"][1])
        p.turn(r["hips"], UP, pelvis["rot"][2])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)
                p.turn(s, FORWARD, spine_st["roll"] / num_spine)
                p.turn(s, UP, spine_st["yaw"] / num_spine)

        if r.get("head"):
            p.turn(r["head"], LATERAL, head_st["pitch"])
            p.turn(r["head"], FORWARD, head_st["roll"])
            p.turn(r["head"], UP, head_st["yaw"])

        for side in ("L", "R"):
            leg = legs_st[side]
            thigh_p = leg["thigh_pitch"]
            knee_p = leg["knee_pitch"]
            foot_p = leg["foot_pitch"]
            p.turn(r["thigh." + side], LATERAL, thigh_p)
            p.turn(r["shin." + side], LATERAL, knee_p)
            p.turn(r["foot." + side], LATERAL, -foot_p - (thigh_p + knee_p))

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "splay" if st.get("is_flight") else "relax")
        animate_secondary(p, phase)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("run", root_t(n), run_stride, H, duty=run_p["duty_factor"])
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["run"] = (run_frames, run, True)

    # ---- transitions: walk_to_idle and idle_to_walk -----------------------------------------
    def walk_to_idle(f, n):
        p = Pose()
        tau = f / float(n)
        st = gait.evaluate_biped_walk_to_idle(tau, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, -(leg["thigh_pitch"] + leg["knee_pitch"]))

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "relax")
        animate_secondary(p, tau)
        return p

    clips["walk_to_idle"] = (24, walk_to_idle, False)

    def idle_to_walk(f, n):
        p = Pose()
        tau = f / float(n)
        st = gait.evaluate_biped_idle_to_walk(tau, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, -(leg["thigh_pitch"] + leg["knee_pitch"]))

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "relax")
        animate_secondary(p, tau)
        return p

    clips["idle_to_walk"] = (20, idle_to_walk, False)

    windup_end = 14
    strike_end = 18

    # ---- attack ----
    def shoot(f, n):
        """Brace and bring the weapon up level, hold, recoil.

        This is the wind-up a player actually reads, scrubbed from the real telegraph. The
        arms travel from hanging at the sides to straight out in front -- the biggest outline
        change a humanoid can make without leaving the ground -- and the knees bend into a
        brace at the same time, so "about to fire" is a different shape, not a twitch.
        """
        p = Pose()
        rear = over(f, 0, windup_end - 3)
        strike = over(f, windup_end, windup_end + 2)
        recover = over(f, strike_end, n)
        kick = strike * (1 - recover)

        for a, fa in arms:
            p.orient(a, at_side[a].slerp(level[a], rear * (1 - recover)))
            p.turn(a, LATERAL, -16.0 * kick)                   # the muzzle climbs on the shot
            side_sign = 1.0 if side_of(a) > 0 else -1.0
            p.turn(fa, UP, -side_sign * 8.0 * (1 - rear))
        crouch = rear * (1 - recover)
        for side in ("L", "R"):
            p.turn(r["thigh." + side], LATERAL, -12.0 * crouch)
            p.turn(r["shin." + side], LATERAL, 24.0 * crouch)
            p.turn(r["foot." + side], LATERAL, -12.0 * crouch)
        p.move(r["hips"], -UP * (0.05 * H * crouch) - FORWARD * (0.03 * H * kick))
        lean(p, 6.0 * crouch - 8.0 * kick)
        p.turn(r["head"], LATERAL, -6.0 * crouch)
        curl_fingers(p, "fist", 1.0 if kick > 0.1 else 0.8)
        p.morph("jawOpen", 0.5 * kick)
        animate_secondary(p, f / float(n), impulse_time=since(f, windup_end, n))
        return p

    def smash(f, n):
        """Both arms up and back over the head, then down in front. A wall of a thing, winding up."""
        p = Pose()
        rear = over(f, 0, windup_end - 2)
        strike = over(f, windup_end, strike_end)
        recover = over(f, strike_end, n)

        for a, fa in arms:
            up = at_side[a].slerp(overhead[a], rear)
            p.orient(a, up.slerp(slam[a], strike) if strike < 1 else slam[a].slerp(at_side[a], recover))
            side_sign = 1.0 if side_of(a) > 0 else -1.0
            p.turn(fa, UP, -side_sign * 20.0 * rear * (1 - strike))
        lean(p, -14.0 * rear * (1 - strike) + 22.0 * strike * (1 - recover))
        drop = strike * (1 - recover)
        p.move(r["hips"], UP * (0.03 * H * rear * (1 - strike)) - UP * (0.07 * H * drop))
        for side in ("L", "R"):
            p.turn(r["thigh." + side], LATERAL, -18.0 * drop)
            p.turn(r["shin." + side], LATERAL, 34.0 * drop)
            p.turn(r["foot." + side], LATERAL, -16.0 * drop)
        curl_fingers(p, "fist")
        p.morph("jawOpen", 0.8 * strike)
        p.morph("viseme_aa", 0.6 * strike)
        animate_secondary(p, f / float(n), impulse_time=since(f, windup_end, n))
        return p

    clips["attack"] = (24, shoot if shooter else smash, False)

    def hit(f, n):
        # Bigger than the creatures' jolt. An upright body leaning back a dozen degrees barely
        # changes its outline side-on -- the first previews showed nothing at all -- so the
        # head snaps further, the hips are shoved back and the arms fly up.
        p = Pose()
        k = math.sin(math.pi * min(1.0, f / float(n))) * (1.0 - 0.3 * f / float(n))
        arms_down(p)
        lean(p, -22.0 * k)
        p.turn(r["head"], LATERAL, -24.0 * k)
        p.move(r["hips"], -FORWARD * (0.06 * H * k))
        for a, _ in arms:
            p.turn(a, LATERAL, -28.0 * k)
        curl_fingers(p, "splay", 0.8 * k)
        p.morph("eyeBlink_L", k * 0.8)
        p.morph("eyeBlink_R", k * 0.8)
        p.morph("jawOpen", k * 0.5)
        animate_secondary(p, f / float(n), impulse_time=since(f, 0, n))
        return p

    clips["hit"] = (8, hit, False)

    def die(f, n):
        """Knees go, then over backwards, flat. The hips carry everything, so it falls whole."""
        p = Pose()
        buckle = over(f, 0, 4)
        fall = over(f, 3, 13)
        arms_down(p)
        for a, _ in arms:
            p.turn(a, LATERAL, -50.0 * fall)                  # flung up past the head
        for side in ("L", "R"):
            p.turn(r["thigh." + side], LATERAL, -14.0 * buckle)
            p.turn(r["shin." + side], LATERAL, 26.0 * buckle * (1 - fall * 0.7))
        p.turn(r["hips"], LATERAL, -88.0 * fall)
        # Rotating about the hip joint leaves the body lying at hip height; bring it down to
        # the floor, and a little back, the way a falling body travels.
        p.move(r["hips"], -UP * (0.08 * H * buckle + 0.8 * H * fall) - FORWARD * (0.25 * H * fall))
        p.turn(r["head"], LATERAL, -10.0 * fall)
        curl_fingers(p, "relax", 0.4)
        p.morph("eyeBlink_L", min(1.0, fall * 1.2))
        p.morph("eyeBlink_R", min(1.0, fall * 1.2))
        p.morph("jawOpen", fall * 0.35)
        animate_secondary(p, f / float(n), impulse_time=since(f, 3, n))
        return p

    clips["die"] = (18, die, False)

    # ---- agility: jump, roll, block --------------------------------------------------------
    def jump_start(f, n):
        p = Pose()
        tau = f / float(n)
        st = gait.evaluate_biped_jump_start(tau, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, leg["foot_pitch"])

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "fist" if tau < 0.6 else "splay")
        animate_secondary(p, tau)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("jump_start", tau, stride, H)
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["jump_start"] = (12, jump_start, False)

    def jump_loop(f, n):
        p = Pose()
        phase = f / float(n)
        st = gait.evaluate_biped_jump_loop(phase, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, leg["foot_pitch"])

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "splay")
        animate_secondary(p, phase)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("jump_loop", root_t(n), stride, H)
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["jump_loop"] = (16, jump_loop, True)

    def jump_land(f, n):
        p = Pose()
        tau = f / float(n)
        st = gait.evaluate_biped_jump_land(tau, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, leg["foot_pitch"])

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "relax")
        animate_secondary(p, tau)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("jump_land", tau, stride, H)
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["jump_land"] = (14, jump_land, False)

    def roll(f, n):
        p = Pose()
        tau = f / float(n)
        st = gait.evaluate_biped_roll(tau, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, leg["foot_pitch"])

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "fist" if 0.2 < tau < 0.8 else "relax")
        animate_secondary(p, tau)
        if root_motion and rm_bone:
            rm = gait.compute_root_motion_displacement("roll", tau, stride, H)
            p.move(rm_bone, FORWARD * rm[1])
        return p

    clips["roll"] = (20, roll, False)

    def block(f, n):
        p = Pose()
        phase = f / float(n)
        st = gait.evaluate_biped_block(phase, H, H, gait_params, is_shooter=shooter)
        pelvis = st["pelvis"]
        legs_st = st["legs"]
        arms_st = st["arms"]
        spine_st = st["spine"]

        arms_down(p, bend=None)
        p.move(r["hips"], LATERAL * pelvis["pos"][0] + UP * pelvis["pos"][2] + FORWARD * pelvis["pos"][1])
        p.turn(r["hips"], LATERAL, pelvis["rot"][0])
        p.turn(r["hips"], UP, pelvis["rot"][2])

        if spine:
            num_spine = len(spine)
            for s in spine:
                p.turn(s, LATERAL, spine_st["pitch"] / num_spine)
                p.turn(s, UP, spine_st["yaw"] / num_spine)

        for side in ("L", "R"):
            leg = legs_st[side]
            p.turn(r["thigh." + side], LATERAL, leg["thigh_pitch"])
            p.turn(r["shin." + side], LATERAL, leg["knee_pitch"])
            p.turn(r["foot." + side], LATERAL, leg["foot_pitch"])

        for side in ("L", "R"):
            arm_b, fa_b = r["arm." + side], r["forearm." + side]
            side_sign = 1.0 if side_of(arm_b) > 0 else -1.0
            p.turn(arm_b, LATERAL, arms_st[side]["pitch"])
            p.turn(arm_b, UP, side_sign * arms_st[side]["yaw"])
            p.turn(fa_b, UP, -side_sign * arms_st[side]["forearm_pitch"])

        curl_fingers(p, "fist")
        animate_secondary(p, phase)
        return p

    clips["block"] = (16, block, True)

    return clips, {"windUpEnd": windup_end / 24.0, "stride": stride, "walkFrames": walk_frames,
                   "duty": walk_duty}


# ---------------------------------------------------------------------------------------------
# The Flyer set: fly, attack, hit, die
# ---------------------------------------------------------------------------------------------


class Flyer:
    """The Flyer set for any rig with a root, from whatever it has: wings, trailing chains,
    dangling IK legs, a bell, flames.

    The whole creature moves from the root rather than the body bone, so the legs and wings
    go with it; moving the body would leave the IK-held feet hanging where they were. Wings
    beat about the forward axis -- up and down in the plane the side-on camera sees -- root
    link first, outer links lagging so the tips whip rather than the wing hinging stiffly.
    """

    def __init__(self, rig, spec):
        self.rig = rig
        self.L = rig.size
        self.wings = [c for c in rig.chains(("wing",)) if c[1] != 0.0]
        self.trails = rig.chains(TRAILING)
        self.beats = int(spec.get("beats", 2))
        self.flap = float(spec.get("flap", 32.0))
        self.ripple = bool(spec.get("ripple", False))
        self.flicker = bool(spec.get("flicker", False))
        self.bell = spec.get("pulse") if spec.get("pulse") in rig.names else None
        self.loop = int(spec.get("loop", 24))
        self.root = rig.root or rig.body
        self.points = [v.copy() for b in rig.arm.data.bones for v in (b.head_local, b.tail_local)]
        self.floor = min(v.dot(UP) for v in self.points)

    def landing(self, pitch, roll):
        """How far to move the root so that, turned by `pitch` then `roll` about it, the lowest
        bone sits where the lowest bone stood at rest.

        A fixed drop does not work across rigs: one flyer's root is at its middle and another's
        is at its feet, so the same half-length fall left one lying on the floor and buried
        the other. This is the Walker's rule for its flip, done by measuring.
        """
        r = (Quaternion(FORWARD, math.radians(roll)) @ Quaternion(LATERAL, math.radians(pitch))).to_matrix()
        return self.floor - min((r @ v).dot(UP) for v in self.points)

    # ---- parts ----

    def beat(self, p, w, amount=1.0, lift=0.0, outer_lift=0.0):
        for bones, side in self.wings:
            # A moth's hind wing trails its fore wing slightly; a single pair is unaffected.
            late = 0.35 if "hind" in bones[0] else 0.0
            for i, b in enumerate(bones):
                lag = (0.9 if self.ripple else 0.5) * i + late
                amp = self.flap * amount * (1.0 if i == 0 else (0.7 if self.ripple else 0.4))
                held = lift if i == 0 else outer_lift
                p.turn(b, FORWARD, side * (amp * math.sin(w - lag) + held))

    def trail(self, p, t, amount, rate=1):
        for bones, side in self.trails:
            for i, b in enumerate(bones):
                ph = rate * t - 0.8 * (i + 1) + 0.6 * side
                p.turn(b, LATERAL, amount * (0.6 + 0.25 * i) * math.sin(ph))
                p.turn(b, UP, 0.4 * amount * (0.6 + 0.25 * i) * math.sin(ph + 1.1))

    def curl(self, p, amount, per_link):
        """Every trailing chain bent the same way: up behind the pivot for positive amounts."""
        for bones, _ in self.trails:
            for i, b in enumerate(bones):
                p.turn(b, LATERAL, amount + per_link * i)

    def glow(self, p, t, amount, extra=0.0):
        """Flames and wisps flare and gutter. Only with the spec's flicker=true: a creature whose light is the
        model."""
        if not self.flicker:
            return
        for k, (bones, _) in enumerate(self.trails):
            if bones[0].startswith(("flame", "wisp")):
                s = 1.0 + extra + amount * math.sin(3 * t + 1.7 * k)
                p.grow(bones[0], (s, s, s))

    def pulse(self, p, c):
        """The bell: c=1 fully contracted (narrow and tall), negative for relaxed wide."""
        if self.bell:
            p.grow(self.bell, (1 - 0.22 * c, 1 + 0.16 * c, 1 - 0.22 * c))

    def dangle(self, p, t, forward=0.0, up=0.0):
        """Legs hang a little behind, paddling slightly, the two sides opposite."""
        L = self.L
        for foot in self.rig.feet:
            ph = t + (math.pi if foot.get("group") else 0.0)
            p.move(foot["name"], FORWARD * (-0.04 * L + forward + 0.015 * L * math.sin(ph))
                   + UP * (0.02 * L + up + 0.01 * L * math.cos(ph)))

    # ---- clips ----

    def fly(self, f, n, amount=1.0):
        """The loop. The body rises on each downstroke -- a quarter beat behind the wing --
        which is what makes a beat look like it is holding something up."""
        p = Pose()
        L = self.L
        t = 2 * math.pi * f / n
        w = self.beats * t
        bob = (0.02 if self.wings else 0.035) * L * amount
        if self.bell:
            # Contract, rise; relax, sink. The rise lags the squeeze.
            c = 0.5 - 0.5 * math.cos(w)
            self.pulse(p, c * amount)
            p.move(self.root, UP * (0.05 * L * amount * math.sin(w - 1.2)))
        else:
            p.move(self.root, UP * (-bob * math.sin(w - 0.6)))
        p.turn(self.root, LATERAL, 3.0 * amount * math.sin(t))
        self.beat(p, w, amount)
        self.trail(p, t, 6.0 * amount, rate=max(1, self.beats))
        p.turn(self.rig.head, LATERAL, 3.0 * amount * math.sin(t + 0.5))
        self.dangle(p, t)
        self.glow(p, t, 0.12 * amount)
        return p

    def attack(self, f, n):
        """Rear back and up with the wings raised and shivering, then dive through.

        As with the Walkers, the rear can be scrubbed by a game's own telegraph and hold while
        the creature aims, so it is built to be the biggest outline change the rig can make:
        wings up high, body tipped back, everything trailing curled under.
        """
        p = Pose()
        L = self.L
        rear = over(f, 0, WINDUP_END - 2)
        strike = over(f, WINDUP_END, STRIKE_END)
        recover = over(f, STRIKE_END, n)
        hold = rear * (1 - strike)
        lunge = strike * (1 - recover)
        shiver = math.sin(f * 2.6) * over(f, WINDUP_END - 6, WINDUP_END) * (1 - strike)

        p.move(self.root, -FORWARD * (0.10 * L * hold) + UP * (0.06 * L * hold)
               + FORWARD * (0.22 * L * lunge) - UP * (0.04 * L * lunge))
        p.turn(self.root, LATERAL, -16.0 * hold + 20.0 * lunge + 1.5 * shiver)
        self.beat(p, 0.0, 0.0, lift=50.0 * hold - 40.0 * lunge + 6.0 * shiver,
                  outer_lift=12.0 * hold - 18.0 * lunge)
        self.curl(p, 10.0 * hold - 8.0 * lunge, 4.0 * hold - 3.0 * lunge)
        p.turn(self.rig.head, LATERAL, -10.0 * hold + 6.0 * lunge)
        self.dangle(p, 0.0, forward=0.06 * L * hold + 0.12 * L * lunge, up=0.05 * L * hold)
        self.glow(p, 0.0, 0.0, extra=0.35 * hold + 0.08 * shiver)
        self.pulse(p, hold - 0.4 * lunge)
        return p

    def hit(self, f, n):
        p = Pose()
        L = self.L
        k = math.sin(math.pi * min(1.0, f / float(n))) * (1.0 - 0.3 * f / float(n))
        p.move(self.root, -FORWARD * (0.06 * L * k) + UP * (0.03 * L * k))
        p.turn(self.root, LATERAL, -14.0 * k)
        self.beat(p, 0.0, 0.0, lift=-30.0 * k, outer_lift=-10.0 * k)
        self.curl(p, 8.0 * k, 2.0 * k)
        p.turn(self.rig.head, LATERAL, -12.0 * k)
        self.glow(p, 0.0, 0.0, extra=-0.3 * k)
        self.pulse(p, 0.8 * k)
        return p

    def die(self, f, n):
        """The wings stop, it tips nose-down and rolls belly-up as it drops, then lies still.

        An engine usually holds the last frame, so the final pose is what the player sees of the body.
        """
        p = Pose()
        L = self.L
        lose = over(f, 0, 4)
        fall = over(f, 2, 18)
        settle = math.sin(math.pi * over(f, 18, 23))
        p.turn(self.root, LATERAL, 55.0 * fall)
        p.turn(self.root, FORWARD, 120.0 * fall)
        p.move(self.root, UP * (self.landing(55.0, 120.0) * fall + 0.03 * L * settle))
        self.beat(p, 0.0, 0.0, lift=-45.0 * lose, outer_lift=-20.0 * fall)
        self.curl(p, 12.0 * fall, 3.0 * fall)
        p.turn(self.rig.head, LATERAL, 15.0 * fall)
        self.dangle(p, 0.0, forward=0.03 * L * fall, up=0.05 * L * fall)
        self.glow(p, 0.0, 0.0, extra=-0.5 * fall)
        if self.flicker:
            s = 1.0 - 0.3 * fall
            p.grow(self.rig.body, (s, s, s))
        self.pulse(p, -0.5 * fall)
        return p


def flyer_clips(rig, spec):
    fl = Flyer(rig, spec)
    clips = {
        "fly": (fl.loop, fl.fly, True),
        "attack": (24, fl.attack, False),
        "hit": (8, fl.hit, False),
        "die": (24, fl.die, False),
    }
    # Nothing walks, so no stride; the loop plays at its own pace however fast it travels.
    return clips, {"windUpEnd": WINDUP_END / 24.0, "stride": 0.0, "walkFrames": fl.loop}


# ---------------------------------------------------------------------------------------------
# The Exploder set: walk, arm, explode (and an idle)
# ---------------------------------------------------------------------------------------------

def exploder_clips(rig, spec):
    """A creature that arms and explodes: it needs an unmistakable arming telegraph.

    The arm clip is meant to be scrubbed, start to end, by the fuse rather than played, so
    its timing is the fuse's and it is keyed linearly: it hunkers down and braces in the
    first part, then shakes harder and harder to the end. A game's own swell or strobe can
    run on top of it. Explode is the death, whichever way it came: a pop outward, then a
    collapse flat.
    """
    L = rig.size
    legged = bool(rig.feet)
    fl = Flyer(rig, spec)
    spine = sorted((n for n in rig.names if n.startswith("spine")), key=trailing_number)
    neck = first(rig.names, ("neck",))
    body = rig.body

    if legged:
        walker, facts = creature_walker(rig, "bite")
        clips = {"idle": walker["idle"], "walk": walker["walk"]}
    else:
        # A ceiling-hanger: a slow half-strength flutter standing still, the full loop moving.
        clips = {"idle": (48, lambda f, n: fl.fly(f, n, 0.45), True), "walk": (24, fl.fly, True)}
        facts = {"windUpEnd": 0.5, "stride": 0.0, "walkFrames": 24}

    def arm(f, n):
        p = Pose()
        a = f / float(n - 1)
        brace = ease(min(1.0, a * 2.5))
        shake = a * a
        j1 = math.sin(f * 2.9)
        j2 = math.sin(f * 3.7 + 1.0)

        p.move(body, -UP * (0.09 * L * brace) + UP * (0.015 * L * shake * j1))
        p.turn(body, FORWARD, 6.0 * shake * j2)
        p.turn(body, LATERAL, 5.0 * shake * j1)

        # Back humped, head down low: the shoulders lift and the neck curls under. The first
        # preview at half these read as a creature stretching, not one about to go off.
        for i, s in enumerate(spine):
            frac = i / float(max(1, len(spine) - 1))
            p.turn(s, LATERAL, brace * (-22.0 + 36.0 * frac))
        p.turn(neck, LATERAL, 18.0 * brace)
        p.turn(rig.head, LATERAL, 10.0 * brace - 5.0 * shake * j2)

        # Tail curled up over the back, scorpion-like. A tail that hangs straight down at
        # rest so a modest lift per link only swings it back to horizontal -- which the first
        # preview did, a stiff straight tail. It needs a big turn at the base to get it
        # up at all before the outer links can curl it over.
        for i, b in enumerate(rig.tail):
            p.turn(b, LATERAL, (30.0 + 8.0 * i) * brace + 5.0 * shake * j1)

        # Stance widened along the body, front feet forward and hind feet back: braced.
        for foot in rig.feet:
            ahead = 1.0 if foot["name"] in rig.front else -1.0
            p.move(foot["name"], Vector((foot["rest"].x * 0.35, 0.0, 0.0)) * brace
                   + FORWARD * (0.04 * L * ahead * brace))

        if not legged:
            # Wings clamped down around it, everything trailing curled up tight.
            fl.beat(p, 0.0, 0.0, lift=-35.0 * brace + 8.0 * shake * j1, outer_lift=-15.0 * brace)
            fl.curl(p, 12.0 * brace, 6.0 * brace)
        return p

    def explode(f, n):
        p = Pose()
        burst = over(f, 0, 3) * (1 - over(f, 3, 7))
        slump = over(f, 4, 14)
        bounce = math.sin(math.pi * over(f, 14, 18)) * 0.08
        pop = 1.0 + 0.25 * burst
        p.grow(body, (pop, pop, pop))

        if legged:
            h = rig.rest[body].to_translation().dot(UP) if body else 0.2 * L
            p.move(body, UP * (0.10 * L * burst) - UP * (h * (0.7 * slump - bounce)))
            p.turn(body, LATERAL, -18.0 * burst + 6.0 * slump)
            p.turn(rig.head, LATERAL, -25.0 * burst + 20.0 * slump)
            # The tail is lifted as the body sinks, so it ends lying out along the floor behind: curled down under a
            # sunk body it went through the floor, and a corpse lifted out of the floor rested on its tip.
            for i, b in enumerate(rig.tail):
                p.turn(b, LATERAL, (18.0 + 4.0 * i) * burst + (TAIL_LIE + 2.0 * i) * slump)
            # Legs thrown out and left splayed, so the collapse reads as flat, not crouched.
            for foot in rig.feet:
                ahead = 1.0 if foot["name"] in rig.front else -1.0
                p.move(foot["name"], Vector((foot["rest"].x * 0.8, 0.0, 0.0)) * slump
                       + UP * (0.08 * L * burst) + FORWARD * (0.10 * L * ahead * slump))
        else:
            root = rig.root or body
            p.turn(root, LATERAL, 70.0 * slump)
            p.turn(root, FORWARD, 120.0 * slump)
            p.move(root, UP * (fl.landing(70.0, 120.0) * slump))
            fl.beat(p, 0.0, 0.0, lift=50.0 * burst - 40.0 * slump, outer_lift=-15.0 * slump)
            fl.curl(p, 12.0 * slump - 20.0 * burst, 3.0 * slump)
        return p

    clips["arm"] = (24, arm, False)
    clips["explode"] = (20, explode, False)
    return clips, facts


# ---------------------------------------------------------------------------------------------
# The swimmer: the Walker's clip names, played by a wave down one long chain
# ---------------------------------------------------------------------------------------------

def swimmer_clips(rig, spec):
    """A serpent. Its rig is one chain -- head, neck, body_N, tail_N -- hung from the head.

    A travelling wave of bend down the chain: each link turns a little, the turn arriving
    later at each link and growing toward the tail, so the curve moves backward along the
    body while the head stays steady. The wave is mostly vertical, about the lateral axis,
    because the camera is side-on: a real serpent's side-to-side swim is a wave pointed straight
    at the lens, which is why the procedural motion made it look like a plank. A third as
    much sideways goes with it, so it is not flat from a tilted camera.
    """
    L = rig.size
    head = rig.head
    spine = rig.chain_from(head)
    N = max(1, len(spine))
    # A serpent may rest reared up, head highest and tail along the floor, so the head can be well
    # above the lowest point of the body.
    floor = min(v.dot(UP) for b in rig.arm.data.bones for v in (b.head_local, b.tail_local))
    head_height = (rig.rest[head].to_translation().dot(UP) - floor) if head else 0.0

    def wave(p, phase, amp, k=0.85):
        for i, b in enumerate(spine):
            g = 0.45 + 0.55 * i / float(max(1, N - 1))
            p.turn(b, LATERAL, amp * g * math.sin(phase - k * i))
            p.turn(b, UP, 0.35 * amp * g * math.sin(phase - k * i + 0.6))

    def s_curve(p, amount):
        for i, b in enumerate(spine):
            p.turn(b, LATERAL, amount * math.sin(2 * math.pi * i / float(N)))

    clips = {}

    def idle(f, n):
        p = Pose()
        t = 2 * math.pi * f / n
        wave(p, t, 6.0)
        p.move(head, UP * (0.01 * L * math.sin(t)))
        p.turn(head, LATERAL, 1.5 * math.sin(t + 0.8))
        return p

    clips["idle"] = (48, idle, True)

    def swim(f, n):
        p = Pose()
        t = 2 * math.pi * f / n
        # 9 degrees a link was invisible in the first preview; the tail barely stirred.
        wave(p, t, 16.0, k=0.7)
        p.move(head, UP * (0.015 * L * math.sin(2 * t)))
        p.turn(head, LATERAL, 2.0 * math.sin(t + 1.5))
        return p

    clips["walk"] = (24, swim, True)

    def attack(f, n):
        """Drawn back into an S, head raised; then the head drives forward and the S snaps
        straight, the tail whipping behind it."""
        p = Pose()
        rear = over(f, 0, WINDUP_END - 2)
        strike = over(f, WINDUP_END, STRIKE_END)
        recover = over(f, STRIKE_END, n)
        hold = rear * (1 - strike)
        lunge = strike * (1 - recover)
        s_curve(p, 25.0 * hold)
        wave(p, f * 1.2, 12.0 * lunge)
        p.move(head, -FORWARD * (0.15 * L * hold) + UP * (0.08 * L * hold) + FORWARD * (0.30 * L * lunge))
        p.turn(head, LATERAL, -14.0 * hold + 12.0 * lunge)
        return p

    clips["attack"] = (24, attack, False)

    def hit(f, n):
        p = Pose()
        k = math.sin(math.pi * min(1.0, f / float(n))) * (1.0 - 0.3 * f / float(n))
        p.move(head, -FORWARD * (0.05 * L * k) + UP * (0.02 * L * k))
        p.turn(head, LATERAL, -10.0 * k)
        for i, b in enumerate(spine):
            p.turn(b, LATERAL, 14.0 * k * math.sin(math.pi * i / float(N)))
        return p

    clips["hit"] = (8, hit, False)

    def die(f, n):
        """Writhes hard, then goes limp in a loose curl, rolled onto its side, and stays."""
        p = Pose()
        writhe = 1.0 - over(f, 6, 14)
        limp = over(f, 8, 18)
        wave(p, f * 1.1, 14.0 * writhe)
        for b in spine:
            p.turn(b, LATERAL, 7.0 * limp)
        p.turn(head, FORWARD, 80.0 * limp)
        # Rolled about the head, the body swings out level with it; bring it all down to the
        # floor, or it lies dead in mid-air at the height the head was reared to.
        p.move(head, -UP * (0.9 * head_height * limp))
        return p

    clips["die"] = (24, die, False)

    # One swim cycle carries the body about 0.6 of its length.
    return clips, {"windUpEnd": WINDUP_END / 24.0, "stride": 0.3 * L, "walkFrames": 24}




TOPPLE = 80.0     # the whole plant goes over from its root
SAG = 25.0        # the stalk sags toward the floor as it lands
POD_LIE = 40.0    # and the pod turns back onto its side, so the stalk lies between pod and roots


def turret_clips(rig, spec):
    """A rooted thing that attacks from where it stands (a plant pod: base > stalk > pod, tendrils at its foot).

    No walk. The wind-up is the read: the stalk draws back and the pod swells, so "about to fire" is a change of
    outline side-on; the strike snaps it forward and squeezes the pod; death wilts it over and the pod shrinks."""
    stalk = [b for b in rig.skeleton.get("spine", []) if b in rig.names][1:] or [b for b in rig.names if b.startswith(("stalk", "spine"))]
    pod = rig.head if rig.head else ([b for b in rig.names if b.startswith("pod")] or [None])[0]
    if pod in stalk: stalk.remove(pod)
    tendrils = rig.chains(("tendril", "tentacle"))
    L = rig.size

    def ripple(p, t, amount):
        for k, (bones, side) in enumerate(tendrils):
            for i, b in enumerate(bones):
                p.turn(b, LATERAL, amount * (0.6 + 0.3 * i) * math.sin(t - 0.9 * i + 1.3 * k))

    def idle(f, n):
        p = Pose(); t = 2 * math.pi * f / n
        for i, s in enumerate(stalk): p.turn(s, LATERAL, 2.5 * math.sin(t - 0.6 * i)); p.turn(s, FORWARD, 1.5 * math.sin(0.5 * t))
        b = 1.0 + 0.03 * math.sin(2 * t)
        p.grow(pod, (b, b, b))
        ripple(p, t, 5.0)
        return p

    def attack(f, n):
        p = Pose()
        rear = over(f, 0, WINDUP_END - 2)
        strike = over(f, WINDUP_END, STRIKE_END)
        recover = over(f, STRIKE_END, n)
        hold = rear * (1 - strike); lunge = strike * (1 - recover)
        shiver = math.sin(f * 2.7) * over(f, WINDUP_END - 5, WINDUP_END) * (1 - strike)
        for i, s in enumerate(stalk):
            p.turn(s, LATERAL, 16.0 * hold - 22.0 * lunge + 1.5 * shiver)
        sw = 1.0 + 0.25 * hold - 0.18 * lunge
        p.grow(pod, (sw, 1.0 + 0.1 * hold, sw))
        p.turn(pod, LATERAL, 10.0 * hold - 14.0 * lunge)
        for bones, side in tendrils:
            for i, b in enumerate(bones): p.turn(b, LATERAL, (-12.0 - 5.0 * i) * hold + 6.0 * lunge)
        return p

    def hit(f, n):
        p = Pose(); k = math.sin(math.pi * min(1.0, f / float(n))) * (1.0 - 0.3 * f / float(n))
        for s in stalk: p.turn(s, LATERAL, 10.0 * k)
        p.grow(pod, (1 - 0.08 * k, 1 - 0.08 * k, 1 - 0.08 * k))
        ripple(p, 0.0, 6.0 * k)
        return p

    def die(f, n):
        """Over backwards onto its side, the whole plant, roots and all; the stalk sags and the pod shrinks.

        Wilting the stalk alone from a planted base left the pod hanging on a bent trunk, and a game that keeps a
        corpse's lowest point on the floor then stood it on its crown with the trunk in the air. Toppled from the
        root, trunk and pod lie along the floor (make_clips' grounding puts the lowest point on it)."""
        p = Pose(); wilt = over(f, 2, 18); bounce = math.sin(math.pi * over(f, 18, 22)) * 0.08
        if rig.root:
            p.turn(rig.root, LATERAL, -TOPPLE * (wilt - bounce))
        for i, s in enumerate(stalk): p.turn(s, LATERAL, -(SAG + 4.0 * i) * wilt)
        p.turn(pod, LATERAL, POD_LIE * wilt)
        sh = 1.0 - 0.2 * wilt
        p.grow(pod, (sh, sh, sh))
        for bones, side in tendrils:
            for i, b in enumerate(bones): p.turn(b, LATERAL, 18.0 * wilt + 6.0 * i * wilt)
        return p

    clips = {"idle": (48, idle, True), "attack": (24, attack, False), "hit": (8, hit, False), "die": (24, die, False)}
    return clips, {"windUpEnd": WINDUP_END / 24.0, "stride": 0.0, "walkFrames": 48}


def side_fall(rig):
    """A four-legged beast's death: the legs give, it goes over onto its flank, the legs fold, it lies still. A
    beetle's flip onto its back (the walker's die) reads as a dog on its back in the floor."""
    L = rig.size
    half_w = 0.5 * max((abs(v.x) for b in rig.arm.data.bones for v in (b.head_local, b.tail_local)), default=0.1)

    def die(f, n):
        p = Pose()
        give = over(f, 0, 5); fall = over(f, 3, 14); bounce = math.sin(math.pi * over(f, 14, 18)) * 0.08
        if rig.root:
            p.turn(rig.root, FORWARD, 88.0 * (fall - bounce))
            p.move(rig.root, UP * (half_w * 1.2 * fall) - UP * (0.04 * L * give * (1 - fall)))
        for foot in rig.feet:
            p.move(foot["name"], UP * (0.06 * L * fall) + Vector((-foot["rest"].x * 0.4, 0, 0)) * fall
                   + FORWARD * (0.03 * L * (1 if foot["name"] in rig.front else -1) * fall))
        p.turn(rig.head, LATERAL, 12.0 * fall)
        for i, b in enumerate(rig.tail): p.turn(b, UP, 6.0 * fall)
        for j in rig.jaws: turn_jaw(p, j, 10.0 * fall)
        return p
    return die


def machine_clips(rig, spec):
    """A machine of rigid parts (rig.json rigid_parts): each moving part spins about its own bone, whose head is the
    part's hub and whose length is its axle. `spin` {bone: turns per loop} turns all the time (fans); `work` {bone:
    turns per loop} turns as well while it works (a drill). Whole turns a loop, so the loops are seamless. Clips:
    idle (spin), work (spin and work), both loops of `loop` frames (default 24)."""
    n = int(spec.get("loop", 24))
    spin = {b: float(t) for b, t in spec.get("spin", {}).items() if b in rig.names}
    work = {b: float(t) for b, t in spec.get("work", {}).items() if b in rig.names}

    def turning(parts):
        def fn(f, frames):
            p = Pose()
            for b, turns in parts.items():
                p.turn(b, rig.direction(b), 360.0 * turns * f / float(frames))
            return p
        return fn
    both = dict(spin); both.update({b: both.get(b, 0.0) + t for b, t in work.items()})
    clips = {"idle": (n, turning(spin), True), "work": (n, turning(both), True)}
    return clips, {"windUpEnd": 0.0, "stride": 0.0, "walkFrames": n}


CREATURE = {"walker": walker_clips, "quadruped": walker_clips, "flyer": flyer_clips, "exploder": exploder_clips, "swimmer": swimmer_clips,
            "turret": turret_clips, "machine": machine_clips}


class Authored:
    """A creature archetype with the interface the build and export below share with Winged: clips(), events(),
    windup_end(), notes(). Names follow the export format (docs/FORMATS.md): "death", not "die";
    "attack_windup" is the attack's wind-up to full, held; "strike" is the release onward. The full "attack" stays,
    for an engine that scrubs its wind-up from its own telegraph timer (windUpEnd)."""

    def __init__(self, rig, spec):
        self.rig, self.spec = rig, spec
        self.raw, self.facts = CREATURE[spec["archetype"]](rig, spec)

    def clips(self):
        out = {}
        for name, (frames, fn, loops) in self.raw.items():
            out["death" if name == "die" else name] = (frames, fn, loops)
        if (self.rig.skeleton or {}).get("archetype") == "quadruped" and self.rig.feet and "death" in out:
            out["death"] = (22, side_fall(self.rig), False)
        if self.spec["archetype"] == "flyer" and "idle" not in out:
            fl = Flyer(self.rig, self.spec)
            out["idle"] = (fl.loop * 2, lambda f, n: fl.fly(f, n, 0.5), True)   # a hover: the beat at half strength
        if "attack" in out:
            frames, fn, loops = out["attack"]
            out["attack_windup"] = (WINDUP_END, lambda f, n, fn=fn, fr=frames: fn(min(f, WINDUP_END - 1), fr), False)
            out["strike"] = (frames - WINDUP_END + 2, lambda f, n, fn=fn, fr=frames: fn(WINDUP_END - 2 + f, fr), False)
        return out

    def events(self, made):
        s = lambda frames: frames / float(CREATURE_FPS)
        ev = {}
        for name, last, loops in made:
            length = s(last if loops else last + 1)
            if name == "walk" and self.rig.feet:
                ev[name] = [{"name": "footfall", "time": round(s(last) * (0.5 * g), 4),
                             "detail": ",".join(f["name"][3:] for f in self.rig.feet if f["group"] == g)} for g in (0, 1)]
            elif name == "attack":
                ev[name] = [{"name": "windup_full", "time": s(WINDUP_END - 2)}, {"name": "strike_release", "time": s(WINDUP_END)},
                            {"name": "strike_impact", "time": s(STRIKE_END)}]
            elif name == "attack_windup":
                ev[name] = [{"name": "windup_full", "time": round(length, 4)}]
            elif name == "strike":
                ev[name] = [{"name": "strike_release", "time": s(2)}, {"name": "strike_impact", "time": s(STRIKE_END - WINDUP_END + 2)}]
            elif name == "hit":
                ev[name] = [{"name": "hit", "time": 0.0}]
            elif name in ("death", "explode"):
                ev[name] = [{"name": "death_rest", "time": round(length, 4)}]
            elif name == "jump_start":
                ev[name] = [{"name": "jump_launch", "time": round(s(6), 4)}]
            elif name == "jump_land":
                ev[name] = [{"name": "land_impact", "time": 0.0}, {"name": "land_recover", "time": round(s(5), 4)}]
            elif name == "roll":
                ev[name] = [{"name": "roll_contact", "time": round(s(4), 4)}, {"name": "roll_recover", "time": round(s(16), 4)}]
            elif name == "block":
                ev[name] = [{"name": "block_brace", "time": 0.0}]
            elif name == "arm":
                ev[name] = [{"name": "armed", "time": round(length, 4)}]
        return ev

    def windup_end(self):
        # a fraction of the attack clip: an engine can scrub attack[0 .. windUpEnd * length] from its telegraph
        frames = self.raw.get("attack", (24,))[0]
        return WINDUP_END / float(frames)

    def notes(self):
        return {"archetype": self.spec["archetype"], "source": "Autorig Workbench make_clips.py (creature archetypes)"}


def author(key, spec, argv):
    """The creature path: author from the rig in rigged/, write clips/ in the model folder."""
    pack = layout.pack_dir(key)
    cp = os.path.join(pack, "model.json")
    if os.path.exists(cp):
        card = json.load(open(cp, encoding="utf-8"))
    else:
        # a model with no card: the rig's own QA log carries its bone roles, and its size is its own
        card = {"rig": {}}
        q = os.path.join(layout.work_dir("qa"), key + ".json")
        if os.path.exists(q):
            card["rig"]["skeleton"] = json.load(open(q, encoding="utf-8")).get("skeleton")
    blend = os.path.join(layout.rigged_dir(key), layout.leaf(key) + ".blend")
    bpy.ops.wm.open_mainfile(filepath=blend)
    for o in [o for o in bpy.data.objects if o.name.startswith("qa_")]: bpy.data.objects.remove(o, do_unlink=True)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    mesh = next(o for o in bpy.data.objects if o.type == 'MESH' and o.find_armature() == arm)
    bpy.context.scene.render.fps = CREATURE_FPS
    rig = CreatureRig(arm, card, spec.get("body"))
    warnings = []
    if not rig.feet and any(n.split(":")[-1].lower().startswith("leg") for n in rig.names) and             spec.get("archetype", "walker") in ("walker", "quadruped"):
        # the walk moves feet by their IK controls: a rig built with no IK on its legs (a placed chain without
        # "ik": true) gets the legless heave, the body rocking while the legs hang, and every foot slides
        warnings.append("legs without IK controls: the walk cannot step them (give the leg chains \"ik\": true "
                        "in rig.json and rig again)")
        print("MAKE_CLIPS warning: %s: %s" % (key, warnings[-1]))
    arche = Authored(rig, spec)
    clips = arche.clips()
    made = build(rig, clips)          # IK stays live: feet are placed by their targets and the export bakes the result
    grounded = ground_deaths(rig, mesh, clips, made)
    if grounded:
        made = build(rig, clips)

    out = os.path.join(pack, "clips")
    os.makedirs(out, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, key + "_clips.blend"), check_existing=False, copy=True)

    budget = layout.budget(key) or 10 ** 9   # None: a model kept at full resolution
    dup, dec = decimated_copy(mesh, budget)
    fbx = os.path.join(out, key + ".fbx")
    export_fbx(fbx, [arm, dup], 1.0, True)

    co = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
    longest = max(max(p[k] for p in co) - min(p[k] for p in co) for k in range(3)) if co else 1.0
    longest = max(1e-4, float(longest))
    metres = max(1e-4, float(card.get("metres") or longest))
    per_metre = longest / metres
    ev = arche.events(made)
    facts = arche.facts
    walk_s = facts["walkFrames"] / float(CREATURE_FPS)
    # a planted foot goes back one stride in `duty` of the cycle, so the body goes stride / (duty x cycle): twice
    # the stride per cycle only when duty is a half (the 2-group gait)
    duty_f = float(facts.get("duty", 0.5)) or 0.5
    speed_m = facts["stride"] / per_metre / (duty_f * walk_s) if facts.get("stride") else None
    data = {
        "format": "autorig-clips/1",
        "model": key, "display": spec.get("display") or key.replace("_", " ").title(),
        "category": spec.get("category") or "Characters",
        "fbx": key + ".fbx", "blend": key + "_clips.blend",
        "rig": "%s/%s.fbx" % (layout.rig_folder(key), key),
        "units": "the model's own units, as rigged/<model>.fbx (Y up in the FBX, facing +Z); unitsPerMetre converts",
        "metres": metres, "unitsPerMetre": round(per_metre, 6),
        "fps": CREATURE_FPS, "archetype": spec.get("archetype", "walker"),
        "skeleton": rig.skeleton or None,
        **clip_contract.header(),
        # each clip says its slot, whether its rate follows ground speed, its speed and its own wind-up end
        # (core/clip_contract.py, docs/FORMATS.md "The engine contract")
        "clips": [{"name": n, "take": n, "frames": (last if loops else last + 1),
                   "seconds": round((last if loops else last + 1) / float(CREATURE_FPS), 4), "loops": loops,
                   **clip_contract.clip_fields(n, (last if loops else last + 1) / float(CREATURE_FPS),
                                               [m[0] for m in made], arche.windup_end(),
                                               speed_m if n == "walk" else None),
                   "events": ev.get(n, [])} for n, last, loops in made],
        "windUpEnd": round(arche.windup_end(), 4),              # a fraction of "attack" (each clip has its own too)
        # walkSpeed: the walk's speed in metres a second, as the walk clip's "speed". It was the stride times metres
        # per cycle, right only when the rig's longest side was 1 unit.
        "walkSpeed": round(speed_m, 4) if speed_m else 1.0,
        **({"warnings": warnings} if warnings else {}),
        "decimation": dec,
        "licence": LICENCE,
    }
    with open(os.path.join(out, key + "_clips.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1)
    if "--preview" in argv:
        preview(Rig(arm, mesh), dup, mesh, made, argv[argv.index("--preview") + 1])
    print("CLIPS " + json.dumps({"model": key, "clips": [m[0] for m in made], "fbx": os.path.relpath(fbx, layout.ROOT),
                                 "feet": len(rig.feet), "tail": len(rig.tail), "jaws": rig.jaws, "decimation": dec}))


# ---------------------------------------------------------------------------------------------------------------
# Build, save, export
# ---------------------------------------------------------------------------------------------------------------

def mute_constraints(arm):
    for pb in arm.pose.bones:
        for c in pb.constraints: c.mute = True


def build(rig, clips):
    arm = rig.arm
    arm.animation_data_create()
    # Retargeted mocap (retarget_worker.py tags its actions) survives a rebake and ships with the clips, unless an
    # authored clip of the same name replaces it; every other action is rebuilt from scratch.
    kept = [a for a in bpy.data.actions if a.get("autorig_retarget") and a.name not in clips]
    for a in list(bpy.data.actions):
        if a not in kept: bpy.data.actions.remove(a)
    made = []
    # Morph targets: each clip keys its blinks, jaw and visemes into a shape-key action of its own (<clip>_morph),
    # laid on an NLA track named for the clip beside the armature's, so a clip plays its own face. They all used to
    # key the mesh's one shape-key action, and every clip overwrote the one before it.
    mesh = getattr(rig, "mesh", None)
    keys = mesh.data.shape_keys if mesh is not None and getattr(mesh.data, "shape_keys", None) else None
    morph_actions = {}
    if keys is not None:
        keys.animation_data_create()
    for name, (frames, fn, loops) in clips.items():
        action = bpy.data.actions.new(name)
        action.use_fake_user = True
        arm.animation_data.action = action
        if keys is not None:
            ka = bpy.data.actions.new(name + "_morph")
            ka.use_fake_user = True
            keys.animation_data.action = ka
            morph_actions[name] = ka
        last = frames if loops else frames - 1           # a loop's last key is its first again: no hitch at the seam
        # live IK (creature rigs) keyed on in every authored clip: a retargeted clip keys it off (retarget_worker),
        # and a channel one action keys and another does not keeps whatever was played last
        for pb in arm.pose.bones:
            for c in pb.constraints:
                if not c.mute and c.type in ("IK", "COPY_ROTATION", "DAMPED_TRACK", "LOCKED_TRACK", "TRACK_TO"):
                    c.influence = 1.0
                    c.keyframe_insert("influence", frame=0)
        global _KEYS
        _KEYS = None if os.environ.get("AUTORIG_SLOW_KEYS") else {}     # AUTORIG_SLOW_KEYS=1: the old way, to compare
        try:
            for f in range(0, last + 1):
                KEY["f"] = f                               # root motion reads the unwrapped key (root_t)
                apply(rig, fn(f % frames if loops else f, frames), f)
            if _KEYS:
                flush_keys(action, arm, _KEYS)
        finally:
            _KEYS = None
        action.use_frame_range = True
        action.frame_start, action.frame_end = 0, last
        made.append((name, last, loops))
    if keys is not None:
        keys.animation_data.action = None
        for t in list(keys.animation_data.nla_tracks): keys.animation_data.nla_tracks.remove(t)
        for name, ka in morph_actions.items():
            tr = keys.animation_data.nla_tracks.new(); tr.name = name
            st = tr.strips.new(name, 0, ka); st.name = name
    for a in kept:
        lo, hi = a.frame_range
        made.append((a.name, int(round(hi - lo)), False))
    arm.animation_data.action = None
    # One NLA track per clip, so the exporter writes each as a take named exactly for the clip (with every action
    # it would name them "<armature>|<clip>").
    ad = arm.animation_data
    for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)
    for name, last, loops in made:
        tr = ad.nla_tracks.new(); tr.name = name
        st = tr.strips.new(name, 0, bpy.data.actions[name])
        st.name = name
        tr.mute = False
    rest(rig)
    return made


TAIL_LIE = 18.0
RESTING = ("death", "explode")   # clips whose last frame is how the body lies, and stays


def lowest_point(mesh):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg); me = ev.to_mesh()
    n = len(me.vertices)
    if n:
        co = np.empty(n * 3, dtype=np.float32); me.vertices.foreach_get("co", co)
        m = np.array(mesh.matrix_world, dtype=np.float64)
        z = float((co.reshape(-1, 3).astype(np.float64) @ m[2, :3] + m[2, 3]).min())
    else:
        z = min((mesh.matrix_world @ v.co).z for v in me.vertices)
    ev.to_mesh_clear()
    return z


def ground_deaths(rig, mesh, clips, made):
    """A standing creature's death ends lying on the floor it stood on: its lowest point, measured on the skinned mesh
    in the last frame, where its lowest point was at rest. Poses are authored from bones, and a body lowered by its
    hip height with a tail curled under it can end with the tail tip below the floor;
    a game that lifts a corpse out of the floor then stood it on that tail tip. Clips that end off the floor get the
    difference as a root move. Only for models that stand on z=0 (flyers are centred and fall
    in the engine). Returns the clips it changed; `clips` is updated in place, to be built again."""
    if not rig.root:
        return {}
    ad = rig.arm.animation_data
    muted = [(t, t.mute) for t in ad.nla_tracks]     # the NLA tracks would stack every clip on the one measured
    for t, _ in muted: t.mute = True
    rest(rig)
    floor = lowest_point(mesh)
    size = rig.size or 1.0
    changed = {}
    for name, last, loops in made:
        if abs(floor) > 0.02 * size: break
        if name not in RESTING or loops:
            continue
        ad.action = bpy.data.actions[name]
        under = []
        for f in range(last + 1):
            bpy.context.scene.frame_set(f)
            bpy.context.view_layer.update()
            under.append(floor - lowest_point(mesh))
        d = under[-1]
        if abs(d) < 0.005 * size:
            continue
        # Frame by frame: a body going under the floor is lifted as far as it has gone under, so it never sinks on
        # the way down either; one that would end above the floor is lowered onto it, eased in over the clip.
        lift = [max(0.0, u) if d > 0 else d * over(f, 0, last) for f, u in enumerate(under)]
        frames, fn, lp = clips[name]
        def lifted(f, n, fn=fn, lift=lift):
            p = fn(f, n)
            p.move(rig.root, UP * lift[min(f, len(lift) - 1)])
            return p
        clips[name] = (frames, lifted, lp)
        changed[name] = round(d / size, 4)
    for t, m in muted: t.mute = m
    rest(rig)
    if changed:
        print("GROUNDED " + json.dumps(changed))
    return changed


def rest(rig):
    rig.arm.animation_data.action = None
    for pb in rig.pose:
        pb.location = Vector(); pb.rotation_quaternion = Quaternion(); pb.scale = Vector((1, 1, 1))
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()


def decimated_copy(mesh, target):
    """A copy of the mesh at the engine budget, weights limited to 4 a vertex and normalised after the cut
    (collapse interpolates the groups, and can leave five)."""
    bpy.ops.object.select_all(action='DESELECT')
    mesh.select_set(True); bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.duplicate()
    dup = bpy.context.active_object
    # the exported mesh keeps the rig's mesh name (the full-resolution one is only renamed in memory)
    keep = mesh.name; mesh.name = keep + "_full"; dup.name = keep; dup.data.name = keep
    dup.data.calc_loop_triangles(); before = len(dup.data.loop_triangles)
    skipped = None
    if before > target and getattr(dup.data, "shape_keys", None):
        # Blender cannot apply a modifier to a mesh with shape keys: the engine copy keeps its morph targets
        # (morph_targets: true asked for them) at full resolution rather than failing the whole step
        skipped = "shape keys: decimation skipped, the morph targets kept"
    elif before > target:
        mod = dup.modifiers.new("Decimate", 'DECIMATE')
        mod.decimate_type = 'COLLAPSE'; mod.use_collapse_triangulate = True; mod.ratio = target / float(before)
        while dup.modifiers[0] != mod: bpy.ops.object.modifier_move_up(modifier=mod.name)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    dup.data.calc_loop_triangles()
    worst = max((sum(1 for g in v.groups if g.weight > 0) for v in dup.data.vertices), default=0)
    out = {"triangles_before": before, "triangles": len(dup.data.loop_triangles), "max_influences": worst}
    if skipped:
        out["note"] = skipped
    return dup, out


def export_fbx(path, objs, scale, animated):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    kinds = {o.type for o in objs}
    # The FBX exporter solos each take's NLA strip on objects only, never on a mesh's shape keys: every clip's morph
    # track would play at once and the top one (the last clip's face) would go into every take. They are muted for
    # the export, so takes carry a neutral face; the per-clip faces stay in the clips .blend.
    # Their values are zeroed as well: unanimated, a key holds whatever the last frame evaluated left in it.
    muted, values = [], []
    for o in objs:
        sk = getattr(o.data, "shape_keys", None) if o.type == 'MESH' else None
        if sk is None:
            continue
        if sk.animation_data:
            for t in sk.animation_data.nla_tracks:
                muted.append((t, t.mute)); t.mute = True
        for kb in sk.key_blocks[1:]:                 # [0] is the basis
            values.append((kb, kb.value)); kb.value = 0.0
    try:
        _write_fbx(path, kinds, scale, animated)
    finally:
        for t, was in muted: t.mute = was
        for kb, v in values: kb.value = v


def _write_fbx(path, kinds, scale, animated):
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types=kinds, global_scale=scale,
        apply_scale_options='FBX_SCALE_UNITS', bake_space_transform=True,
        axis_forward='-Z', axis_up='Y', add_leaf_bones=False, use_armature_deform_only=True,
        bake_anim=animated, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=animated,
        bake_anim_use_all_bones=True, bake_anim_force_startend_keying=True,
        bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
        mesh_smooth_type='OFF', path_mode='STRIP', embed_textures=False)


def texture_of(mesh):
    for s in mesh.material_slots:
        m = s.material
        if m and m.use_nodes:
            for nd in m.node_tree.nodes:
                if nd.type == 'TEX_IMAGE' and nd.image: return nd.image
    return None


def bone_map(rig, arche):
    """Roles to bones, for a reader that wants "the head" or "the left wing" without parsing names."""
    m = {"root": "root", "spine": arche.spine, "neck": arche.neck, "head": arche.head, "jaw": arche.jaw_b,
         "tail": arche.tails}
    for s, _ in arche.sides:
        k = "left" if s == ".L" else "right"
        m["leg_" + k] = arche.legs[s]
        m["arm_" + k] = arche.arms[s]
        m["wing_" + k] = rig.chain("wing", s)
        m["wing_fingers_" + k] = [rig.chain("wing_finger%d" % i, s) for i in (1, 2, 3) if rig.chain("wing_finger%d" % i, s)]
    return m


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv: sys.exit("usage: blender -b --python autorig/steps/make_clips.py -- <model> [--preview dir] [--split-clips dir]")
    key = argv[0]
    spec = MODELS.get(key)
    if not spec and "--archetype" in argv:
        idx = argv.index("--archetype")
        if idx + 1 < len(argv):
            spec = {"archetype": argv[idx + 1]}
    if not spec:
        sys.exit("%s has no \"clips\" section in its rig.json" % key)
    if spec["archetype"] in CREATURE:
        return author(key, spec, argv)
    pack = layout.pack_dir(key)
    cp = os.path.join(pack, "model.json")
    if os.path.exists(cp):
        card = json.load(open(cp, encoding="utf-8"))
    else:
        # no card yet (Publish has not run): as the creature path does, the rig's QA log gives its bone roles. This
        # path used to need the card, and fields the spec editor never writes, and failed without them
        card = {"rig": {}}
        q = os.path.join(layout.work_dir("qa"), key + ".json")
        if os.path.exists(q):
            card["rig"]["skeleton"] = json.load(open(q, encoding="utf-8")).get("skeleton")
    spec = dict(spec)
    spec.setdefault("rig", os.path.basename(layout.rigged_dir(key)))    # the rig folder, as the other steps find it
    spec.setdefault("display", key)
    spec.setdefault("category", "Creatures")
    if spec.get("triangles") is None:
        b = layout.budget(key)
        spec["triangles"] = b if b else 10 ** 9                        # no budget: full resolution
    rig_dir = os.path.join(pack, spec["rig"])
    blend = os.path.join(rig_dir, key + ".blend")
    bpy.ops.wm.open_mainfile(filepath=blend)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    mesh = next(o for o in bpy.data.objects if o.type == 'MESH' and o.find_armature() == arm)
    for o in [o for o in bpy.data.objects if o.name.startswith("qa_")]: bpy.data.objects.remove(o, do_unlink=True)
    mute_constraints(arm)
    rig = Rig(arm, mesh)
    arche = ARCHETYPES[spec["archetype"]](rig, spec)
    clips = arche.clips()
    made = build(rig, clips)

    # 1. the source: the rig with every clip as an action
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=blend, check_existing=False)

    # 2. the portable pack, in metres
    name = spec["display"]
    out = os.path.join(rig_dir, name)
    os.makedirs(out, exist_ok=True)
    dup, dec = decimated_copy(mesh, spec["triangles"])
    img = texture_of(dup)
    tex_file = None
    if img:
        src = bpy.path.abspath(img.filepath)
        tex_file = name + os.path.splitext(src)[1].lower()
        shutil.copyfile(src, os.path.join(out, tex_file))
        img.filepath = "//" + tex_file                     # what the FBX will name: the copy beside it
    longest = max(1e-4, float(max(rig.hi - rig.lo)))
    metres = max(1e-4, float(card.get("metres") or longest))
    export_fbx(os.path.join(out, name + ".fbx"), [arm, dup], metres / longest, True)
    if img: img.filepath = src

    # 3. the split pair (armature and takes in the rig's own units; the importer scales them to metres)
    split = argv[argv.index("--split-clips") + 1] if "--split-clips" in argv else None
    if split:
        os.makedirs(split, exist_ok=True)
        export_fbx(os.path.join(split, name + ".fbx"), [arm], 1.0, True)
        # and the mesh file it binds to, from the same armature with the same settings
        export_fbx(os.path.join(split, name + "_model.fbx"), [arm, dup], 1.0, False)
        meta = {**clip_contract.header(),
                "clips": [{"name": n, "seconds": round((last if loops else last + 1) / float(FPS), 4), "loops": loops,
                           **clip_contract.clip_fields(n, (last if loops else last + 1) / float(FPS),
                                                       [m[0] for m in made], arche.windup_end(),
                                                       spec.get("flySpeed") if n == "fly" else None)}
                          for n, last, loops in made],
                "fps": FPS, "walkSpeed": spec.get("splitWalkSpeed", 1.0), "windUpEnd": round(arche.windup_end(), 4)}
        with open(os.path.join(split, name + "_clips.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, indent=2)

    # 4. the manifest
    ev = arche.events()
    size = (rig.hi - rig.lo) * (metres / longest)
    manifest = {
        "format": "autorig-export/1",
        "name": name, "category": spec["category"], "fbx": name + ".fbx",
        "units": "metres (its longest axis is 'metres'). Y up.",
        "metres": metres,
        "size": {"length": round(size.y, 4), "height": round(size.z, 4), "width": round(size.x, 4)},
        "facing": "+Z (Unity's forward; -Y forward in Blender, +X in Unreal after its FBX import).",
        "pivot": "feet",
        "pivotNote": "The origin is under its feet as it stands. Flying clips turn the body about its middle and "
                     "hold that middle where it is at rest, about half its height above the origin.",
        "skeleton": "winged/1 (SKELETONS.md)",
        "bones": bone_map(rig, arche),
        "motion": "authored clips (Autorig Workbench make_clips.py, archetype %s)" % spec["archetype"],
        "flies": True,
        "frameRate": FPS,
        **clip_contract.header(),
        "clips": [{"name": n, "take": n, "length": round(last / float(FPS) if loops else (last + 1) / float(FPS), 4),
                   "frames": last if loops else last + 1, "loop": loops,
                   **clip_contract.clip_fields(n, last / float(FPS) if loops else (last + 1) / float(FPS),
                                               [m[0] for m in made], arche.windup_end(),
                                               spec.get("flySpeed") if n == "fly" else None),
                   "events": ev.get(n, [])} for n, last, loops in made],
        "windUpEnd": round(arche.windup_end(), 4),
        "notes": arche.notes(),
        "textures": [{"file": tex_file, "role": "baseColor", "material": dup.material_slots[0].material.name
                      if dup.material_slots and dup.material_slots[0].material else name}] if tex_file else [],
        "decimation": dec,
        "provenance": {"pack": os.path.relpath(pack, layout.ROOT).replace("\\", "/"),
                       "rig": "%s/%s.blend" % (spec["rig"], key), "card": card},
        "licence": LICENCE,
        "limits": ["IK is muted in the .blend and absent from the FBX: the clips are FK, baked every frame.",
                   "Facing left is not a clip: mirror X or turn it round."],
    }
    with open(os.path.join(out, name + ".json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1)

    if "--preview" in argv:
        preview(rig, dup, mesh, made, argv[argv.index("--preview") + 1])

    print("CLIPS " + json.dumps({"model": key, "clips": [m[0] for m in made], "pack": out, "split": split,
                                 "decimation": dec, "windUpEnd": manifest["windUpEnd"]}))


def preview(rig, dup, mesh, made, out_dir):
    """Side-on frames of every clip (from its right, as a side-on game shows it), for judging a clip without an engine."""
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 420, 320
    scene.display.shading.light = 'STUDIO'; scene.display.shading.color_type = 'TEXTURE'
    scene.render.film_transparent = False
    dup.hide_render = True; mesh.hide_render = False
    cd = bpy.data.cameras.new("pv"); cd.type = 'ORTHO'; cd.ortho_scale = rig.size * 2.1
    cam = bpy.data.objects.new("pv", cd); scene.collection.objects.link(cam); scene.camera = cam
    c = rig.centre
    cam.location = (c.x - 5 * rig.size, c.y, c.z)
    cam.rotation_euler = (math.radians(90), 0, math.radians(-90))
    cd.clip_end = 20 * rig.size
    ad = rig.arm.animation_data
    if ad:
        for t in ad.nla_tracks: t.mute = True
    else:
        ad = rig.arm.animation_data_create()
    for n, last, loops in made:
        ad.action = bpy.data.actions[n]
        k = 6
        for i in range(k):
            f = round(i * (last if loops else last) / float(k - 1 if not loops else k))
            scene.frame_set(f)
            scene.render.filepath = os.path.join(out_dir, "%s_%02d.png" % (n, f))
            bpy.ops.render.render(write_still=True)
    ad.action = None


main()

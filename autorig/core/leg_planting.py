# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: feet that step and plant, for any number of legs. Pure Python.
#
# Ported from Spider Walker (Roman Gibadullin, MIT, https://github.com/iltaen/spider_walker), a Blender 5.2 add-on
# that walks a many-legged rig live. What is kept:
#   - each leg's target is its rest position relative to the body, shifted ahead in the direction of travel
#     (get_predicted_target), so a foot lands in front of the body instead of trailing it;
#   - the target is raycast down onto the ground (raycast_surface), the foot plants on the hit;
#   - a step lerps from the old plant to the new one by smoothstep and lifts along the surface normal by
#     sin(pi t) x height (update_step);
#   - legs take turns (find_next_leg's round-robin), so they do not all step at once.
# What is different: Spider Walker steps when a foot has drifted far enough, in real time. A clip has to loop, so here
# every leg steps once a cycle at its own phase (a quadruped's lateral sequence, an insect's tripod, round-robin for
# anything else), and the drift that triggers it is exactly one cycle's travel. The result is periodic by
# construction: the last frame is the first.
#
# Frames are the clip's own (in place): the ground moves back under the body at `stride / (duty x frames)` a frame,
# so a planted foot moves back with it. Positions are (x, y, z), Z up; forward is the rig's -Y unless given.
import math


def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _lerp(a, b, t): return _add(a, _mul(_sub(b, a), t))


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def flat_ground(height):
    """A raycast onto the plane z = height: (hit, normal), or None when the ray points up."""
    def cast(origin, direction):
        if direction[2] >= -1e-12: return None
        s = (height - origin[2]) / direction[2]
        return _add(origin, _mul(direction, s)), (0.0, 0.0, 1.0)
    return cast


def phases_for(legs, kind="walk"):
    """When each leg's stance starts, as a share of the cycle. legs: [{name, side (+1 left), along (forward
    position), is_front}]. Four legs: the lateral-sequence walk (LH, LF, RH, RF), the diagonal trot or the rotary
    gallop, as gait.py's evaluators time them. Six: the alternating tripod. Anything else: Spider Walker's round
    robin, front to back down one side and then the other, offset by half a step."""
    out = {}
    if len(legs) == 4:
        maps = {"walk": {"LH": 0.00, "LF": 0.25, "RH": 0.50, "RF": 0.75},
                "trot": {"LF": 0.00, "RH": 0.00, "RF": 0.50, "LH": 0.50},
                "gallop": {"LH": 0.00, "RH": 0.10, "LF": 0.52, "RF": 0.62}}
        m = maps.get(kind, maps["walk"])
        for leg in legs:
            key = ("L" if leg.get("side", 1.0) > 0 else "R") + ("F" if leg.get("is_front") else "H")
            out[leg["name"]] = m[key]
        return out
    left = sorted((l for l in legs if l.get("side", 1.0) > 0), key=lambda l: -l.get("along", 0.0))
    right = sorted((l for l in legs if l.get("side", 1.0) <= 0), key=lambda l: -l.get("along", 0.0))
    if len(legs) == 6 and len(left) == 3 and len(right) == 3:
        for i, leg in enumerate(left): out[leg["name"]] = 0.5 * (i % 2)
        for i, leg in enumerate(right): out[leg["name"]] = 0.5 * ((i + 1) % 2)
        return out
    order = left + right
    for i, leg in enumerate(order):
        out[leg["name"]] = (i / float(len(order)) + (0.5 / len(order) if leg in right else 0.0)) % 1.0
    return out


def step_cycle(rest_feet, frames, stride, duty, lift, phases, forward=(0.0, -1.0, 0.0), raycast=None,
               probe=None, lead=None):
    """Where each foot is on every frame of one looping cycle: {name: [(x, y, z) for frames 0..frames-1]}.

    rest_feet: {name: rest position}. stride: how far a planted foot travels back (relative to the body) while it is
    down; the body covers stride / duty a cycle. duty: the share of the cycle a foot is down. lift: the step's
    height. phases: {name: when its stance starts}. raycast(origin, direction) -> (hit, normal) or None: the ground
    (default: flat, at the lowest rest foot). probe: how far above a target the ray starts (default 4 x lift + the
    stride). lead: how far ahead of its rest position a foot lands (default half the stride, so its stance is centred
    under the hip: Spider Walker's prediction distance)."""
    n = int(frames)
    if n < 2 or not rest_feet: return {k: [tuple(p)] * max(n, 1) for k, p in rest_feet.items()}
    duty = max(0.05, min(0.95, float(duty)))
    fwd = forward
    fl = math.sqrt(sum(c * c for c in fwd)) or 1.0
    fwd = _mul(fwd, 1.0 / fl)
    v = stride / (duty * n)                               # the body's travel a frame
    lead = 0.5 * stride if lead is None else lead
    if raycast is None: raycast = flat_ground(min(p[2] for p in rest_feet.values()))
    probe = (4.0 * lift + stride) if probe is None else probe
    down = (0.0, 0.0, -1.0)

    def body(f):                                          # the body's position in the ground's frame
        return _mul(fwd, v * f)

    out = {}
    for name, rest in rest_feet.items():
        off = phases.get(name, 0.0) % 1.0
        plants = {}

        def plant(k):
            # stance k starts at frame (k + off) x n: aim at the rest spot under where the body will then be,
            # ahead by the lead, and raycast down from above it
            if k not in plants:
                aim = _add(_add(rest, body((k + off) * n)), _mul(fwd, lead))
                hit = raycast(_add(aim, (0.0, 0.0, probe)), down)
                plants[k] = (hit[0], hit[1]) if hit else (aim, (0.0, 0.0, 1.0))
            return plants[k]

        track = []
        for f in range(n):
            x = f / float(n) - off
            k = math.floor(x)
            u = x - k
            if u < duty:
                world = plant(k)[0]
            else:
                t = (u - duty) / (1.0 - duty)
                (a, na), (b, nb) = plant(k), plant(k + 1)
                normal = _lerp(na, nb, smoothstep(t))
                nl = math.sqrt(sum(c * c for c in normal)) or 1.0
                world = _add(_lerp(a, b, smoothstep(t)), _mul(normal, lift * math.sin(math.pi * t) / nl))
            track.append(_sub(world, body(f)))
        out[name] = track
    return out

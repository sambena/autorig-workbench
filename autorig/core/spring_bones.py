# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: secondary motion for tails, ears, antennae and wing membranes. Pure Python.
#
# After Wiggle 2 (Steve Miller, GPL-3.0, https://github.com/shteeve3d/blender-wiggle-2), a Blender add-on that puts
# spring physics on bone tails. Ported here (move, constrain and the velocity update of its frame handler):
#   - each bone's tail is a point with a velocity, damped each step by (1 - damping x dt) and pulled by gravity;
#   - its target is where the animation puts the tail, carried by the parent's simulated turn (so a chain lags
#     link by link), and a spring moves it stiffness x dt^2 of the way there (never past it), in a few iterations;
#   - the bone keeps its length (Wiggle's stretch 0);
#   - the velocity is the step's own displacement.
# What is ours: a baked clip, not a live handler. A loop is run for a few cycles until it settles and the last cycle
# is kept, with what is left of the seam spread across it so the clip still loops; a one-shot starts at rest.
#
# A chain is a list of joints, root first: the first bone's head, then each bone's tail (bones joined head to tail).
import math


def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _len(a): return math.sqrt(_dot(a, a))
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _turn(a, b, v):
    """v turned by the rotation that takes direction a to direction b (Rodrigues)."""
    la, lb = _len(a), _len(b)
    if la < 1e-12 or lb < 1e-12: return v
    a, b = _mul(a, 1 / la), _mul(b, 1 / lb)
    axis = _cross(a, b)
    s, c = _len(axis), _dot(a, b)
    if s < 1e-12:
        if c > 0: return v
        # half a turn about any axis normal to a
        n = _cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0))
        n = _mul(n, 1 / _len(n))
        return _sub(_mul(n, 2 * _dot(n, v)), v)
    k = _mul(axis, 1 / s)
    return _add(_add(_mul(v, c), _mul(_cross(k, v), s)), _mul(k, _dot(k, v) * (1 - c)))


DEFAULTS = {"stiffness": 400.0, "damping": 1.0, "gravity": 1.0, "iterations": 2}   # Wiggle 2's defaults


def _step(anim, pos, last, lengths, damp, gravity_step, stiffness, iterations, dt2):
    """One frame: every joint after the root moves (damped velocity, gravity), then springs toward where the
    animation puts it, carried by its parent's simulated turn, and keeps its bone's length."""
    new = [tuple(anim[0])]
    for i in range(1, len(anim)):
        head = new[i - 1]
        bone = _sub(anim[i], anim[i - 1])
        if i >= 2:                                       # Wiggle's relative matrix: the parent's lag carries it
            bone = _turn(_sub(anim[i - 1], anim[i - 2]), _sub(new[i - 1], new[i - 2]), bone)
        target = _add(head, bone)
        p = _add(_add(pos[i], _mul(_sub(pos[i], last[i]), damp)), gravity_step)
        for _ in range(max(1, int(iterations))):
            s = _sub(target, p)
            step = _mul(s, stiffness / iterations * dt2)
            p = _add(p, s if _len(step) > _len(s) else step)     # a spring never pulls past its target
            d = _sub(p, head)
            dl = _len(d)
            p = _add(head, _mul(d, lengths[i - 1] / dl)) if dl > 1e-12 else target
        new.append(p)
    return new


def simulate(frames, fps=24, stiffness=400.0, damping=1.0, gravity=1.0, iterations=2, loop=True, cycles=4,
             g=(0.0, 0.0, -9.81), scale=1.0):
    """frames: the chain's animated joints on each frame ([joint (x, y, z)], root first). Returns the simulated joints
    on each frame: the root where the animation puts it, every other joint lagging on its spring. `scale`: the model's
    units a metre, for gravity. A loop runs `cycles` times and keeps the last, its seam spread across it; a one-shot
    starts from its first frame at rest."""
    n = len(frames)
    if n == 0 or len(frames[0]) < 2: return [list(map(tuple, f)) for f in frames]
    dt2 = (1.0 / float(fps)) ** 2
    damp = max(0.0, min(1.0, 1.0 - damping / float(fps)))
    gravity_step = _mul(g, gravity * scale * dt2)
    lengths = [_len(_sub(frames[0][i + 1], frames[0][i])) for i in range(len(frames[0]) - 1)]
    args = (lengths, damp, gravity_step, stiffness, iterations, dt2)
    pos = [tuple(p) for p in frames[0]]
    last = list(pos)
    runs = max(2, int(cycles)) if loop else 1
    out = []
    for c in range(runs):
        for f in range(n):
            last, pos = pos, _step(frames[f], pos, last, *args)
            if c == runs - 1: out.append(pos)
    if loop:
        # the kept cycle ends where the next would begin, not quite where it began: spread the difference back
        # across the cycle so frame n is frame 0 again
        nxt = _step(frames[0], pos, last, *args)
        for f in range(n):
            w = f / float(n)
            row = [out[f][0]]
            for i in range(1, len(out[f])):
                p = _add(out[f][i], _mul(_sub(nxt[i], out[0][i]), -w))
                d = _sub(p, row[i - 1])
                dl = _len(d)
                row.append(_add(row[i - 1], _mul(d, lengths[i - 1] / dl)) if dl > 1e-12 else p)
            out[f] = row
    return out

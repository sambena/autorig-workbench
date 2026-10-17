# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a leg's joints re-solved to put its foot on a target. Pure Python.
#
# FABRIK (Aristidou and Lasenby, 2011): forward and backward passes along the chain, each joint pulled onto the line
# to its neighbour at the bone's length. Started from the leg's own pose it keeps the knee on the side it bends to,
# which is what an IK re-solve after a contact fix wants (MoMask and GenMM re-solve with Jacobian IK over the whole
# skeleton; one leg at a time is enough for clips authored per limb). Any number of bones, so it fits a dog's two-bone
# leg, a horse's three and a beetle's.
import math


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _len(a): return math.sqrt(_dot(a, a))


def _toward(a, b, length):
    """The point `length` from a toward b."""
    d = _sub(b, a)
    l = _len(d)
    if l < 1e-12: return _add(a, (0.0, 0.0, -length))
    return _add(a, _mul(d, length / l))


def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def bend_of(joints):
    """How far a chain is from straight: the largest distance of an inner joint from the root-to-end line, as a share
    of the chain's length."""
    root, end = joints[0], joints[-1]
    axis = _sub(end, root)
    la = _len(axis)
    total = sum(_len(_sub(joints[i + 1], joints[i])) for i in range(len(joints) - 1)) or 1.0
    if la < 1e-12: return 1.0
    return max((_len(_cross(_sub(p, root), axis)) / la for p in joints[1:-1]), default=0.0) / total


def fabrik(joints, target, iterations=20, tolerance=1e-5, pole=None):
    """joints: the chain's positions root first (hip, knee, ..., foot). Returns new positions with the root where it
    was, every bone its length, and the last joint on the target or as near as the chain reaches (straight toward
    it). pole: the direction the inner joints bend toward when the chain starts out straight (a straight leg has no
    knee side of its own, and FABRIK would pick one frame by frame)."""
    pts = [tuple(p) for p in joints]
    if len(pts) < 2: return pts
    lengths = [_len(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1)]
    root = pts[0]
    reach = sum(lengths)
    if _len(_sub(target, root)) >= reach:
        out = [root]
        for l in lengths: out.append(_toward(out[-1], target, l))
        return out
    if pole is not None and len(pts) > 2 and bend_of(pts) < 0.02:
        nudge = _mul(pole, 0.05 * reach / max(_len(pole), 1e-12))
        pts = [pts[0]] + [_add(p, nudge) for p in pts[1:-1]] + [pts[-1]]
    for _ in range(iterations):
        pts[-1] = tuple(target)
        for i in range(len(pts) - 2, -1, -1):
            pts[i] = _toward(pts[i + 1], pts[i], lengths[i])
        pts[0] = root
        for i in range(len(pts) - 1):
            pts[i + 1] = _toward(pts[i], pts[i + 1], lengths[i])
        if _len(_sub(pts[-1], target)) < tolerance: break
    return pts


def rotation_between(a, b):
    """The shortest rotation taking direction a to direction b, as a quaternion (w, x, y, z)."""
    la, lb = _len(a), _len(b)
    if la < 1e-12 or lb < 1e-12: return (1.0, 0.0, 0.0, 0.0)
    a, b = _mul(a, 1 / la), _mul(b, 1 / lb)
    c = _dot(a, b)
    if c < -0.999999:
        axis = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
        x = (a[1] * axis[2] - a[2] * axis[1], a[2] * axis[0] - a[0] * axis[2], a[0] * axis[1] - a[1] * axis[0])
        lx = _len(x)
        return (0.0, x[0] / lx, x[1] / lx, x[2] / lx)
    x = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    w = 1.0 + c
    l = math.sqrt(w * w + _dot(x, x))
    return (w / l, x[0] / l, x[1] / l, x[2] / l)

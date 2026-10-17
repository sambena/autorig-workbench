# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: varied, non-repeating variants of one clip, on any skeleton, on the CPU. Needs numpy (Blender's
# own Python has it); the synthesis is GenMM's (core/genmm, Apache-2.0, ported to NumPy).
#
# A clip is a set of channels, each one bone property over the clip's frames: rotations (quaternions) and vectors
# (locations, scales, constraint influences). Rotations go to GenMM as the 6D representation it uses (two columns of
# the rotation matrix, continuous where quaternions flip sign), vectors as they are, locations divided by the
# model's size so a bone's travel weighs like its turn. GenMM rearranges and blends patches of the example into a
# new, longer motion; a looping example gives a looping variant (patches run across both seams).
import numpy as np

from genmm import GenMM, PatchCoherentLoss


def quat_to_6d(q):
    """(T, 4) quaternions (w, x, y, z) -> (T, 6): the rotation matrix's first two columns."""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    c0 = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)], axis=1)
    c1 = np.stack([2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)], axis=1)
    return np.concatenate([c0, c1], axis=1)


def sixd_to_quat(r):
    """(T, 6) -> (T, 4) unit quaternions, by Gram-Schmidt on the two columns, each the same side of the sphere as the
    one before."""
    r = np.asarray(r, dtype=np.float64)
    a = r[:, :3] / np.maximum(np.linalg.norm(r[:, :3], axis=1, keepdims=True), 1e-12)
    b = r[:, 3:] - (a * r[:, 3:]).sum(1, keepdims=True) * a
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    c = np.cross(a, b)
    m = np.stack([a, b, c], axis=2)                      # columns
    out = np.empty((len(r), 4))
    for i, M in enumerate(m):
        t = M[0, 0] + M[1, 1] + M[2, 2]
        if t > 0:
            s = 0.5 / np.sqrt(t + 1.0)
            out[i] = (0.25 / s, (M[2, 1] - M[1, 2]) * s, (M[0, 2] - M[2, 0]) * s, (M[1, 0] - M[0, 1]) * s)
        elif M[0, 0] > M[1, 1] and M[0, 0] > M[2, 2]:
            s = 2.0 * np.sqrt(1.0 + M[0, 0] - M[1, 1] - M[2, 2])
            out[i] = ((M[2, 1] - M[1, 2]) / s, 0.25 * s, (M[0, 1] + M[1, 0]) / s, (M[0, 2] + M[2, 0]) / s)
        elif M[1, 1] > M[2, 2]:
            s = 2.0 * np.sqrt(1.0 + M[1, 1] - M[0, 0] - M[2, 2])
            out[i] = ((M[0, 2] - M[2, 0]) / s, (M[0, 1] + M[1, 0]) / s, 0.25 * s, (M[1, 2] + M[2, 1]) / s)
        else:
            s = 2.0 * np.sqrt(1.0 + M[2, 2] - M[0, 0] - M[1, 1])
            out[i] = ((M[1, 0] - M[0, 1]) / s, (M[0, 2] + M[2, 0]) / s, (M[1, 2] + M[2, 1]) / s, 0.25 * s)
        if i and np.dot(out[i], out[i - 1]) < 0: out[i] = -out[i]
    return out


def patch_size_for(frames):
    """GenMM's patch is 11 frames on mocap at 30 fps; a short authored loop wants about an eighth of itself, odd,
    3 to 11."""
    p = int(round(frames / 8.0))
    p = max(3, min(11, p))
    return p if p % 2 else p + 1


def synthesize(channels, frames, loop=True, seed=0, size=1.0, patch_size=None, alpha=0.01, steps=3, noise=1.0,
               pyr_factor=0.75):
    """channels: {key: (kind, array (T, k))}, kind "quat" or "vec" (or "loc": a vector in model units). Returns the
    same keys with `frames` frames each. GenMM's defaults: alpha 0.01, 3 steps a level, a 0.75 pyramid; the coarsest
    level five patches long (its "5x_patchsize")."""
    keys = list(channels)
    if not keys: return {}
    T = len(next(iter(channels.values()))[1])
    blocks, layout = [], []
    for k in keys:
        kind, a = channels[k]
        a = np.asarray(a, dtype=np.float64).reshape(T, -1)
        if kind == "quat":
            b = quat_to_6d(a)
        elif kind == "loc":
            b = a / max(float(size), 1e-9)
        else:
            b = a
        layout.append((k, kind, b.shape[1]))
        blocks.append(b)
    x = np.concatenate(blocks, axis=1).T                 # (channels, frames)
    p = patch_size or patch_size_for(T)
    coarse = min(1.0, 5.0 * p / float(T))
    criteria = PatchCoherentLoss(patch_size=p, alpha=alpha, loop=loop, cache=True)
    y = GenMM(seed).run([x], criteria, frames, num_steps=steps, noise_sigma=noise, coarse_ratio=coarse,
                        pyr_factor=pyr_factor).T
    out, at = {}, 0
    for k, kind, width in layout:
        b = y[:, at:at + width]; at += width
        if kind == "quat":
            out[k] = sixd_to_quat(b)
        elif kind == "loc":
            out[k] = b * float(size)
        else:
            out[k] = b
    return out


def difference(a, b):
    """How different two motions are: the mean absolute difference over their shared frames and channels (for
    telling a variant from a copy)."""
    n = min(len(a), len(b))
    return float(np.abs(np.asarray(a[:n]) - np.asarray(b[:n])).mean()) if n else 0.0

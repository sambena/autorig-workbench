# SPDX-License-Identifier: Apache-2.0
# Copyright the GenMM authors (https://github.com/liyuuuu98/GenMM), Apache License 2.0; see NOTICE and LICENSE.
# From nearest_neighbor/utils.py, which borrows from Efficient-GPNN (https://github.com/ariel415el/Efficient-GPNN,
# utils/NN.py). Modified for Autorig Workbench: ported from PyTorch and unfoldNd to NumPy.
#
# A motion is an array (channels, frames). A patch is `patch_size` consecutive frames of every channel, flattened.
import numpy as np


def extract_patches(x, patch_size, stride=1, loop=False):
    """Extract patches from a motion sequence: (n_patches, channels x patch_size). A loop is padded with its own
    other end, so patches run across the seam."""
    assert stride == 1, "Only support stride of 1"
    c, _t = x.shape
    half = patch_size // 2
    if loop and half:
        x = np.concatenate([x[:, -half:], x, x[:, :half]], axis=-1)
    n = x.shape[-1] - patch_size + 1
    idx = np.arange(patch_size)[None, :] + np.arange(n)[:, None]          # (n, patch_size)
    return x[:, idx].transpose(1, 0, 2).reshape(n, c * patch_size)


def combine_patches(x_shape, ys, patch_size, stride=1, loop=False):
    """Combine motion patches: each frame is the mean of every patch that covers it (unfoldNd's fold, divided by the
    fold of ones). A loop's padding is folded back onto the frames it repeats."""
    c, t = x_shape
    half = patch_size // 2
    out_t = t + 2 * half if loop else t
    n = out_t - patch_size + 1
    ys = ys.reshape(n, c, patch_size)
    combined = np.zeros((c, out_t))
    divisor = np.zeros(out_t)
    for k in range(patch_size):
        combined[:, k:k + n] += ys[:, :, k].T
        divisor[k:k + n] += 1.0
    combined /= divisor[None, :]
    if loop and half:
        front, tail = combined[:, :half].copy(), combined[:, -half:].copy()
        combined[:, half:2 * half] = (combined[:, half:2 * half] + tail) / 2
        combined[:, -2 * half:-half] = (front + combined[:, -2 * half:-half]) / 2
        combined = combined[:, half:-half]
    return combined


def efficient_cdist(X, Y):
    """Borrowed from Efficient-GPNN: squared distances between every row of X and every row of Y, divided by the
    vector size (so the same alpha suits any patch size). No square root."""
    dist = (X * X).sum(1)[:, None] + (Y * Y).sum(1)[None, :] - 2.0 * X @ Y.T
    return dist / X.shape[1]


def get_col_mins_efficient(X, Y, b=1024):
    """Borrowed from Efficient-GPNN: for each row of Y, its distance to the nearest row of X."""
    mins = np.empty(len(Y))
    for i in range(0, len(Y), b):
        mins[i:i + b] = efficient_cdist(X, Y[i:i + b]).min(0)
    return mins


def get_nns_dists(X, Y, alpha=None, b=1024):
    """Borrowed from Efficient-GPNN: the nearest row of Y for each row of X, and the distance. With alpha, each
    distance is divided by (alpha + how near that Y row comes to any X): GenMM's completeness term, which makes an
    example patch the synthesis has not used yet more attractive, so every part of the example appears."""
    norm = (alpha + get_col_mins_efficient(X, Y, b=b))[None, :] if alpha is not None else 1.0
    nns = np.empty(len(X), dtype=np.int64)
    dists = np.empty(len(X))
    for i in range(0, len(X), b):
        d = efficient_cdist(X[i:i + b], Y) / norm
        nns[i:i + b] = d.argmin(1)
        dists[i:i + b] = d[np.arange(len(d)), nns[i:i + b]]
    return nns, dists

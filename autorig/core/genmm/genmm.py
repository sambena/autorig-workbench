# SPDX-License-Identifier: Apache-2.0
# Copyright the GenMM authors (https://github.com/liyuuuu98/GenMM), Apache License 2.0; see NOTICE and LICENSE.
# From GenMM.py and nearest_neighbor/losses.py. Modified for Autorig Workbench: ported from PyTorch to NumPy; no
# device, logging or keyframe pinning; seeded, per-channel-scaled initial noise.
#
# Generative motion matching: a coarse-to-fine pyramid. At the coarsest level the synthesis starts as the example,
# resampled to the new length, plus noise. At each level every patch of the synthesis is replaced by its nearest
# example patch (with GenMM's completeness weighting) and the overlapping patches are averaged ("match and blend"),
# a few times; then the result is resampled to the next level's length. No training, no network.
import numpy as np

from .patches import extract_patches, combine_patches, get_nns_dists


def interpolate(x, size, align_corners=False):
    """torch.nn.functional.interpolate(mode='linear') on (channels, frames)."""
    t = x.shape[-1]
    if size == t: return x.copy()
    if align_corners:
        pos = np.linspace(0.0, t - 1.0, size) if size > 1 else np.zeros(1)
    else:
        pos = np.clip((np.arange(size) + 0.5) * t / float(size) - 0.5, 0.0, t - 1.0)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, t - 1)
    w = pos - lo
    return x[:, lo] * (1.0 - w) + x[:, hi] * w


def pyramid_lengths(final_len, coarse_ratio, pyr_factor):
    """Get a list of pyramid lengths using given target length and ratio."""
    lengths = [int(np.round(final_len * coarse_ratio))]
    while lengths[-1] < final_len:
        lengths.append(int(np.round(lengths[-1] / pyr_factor)))
        if lengths[-1] == lengths[-2]:
            lengths[-1] += 1
    lengths[-1] = final_len
    return lengths


class PatchCoherentLoss:
    """For each patch in the synthesis find its nearest example patch; the blend of those patches, and the mean
    distance."""

    def __init__(self, patch_size=7, stride=1, alpha=None, loop=False, cache=False):
        assert patch_size % 2 == 1, "Only support odd patch size"
        assert stride == 1, "Only support stride of 1"
        self.patch_size, self.stride, self.alpha, self.loop, self.cache = patch_size, stride, alpha, loop, cache
        self.cached_data = None

    def __call__(self, X, Ys, return_blended_results=False):
        x_patches = extract_patches(X, self.patch_size, self.stride, loop=self.loop)
        if not self.cache or self.cached_data is None:
            # an example that loops gives patches across its own seam too
            y_patches = np.concatenate([extract_patches(y, self.patch_size, self.stride, loop=self.loop) for y in Ys])
            self.cached_data = y_patches
        else:
            y_patches = self.cached_data
        nnf, dist = get_nns_dists(x_patches, y_patches, self.alpha)
        if return_blended_results:
            return combine_patches(X.shape, y_patches[nnf], self.patch_size, self.stride, loop=self.loop), dist.mean()
        return dist.mean()

    def clean_cache(self):
        self.cached_data = None


class GenMM:
    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed)

    def run(self, targets, criteria, num_frames, num_steps=3, noise_sigma=1.0, coarse_ratio=0.2, pyr_factor=0.75):
        """targets: example motions, each (channels, frames). Returns the synthesis, (channels, num_frames)."""
        lengths = [pyramid_lengths(t.shape[-1], coarse_ratio, pyr_factor) for t in targets]
        levels = min(len(ls) for ls in lengths)
        lengths = [ls[-levels:] for ls in lengths]
        pyramid = [[interpolate(t, ls[step]) for t, ls in zip(targets, lengths)] for step in range(levels)]
        syn_lengths = pyramid_lengths(int(num_frames), coarse_ratio, pyr_factor)
        syn_lengths = ([syn_lengths[0]] * levels + syn_lengths)[-levels:]
        synthesized = interpolate(np.concatenate(pyramid[0], axis=-1), syn_lengths[0], align_corners=True)
        if noise_sigma > 0:
            spread = np.concatenate(targets, axis=-1).std(axis=-1, keepdims=True)
            synthesized = synthesized + self.rng.standard_normal(synthesized.shape) * spread * noise_sigma
        for lvl, lvl_targets in enumerate(pyramid):
            if lvl > 0:
                synthesized = interpolate(synthesized, syn_lengths[lvl])
            for _ in range(num_steps):
                synthesized, _loss = criteria(synthesized, lvl_targets, return_blended_results=True)
            criteria.clean_cache()
        return synthesized

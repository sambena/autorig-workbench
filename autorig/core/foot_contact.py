# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: foot contacts, foot skate, and where a planted foot should stay. Pure Python (no bpy, no numpy),
# so clip-check, make_clips and the tests share one definition.
#
# Ported ideas, with thanks (both MIT/Apache, compatible with GPL-3.0):
#   MoMask (Chuan Guo et al., MIT): visualization/remove_fs.py: a foot is in contact when it is low and slow; each
#     contact run is pinned to its mean position on the floor, and the frames either side are eased in and out of the
#     pin with the cubic alpha(t) = 2t^3 - 3t^2 + 1. compute_foot_sliding: slip weighted by (2 - 2^(h / H)), so a
#     foot at the floor counts fully and one at height H not at all.
#     https://github.com/EricGuo5513/momask-codes
#   GenMM (Weiyu Li et al., Apache-2.0): fix_contact.py and utils/contact.py: the same pin-and-ease constraint, and
#     continuous_filter, which drops contact flickers of a frame or two. https://github.com/liyuuuu98/GenMM
#
# What is ours: the clips are authored in place, so a planted foot of a walk slides back at the walk's speed. That is
# not skate: the ground moves with it. Every measure here works in the ground's frame, which moves at a ground
# velocity (estimated from the feet unless the caller knows it), and loops wrap round.
#
# Positions are (x, y, z) tuples, Z up. A track is one foot's positions, one per frame.
import math


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _lerp(a, l, r): return _add(_mul(l, 1.0 - a), _mul(r, a))
def _hlen(v): return math.hypot(v[0], v[1])


def alpha(t):
    """MoMask's ease: 1 at t = 0, 0 at t = 1, flat at both ends."""
    return 2.0 * t * t * t - 3.0 * t * t + 1.0


def floor_height(tracks):
    """MoMask's floor: the mean of the 25th to 50th percentile of the lowest foot per frame (robust to a jump, and to
    one foot dipping)."""
    if not tracks or not tracks[0]: return 0.0
    lows = sorted(min(t[f][2] for t in tracks) for f in range(len(tracks[0])))
    lo, hi = int(0.25 * len(lows)), max(int(0.5 * len(lows)), int(0.25 * len(lows)) + 1)
    part = lows[lo:hi]
    return sum(part) / len(part)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


# Clips that travel: authored in place, the ground moves under them. Every other clip stands on still ground, so a
# foot that moves while it is down is skating, however many feet do it together.
TRAVELS = ("walk", "trot", "gallop", "canter", "run", "jog", "crawl", "scuttle", "slither", "sneak", "pace", "swim")


def travels(clip):
    n = (clip or "").lower()
    return any(w in n for w in TRAVELS) and not any(w in n for w in ("to_idle", "idle_to", "stop", "start"))


def ground_velocity(tracks, floor, height, loop=False, still=None):
    """The ground's velocity (units a frame, horizontal) under an in-place clip: the median velocity of every foot
    frame near the floor. A planted foot of an in-place walk moves back at the walk's speed, and most low foot frames
    are planted ones. `still`: a speed below which the ground is taken to be still (an idle's feet jitter, they do not
    travel); default 1% of `height` a frame."""
    vx, vy = [], []
    for t in tracks:
        n = len(t)
        for f in range(n if loop else n - 1):
            a, b = t[f], t[(f + 1) % n]
            if a[2] - floor < height and b[2] - floor < height:
                vx.append(b[0] - a[0]); vy.append(b[1] - a[1])
    if not vx: return (0.0, 0.0, 0.0)
    v = (_median(vx), _median(vy), 0.0)
    if _hlen(v) < (0.01 * height if still is None else still): return (0.0, 0.0, 0.0)
    return v


def to_ground(track, velocity):
    """A track in the ground's frame. The ground moves by `velocity` a frame in the clip's frame (backward under an
    in-place walk), so frame f is moved back by f x velocity."""
    return [_sub(p, _mul(velocity, f)) for f, p in enumerate(track)]


def from_ground(track, velocity):
    return [_add(p, _mul(velocity, f)) for f, p in enumerate(track)]


def continuous_filter(contact, length=2, loop=False):
    """GenMM's filter: a run of `length` frames or fewer takes the value that ends it (a one-frame lift in a stance,
    or a one-frame touch in a swing, is noise)."""
    c = list(contact)
    n = len(c)
    if n == 0 or all(v == c[0] for v in c): return c
    k = 0
    if loop:
        # start at a change, so no run is split across the seam; the first run then follows the last
        k = next(i for i in range(n) if c[i] != c[i - 1])
        c = _rotate(c, k)
    run, prev = 0, c[0]
    for i in range(n):
        if c[i] == prev:
            run += 1
        else:
            if run <= length and (loop or i - run > 0):   # a clip's first run has nothing before it to join
                for j in range(i - run, i): c[j] = c[i]
            run, prev = 1, c[i]
    return _rotate(c, -k) if loop else c


def _rotate(xs, k):
    k %= len(xs) if xs else 1
    return xs[k:] + xs[:k]


def detect_contacts(track, floor, height, speed, loop=False, filter_length=2):
    """MoMask's foot_detect: in contact when the foot is below floor + height and moves less than `speed` a frame
    (the track in the ground's frame). Then GenMM's continuous_filter."""
    n = len(track)
    out = []
    for f in range(n):
        g = (f + 1) % n if loop else min(f + 1, n - 1)
        h = (f - 1) % n if loop else max(f - 1, 0)
        # the slower of the two frame steps: a contact's first and last frames border a moving foot
        v = min(_hlen(_sub(track[g], track[f])) if g != f else 0.0, _hlen(_sub(track[f], track[h])) if h != f else 0.0)
        out.append(track[f][2] - floor < height and v < speed)
    return continuous_filter(out, filter_length, loop) if filter_length else out


def contact_runs(contact, loop=False):
    """[(start, end)] inclusive. In a loop a run may wrap: end < start."""
    n = len(contact)
    if not any(contact): return []
    if all(contact): return [(0, n - 1)]
    runs, f = [], 0
    if loop:
        f = next(i for i in range(n) if contact[i] and not contact[i - 1])
    seen = 0
    while seen < n:
        if contact[f % n]:
            s = f
            while contact[(f + 1) % n] and seen < n - 1:
                f += 1; seen += 1
            runs.append((s % n, f % n))
        f += 1; seen += 1
        if not loop and f >= n: break
    return runs


def _run_frames(run, n):
    s, e = run
    return list(range(s, e + 1)) if e >= s else list(range(s, n)) + list(range(0, e + 1))


def pin_contacts(track, contact, floor=None, blend=5, loop=False):
    """MoMask's remove_fs constraint on one foot (a track in the ground's frame): every contact run is held at its mean
    position (on the floor when `floor` is given), and up to `blend` frames either side ease from the pin back to the
    original motion. Returns the pinned track."""
    n = len(track)
    out = list(track)
    fixed = [False] * n
    for run in contact_runs(contact, loop):
        frames = _run_frames(run, n)
        avg = _mul(tuple(map(sum, zip(*(track[f] for f in frames)))), 1.0 / len(frames))
        if floor is not None: avg = (avg[0], avg[1], floor)
        for f in frames:
            out[f] = avg; fixed[f] = True
    if not any(fixed) or all(fixed): return out
    pinned = list(out)

    def near(s, step):
        for k in range(blend):
            j = s + step * (k + 1)
            if not loop and not 0 <= j < n: return None
            if fixed[j % n]: return j
        return None

    for s in range(n):
        if fixed[s]: continue
        l, r = near(s, -1), near(s, 1)
        if l is None and r is None: continue
        here = track[s]
        if l is not None and r is not None:
            litp = _lerp(alpha((s - l + 1) / (blend + 1.0)), here, pinned[l % n])
            ritp = _lerp(alpha((r - s + 1) / (blend + 1.0)), here, pinned[r % n])
            out[s] = _lerp(alpha((s - l + 1) / float(r - l + 1)), ritp, litp)
        elif l is not None:
            out[s] = _lerp(alpha((s - l + 1) / (blend + 1.0)), here, pinned[l % n])
        else:
            out[s] = _lerp(alpha((r - s + 1) / (blend + 1.0)), here, pinned[r % n])
    return out


def foot_skate(tracks, size, fps=30, loop=False, velocity=None, height=None, floor=None):
    """The skate of a clip's feet: MoMask's compute_foot_sliding in the ground's frame, scaled by the model's size.

    Each frame step a foot moves horizontally by d (relative to the ground) while its mean height above the floor is
    h < H (3% of the size: MoMask's 5 cm on a person) counts d x (2 - 2^(h / H)). `score` is the sum over the feet and frames divided by the frames, in
    thousandths of the model's size a frame (MoMask reports millimetres a frame for a human): 0 is planted, and a
    walk that slides a tenth of its stride a step scores about 5. `ratio` is the share of low foot frames that slip
    more than 0.25% of the size a frame. velocity: the ground's velocity a frame, if known; otherwise estimated.
    floor: the ground's height, if known (the feet at rest: a flier's hover is not its floor); otherwise MoMask's.
    Returns {score, ratio, worst, per_foot, ground_speed, floor}; per_foot is keyed like `tracks` when it is a dict."""
    names = list(tracks) if isinstance(tracks, dict) else list(range(len(tracks)))
    tr = [tracks[k] for k in names]
    size = max(float(size), 1e-9)
    H = height if height is not None else 0.03 * size
    if not tr or len(tr[0]) < 2:
        return {"score": 0.0, "ratio": 0.0, "worst": None, "per_foot": {}, "ground_speed": 0.0, "floor": 0.0}
    floor = floor_height(tr) if floor is None else floor
    v = velocity if velocity is not None else ground_velocity(tr, floor, H, loop)
    n = len(tr[0])
    steps = n if loop else n - 1
    per, low, slipping = {}, 0, 0
    for k, t in zip(names, tr):
        total = 0.0
        for f in range(steps):
            a, b = t[f], t[(f + 1) % n]
            d = _hlen(_sub(_sub(b, a), v))            # the step in the ground's frame
            h = 0.5 * (a[2] + b[2]) - floor
            if h < H:
                low += 1
                w = 2.0 - 2.0 ** (max(h, 0.0) / H)
                total += d * w
                if d > 0.0025 * size: slipping += 1
        per[k] = total / steps / size * 1000.0
    score = sum(per.values())
    worst = max(per, key=per.get) if per else None
    return {"score": round(score, 4), "ratio": round(slipping / float(low), 4) if low else 0.0,
            "worst": worst, "per_foot": {k: round(s, 4) for k, s in per.items()},
            "ground_speed": round(_hlen(v) * fps, 6), "floor": round(floor, 6)}


def plan_pins(tracks, size, loop=False, velocity=None, height=None, speed=None, blend=4, floor=None):
    """Where each foot should be (in the clip's own frame) so it does not skate: detect contacts in the ground's
    frame, pin them (MoMask/GenMM), and move back to the clip's frame. Returns ({foot: track}, {foot: contacts},
    ground velocity). Feet with no contact keep their own track."""
    names = list(tracks)
    tr = [tracks[k] for k in names]
    size = max(float(size), 1e-9)
    H = height if height is not None else 0.04 * size
    S = speed if speed is not None else 0.012 * size
    if not tr or len(tr[0]) < 2: return dict(tracks), {k: [] for k in names}, (0.0, 0.0, 0.0)
    floor = floor_height(tr) if floor is None else floor
    v = velocity if velocity is not None else ground_velocity(tr, floor, H, loop)
    n = len(tr[0])
    out, contacts = {}, {}
    for k, t in zip(names, tr):
        g = to_ground(t, v)
        if loop and any(v):
            # a loop in the ground's frame does not close: the seam frame's neighbour is one cycle on. Unroll three
            # cycles, solve the middle one.
            g3 = to_ground(t + t + t, v)
            c3 = detect_contacts(g3, floor, H, S, loop=False)
            p3 = pin_contacts(g3, c3, floor=floor, blend=blend, loop=False)
            c, p = c3[n:2 * n], from_ground(p3, v)[n:2 * n]
        else:
            c = detect_contacts(g, floor, H, S, loop=loop)
            p = from_ground(pin_contacts(g, c, floor=floor, blend=blend, loop=loop), v)
        out[k], contacts[k] = p, c
    return out, contacts, v

# SPDX-License-Identifier: GPL-3.0-or-later
"""Seeded foliage atlas artwork adapted from storm-chaser/Tools/gen_leaves.py.

See docs/MODEL-STUDIO.md for provenance. build() returns an RGBA Pillow image.
"""
import math
import random

from PIL import Image, ImageDraw, ImageFilter

CELL = 512
SS = 2                      # supersampling
C = CELL * SS


def hexc(s, a=255):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), a)


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def jitter(c, rng, amount):
    v = rng.uniform(1 - amount, 1 + amount)
    return tuple(max(0, min(255, int(c[i] * v))) for i in range(3)) + (c[3] if len(c) > 3 else 255,)


# palettes, kin to gen_models_nature.py's so TITAN keeps the countryside's colours
OAK = [hexc("#1c4a26"), hexc("#2f7034"), hexc("#4a9444"), hexc("#7cbc5c")]
ROUND = [hexc("#36602a"), hexc("#55883a"), hexc("#7fae4c"), hexc("#b0d070")]
BUSH = [hexc("#2a4c22"), hexc("#3f6e2e"), hexc("#5f9440"), hexc("#94c060")]
PINE = [hexc("#173c29"), hexc("#225236"), hexc("#2e6a44"), hexc("#428456"), hexc("#5c9c6a")]
LITTER = [hexc("#5a3a1c"), hexc("#8a5a26"), hexc("#b07a32"), hexc("#7a7a30"), hexc("#4f6a2a")]
TWIG = [hexc("#2e1f14"), hexc("#4a3222"), hexc("#6a4a30"), hexc("#8a6a4a")]
GRASS = [hexc("#2f5a22"), hexc("#4a7c30"), hexc("#6c9c40"), hexc("#94bc58")]


def leaf_outline(base, tip, width, lobes=0, rng=None):
    """A leaf's outline from `base` to `tip`: pointed at the tip, rounded at the base, optionally lobed like an oak."""
    bx, by = base
    tx, ty = tip
    dx, dy = tx - bx, ty - by
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    left, right = [], []
    steps = 18
    for i in range(steps + 1):
        t = i / steps
        w = math.sin(math.pi * min(1.0, t * 1.08)) ** 0.8 * width * 0.5
        if lobes:
            w *= 0.72 + 0.28 * abs(math.sin(t * math.pi * lobes))
        if rng:
            w *= rng.uniform(0.94, 1.06)
        cx, cy = bx + dx * t, by + dy * t
        left.append((cx + px * w, cy + py * w))
        right.append((cx - px * w, cy - py * w))
    return left + right[::-1]


def draw_leaf(img, base, tip, width, pal, rng, lobes=0, light=(0.0, -1.0)):
    """One leaf: filled, lighter on the side towards the light, a darker midrib and a darker rim."""
    poly = leaf_outline(base, tip, width, lobes, rng)
    d = ImageDraw.Draw(img)
    # which way the leaf faces decides its shade: those pointing up catch the sky
    dx, dy = tip[0] - base[0], tip[1] - base[1]
    ln = math.hypot(dx, dy) or 1
    facing = (dx * light[0] + dy * light[1]) / ln * 0.5 + 0.5
    shade = min(len(pal) - 1.001, max(0.0, facing * (len(pal) - 1) + rng.uniform(-0.6, 0.6)))
    lo = int(shade)
    body = mix(pal[lo], pal[min(lo + 1, len(pal) - 1)], shade - lo)
    body = jitter(body, rng, 0.08)
    d.polygon(poly, fill=body)
    # the half of the leaf away from the light is a little darker
    half = poly[: len(poly) // 2 + 1] + [tip, base]
    d.polygon(half, fill=mix(body, (0, 0, 0, 255), 0.14))
    d.line(poly + [poly[0]], fill=mix(body, (0, 0, 0, 255), 0.35), width=max(1, SS))
    rib = mix(body, (230, 240, 190, 255), 0.25)
    d.line([base, (base[0] + dx * 0.9, base[1] + dy * 0.9)], fill=rib, width=max(1, SS))
    if width > 18 * SS:
        # side veins
        for k in range(1, 5):
            t = k / 5
            cx, cy = base[0] + dx * t, base[1] + dy * t
            px, py = -dy / ln, dx / ln
            for side in (-1, 1):
                ex = cx + (px * side * width * 0.32) + dx * 0.08
                ey = cy + (py * side * width * 0.32) + dy * 0.08
                d.line([(cx, cy), (ex, ey)], fill=mix(body, rib, 0.5), width=max(1, SS // 2))


def stick(img, a, b, w0, w1, pal, rng):
    """A tapering stick from a to b."""
    d = ImageDraw.Draw(img)
    n = 10
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        w = w0 + (w1 - w0) * t0
        d.line([p0, p1], fill=pal[1 + (i % 2)], width=max(1, int(w)))
    d.line([a, b], fill=pal[3], width=max(1, int(w1 * 0.4)))


def branchy(rng, origin, angle, length, depth, out):
    """A little branching twig: a list of (from, to, depth) segments, from the bottom middle up."""
    x, y = origin
    ex = x + math.cos(angle) * length
    ey = y - math.sin(angle) * length
    out.append(((x, y), (ex, ey), depth))
    if depth <= 0:
        return
    for _ in range(2 if depth > 1 else rng.choice((1, 2))):
        t = rng.uniform(0.45, 0.9)
        fx, fy = x + (ex - x) * t, y + (ey - y) * t
        branchy(rng, (fx, fy), angle + rng.uniform(-0.9, 0.9), length * rng.uniform(0.5, 0.7), depth - 1, out)


def spray(rng, pal, leaf_len, leaf_w, count, lobes=0, depth=3, spread=1.0, stem=TWIG):
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    segs = []
    branchy(rng, (C * 0.5, C * 0.97), math.pi / 2 + rng.uniform(-0.1, 0.1), C * 0.3, depth, segs)
    for (a, b, dpt) in segs:
        stick(img, a, b, (dpt + 1) * 3.2 * SS, (dpt + 0.5) * 2.2 * SS, stem, rng)
    # leaves along the outer segments, back ones first and darker, so the cluster has depth
    spots = []
    for (a, b, dpt) in segs:
        if dpt > 2:
            continue
        for _ in range(count // max(1, sum(1 for s in segs if s[2] <= 2)) + 1):
            t = rng.uniform(0.15, 1.0) if dpt < 2 else rng.uniform(0.6, 1.0)
            spots.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, math.atan2(-(b[1] - a[1]), b[0] - a[0])))
    rng.shuffle(spots)
    for i, (x, y, along) in enumerate(spots):
        back = i < len(spots) * 0.35
        ang = along + rng.uniform(-1.3, 1.3) * spread
        L = leaf_len * rng.uniform(0.75, 1.15) * SS
        W = leaf_w * rng.uniform(0.8, 1.15) * SS
        tip = (x + math.cos(ang) * L, y - math.sin(ang) * L)
        p = [mix(c, (0, 0, 0, 255), 0.25) for c in pal] if back else pal
        draw_leaf(img, (x, y), tip, W, p, rng, lobes)
    return img


def pine_spray(rng):
    """A fir bough filling its card: a stem up the middle and dense side branchlets the whole way, longest near the
    foot and shortening to the tip, each thick with short needles, lit lighter on top. From a few metres it reads as a
    mass of needles, not a stick on paper."""
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    foot, tip = (C * 0.5, C * 0.98), (C * 0.5, C * 0.04)
    stick(img, foot, tip, 7 * SS, 2 * SS, TWIG, rng)
    rows = 34
    for k in range(rows):
        t = k / (rows - 1)
        y = foot[1] + (tip[1] - foot[1]) * t
        x = foot[0]
        reach = C * 0.46 * (1.0 - 0.8 * t) + C * 0.04
        for side in (-1, 1):
            ang = math.pi / 2 - side * rng.uniform(0.85, 1.15)   # up and out
            L = reach * rng.uniform(0.85, 1.05)
            ex, ey = x + math.cos(ang) * L, y - math.sin(ang) * L
            d.line([(x, y), (ex, ey)], fill=TWIG[1], width=max(1, int(2 * SS)))
            n = int(L / (2.2 * SS))
            for i in range(1, n):
                u = i / n
                px, py = x + (ex - x) * u, y + (ey - y) * u
                for s2 in (-1, 1):
                    a2 = ang + s2 * rng.uniform(0.7, 1.1)
                    nl_ = rng.uniform(12, 20) * SS * (1.0 - 0.4 * u)
                    shade = min(len(PINE) - 1, max(0, int((0.35 + 0.65 * (s2 > 0) * 0.5 + rng.uniform(-0.2, 0.35)) * len(PINE))))
                    col = jitter(PINE[shade], rng, 0.08)
                    d.line([(px, py), (px + math.cos(a2) * nl_, py - math.sin(a2) * nl_)], fill=col, width=max(1, int(2.0 * SS)))
    return img


def litter(rng):
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    cx = cy = C / 2
    for i in range(150):
        r = C * 0.42 * math.sqrt(rng.random())
        a = rng.uniform(0, math.tau)
        x, y = cx + math.cos(a) * r, cy + math.sin(a) * r
        if not (C * 0.08 < x < C * 0.92 and C * 0.08 < y < C * 0.92):
            continue
        ang = rng.uniform(0, math.tau)
        L = rng.uniform(26, 46) * SS
        pal = [jitter(LITTER[rng.randrange(len(LITTER))], rng, 0.15) for _ in range(3)]
        draw_leaf(img, (x, y), (x + math.cos(ang) * L, y + math.sin(ang) * L), L * rng.uniform(0.45, 0.7), pal, rng, rng.choice((0, 0, 3)))
    return img


def twigs(rng):
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    for k in range(3):
        a = rng.uniform(0, math.pi)
        cx, cy = C * rng.uniform(0.35, 0.65), C * rng.uniform(0.35, 0.65)
        L = C * rng.uniform(0.25, 0.4)
        p0 = (cx - math.cos(a) * L, cy - math.sin(a) * L)
        p1 = (cx + math.cos(a) * L, cy + math.sin(a) * L)
        stick(img, p0, p1, 9 * SS, 5 * SS, TWIG, rng)
        for _ in range(2):
            t = rng.uniform(0.3, 0.8)
            fx, fy = p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t
            b = a + rng.choice((-1, 1)) * rng.uniform(0.4, 0.9)
            stick(img, (fx, fy), (fx + math.cos(b) * L * 0.35, fy + math.sin(b) * L * 0.35), 5 * SS, 2 * SS, TWIG, rng)
    return img


def blades(img, rng, n, height):
    d = ImageDraw.Draw(img)
    for _ in range(n):
        x = C * 0.5 + rng.gauss(0, C * 0.12)
        lean = rng.uniform(-0.35, 0.35)
        h = C * height * rng.uniform(0.6, 1.0)
        tip = (x + lean * h, C * 0.98 - h)
        col = jitter(GRASS[rng.randrange(len(GRASS))], rng, 0.1)
        w = rng.uniform(5, 9) * SS
        d.polygon([(x - w / 2, C * 0.98), (x + w / 2, C * 0.98), tip], fill=col)


def flower(d, x, y, r, petal, centre, rng, petals=6):
    for k in range(petals):
        a = k / petals * math.tau + rng.uniform(-0.2, 0.2)
        px, py = x + math.cos(a) * r * 0.6, y + math.sin(a) * r * 0.6
        d.ellipse([px - r * 0.5, py - r * 0.5, px + r * 0.5, py + r * 0.5], fill=jitter(petal, rng, 0.05))
    d.ellipse([x - r * 0.35, y - r * 0.35, x + r * 0.35, y + r * 0.35], fill=centre)


def flowers(rng):
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    blades(img, rng, 26, 0.75)
    d = ImageDraw.Draw(img)
    for k in range(7):
        x = C * 0.5 + rng.gauss(0, C * 0.14)
        top = C * rng.uniform(0.2, 0.5)
        d.line([(x, C * 0.98), (x + rng.uniform(-20, 20) * SS, top)], fill=GRASS[1], width=int(3 * SS))
        white = k % 3 != 0
        flower(d, x, top, rng.uniform(22, 30) * SS, hexc("#f4f4ea") if white else hexc("#f2c830"), hexc("#e8b020") if white else hexc("#b07818"), rng)
    return img


def clump(rng, dry):
    """A tussock of prairie grass: a dense fan of blades from one root, 0.9 m on the card, a few seed heads over it.
    Two cards crossed stand in for a clump of real turf among the single blades (Sam, 2026-10-11: the ground read
    as grey sand with sparse needles)."""
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    pal = [hexc("#6a7a34"), hexc("#8a9a44"), hexc("#a8b058"), hexc("#c4c270")] if dry else GRASS
    d = ImageDraw.Draw(img)
    for _ in range(170):
        x = C * 0.5 + rng.gauss(0, C * 0.05)
        lean = rng.gauss(0, 0.5)
        h = C * rng.uniform(0.35, 0.92)
        # a blade bows over: a short polyline from the root out and up, then over
        pts = [(x, C * 0.98)]
        for k in range(1, 5):
            t = k / 4
            pts.append((x + lean * h * t * t * 1.1, C * 0.98 - h * t * (1.0 - 0.15 * t * t)))
        col = jitter(pal[rng.randrange(len(pal))], rng, 0.12)
        d.line(pts, fill=col, width=int(rng.uniform(4, 8) * SS), joint="curve")
    for _ in range(9):
        x = C * 0.5 + rng.gauss(0, C * 0.08)
        top = C * rng.uniform(0.06, 0.3)
        d.line([(x, C * 0.98), (x + rng.uniform(-40, 40) * SS, top)], fill=pal[1], width=int(2.5 * SS))
        d.ellipse([x - 6 * SS, top - 16 * SS, x + 6 * SS, top + 16 * SS], fill=jitter(hexc("#b8a868") if dry else hexc("#8c9c58"), rng, 0.1))
    return img


def weeds(rng):
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    # a rosette of broad leaves
    for k in range(9):
        a = math.pi * (0.1 + 0.8 * k / 8) + rng.uniform(-0.1, 0.1)
        L = C * rng.uniform(0.3, 0.45)
        base = (C * 0.5, C * 0.97)
        draw_leaf(img, base, (base[0] + math.cos(a) * L, base[1] - math.sin(a) * L), L * 0.42, GRASS, rng, 0)
    d = ImageDraw.Draw(img)
    for k in range(3):
        x = C * 0.5 + rng.uniform(-90, 90) * SS
        top = C * rng.uniform(0.15, 0.3)
        d.line([(x, C * 0.9), (x, top)], fill=GRASS[1], width=int(4 * SS))
        for _ in range(26):
            a, r = rng.uniform(0, math.tau), rng.uniform(0, 26) * SS
            px, py = x + math.cos(a) * r, top + math.sin(a) * r * 0.8
            d.ellipse([px - 7 * SS, py - 7 * SS, px + 7 * SS, py + 7 * SS], fill=jitter(hexc("#a65fb8"), rng, 0.15))
    return img


CORN = [hexc("#173d17"), hexc("#21521f"), hexc("#2e6a28"), hexc("#3f8032"), hexc("#5a9a3c")]
CORN_DRY = hexc("#9a8a48")
TASSEL = [hexc("#7d6a3a"), hexc("#9a8650"), hexc("#b29e66")]


def strap_leaf(img, base, side, rise, reach, droop, width, pal, rng):
    """A long corn leaf from the stalk at `base`: up and out to `side` (-1 left, 1 right), arching over and drooping,
    widest a third of the way out and tapering to a point, a paler rib down its middle."""
    d = ImageDraw.Draw(img)
    pts_l, pts_r, rib = [], [], []
    steps = 24
    for i in range(steps + 1):
        t = i / steps
        # a quadratic arch: up and out, then over and down
        x = base[0] + side * reach * t
        y = base[1] - rise * (2 * t - t * t) * 1.0 + droop * t * t * t
        w = width * (math.sin(math.pi * min(1.0, t * 1.1 + 0.05)) ** 0.7) * (1.0 - 0.75 * t)
        # the normal of the curve, roughly
        nx, ny = 0.0, 1.0
        if i > 0:
            px, py = rib[-1]
            dx, dy = x - px, y - py
            ln = math.hypot(dx, dy) or 1
            nx, ny = -dy / ln, dx / ln
        rib.append((x, y))
        pts_l.append((x + nx * w, y + ny * w))
        pts_r.append((x - nx * w, y - ny * w))
    shade = rng.randrange(1, len(pal))
    body = jitter(pal[shade], rng, 0.08)
    d.polygon(pts_l + pts_r[::-1], fill=body)
    # the underside half a touch darker, for a fold down the rib
    d.polygon(pts_r + rib[::-1], fill=mix(body, (0, 0, 0, 255), 0.18))
    d.line(rib, fill=mix(body, (220, 235, 170, 255), 0.25), width=max(1, SS))


def corn_plant(rng, ear_side):
    """A whole corn plant, 2 m tall on the card: a stalk up the middle, long strap leaves alternating side to side and
    arching over, the lowest ones drying, an ear in its husk, and a tassel of straw spikes on top."""
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    foot, top = (C * 0.5, C * 0.995), C * 0.1
    # the stalk, a little thicker at the foot
    for i in range(40):
        t0, t1 = i / 40, (i + 1) / 40
        y0, y1 = foot[1] + (top - foot[1]) * t0, foot[1] + (top - foot[1]) * t1
        w = (14 - 8 * t0) * SS
        x = foot[0] + math.sin(t0 * 3.0) * 3 * SS
        d.line([(x, y0), (x, y1)], fill=CORN[1 + (i % 2)], width=int(w))
    side = 1 if rng.random() < 0.5 else -1
    nodes = 9
    for k in range(nodes):
        t = 0.1 + 0.72 * k / (nodes - 1)
        y = foot[1] + (top - foot[1]) * t
        x = foot[0] + math.sin(t * 3.0) * 3 * SS
        reach = C * rng.uniform(0.32, 0.46) * (1.0 - 0.35 * t)
        rise = C * rng.uniform(0.08, 0.16)
        droop = C * rng.uniform(0.12, 0.26)
        pal = CORN
        if k < 2:   # the lowest leaves are drying out
            pal = [mix(c, CORN_DRY, 0.55) for c in CORN]
        strap_leaf(img, (x, y), side, rise, reach, droop, rng.uniform(18, 26) * SS, pal, rng)
        side = -side
    # the ear in its husk, standing out from the stalk half way up
    ey = foot[1] + (top - foot[1]) * 0.45
    ex = foot[0] + ear_side * 22 * SS
    ear = [(foot[0], ey + 30 * SS), (ex + ear_side * 10 * SS, ey - 10 * SS), (ex + ear_side * 26 * SS, ey - 70 * SS),
           (ex + ear_side * 6 * SS, ey - 60 * SS), (foot[0] + ear_side * 6 * SS, ey)]
    d.polygon(ear, fill=hexc("#6f9a44"))
    d.line([ear[2], (ear[2][0] + ear_side * 8 * SS, ear[2][1] - 18 * SS)], fill=hexc("#c8a868"), width=3 * SS)
    # the tassel: straw spikes fanning up from the top of the stalk
    tx, ty = foot[0], top
    for k in range(13):
        a = math.pi / 2 + rng.uniform(-0.9, 0.9)
        L = C * rng.uniform(0.05, 0.11)
        col = TASSEL[rng.randrange(len(TASSEL))]
        d.line([(tx, ty), (tx + math.cos(a) * L, ty - math.sin(a) * L)], fill=col, width=int(3 * SS))
    return img


def harden(cell):
    """Scale down to the cell size and make the alpha hard enough for a 0.5 cutoff, with a soft one-pixel edge."""
    cell = cell.resize((CELL, CELL), Image.LANCZOS)
    r, g, b, a = cell.split()
    a = a.point(lambda v: 0 if v < 70 else 255 if v > 150 else int((v - 70) * 255 / 80))
    # the colour of transparent pixels bleeds out from the leaves, so filtering never pulls in black fringes
    rgb = Image.merge("RGB", (r, g, b))
    grown = rgb
    for _ in range(6):
        grown = grown.filter(ImageFilter.MaxFilter(3))
    rgb = Image.composite(rgb, grown, a.point(lambda v: 255 if v > 0 else 0))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def build(seed=2718):
    rng = random.Random(seed)
    cells = [
        spray(rng, OAK, 72, 46, 90, lobes=3),
        spray(rng, ROUND, 54, 44, 120),
        spray(rng, BUSH, 40, 26, 190, depth=4, spread=1.4),
        pine_spray(rng),
        litter(rng),
        twigs(rng),
        flowers(rng),
        weeds(rng),
    ]
    # the third row: two corn plants (FoliageTitan.Corn, Corn2)
    cells.append(corn_plant(rng, 1))
    cells.append(corn_plant(rng, -1))
    # and two grass clumps (FoliageTitan.Clump, ClumpDry)
    cells.append(clump(rng, False))
    cells.append(clump(rng, True))
    atlas = Image.new("RGBA", (CELL * 4, CELL * 3), (0, 0, 0, 0))
    for i, cell in enumerate(cells):
        atlas.paste(harden(cell), ((i % 4) * CELL, (i // 4) * CELL))
    return atlas

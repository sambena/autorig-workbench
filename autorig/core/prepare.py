# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: decisions for the prepare step (steps/prepare.py).
#
# Prepare writes a cleaned copy of a source mesh before rigging. The raw file stays where it is.
# This module is pure Python: the yaw, the feet offset, the UV stretch check and the joint-band
# check. Blender applies them. Nothing here calls Quad Remesher or any other remesher. A full
# remesh throws away the sculpt and the UVs the texture was baked against.

import copy
import math


# An upright body, as a fraction of its height up from the feet. These are where a person
# bends. They are a guess, used only to say whether that band is short of edges.
BANDS = (
    ("knee", 0.22, 0.32),
    ("hip", 0.45, 0.55),
    ("elbow", 0.58, 0.68),
    ("shoulder", 0.72, 0.82),
)

# Fewer edges than this crossing the middle of a band, on an upright body that has mesh there,
# and the band is called thin. Two legs with a single ring of quads sit around this line.
THIN_CROSSINGS = 8

# Shape stretch is the ratio of the two singular values of the UV-to-3D map. 1 is even.
# A baked texture is left alone unless the tail of that ratio, or a block of missing UVs, fails.
USABLE_P95 = 2.5
USABLE_HOT = 4.0
USABLE_HOT_SHARE = 0.02
USABLE_MISSING_SHARE = 0.05

NEG_Y = (0.0, -1.0, 0.0)


def as_forward(value):
    """A facing in the ground plane, or None when the value is not one (a word, straight up, empty)."""
    if isinstance(value, str) or value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
        z = float(value[2]) if len(value) > 2 else 0.0
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(c) for c in (x, y, z)):
        return None
    if math.hypot(x, y) < 1e-8:
        return None
    return (x, y, z)


def is_neg_y(forward):
    """True when the ground-plane part of forward already points along -Y."""
    v = as_forward(forward)
    if not v:
        return False
    n = math.hypot(v[0], v[1])
    return (-v[1] / n) >= 0.999


def raw_forward(spec_forward, saved_before, same_source):
    """Which way the raw file faces, before this prepare run.

    None means measure the mesh. A re-run on the same file uses the facing recorded last time,
    never the spec: that spec may already say -Y because prepare rewrote it. A file that changed
    since then is measured again. A first run uses the spec when it names a real direction.
    """
    if same_source:
        v = as_forward(saved_before)
        return list(v) if v else None
    if saved_before is not None:
        return None
    v = as_forward(spec_forward)
    return list(v) if v else None


def yaw_to_neg_y(forward):
    """Radians about Z that turn this facing onto -Y. The same difference rerig.normalise uses."""
    v = as_forward(forward)
    if v is None:
        return 0.0
    return math.atan2(-1.0, 0.0) - math.atan2(v[1], v[0])


def snap_quarter(yaw):
    """Snap a yaw to the nearest right angle, as normalise does unless the spec sets straighten."""
    q = math.pi / 2.0
    return round(yaw / q) * q


def feet_offset(lo, hi, origin="feet"):
    """Translation that centres the body on x=0 and y=0 and puts the feet (or the middle) on z=0.

    lo and hi are the two corners of the bounding box, each (x, y, z).
    """
    cx = (lo[0] + hi[0]) * 0.5
    cy = (lo[1] + hi[1]) * 0.5
    if origin == "center":
        cz = (lo[2] + hi[2]) * 0.5
    else:
        cz = lo[2]
    return (-cx, -cy, -cz)


def tri_stretch(p0, p1, p2, q0, q1, q2):
    """Shape stretch of one triangle: max/min singular value of its UV-to-3D map.

    1 means the triangle is scaled evenly. None means the triangle, or its UVs, has no area.
    Texel size cancels out of the ratio, so a uniformly small UV island is not called stretched.
    """
    e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
    u1 = (q1[0] - q0[0], q1[1] - q0[1])
    u2 = (q2[0] - q0[0], q2[1] - q0[1])
    det = u1[0] * u2[1] - u1[1] * u2[0]
    if abs(det) < 1e-12:
        return None
    # [e1 e2] = [Pu Pv] * [u1 u2], so [Pu Pv] = [e1 e2] * inverse([u1 u2]).
    inv00, inv01 = u2[1] / det, -u2[0] / det
    inv10, inv11 = -u1[1] / det, u1[0] / det
    pu = (e1[0] * inv00 + e2[0] * inv10, e1[1] * inv00 + e2[1] * inv10, e1[2] * inv00 + e2[2] * inv10)
    pv = (e1[0] * inv01 + e2[0] * inv11, e1[1] * inv01 + e2[1] * inv11, e1[2] * inv01 + e2[2] * inv11)
    e = pu[0] * pu[0] + pu[1] * pu[1] + pu[2] * pu[2]
    g = pv[0] * pv[0] + pv[1] * pv[1] + pv[2] * pv[2]
    f = pu[0] * pv[0] + pu[1] * pv[1] + pu[2] * pv[2]
    disc = math.sqrt(max(0.0, (e - g) * (e - g) + 4.0 * f * f))
    s1 = math.sqrt(max(0.0, (e + g + disc) * 0.5))
    s2 = math.sqrt(max(0.0, (e + g - disc) * 0.5))
    if s1 < 1e-12 or s2 < 1e-12:
        return None
    return max(s1, s2) / min(s1, s2)


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    i = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[max(0, min(len(sorted_vals) - 1, i))]


def uv_report(ratios, missing=0, total=None):
    """Whether the UVs can stay. ratios are per-face stretch values from tri_stretch (None already dropped).

    missing counts faces that had no UV area. total defaults to the faces that were measured plus those.
    """
    finite = sorted(float(r) for r in ratios if r is not None and math.isfinite(r) and r > 0)
    total = len(finite) + int(missing) if total is None else int(total)
    missing_share = (float(missing) / total) if total else 1.0
    if not finite:
        return {
            "usable": False, "faces": 0, "missing_share": round(missing_share, 4),
            "note": "no UVs to check. Smart UV Project (--unwrap) would make some; a baked texture will not follow them.",
        }
    p95 = _percentile(finite, 0.95)
    median = _percentile(finite, 0.50)
    hot = sum(1 for r in finite if r > USABLE_HOT) / len(finite)
    usable = (p95 <= USABLE_P95 and hot <= USABLE_HOT_SHARE and missing_share <= USABLE_MISSING_SHARE)
    if usable:
        note = "UVs are usable; left unchanged."
    else:
        note = ("UVs are stretched (p95 %.2f). Re-run with --unwrap for Smart UV Project. "
                "A baked texture will not follow the new UVs." % p95)
    return {
        "usable": usable,
        "faces": len(finite),
        "median": round(median, 4),
        "p95": round(p95, 4),
        "worst": round(finite[-1], 4),
        "hot_share": round(hot, 4),
        "missing_share": round(missing_share, 4),
        "note": note,
    }


def crossings(verts, edges, z):
    """How many edges cross the horizontal plane z. A ring of quads around a limb counts once per edge."""
    n = 0
    nv = len(verts)
    for edge in edges:
        if len(edge) < 2:
            continue
        a, b = edge[0], edge[1]
        if not (0 <= a < nv and 0 <= b < nv):
            continue
        za, zb = verts[a][2], verts[b][2]
        if (za - z) * (zb - z) < 0:
            n += 1
    return n


def joint_report(verts, edges, lo, hi):
    """Edge counts across the knee, hip, elbow and shoulder of an upright body.

    A body that is wider than it is tall is left alone: those heights would not be its joints.
    A thin band wants one more loop, cut by hand or with --loops. This does not remesh.
    """
    size = [hi[i] - lo[i] for i in range(3)]
    upright = size[2] >= 1.15 * max(size[0], size[1], 1e-9)
    if not upright:
        return {
            "upright": False, "bands": [], "thin": [],
            "note": "Not an upright body, so shoulder, elbow, hip and knee bands were not guessed.",
        }
    h = max(size[2], 1e-9)
    bands = []
    thin = []
    for name, a, b in BANDS:
        z0 = lo[2] + a * h
        z1 = lo[2] + b * h
        z_mid = (z0 + z1) * 0.5
        inside = sum(1 for v in verts if z0 <= v[2] <= z1)
        n = crossings(verts, edges, z_mid)
        # A long face across a joint has no vertex in the band and only a few edges through it.
        # Nothing crossing the plane is empty air, not a thin joint.
        is_thin = 0 < n < THIN_CROSSINGS
        bands.append({
            "name": name, "z": round(z_mid, 4), "crossings": n, "verts": inside, "thin": is_thin,
        })
        if is_thin:
            thin.append(name)
    if thin:
        note = ("Thin at %s: add a loop there (--loops), or fix it by hand. "
                "A full remesh throws away the UVs and the sculpt." % ", ".join(thin))
    else:
        note = "Joint bands have enough edges crossing them."
    return {"upright": True, "bands": bands, "thin": thin, "note": note}


def _fmt_forward(before):
    v = as_forward(before)
    if not v:
        return "unknown"
    return "[%s]" % ", ".join("%g" % round(c, 4) for c in v)


def rewrite_forward(data, before):
    """A copy of a rig.json with rig.forward, and humanoid.forward when it names one, set to -Y.

    Returns None when both already face -Y or neither names a direction. Unknown keys are kept.
    The note says what the raw file faced, so deleting prepared/ has a way back.
    """
    if not isinstance(data, dict):
        return None
    out = copy.deepcopy(data)
    changed = False
    rig = out.get("rig")
    if isinstance(rig, dict) and as_forward(rig.get("forward")) and not is_neg_y(rig.get("forward")):
        rig["forward"] = [0, -1, 0]
        changed = True
    hum = out.get("humanoid")
    if isinstance(hum, dict) and as_forward(hum.get("forward")) and not is_neg_y(hum.get("forward")):
        hum["forward"] = [0, -1, 0]
        changed = True
    if not changed:
        return None
    notes = out.get("notes")
    if not isinstance(notes, dict):
        notes = {}
        out["notes"] = notes
    notes["rig.forward"] = (
        "Prepare turned the mesh to face -Y. The raw file faced %s. "
        "Delete prepared/ and put this forward back to rig the raw file." % _fmt_forward(before)
    )
    return out

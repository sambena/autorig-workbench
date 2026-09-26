# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Automated Hand & Paw Digit Articulation.
#
# Generates anatomically proportioned 3-segment phalanges for humanoids (Thumb, Index, Middle,
# Ring, Pinky) and multi-toe/claw chains for quadrupeds and creatures.

import math

DIGIT_NAMES_HUMANOID = ["Thumb", "Index", "Middle", "Ring", "Pinky"]
DIGIT_NAMES_PAW = ["digit_1", "digit_2", "digit_3", "digit_4"]


def generate_humanoid_digits(wrist, knuckle, tip, side="Left", hand_width=None, num_fingers=5):
    """Synthesizes articulated finger chains from wrist, knuckle, and tip landmarks.
    wrist, knuckle, tip are 3D coordinate tuples/vectors (x, y, z).
    Returns list of chain dicts:
      [
        {"role": "finger", "name": "LeftHandIndex", "bones": ["LeftHandIndex1", "LeftHandIndex2", "LeftHandIndex3"],
         "points": [p0, p1, p2, p3], "parent_bone": side + "Hand"},
        ...
      ]
    """
    wx, wy, wz = wrist
    kx, ky, kz = knuckle
    tx, ty, tz = tip

    # Forward direction along palm
    fx = tx - wx
    fy = ty - wy
    fz = tz - wz
    flen = math.sqrt(fx * fx + fy * fy + fz * fz)
    if flen < 1e-5:
        fx, fy, fz = (0.0, -1.0, 0.0) if wy > ty else (0.0, 1.0, 0.0)
        flen = 1.0
    fx /= flen
    fy /= flen
    fz /= flen

    # Palm width estimate
    w_span = hand_width if (hand_width and hand_width > 1e-4) else (flen * 0.42)

    # Lateral direction across the palm, from the thumb side (negative offsets) to the little finger. In a T-pose
    # (arm along X, palms down) the thumbs of both hands point forward (-Y), so the lateral is +Y on both sides: it
    # does not flip with the side (flipping it put the right thumb behind the hand). Only when the arm runs along Y or
    # Z does the lateral run across X, and there it mirrors.
    if abs(fx) > 0.7:  # arm along X
        lx, ly, lz = 0.0, 1.0, 0.0
    else:  # arm along Y or Z
        lx, ly, lz = (1.0 if side == "Left" else -1.0), 0.0, 0.0

    chains = []
    # Finger layout configs: (name, lateral_offset, length_mult, angle_offset)
    finger_configs = [
        ("Thumb",  -0.45, 0.72,  0.45),
        ("Index",  -0.18, 0.95,  0.08),
        ("Middle",  0.02, 1.00,  0.00),
        ("Ring",    0.20, 0.92, -0.08),
        ("Pinky",   0.38, 0.75, -0.18),
    ]

    if num_fingers == 3:
        finger_configs = [
            ("Thumb",  -0.40, 0.75, 0.40),
            ("Index",   0.00, 1.00, 0.00),
            ("Pinky",   0.35, 0.80, -0.20),
        ]
    elif num_fingers == 4:
        finger_configs = [
            ("Thumb",  -0.42, 0.75, 0.40),
            ("Index",  -0.12, 0.96, 0.05),
            ("Middle",  0.10, 1.00, -0.05),
            ("Pinky",   0.32, 0.78, -0.18),
        ]

    palm_len = flen * 0.48
    finger_base_len = flen * 0.52

    for fname, lat_off, len_mult, ang in finger_configs:
        bone_names = [f"{side}Hand{fname}1", f"{side}Hand{fname}2", f"{side}Hand{fname}3"]
        total_len = finger_base_len * len_mult

        # Knuckle position
        k_x = wx + fx * palm_len + lx * (w_span * lat_off)
        k_y = wy + fy * palm_len + ly * (w_span * lat_off)
        k_z = wz + fz * palm_len + lz * (w_span * lat_off)

        # Segment lengths: proximal 42%, intermediate 32%, distal 26%
        l1 = total_len * 0.42
        l2 = total_len * 0.32
        l3 = total_len * 0.26

        # Finger direction with a subtle spread, away from the middle finger: toward the finger's own side of the
        # palm (the thumb out one way, the little finger the other), never across the hand
        spread = abs(ang) * (1.0 if lat_off > 0 else -1.0 if lat_off < 0 else 0.0)
        cos_a = math.cos(spread)
        sin_a = math.sin(spread)
        fdx = fx * cos_a + lx * sin_a
        fdy = fy * cos_a + ly * sin_a
        fdz = fz * cos_a + lz * sin_a

        p0 = (k_x, k_y, k_z)
        p1 = (k_x + fdx * l1, k_y + fdy * l1, k_z + fdz * l1)
        p2 = (p1[0] + fdx * l2, p1[1] + fdy * l2, p1[2] + fdz * l2)
        p3 = (p2[0] + fdx * l3, p2[1] + fdy * l3, p2[2] + fdz * l3)

        chains.append({
            "role": "finger",
            "name": f"{side}Hand{fname}",
            "finger": fname,
            "side": side,
            "bones": bone_names,
            "points": [p0, p1, p2, p3],
            "parent_bone": f"{side}Hand"
        })

    return chains


def generate_paw_digits(ankle, foot, toe, side="Left", paw_width=None, num_claws=4, parent_bone=None):
    """Synthesizes claw/digit chains for quadrupeds/creatures. parent_bone: the foot bone they hang from (the rig's
    own name for it, e.g. leg_hind_3.L); by default foot.L / foot.R."""
    ax, ay, az = ankle
    fx, fy, fz = foot
    tx, ty, tz = toe

    dx = tx - fx
    dy = ty - fy
    dz = tz - fz
    dlen = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dlen < 1e-5:
        dx, dy, dz = (0.0, -1.0, 0.0)
        dlen = 1.0
    dx /= dlen
    dy /= dlen
    dz /= dlen

    width = paw_width if (paw_width and paw_width > 1e-4) else (dlen * 0.50)
    # Lateral axis
    lx = 1.0 if side == "Left" else -1.0
    ly = 0.0
    lz = 0.0

    chains = []
    offsets = [-0.35, -0.12, 0.12, 0.35] if num_claws == 4 else [-0.30, 0.0, 0.30]

    for i, off in enumerate(offsets):
        dname = f"toe_{i+1}.{side[0]}"
        bnames = [f"toe_{i+1}_1.{side[0]}", f"toe_{i+1}_2.{side[0]}"]
        l1 = dlen * 0.55
        l2 = dlen * 0.45

        k_x = fx + lx * (width * off)
        k_y = fy + ly * (width * off)
        k_z = fz + lz * (width * off)

        p0 = (k_x, k_y, k_z)
        p1 = (k_x + dx * l1, k_y + dy * l1, k_z + dz * l1)
        p2 = (p1[0] + dx * l2, p1[1] + dy * l2, p1[2] + dz * l2)

        chains.append({
            "role": "toe",
            "name": dname,
            "side": side,
            "bones": bnames,
            "points": [p0, p1, p2],
            "parent_bone": parent_bone or f"foot.{side[0]}"
        })

    return chains


def compute_finger_curl_angles(state="relax", curl_intensity=1.0):
    """Returns (pitch_curl_deg, spread_deg) for finger animation poses:
    - 'relax': gentle anatomical resting curve (idle)
    - 'fist': tight grip (combat attack/smash)
    - 'splay': wide extension (sprint/strike)
    - 'point': index pointing, others curled
    """
    scale = max(0.0, min(2.5, float(curl_intensity)))
    if state == "fist":
        # Tight fist: proximal 65 deg, intermediate 85 deg, distal 55 deg
        return {
            "Thumb": (45.0 * scale, -15.0 * scale),
            "Index": (75.0 * scale, 0.0),
            "Middle": (80.0 * scale, 0.0),
            "Ring": (78.0 * scale, 0.0),
            "Pinky": (72.0 * scale, 0.0),
        }
    elif state == "splay":
        # Wide open hand
        return {
            "Thumb": (-15.0 * scale, -25.0 * scale),
            "Index": (-10.0 * scale, -12.0 * scale),
            "Middle": (-8.0 * scale, 0.0),
            "Ring": (-10.0 * scale, 12.0 * scale),
            "Pinky": (-14.0 * scale, 22.0 * scale),
        }
    elif state == "point":
        return {
            "Thumb": (40.0 * scale, -10.0 * scale),
            "Index": (0.0, 0.0),
            "Middle": (80.0 * scale, 0.0),
            "Ring": (78.0 * scale, 0.0),
            "Pinky": (72.0 * scale, 0.0),
        }
    else:  # 'relax' (default resting curve)
        return {
            "Thumb": (14.0 * scale, -5.0 * scale),
            "Index": (18.0 * scale, -2.0 * scale),
            "Middle": (22.0 * scale, 0.0),
            "Ring": (24.0 * scale, 3.0 * scale),
            "Pinky": (25.0 * scale, 6.0 * scale),
        }

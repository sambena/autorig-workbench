# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Procedural Biomechanical Gait Engine.
#
# Provides analytical, physically-consistent locomotion synthesis for biped/humanoid and
# quadruped/creature characters without external pip dependencies.
#
# Core Capabilities:
# 1. Non-skating stance constraint with foot-roll (heel strike -> foot flat -> heel rise -> toe push-off).
# 2. 6-DoF Pelvis kinematics: vertical oscillation (bob), lateral weight shift (sway), pelvic yaw, and hip drop.
# 3. Thoracic counter-rotation & spine lateral flexion for balanced, lifelike upper-body motion.
# 4. Reciprocal arm swing with forearm elbow lag and anatomical carrying angle.
# 5. True 4-beat lateral sequence walk ($LH \to LF \to RH \to RF$) and 2-beat trot for quadrupeds.
# 6. Parameterized style presets (Natural, Soldier, Swagger, Stealth, Heavy, Quadruped Walk, Trot).

import math

# ---------------------------------------------------------------------------------------------------------------
# Gait Presets and Tunable Parameters
# ---------------------------------------------------------------------------------------------------------------

GAIT_PRESETS = {
    "natural": {
        "label": "Natural / Balanced",
        "description": "Natural everyday biped walk with balanced arm swing and relaxed pelvis sway.",
        "stride": 1.0,
        "cadence": 1.0,
        "bob": 1.0,
        "sway": 1.0,
        "yaw": 1.0,
        "lean": 3.5,
        "hip_drop": 3.5,
        "counter_twist": 1.0,
        "arm_swing": 1.0,
        "arm_bend": 14.0,
        "knee_fold": 40.0,
        "foot_lift": 1.0,
        "duty_factor": 0.62,
    },
    "soldier": {
        "label": "Soldier / March",
        "description": "Disciplined, upright posture with sharp arm swing, reduced sway, and weapon-ready carrying angle.",
        "stride": 1.15,
        "cadence": 1.10,
        "bob": 1.25,
        "sway": 0.40,
        "yaw": 0.70,
        "lean": 1.0,
        "hip_drop": 2.0,
        "counter_twist": 0.60,
        "arm_swing": 1.60,
        "arm_bend": 22.0,
        "knee_fold": 45.0,
        "foot_lift": 1.20,
        "duty_factor": 0.60,
    },
    "swagger": {
        "label": "Swagger / Confident",
        "description": "Pronounced lateral hip sway, energetic pelvic twist, and relaxed trailing arm movement.",
        "stride": 1.05,
        "cadence": 0.90,
        "bob": 1.10,
        "sway": 2.20,
        "yaw": 1.50,
        "lean": 4.5,
        "hip_drop": 5.0,
        "counter_twist": 1.30,
        "arm_swing": 1.35,
        "arm_bend": 12.0,
        "knee_fold": 38.0,
        "foot_lift": 1.0,
        "duty_factor": 0.62,
    },
    "stealth": {
        "label": "Stealth / Crouch",
        "description": "Low center of gravity, deep knee flexion, quiet heel-toe roll, and minimal vertical bounce.",
        "stride": 0.85,
        "cadence": 0.75,
        "bob": 0.20,
        "sway": 0.60,
        "yaw": 0.80,
        "lean": 12.0,
        "hip_drop": 2.0,
        "counter_twist": 0.90,
        "arm_swing": 0.30,
        "arm_bend": 35.0,
        "knee_fold": 50.0,
        "foot_lift": 0.70,
        "duty_factor": 0.68,
    },
    "heavy": {
        "label": "Heavy / Armoured",
        "description": "Wide base of support, deliberate cadence, heavy foot stomp, and dampened arm swing.",
        "stride": 0.90,
        "cadence": 0.85,
        "bob": 1.40,
        "sway": 1.60,
        "yaw": 0.80,
        "lean": 6.0,
        "hip_drop": 3.0,
        "counter_twist": 0.80,
        "arm_swing": 0.70,
        "arm_bend": 20.0,
        "knee_fold": 35.0,
        "foot_lift": 0.90,
        "duty_factor": 0.66,
    },
    "run": {
        "label": "Biped Run",
        "description": "High-speed bipedal run with ballistic aerial flight phase, forward pelvic pitch, high knee flexion, and compact arm pump.",
        "stride": 1.40,
        "cadence": 1.50,
        "bob": 1.60,
        "sway": 0.50,
        "yaw": 1.10,
        "lean": 10.0,
        "hip_drop": 3.0,
        "counter_twist": 1.10,
        "arm_swing": 1.50,
        "arm_bend": 70.0,
        "knee_fold": 65.0,
        "foot_lift": 1.50,
        "duty_factor": 0.38,
    },
    "sprint": {
        "label": "Biped Sprint",
        "description": "Maximum velocity sprint with aggressive forward lean, deep knee drive, and rapid cadence.",
        "stride": 1.70,
        "cadence": 1.80,
        "bob": 1.90,
        "sway": 0.35,
        "yaw": 1.30,
        "lean": 16.0,
        "hip_drop": 2.0,
        "counter_twist": 1.25,
        "arm_swing": 1.80,
        "arm_bend": 85.0,
        "knee_fold": 78.0,
        "foot_lift": 1.80,
        "duty_factor": 0.32,
    },
    "quadruped_walk": {
        "label": "Quadruped Lateral Walk",
        "description": "Classic 4-beat lateral sequence walk with horizontal S-curve spine undulation.",
        "stride": 1.0,
        "cadence": 1.0,
        "bob": 0.80,
        "sway": 1.0,
        "yaw": 1.0,
        "lean": 0.0,
        "hip_drop": 0.0,
        "counter_twist": 0.0,
        "arm_swing": 0.0,
        "arm_bend": 0.0,
        "knee_fold": 35.0,
        "foot_lift": 1.0,
        "duty_factor": 0.65,
    },
    "quadruped_trot": {
        "label": "Quadruped Trot",
        "description": "High-speed 2-beat diagonal suspension trot with alternating diagonal pairs.",
        "stride": 1.30,
        "cadence": 1.50,
        "bob": 1.60,
        "sway": 0.50,
        "yaw": 0.50,
        "lean": 0.0,
        "hip_drop": 0.0,
        "counter_twist": 0.0,
        "arm_swing": 0.0,
        "arm_bend": 0.0,
        "knee_fold": 42.0,
        "foot_lift": 1.30,
        "duty_factor": 0.50,
    },
    "quadruped_gallop": {
        "label": "Quadruped Rotary Gallop",
        "description": "High-speed asymmetric rotary gallop with gathered and extended suspension flight phases and dynamic spine flexion.",
        "stride": 1.60,
        "cadence": 1.75,
        "bob": 2.0,
        "sway": 0.40,
        "yaw": 0.60,
        "lean": 0.0,
        "hip_drop": 0.0,
        "counter_twist": 0.0,
        "arm_swing": 0.0,
        "arm_bend": 0.0,
        "knee_fold": 55.0,
        "foot_lift": 1.60,
        "duty_factor": 0.32,
    },
}


def merge_gait_params(preset_name="natural", overrides=None):
    """Merges a base preset with optional user overrides, clamping values to safe physical bounds."""
    base = dict(GAIT_PRESETS.get(preset_name, GAIT_PRESETS["natural"]))
    if overrides and isinstance(overrides, dict):
        base.update({k: v for k, v in overrides.items() if k in base and isinstance(v, (int, float))})
    # Safety clamps
    base["stride"] = max(0.2, min(2.5, float(base["stride"])))
    base["cadence"] = max(0.2, min(3.0, float(base["cadence"])))
    base["bob"] = max(0.0, min(3.0, float(base["bob"])))
    base["sway"] = max(0.0, min(3.5, float(base["sway"])))
    base["yaw"] = max(0.0, min(3.0, float(base["yaw"])))
    base["lean"] = max(-10.0, min(40.0, float(base["lean"])))
    base["hip_drop"] = max(0.0, min(15.0, float(base["hip_drop"])))
    base["arm_swing"] = max(0.0, min(3.0, float(base["arm_swing"])))
    base["foot_lift"] = max(0.1, min(3.0, float(base["foot_lift"])))
    base["duty_factor"] = max(0.20, min(0.85, float(base["duty_factor"])))
    return base


# ---------------------------------------------------------------------------------------------------------------
# Mathematical Easing and Splines
# ---------------------------------------------------------------------------------------------------------------

def _smooth_step(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / max(1e-9, (edge1 - edge0))))
    return t * t * (3.0 - 2.0 * t)


def _hermite(p0, m0, p1, m1, t):
    """Cubic Hermite spline interpolation for smooth derivative transitions."""
    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    return h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1


# ---------------------------------------------------------------------------------------------------------------
# Biped Locomotion Synthesis
# ---------------------------------------------------------------------------------------------------------------

def evaluate_biped_walk(phase, hip_height, leg_length, params=None, is_shooter=False):
    """Synthesizes a complete biomechanical humanoid walk state at normalized cycle phase [0, 1).
    Phase 0.0 corresponds to Left Heel Strike; Phase 0.5 corresponds to Right Heel Strike.

    Returns a dict containing:
      pelvis: { pos: (x, y, z), rot: (pitch, roll, yaw) }
      spine:  { pitch, roll, yaw }
      chest:  { pitch, roll, yaw }
      head:   { pitch, roll, yaw }
      legs:   {
        L: { thigh_pitch, knee_pitch, foot_pitch, along, up },
        R: { thigh_pitch, knee_pitch, foot_pitch, along, up }
      }
      arms:   {
        L: { pitch, yaw, roll, forearm_pitch },
        R: { pitch, yaw, roll, forearm_pitch }
      }
    """
    p = merge_gait_params("natural", params)
    H = float(hip_height)
    L = float(leg_length)

    # Fundamental frequency: 1 full cycle = 2 steps (left and right)
    u = phase % 1.0
    w = 2.0 * math.pi * u

    # 1. Stride geometry
    base_thigh_amplitude = 22.0 * p["stride"]
    stride_distance = 2.0 * L * math.sin(math.radians(base_thigh_amplitude))
    lift_height = 0.07 * L * p["foot_lift"]

    # 2. Pelvis 6-DoF Kinematics
    # Vertical bounce (double frequency: bobs twice per walk cycle, lowest at double support)
    # Mid-stance is around phase 0.25 (Left) and 0.75 (Right) -> peak height
    pelvis_z = 0.022 * H * p["bob"] * math.cos(2.0 * w)

    # Lateral sway (single frequency: shifts over Left stance during 0..0.5, Right during 0.5..1.0)
    # Peak lateral shift occurs near mid-stance
    pelvis_x = 0.032 * H * p["sway"] * math.sin(w)

    # Pelvic yaw (transverse plane rotation to extend step reach: Left hip twists forward at strike)
    pelvis_yaw = 4.5 * p["yaw"] * math.sin(w)

    # Pelvic roll (hip drop / Trendelenburg gait: hip tilts down on the swinging leg side)
    pelvis_roll = p["hip_drop"] * math.sin(w)

    # Pelvic pitch (forward lean)
    pelvis_pitch = p["lean"] + 0.8 * math.cos(2.0 * w)

    # 3. Spine and Chest Counter-Kinematics
    # Thoracic counter-rotation: ribcage twists opposite to pelvic yaw to maintain forward facing
    chest_yaw = -pelvis_yaw * 0.75 * p["counter_twist"]
    chest_roll = -pelvis_roll * 0.65 * p["counter_twist"]
    chest_pitch = -0.5 * pelvis_pitch * 0.4

    spine_yaw = (pelvis_yaw + chest_yaw) * 0.5
    spine_roll = (pelvis_roll + chest_roll) * 0.5
    spine_pitch = pelvis_pitch * 0.6

    # Head stabilization: counteracts torso motion to keep eye-line level
    head_yaw = -chest_yaw * 0.45
    head_roll = -chest_roll * 0.35
    head_pitch = -chest_pitch * 0.5

    # 4. Leg Kinematics (Left: phase offset 0.0, Right: phase offset 0.5)
    legs = {}
    duty = p["duty_factor"]

    for side, offset in (("L", 0.0), ("R", 0.5)):
        leg_u = (u - offset) % 1.0

        if leg_u < duty:
            # Stance Phase: foot planted on ground, rolling from heel to toe
            # Stance progress 0..1
            s = leg_u / duty

            # Stance foot travels smoothly backward relative to hip
            along = stride_distance * (0.5 - s)
            up = 0.0

            # Dynamic foot roll:
            # 0.0 .. 0.15: Heel strike (dorsiflexion ~ +16° to 0°)
            # 0.15 .. 0.65: Flat foot (locked flat on floor ~ 0°)
            # 0.65 .. 0.85: Heel rise (heel lifts, toe stays on floor ~ 0° to -22°)
            # 0.85 .. 1.00: Push-off flick (~ -22° to -35°)
            if s < 0.15:
                foot_roll = 16.0 * (1.0 - (s / 0.15))
                knee_cushion = 8.0 * math.sin(math.pi * (s / 0.15))  # shock absorption
            elif s < 0.65:
                foot_roll = 0.0
                knee_cushion = 0.0
            elif s < 0.85:
                sub = (s - 0.65) / 0.20
                foot_roll = -22.0 * _smooth_step(0.0, 1.0, sub)
                knee_cushion = 4.0 * sub
            else:
                sub = (s - 0.85) / 0.15
                foot_roll = -22.0 - 13.0 * sub
                knee_cushion = 4.0 + 12.0 * sub

            # Thigh angle in stance: pulls body forward
            thigh_pitch = -base_thigh_amplitude * math.cos(math.pi * s)
            knee_pitch = max(0.0, knee_cushion)
            foot_pitch = foot_roll

        else:
            # Swing Phase: foot lifts off floor, folds knee, reaches forward
            # Swing progress 0..1
            s = (leg_u - duty) / (1.0 - duty)

            # Trajectory forward and up
            along = stride_distance * (-0.5 + s)
            up = lift_height * math.sin(math.pi * s)

            # Swing knee folds up to clear ground, then extends forward for heel strike
            knee_pitch = p["knee_fold"] * math.sin(math.pi * s)
            # Thigh swings from back to front
            thigh_pitch = -base_thigh_amplitude + (2.0 * base_thigh_amplitude) * _smooth_step(0.0, 1.0, s)

            # Foot prepares for heel strike: neutral -> dorsiflexion near landing
            if s > 0.70:
                prep = (s - 0.70) / 0.30
                foot_pitch = 16.0 * _smooth_step(0.0, 1.0, prep)
            else:
                foot_pitch = 5.0 * (1.0 - s)

        legs[side] = {
            "thigh_pitch": thigh_pitch,
            "knee_pitch": knee_pitch,
            "foot_pitch": foot_pitch,
            "along": along,
            "up": up,
        }

    # 5. Arm Kinematics
    # Reciprocal contralateral arm swing:
    # When Left leg strikes forward (u = 0.0), Left arm swings backward (+) and Right arm swings forward (-)
    # When Right leg strikes forward (u = 0.5), Right arm swings backward (+) and Left arm swings forward (-)
    arm_swing_amp = 18.0 * p["arm_swing"]
    if is_shooter:
        arm_swing_amp *= 0.35  # weapon-holding posture

    cos_w = math.cos(w)
    # Pitch: positive swings backward (+Y), negative swings forward (-Y)
    pitch_L = arm_swing_amp * cos_w
    pitch_R = -arm_swing_amp * cos_w

    # Elbow flexion: elbow bends more as arm swings forward, straightens to base_bend when arm swings back
    base_bend = p["arm_bend"]
    elbow_flex_L = base_bend + 18.0 * max(0.0, -cos_w)
    elbow_flex_R = base_bend + 18.0 * max(0.0, cos_w)

    arms = {
        "L": {
            "pitch": pitch_L,
            "yaw": 0.0,
            "roll": 0.0,
            "forearm_pitch": elbow_flex_L,
        },
        "R": {
            "pitch": pitch_R,
            "yaw": 0.0,
            "roll": 0.0,
            "forearm_pitch": elbow_flex_R,
        },
    }

    return {
        "pelvis": {
            "pos": (pelvis_x, 0.0, pelvis_z),
            "rot": (pelvis_pitch, pelvis_roll, pelvis_yaw),
        },
        "spine": {
            "pitch": spine_pitch,
            "roll": spine_roll,
            "yaw": spine_yaw,
        },
        "chest": {
            "pitch": chest_pitch,
            "roll": chest_roll,
            "yaw": chest_yaw,
        },
        "head": {
            "pitch": head_pitch,
            "roll": head_roll,
            "yaw": head_yaw,
        },
        "legs": legs,
        "arms": arms,
    }


# ---------------------------------------------------------------------------------------------------------------
# Quadruped Locomotion Synthesis
# ---------------------------------------------------------------------------------------------------------------

def evaluate_quadruped_walk(phase, length, height, feet_info=None, params=None):
    """Synthesizes a 4-beat lateral sequence quadruped walk state ($LH \to LF \to RH \to RF$)
    at normalized cycle phase [0, 1).

    Returns:
      feet: dict mapping foot names to translation offset Vector (along, lateral, up)
      body: { pos: (x, y, z), rot: (pitch, roll, yaw) }
      head: { yaw, pitch }
      tail: { wave_amount }
    """
    p = merge_gait_params("quadruped_walk", params)
    L = float(length)
    H = float(height)

    u = phase % 1.0
    w = 2.0 * math.pi * u

    stride = 0.28 * L * p["stride"]
    lift = 0.065 * H * p["foot_lift"]
    duty = p["duty_factor"]

    # 4 distinct phase offsets for lateral sequence:
    # LH: 0.00 -> LF: 0.25 -> RH: 0.50 -> RF: 0.75
    phase_map = {
        "LH": 0.00,
        "LF": 0.25,
        "RH": 0.50,
        "RF": 0.75,
    }

    # Evaluate feet positions
    feet_out = {}
    if feet_info:
        for f in feet_info:
            name = f["name"]
            side = f.get("side", 1.0)
            is_front = f.get("is_front", False)

            if side > 0:
                ftype = "LF" if is_front else "LH"
            else:
                ftype = "RF" if is_front else "RH"

            offset = phase_map.get(ftype, 0.0)
            foot_u = (u - offset) % 1.0

            if foot_u < duty:
                # Stance
                s = foot_u / duty
                along = stride * (0.5 - s)
                up = 0.0
            else:
                # Swing
                s = (foot_u - duty) / (1.0 - duty)
                along = stride * (-0.5 + s)
                up = lift * math.sin(math.pi * s)

            feet_out[name] = {
                "along": along,
                "up": up,
                "lateral": 0.0,
                "grounded": foot_u < duty,
            }

    # Body 6-DoF Motion:
    # 4 bobs per cycle corresponding to the landing of each of the 4 paws
    body_z = 0.010 * H * p["bob"] * math.cos(4.0 * w)
    # Lateral S-curve spine sway (yaw)
    body_yaw = 2.8 * p["sway"] * math.sin(w)
    # Body roll with the gait
    body_roll = 1.9 * math.cos(w)
    # Pitch rocking
    body_pitch = 1.2 * math.cos(2.0 * w)

    return {
        "feet": feet_out,
        "body": {
            "pos": (0.0, 0.0, body_z),
            "rot": (body_pitch, body_roll, body_yaw),
        },
        "head": {
            "yaw": -body_yaw * 0.9,
            "pitch": -body_pitch * 0.4,
        },
        "tail_wave": 9.0 * math.sin(w),
    }


def evaluate_biped_run(phase, hip_height, leg_length, params=None, is_shooter=False):
    """Synthesizes a biomechanical running state at normalized phase [0, 1) featuring
    true ballistic aerial flight phases (duty factor < 0.50), spring-mass knee cushioning,
    forward torso lean, high knee drive, and compact pumping arm kinematics."""
    p = merge_gait_params("run", params)
    H = float(hip_height)
    L = float(leg_length)

    u = phase % 1.0
    w = 2.0 * math.pi * u

    duty = p["duty_factor"]
    stride_distance = 2.4 * L * math.sin(math.radians(26.0 * p["stride"]))
    lift_height = 0.14 * L * p["foot_lift"]

    # 1. Pelvis Kinematics (Spring-Mass running model)
    # Pelvis reaches minimum height during mid-stance knee cushioning and maximum during flight apex
    run_bounce_phase = 2.0 * w - 0.72 * math.pi
    pelvis_z = 0.042 * H * p["bob"] * (-math.cos(run_bounce_phase))

    # Narrow running track width
    pelvis_x = 0.016 * H * p["sway"] * math.sin(w)

    # Pelvic yaw, roll, pitch
    pelvis_yaw = 5.5 * p["yaw"] * math.sin(w)
    pelvis_roll = p["hip_drop"] * math.sin(w)
    pelvis_pitch = p["lean"] + 1.2 * math.cos(2.0 * w)

    # 2. Spine & Chest Counter-Rotation
    chest_yaw = -pelvis_yaw * 0.85 * p["counter_twist"]
    chest_roll = -pelvis_roll * 0.55 * p["counter_twist"]
    chest_pitch = -0.4 * pelvis_pitch * 0.3

    spine_yaw = (pelvis_yaw + chest_yaw) * 0.5
    spine_roll = (pelvis_roll + chest_roll) * 0.5
    spine_pitch = pelvis_pitch * 0.65

    head_yaw = -chest_yaw * 0.45
    head_roll = -chest_roll * 0.35
    head_pitch = -chest_pitch * 0.5

    # 3. Leg Kinematics with Ballistic Flight
    legs = {}
    is_flight = (duty <= u < 0.50) or (0.50 + duty <= u < 1.00)

    for side, offset in (("L", 0.0), ("R", 0.5)):
        leg_u = (u - offset) % 1.0

        if leg_u < duty:
            # Stance phase
            s = leg_u / duty
            along = stride_distance * (0.5 - s)
            up = 0.0

            # Spring cushion: knee flexes to absorb shock then extends for push-off
            knee_cushion = 20.0 * math.sin(math.pi * s)
            thigh_pitch = -26.0 * p["stride"] * math.cos(math.pi * s)
            knee_pitch = max(0.0, knee_cushion)

            # Midfoot landing -> push-off flick
            if s < 0.30:
                foot_pitch = 4.0 * (1.0 - (s / 0.30))
            elif s < 0.70:
                foot_pitch = 0.0
            else:
                push = (s - 0.70) / 0.30
                foot_pitch = -32.0 * push
        else:
            # Swing / Flight phase
            s = (leg_u - duty) / (1.0 - duty)
            along = stride_distance * (-0.5 + s)
            up = lift_height * math.sin(math.pi * s)

            # High knee drive
            knee_pitch = p["knee_fold"] * math.sin(math.pi * s)
            # Thigh sweeps from trailing back to high forward drive
            thigh_pitch = 24.0 * p["stride"] - (50.0 * p["stride"]) * _smooth_step(0.0, 1.0, s)

            # Pre-landing foot angle
            if s > 0.75:
                foot_pitch = 8.0 * _smooth_step(0.0, 1.0, (s - 0.75) / 0.25)
            else:
                foot_pitch = 0.0

        legs[side] = {
            "thigh_pitch": thigh_pitch,
            "knee_pitch": knee_pitch,
            "foot_pitch": foot_pitch,
            "along": along,
            "up": up,
            "grounded": leg_u < duty,
        }

    # 4. Compact Pumping Arm Kinematics
    arm_swing_amp = 26.0 * p["arm_swing"]
    if is_shooter:
        arm_swing_amp *= 0.35

    cos_w = math.cos(w)
    pitch_L = arm_swing_amp * cos_w
    pitch_R = -arm_swing_amp * cos_w

    base_bend = p["arm_bend"]  # e.g. 70° - 85°
    elbow_flex_L = base_bend + 14.0 * max(0.0, -cos_w)
    elbow_flex_R = base_bend + 14.0 * max(0.0, cos_w)

    arms = {
        "L": {
            "pitch": pitch_L,
            "yaw": 0.0,
            "roll": 0.0,
            "forearm_pitch": elbow_flex_L,
        },
        "R": {
            "pitch": pitch_R,
            "yaw": 0.0,
            "roll": 0.0,
            "forearm_pitch": elbow_flex_R,
        },
    }

    return {
        "pelvis": {
            "pos": (pelvis_x, 0.0, pelvis_z),
            "rot": (pelvis_pitch, pelvis_roll, pelvis_yaw),
        },
        "spine": {
            "pitch": spine_pitch,
            "roll": spine_roll,
            "yaw": spine_yaw,
        },
        "chest": {
            "pitch": chest_pitch,
            "roll": chest_roll,
            "yaw": chest_yaw,
        },
        "head": {
            "pitch": head_pitch,
            "roll": head_roll,
            "yaw": head_yaw,
        },
        "legs": legs,
        "arms": arms,
        "is_flight": is_flight,
    }


def evaluate_quadruped_gallop(phase, length, height, feet_info=None, params=None):
    """Synthesizes a 4-beat rotary gallop with dual suspension phases (gathered flight and extended flight),
    dynamic spine arching/sagging wave, and ground-clearing paw trajectories."""
    p = merge_gait_params("quadruped_gallop", params)
    L = float(length)
    H = float(height)

    u = phase % 1.0
    w = 2.0 * math.pi * u

    stride = 0.45 * L * p["stride"]
    lift = 0.12 * H * p["foot_lift"]
    duty = p.get("duty_factor", 0.28)  # rotary gallop duty ~0.28

    # Rotary gallop foot sequence: LH -> RH -> LF -> RF
    # Hind contact: 0.00..0.38, Gathered flight: 0.38..0.52
    # Front contact: 0.52..0.90, Extended flight: 0.90..1.00
    phase_map = {
        "LH": 0.00,
        "RH": 0.10,
        "LF": 0.52,
        "RF": 0.62,
    }

    feet_out = {}
    if feet_info:
        for f in feet_info:
            name = f["name"]
            side = f.get("side", 1.0)
            is_front = f.get("is_front", False)

            if side > 0:
                ftype = "LF" if is_front else "LH"
            else:
                ftype = "RF" if is_front else "RH"

            offset = phase_map.get(ftype, 0.0)
            foot_u = (u - offset) % 1.0

            if foot_u < duty:
                s = foot_u / duty
                along = stride * (0.5 - s)
                up = 0.0
            else:
                s = (foot_u - duty) / (1.0 - duty)
                along = stride * (-0.5 + s)
                up = lift * math.sin(math.pi * s)

            feet_out[name] = {
                "along": along,
                "up": up,
                "lateral": 0.0,
                "grounded": foot_u < duty,
            }

    # Gathered flight (0.38..0.52): spine arches up (+Z, back curls)
    # Extended flight (0.90..1.00): spine sags (-Z, body reaches)
    spine_flexion = -14.0 * math.cos(w)

    # Body vertical bounce: double bound per gallop cycle
    body_z = 0.035 * H * p["bob"] * math.sin(2.0 * w)
    body_pitch = 8.0 * math.sin(w)
    body_roll = 2.5 * math.sin(w)
    body_yaw = 2.0 * math.sin(w)

    return {
        "feet": feet_out,
        "body": {
            "pos": (0.0, 0.0, body_z),
            "rot": (body_pitch, body_roll, body_yaw),
        },
        "spine_flexion": spine_flexion,
        "head": {
            "yaw": -body_yaw * 0.7,
            "pitch": -body_pitch * 0.5,
        },
        "tail_wave": 16.0 * math.sin(w),
    }


def evaluate_biped_walk_to_idle(t_norm, hip_height, leg_length, params=None, is_shooter=False):
    """Synthesizes a 24-frame walk-to-idle deceleration transition:
    t_norm in [0, 1].
    - Phase 1 (0.0..0.5): Decelerating leading step, trailing leg steps up parallel.
    - Phase 2 (0.5..1.0): Inertial settling (spring-damper decay), arms return to resting hang."""
    p = merge_gait_params("natural", params)
    H = float(hip_height)
    L = float(leg_length)

    tau = max(0.0, min(1.0, float(t_norm)))

    # Step deceleration:
    if tau < 0.50:
        sub = tau / 0.50
        # Left leg plants, Right leg swings forward to meet it
        thigh_L = -18.0 * (1.0 - sub)
        thigh_R = 18.0 * (1.0 - 2.0 * sub) if sub < 0.5 else 0.0
        knee_L = 8.0 * math.sin(math.pi * sub)
        knee_R = 30.0 * math.sin(math.pi * sub) if sub < 0.5 else 0.0
        arm_pitch_L = 14.0 * (1.0 - sub)
        arm_pitch_R = -14.0 * (1.0 - sub)
        elbow_L = 14.0 + 6.0 * (1.0 - sub)
        elbow_R = 14.0 + 12.0 * (1.0 - sub)
        pelvis_pitch = p["lean"] * (1.0 - 0.5 * sub)
        pelvis_z = 0.015 * H * math.sin(math.pi * sub)
        pelvis_x = 0.02 * H * (1.0 - sub)
    else:
        sub = (tau - 0.50) / 0.50
        # Both feet planted, damped harmonic decay
        decay = math.exp(-3.5 * sub)
        thigh_L = 0.0
        thigh_R = 0.0
        knee_L = 0.0
        knee_R = 0.0
        arm_pitch_L = 0.0
        arm_pitch_R = 0.0
        elbow_L = 12.0 if not is_shooter else 28.0
        elbow_R = 12.0 if not is_shooter else 28.0
        pelvis_pitch = p["lean"] * 0.5 * (1.0 - sub)
        pelvis_z = -0.012 * H * decay * math.cos(6.0 * math.pi * sub)
        pelvis_x = 0.0

    return {
        "pelvis": {
            "pos": (pelvis_x, 0.0, pelvis_z),
            "rot": (pelvis_pitch, 0.0, 0.0),
        },
        "spine": {
            "pitch": pelvis_pitch * 0.6,
            "roll": 0.0,
            "yaw": 0.0,
        },
        "head": {
            "pitch": 0.0,
            "roll": 0.0,
            "yaw": 0.0,
        },
        "legs": {
            "L": {"thigh_pitch": thigh_L, "knee_pitch": knee_L, "foot_pitch": 0.0},
            "R": {"thigh_pitch": thigh_R, "knee_pitch": knee_R, "foot_pitch": 0.0},
        },
        "arms": {
            "L": {"pitch": arm_pitch_L, "forearm_pitch": elbow_L, "yaw": 0.0, "roll": 0.0},
            "R": {"pitch": arm_pitch_R, "forearm_pitch": elbow_R, "yaw": 0.0, "roll": 0.0},
        },
    }


def evaluate_biped_idle_to_walk(t_norm, hip_height, leg_length, params=None, is_shooter=False):
    """Synthesizes a 20-frame idle-to-walk acceleration transition:
    t_norm in [0, 1].
    - Phase 1 (0.0..0.35): Anticipatory postural shift (weight shifts to support foot, forward lean).
    - Phase 2 (0.35..1.0): Left leading foot launches forward, arms ramp into dynamic swing."""
    p = merge_gait_params("natural", params)
    H = float(hip_height)
    L = float(leg_length)

    tau = max(0.0, min(1.0, float(t_norm)))

    if tau < 0.35:
        sub = tau / 0.35
        pelvis_pitch = p["lean"] * sub
        pelvis_x = -0.018 * H * math.sin(math.pi * sub)  # shift weight to Right
        pelvis_z = -0.008 * H * sub
        thigh_L = 0.0
        thigh_R = 0.0
        knee_L = 0.0
        knee_R = 4.0 * sub  # slight knee bend on support leg
        arm_pitch_L = 4.0 * sub
        arm_pitch_R = -4.0 * sub
        elbow_L = 12.0
        elbow_R = 12.0
    else:
        sub = (tau - 0.35) / 0.65
        ease_sub = _smooth_step(0.0, 1.0, sub)
        pelvis_pitch = p["lean"]
        pelvis_x = -0.018 * H * (1.0 - sub)
        pelvis_z = 0.015 * H * math.sin(math.pi * sub)
        # Left leg launches forward
        thigh_L = -22.0 * p["stride"] * ease_sub
        knee_L = 35.0 * math.sin(math.pi * sub)
        thigh_R = 10.0 * ease_sub
        knee_R = 0.0
        arm_pitch_L = 18.0 * p["arm_swing"] * ease_sub  # left arm swings back as left leg goes forward
        arm_pitch_R = -18.0 * p["arm_swing"] * ease_sub
        elbow_L = 14.0
        elbow_R = 14.0 + 16.0 * ease_sub

    return {
        "pelvis": {
            "pos": (pelvis_x, 0.0, pelvis_z),
            "rot": (pelvis_pitch, 0.0, 0.0),
        },
        "spine": {
            "pitch": pelvis_pitch * 0.6,
            "roll": 0.0,
            "yaw": 0.0,
        },
        "head": {
            "pitch": 0.0,
            "roll": 0.0,
            "yaw": 0.0,
        },
        "legs": {
            "L": {"thigh_pitch": thigh_L, "knee_pitch": knee_L, "foot_pitch": 0.0},
            "R": {"thigh_pitch": thigh_R, "knee_pitch": knee_R, "foot_pitch": 0.0},
        },
        "arms": {
            "L": {"pitch": arm_pitch_L, "forearm_pitch": elbow_L, "yaw": 0.0, "roll": 0.0},
            "R": {"pitch": arm_pitch_R, "forearm_pitch": elbow_R, "yaw": 0.0, "roll": 0.0},
        },
    }


# ---------------------------------------------------------------------------------------------------------------
# Procedural Combat & Creature Actions
# ---------------------------------------------------------------------------------------------------------------

def evaluate_biped_attack(phase, hip_height, leg_length, is_shooter=False, style="smash"):
    """Synthesizes a 3-phase procedural combat attack state (tau in [0, 1]):
    1. Telegraph / Windup (0.00..0.58): Character coils, raises arms / braces weapon, center of mass drops.
    2. Strike Snap (0.58..0.75): Explosive forward acceleration and weapon release / slam.
    3. Impact Recoil & Recovery (0.75..1.00): Decelerating follow-through and return toward stance.
    """
    H = float(hip_height)
    L = float(leg_length)
    tau = max(0.0, min(1.0, float(phase)))

    WINDUP_END = 0.58
    STRIKE_END = 0.75

    if tau < WINDUP_END:
        u_wu = tau / WINDUP_END
        s_wu = 3.0 * u_wu**2 - 2.0 * u_wu**3  # smooth Hermite ease
        hold = s_wu
        strike = 0.0
        recover = 0.0
    elif tau < STRIKE_END:
        u_st = (tau - WINDUP_END) / (STRIKE_END - WINDUP_END)
        hold = 1.0 - u_st
        strike = u_st**1.6  # sharp acceleration snap
        recover = 0.0
    else:
        u_rec = (tau - STRIKE_END) / (1.0 - STRIKE_END)
        s_rec = 1.0 - (3.0 * u_rec**2 - 2.0 * u_rec**3)
        hold = 0.0
        strike = s_rec
        recover = u_rec

    if is_shooter:
        # Shooter: weapon brought level forward, knees braced in crouch, muzzle kick on shot
        kick = strike * (1.0 - recover)
        crouch = hold + 0.3 * (1.0 - recover)
        pelvis_pos = (0.0, -0.03 * H * kick, -0.05 * H * crouch)
        pelvis_rot = (6.0 * crouch - 8.0 * kick, 0.0, 0.0)
        spine_rot = (4.0 * crouch - 6.0 * kick, 0.0, 0.0)
        head_rot = (-6.0 * crouch, 0.0, 0.0)

        # Arms: raise to level (pitch -70 deg), kick back on shot (+16 deg)
        arm_p_L = -68.0 * crouch + 16.0 * kick
        arm_p_R = -68.0 * crouch + 16.0 * kick
        elbow_L = 22.0 + 8.0 * (1.0 - crouch)
        elbow_R = 22.0 + 8.0 * (1.0 - crouch)

        leg_thigh = -12.0 * crouch
        leg_knee = 24.0 * crouch
    else:
        # Melee / Smash: arms raise overhead in windup, then slam forward and down
        rear = hold
        lunge = strike
        slam = strike

        pelvis_y = -0.04 * H * rear + 0.08 * H * lunge
        pelvis_z = 0.03 * H * rear - 0.07 * H * slam
        pelvis_pos = (0.0, pelvis_y, pelvis_z)

        pitch = -14.0 * rear + 22.0 * slam
        pelvis_rot = (pitch, 0.0, 0.0)
        spine_rot = (pitch * 0.7, 0.0, 0.0)
        head_rot = (-8.0 * rear + 10.0 * slam, 0.0, 0.0)

        # Overhead smash arms: raise up high (-110 deg) then slam forward/down (+35 deg)
        arm_p_L = -110.0 * rear + 35.0 * slam
        arm_p_R = -110.0 * rear + 35.0 * slam
        elbow_L = 20.0 * rear + 10.0 * (1.0 - slam)
        elbow_R = 20.0 * rear + 10.0 * (1.0 - slam)

        leg_thigh = -18.0 * slam
        leg_knee = 34.0 * slam

    return {
        "pelvis": {"pos": pelvis_pos, "rot": pelvis_rot},
        "spine": {"pitch": spine_rot[0], "roll": 0.0, "yaw": 0.0},
        "chest": {"pitch": spine_rot[0] * 0.8, "roll": 0.0, "yaw": 0.0},
        "head": {"pitch": head_rot[0], "roll": 0.0, "yaw": 0.0},
        "legs": {
            "L": {"thigh_pitch": leg_thigh, "knee_pitch": leg_knee, "foot_pitch": -leg_knee * 0.5},
            "R": {"thigh_pitch": leg_thigh, "knee_pitch": leg_knee, "foot_pitch": -leg_knee * 0.5},
        },
        "arms": {
            "L": {"pitch": arm_p_L, "forearm_pitch": elbow_L, "yaw": 0.0, "roll": 0.0},
            "R": {"pitch": arm_p_R, "forearm_pitch": elbow_R, "yaw": 0.0, "roll": 0.0},
        },
    }


def evaluate_biped_hit(phase, hip_height, leg_length, direction="front"):
    """Synthesizes an 8-frame impulse hit recoil response (tau in [0, 1]):
    - Sudden backward impulse displacement and head snap.
    - Damped spring rebound settling back to stance.
    """
    H = float(hip_height)
    tau = max(0.0, min(1.0, float(phase)))
    # Impulse envelope: peaks at tau ~0.35, then decays smoothly
    k = math.sin(math.pi * tau) * (1.0 - 0.35 * tau)

    dir_mult = 1.0 if direction == "front" else (-1.0 if direction == "back" else 1.0)
    yaw_mult = 1.0 if direction == "left" else (-1.0 if direction == "right" else 0.0)

    pelvis_y = -0.06 * H * k * dir_mult
    pelvis_pitch = -22.0 * k * dir_mult
    spine_pitch = -18.0 * k * dir_mult
    head_pitch = -24.0 * k * dir_mult
    head_yaw = 15.0 * k * yaw_mult

    arm_pitch = -28.0 * k
    elbow = 14.0 + 20.0 * k

    return {
        "pelvis": {"pos": (0.0, pelvis_y, 0.0), "rot": (pelvis_pitch, 0.0, head_yaw * 0.4)},
        "spine": {"pitch": spine_pitch, "roll": 0.0, "yaw": head_yaw * 0.3},
        "chest": {"pitch": spine_pitch * 0.8, "roll": 0.0, "yaw": head_yaw * 0.4},
        "head": {"pitch": head_pitch, "roll": 0.0, "yaw": head_yaw},
        "legs": {
            "L": {"thigh_pitch": -6.0 * k, "knee_pitch": 12.0 * k, "foot_pitch": -6.0 * k},
            "R": {"thigh_pitch": -6.0 * k, "knee_pitch": 12.0 * k, "foot_pitch": -6.0 * k},
        },
        "arms": {
            "L": {"pitch": arm_pitch, "forearm_pitch": elbow, "yaw": 0.0, "roll": 0.0},
            "R": {"pitch": arm_pitch, "forearm_pitch": elbow, "yaw": 0.0, "roll": 0.0},
        },
    }


def evaluate_biped_death(phase, hip_height, leg_length, style="collapse"):
    """Synthesizes an 18-frame biomechanical collapse death sequence (tau in [0, 1]):
    1. Knee Buckle (0.0..0.22): Knee lock releases, knees flex, hips begin dropping.
    2. Gravity Fall (0.22..0.72): Torso rotates backward -88 deg, hips drop -0.8 H to ground.
    3. Ground Impact & Settling (0.72..1.00): Damped bounce and corpse settling on floor plane.
    """
    H = float(hip_height)
    tau = max(0.0, min(1.0, float(phase)))

    # Knee buckle
    if tau < 0.22:
        buckle = tau / 0.22
    else:
        buckle = 1.0

    # Fall
    if tau < 0.18:
        fall = 0.0
    elif tau < 0.72:
        fall = (tau - 0.18) / (0.72 - 0.18)
        fall = fall * fall  # gravity acceleration
    else:
        fall = 1.0

    # Ground impact damped bounce
    if tau >= 0.72:
        impact_t = (tau - 0.72) / 0.28
        bounce = 0.025 * H * math.exp(-4.0 * impact_t) * math.sin(3.0 * math.pi * impact_t)
    else:
        bounce = 0.0

    pelvis_pitch = -88.0 * fall
    pelvis_z = -0.08 * H * buckle - 0.80 * H * fall + bounce
    pelvis_y = -0.25 * H * fall

    arm_pitch = -50.0 * fall
    elbow = 14.0 + 10.0 * (1.0 - fall)

    leg_thigh = -14.0 * buckle
    leg_knee = 26.0 * buckle * (1.0 - fall * 0.7)

    return {
        "pelvis": {"pos": (0.0, pelvis_y, pelvis_z), "rot": (pelvis_pitch, 0.0, 0.0)},
        "spine": {"pitch": -5.0 * fall, "roll": 0.0, "yaw": 0.0},
        "chest": {"pitch": -5.0 * fall, "roll": 0.0, "yaw": 0.0},
        "head": {"pitch": -10.0 * fall, "roll": 0.0, "yaw": 0.0},
        "legs": {
            "L": {"thigh_pitch": leg_thigh, "knee_pitch": leg_knee, "foot_pitch": 0.0},
            "R": {"thigh_pitch": leg_thigh, "knee_pitch": leg_knee, "foot_pitch": 0.0},
        },
        "arms": {
            "L": {"pitch": arm_pitch, "forearm_pitch": elbow, "yaw": 0.0, "roll": 0.0},
            "R": {"pitch": arm_pitch, "forearm_pitch": elbow, "yaw": 0.0, "roll": 0.0},
        },
    }


# ---------------------------------------------------------------------------------------------------------------
# Secondary Appendage Physics (Tails, Capes, Hair, Ears)
# ---------------------------------------------------------------------------------------------------------------

def evaluate_secondary_chain(chain_length, phase, frequency=1.0, base_amplitude=12.0,
                             amplitude_growth=1.25, phase_lag=0.35, damping=0.0, impulse_time=None):
    """Evaluates 2nd-order harmonic wave propagation or damped impulse along a multi-link chain
    (e.g., tail segments, ears, hair, cape, clothing).

    Returns a list of segment rotation angles in degrees [rot_0, rot_1, ..., rot_{N-1}].
    """
    if chain_length <= 0:
        return []

    angles = []
    w = 2.0 * math.pi * float(frequency)

    if impulse_time is not None:
        t = max(0.0, float(impulse_time))
        zeta = max(0.0, float(damping))
        for i in range(chain_length):
            amp = float(base_amplitude) * (float(amplitude_growth) ** i)
            delay = i * (float(phase_lag) / max(1e-6, w))
            t_eff = t - delay
            if t_eff <= 0.0:
                angle = 0.0
            else:
                decay = math.exp(-zeta * t_eff) if zeta > 0.0 else 1.0
                angle = amp * decay * math.sin(w * t_eff)
            angles.append(angle)
    else:
        u = float(phase) % 1.0
        phi = 2.0 * math.pi * u * float(frequency)
        for i in range(chain_length):
            amp = float(base_amplitude) * (float(amplitude_growth) ** i)
            segment_phase = phi - i * float(phase_lag)
            angle = amp * math.sin(segment_phase)
            angles.append(angle)

    return angles


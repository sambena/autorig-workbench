# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Unit tests for procedural biomechanical gait engine (autorig/core/gait.py).
import math
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import blender
import gait


class TestGaitEngine(unittest.TestCase):
    def test_presets_exist_and_validate(self):
        for name, preset in gait.GAIT_PRESETS.items():
            self.assertIn("stride", preset)
            self.assertIn("cadence", preset)
            self.assertIn("bob", preset)
            self.assertIn("sway", preset)
            self.assertIn("duty_factor", preset)
            self.assertGreater(preset["stride"], 0.0)
            self.assertGreater(preset["cadence"], 0.0)
            self.assertGreaterEqual(preset["duty_factor"], 0.25)
            self.assertLessEqual(preset["duty_factor"], 0.85)

    def test_merge_gait_params_clamping(self):
        # Extreme values clamped to safe physical bounds
        p = gait.merge_gait_params("natural", {"stride": 99.0, "sway": -5.0, "bob": 10.0})
        self.assertEqual(p["stride"], 2.5)  # max clamp
        self.assertEqual(p["sway"], 0.0)    # min clamp
        self.assertEqual(p["bob"], 3.0)     # max clamp

    def test_biped_walk_continuity_and_looping(self):
        # State at t=0.0 must match t=1.0 seamlessly
        s0 = gait.evaluate_biped_walk(0.0, hip_height=1.0, leg_length=0.9)
        s1 = gait.evaluate_biped_walk(1.0, hip_height=1.0, leg_length=0.9)

        # Pelvis position and rotation
        for k in range(3):
            self.assertAlmostEqual(s0["pelvis"]["pos"][k], s1["pelvis"]["pos"][k], places=5)
            self.assertAlmostEqual(s0["pelvis"]["rot"][k], s1["pelvis"]["rot"][k], places=5)

        # Spine and chest
        self.assertAlmostEqual(s0["spine"]["yaw"], s1["spine"]["yaw"], places=5)
        self.assertAlmostEqual(s0["chest"]["yaw"], s1["chest"]["yaw"], places=5)

        # Legs
        for side in ("L", "R"):
            self.assertAlmostEqual(s0["legs"][side]["thigh_pitch"], s1["legs"][side]["thigh_pitch"], places=5)
            self.assertAlmostEqual(s0["legs"][side]["knee_pitch"], s1["legs"][side]["knee_pitch"], places=5)
            self.assertAlmostEqual(s0["legs"][side]["foot_pitch"], s1["legs"][side]["foot_pitch"], places=5)
            self.assertAlmostEqual(s0["legs"][side]["along"], s1["legs"][side]["along"], places=5)
            self.assertAlmostEqual(s0["legs"][side]["up"], s1["legs"][side]["up"], places=5)

    def test_biped_left_right_symmetry(self):
        # Right leg at t=0.5 must mirror Left leg at t=0.0
        s0 = gait.evaluate_biped_walk(0.0, hip_height=1.0, leg_length=0.9)
        s_half = gait.evaluate_biped_walk(0.5, hip_height=1.0, leg_length=0.9)

        # Left leg at t=0 vs Right leg at t=0.5
        self.assertAlmostEqual(s0["legs"]["L"]["thigh_pitch"], s_half["legs"]["R"]["thigh_pitch"], places=5)
        self.assertAlmostEqual(s0["legs"]["L"]["knee_pitch"], s_half["legs"]["R"]["knee_pitch"], places=5)
        self.assertAlmostEqual(s0["legs"]["L"]["foot_pitch"], s_half["legs"]["R"]["foot_pitch"], places=5)
        self.assertAlmostEqual(s0["legs"]["L"]["along"], s_half["legs"]["R"]["along"], places=5)
        self.assertAlmostEqual(s0["legs"]["L"]["up"], s_half["legs"]["R"]["up"], places=5)

    def test_biped_heel_strike_and_push_off(self):
        # Heel strike is at beginning of left stance (u=0.0) -> foot dorsiflexion > 0
        s_strike = gait.evaluate_biped_walk(0.01, hip_height=1.0, leg_length=0.9)
        self.assertGreater(s_strike["legs"]["L"]["foot_pitch"], 5.0)  # toes up
        self.assertEqual(s_strike["legs"]["L"]["up"], 0.0)            # on floor

        # Flat foot mid-stance (u=0.25) -> foot flat (pitch ~ 0), planted on floor
        s_mid = gait.evaluate_biped_walk(0.25, hip_height=1.0, leg_length=0.9)
        self.assertAlmostEqual(s_mid["legs"]["L"]["foot_pitch"], 0.0, places=4)
        self.assertEqual(s_mid["legs"]["L"]["up"], 0.0)

        # Push-off near end of stance (u=0.55) -> plantarflexion < 0 (toes pointing back/down)
        s_push = gait.evaluate_biped_walk(0.55, hip_height=1.0, leg_length=0.9)
        self.assertLess(s_push["legs"]["L"]["foot_pitch"], -10.0)

        # Swing phase apex (u=0.80) -> foot lifted off floor
        s_swing = gait.evaluate_biped_walk(0.80, hip_height=1.0, leg_length=0.9)
        self.assertGreater(s_swing["legs"]["L"]["up"], 0.02)
        self.assertGreater(s_swing["legs"]["L"]["knee_pitch"], 20.0)  # knee folded

    def test_pelvis_biomechanics_phase_relationships(self):
        # Mid-stance is around phase 0.25 -> peak height (bob) and max lateral sway toward stance foot
        s_mid = gait.evaluate_biped_walk(0.25, hip_height=1.0, leg_length=0.9)
        self.assertGreater(s_mid["pelvis"]["pos"][0], 0.0)  # sway toward +X (Left stance)

        # Pelvic yaw twists forward on advancing leg
        s_strike = gait.evaluate_biped_walk(0.05, hip_height=1.0, leg_length=0.9)
        self.assertGreater(s_strike["pelvis"]["rot"][2], 0.0)

        # Chest counter-rotation twists in opposition to pelvis
        self.assertLess(s_strike["chest"]["yaw"], 0.0)

    def test_arm_contralateral_reciprocal_swing(self):
        # At u=0.0: Left leg strikes forward (thigh_pitch < 0).
        # Left arm must swing backward (pitch > 0), Right arm forward (pitch < 0).
        s0 = gait.evaluate_biped_walk(0.0, hip_height=1.0, leg_length=0.9)
        self.assertGreater(s0["arms"]["L"]["pitch"], 0.0)   # Left arm backward
        self.assertLess(s0["arms"]["R"]["pitch"], 0.0)      # Right arm forward
        # Right arm is forward, so its elbow flexes more than Left arm
        self.assertGreater(s0["arms"]["R"]["forearm_pitch"], s0["arms"]["L"]["forearm_pitch"])

        # At u=0.5: Right leg strikes forward, Left leg is back.
        # Left arm must swing forward (pitch < 0), Right arm backward (pitch > 0).
        s_half = gait.evaluate_biped_walk(0.5, hip_height=1.0, leg_length=0.9)
        self.assertLess(s_half["arms"]["L"]["pitch"], 0.0)      # Left arm forward
        self.assertGreater(s_half["arms"]["R"]["pitch"], 0.0)   # Right arm backward
        # Left arm is forward, so its elbow flexes more than Right arm
        self.assertGreater(s_half["arms"]["L"]["forearm_pitch"], s_half["arms"]["R"]["forearm_pitch"])

        # No lateral yaw flapping
        self.assertEqual(s0["arms"]["L"]["yaw"], 0.0)
        self.assertEqual(s0["arms"]["R"]["yaw"], 0.0)

    def test_quadruped_lateral_sequence_ordering(self):
        feet = [
            {"name": "ik_leg_front.L", "side": 1.0, "is_front": True},
            {"name": "ik_leg_hind.L", "side": 1.0, "is_front": False},
            {"name": "ik_leg_front.R", "side": -1.0, "is_front": True},
            {"name": "ik_leg_hind.R", "side": -1.0, "is_front": False},
        ]
        # At t=0.00: LH should begin stance/swing cycle
        s0 = gait.evaluate_quadruped_walk(0.0, length=1.4, height=1.0, feet_info=feet)
        self.assertTrue(s0["feet"]["ik_leg_hind.L"]["grounded"])

        # Check all 4 feet present in output
        self.assertEqual(len(s0["feet"]), 4)
        for f in feet:
            self.assertIn(f["name"], s0["feet"])

        # Continuity: t=0.0 matches t=1.0
        s1 = gait.evaluate_quadruped_walk(1.0, length=1.4, height=1.0, feet_info=feet)
        for fname in s0["feet"]:
            self.assertAlmostEqual(s0["feet"][fname]["along"], s1["feet"][fname]["along"], places=5)
            self.assertAlmostEqual(s0["feet"][fname]["up"], s1["feet"][fname]["up"], places=5)

    def test_preset_kinematic_characteristics(self):
        natural = gait.GAIT_PRESETS["natural"]
        soldier = gait.GAIT_PRESETS["soldier"]
        swagger = gait.GAIT_PRESETS["swagger"]
        stealth = gait.GAIT_PRESETS["stealth"]
        heavy = gait.GAIT_PRESETS["heavy"]

        # Soldier has higher cadence and arm swing than natural
        self.assertGreater(soldier["cadence"], natural["cadence"])
        self.assertGreater(soldier["arm_swing"], natural["arm_swing"])

        # Swagger has higher sway and pelvic yaw than natural
        self.assertGreater(swagger["sway"], natural["sway"])
        self.assertGreater(swagger["yaw"], natural["yaw"])

        # Stealth has lower vertical bob and higher trunk lean
        self.assertLess(stealth["bob"], natural["bob"])
        self.assertGreater(stealth["lean"], natural["lean"])

        # Heavy has higher sway and lower cadence
        self.assertGreater(heavy["sway"], natural["sway"])
        self.assertLess(heavy["cadence"], natural["cadence"])

    def test_quadruped_trot_vs_walk(self):
        walk_p = gait.GAIT_PRESETS["quadruped_walk"]
        trot_p = gait.GAIT_PRESETS["quadruped_trot"]

        # Trot has 2-beat 50% duty factor, higher cadence and stride than lateral walk
        self.assertEqual(trot_p["duty_factor"], 0.50)
        self.assertEqual(walk_p["duty_factor"], 0.65)
        self.assertGreater(trot_p["stride"], walk_p["stride"])
        self.assertGreater(trot_p["cadence"], walk_p["cadence"])

    def test_biped_run_ballistic_flight_phase(self):
        # Duty factor 0.38 means:
        # Left stance: 0.00..0.38. Flight 1: 0.38..0.50.
        # Right stance: 0.50..0.88. Flight 2: 0.88..1.00.
        s_stance = gait.evaluate_biped_run(0.15, hip_height=1.0, leg_length=0.9)
        self.assertTrue(s_stance["legs"]["L"]["grounded"])
        self.assertFalse(s_stance["legs"]["R"]["grounded"])
        self.assertFalse(s_stance["is_flight"])

        # Flight 1 apex at u=0.44: neither foot grounded!
        s_flight1 = gait.evaluate_biped_run(0.44, hip_height=1.0, leg_length=0.9)
        self.assertFalse(s_flight1["legs"]["L"]["grounded"])
        self.assertFalse(s_flight1["legs"]["R"]["grounded"])
        self.assertTrue(s_flight1["is_flight"])
        self.assertGreater(s_flight1["pelvis"]["pos"][2], s_stance["pelvis"]["pos"][2])  # airborne height peak

        # Flight 2 apex at u=0.94: neither foot grounded!
        s_flight2 = gait.evaluate_biped_run(0.94, hip_height=1.0, leg_length=0.9)
        self.assertFalse(s_flight2["legs"]["L"]["grounded"])
        self.assertFalse(s_flight2["legs"]["R"]["grounded"])
        self.assertTrue(s_flight2["is_flight"])

        # Running biomechanics: high knee drive at swing apex and compact pumping elbows
        s_swing_apex = gait.evaluate_biped_run(0.69, hip_height=1.0, leg_length=0.9)
        self.assertGreaterEqual(s_swing_apex["legs"]["L"]["knee_pitch"], 55.0)
        self.assertGreaterEqual(s_flight1["arms"]["L"]["forearm_pitch"], 65.0)
        self.assertGreaterEqual(s_flight1["pelvis"]["rot"][0], 8.0)  # forward lean

    def test_biped_sprint_characteristics(self):
        run_p = gait.GAIT_PRESETS["run"]
        sprint_p = gait.GAIT_PRESETS["sprint"]

        self.assertGreater(sprint_p["stride"], run_p["stride"])
        self.assertGreater(sprint_p["cadence"], run_p["cadence"])
        self.assertGreater(sprint_p["lean"], run_p["lean"])
        self.assertLess(sprint_p["duty_factor"], run_p["duty_factor"])  # shorter ground contact, more flight time

    def test_quadruped_gallop_dual_flight_phases(self):
        feet = [
            {"name": "ik_leg_front.L", "side": 1.0, "is_front": True},
            {"name": "ik_leg_hind.L", "side": 1.0, "is_front": False},
            {"name": "ik_leg_front.R", "side": -1.0, "is_front": True},
            {"name": "ik_leg_hind.R", "side": -1.0, "is_front": False},
        ]
        # Gathered flight apex at u=0.45: all feet in air, spine arched up
        s_gathered = gait.evaluate_quadruped_gallop(0.45, length=1.4, height=1.0, feet_info=feet)
        for f in feet:
            self.assertFalse(s_gathered["feet"][f["name"]]["grounded"])
        self.assertGreater(s_gathered["spine_flexion"], 5.0)  # positive = arched back

        # Extended flight apex at u=0.95: all feet in air, spine extended/sagged
        s_extended = gait.evaluate_quadruped_gallop(0.95, length=1.4, height=1.0, feet_info=feet)
        for f in feet:
            self.assertFalse(s_extended["feet"][f["name"]]["grounded"])
        self.assertLess(s_extended["spine_flexion"], -5.0)  # negative = extended spine reach

    def test_biped_transitions(self):
        # Walk to idle deceleration
        w2i_start = gait.evaluate_biped_walk_to_idle(0.0, hip_height=1.0, leg_length=0.9)
        w2i_end = gait.evaluate_biped_walk_to_idle(1.0, hip_height=1.0, leg_length=0.9)
        self.assertGreater(abs(w2i_start["arms"]["L"]["pitch"]), 5.0)
        self.assertAlmostEqual(w2i_end["arms"]["L"]["pitch"], 0.0, places=4)
        self.assertAlmostEqual(w2i_end["legs"]["L"]["thigh_pitch"], 0.0, places=4)
        self.assertAlmostEqual(w2i_end["legs"]["R"]["thigh_pitch"], 0.0, places=4)

        # Idle to walk acceleration
        i2w_start = gait.evaluate_biped_idle_to_walk(0.0, hip_height=1.0, leg_length=0.9)
        i2w_end = gait.evaluate_biped_idle_to_walk(1.0, hip_height=1.0, leg_length=0.9)
        self.assertAlmostEqual(i2w_start["legs"]["L"]["thigh_pitch"], 0.0, places=4)
    def test_procedural_combat_actions(self):
        # 1. Attack evaluation
        atk_windup = gait.evaluate_biped_attack(0.40, hip_height=1.0, leg_length=0.9, is_shooter=False)
        atk_strike = gait.evaluate_biped_attack(0.75, hip_height=1.0, leg_length=0.9, is_shooter=False)
        atk_recover = gait.evaluate_biped_attack(1.00, hip_height=1.0, leg_length=0.9, is_shooter=False)

        # Windup: arms raised overhead, pelvis reared back
        self.assertLess(atk_windup["arms"]["L"]["pitch"], -50.0)
        self.assertLess(atk_windup["pelvis"]["pos"][1], 0.0)

        # Strike snap: forward lunge, arms slam forward
        self.assertGreater(atk_strike["pelvis"]["pos"][1], 0.0)
        self.assertGreater(atk_strike["arms"]["L"]["pitch"], 0.0)
        self.assertGreater(atk_strike["pelvis"]["rot"][0], 10.0)

        # Shooter attack
        shoot_strike = gait.evaluate_biped_attack(0.60, hip_height=1.0, leg_length=0.9, is_shooter=True)
        self.assertLess(shoot_strike["arms"]["L"]["pitch"], -40.0)  # arms raised level
        self.assertGreater(shoot_strike["legs"]["L"]["knee_pitch"], 15.0)  # braced crouch

        # 2. Hit evaluation
        hit_peak = gait.evaluate_biped_hit(0.35, hip_height=1.0, leg_length=0.9)
        hit_end = gait.evaluate_biped_hit(1.00, hip_height=1.0, leg_length=0.9)
        self.assertLess(hit_peak["head"]["pitch"], -15.0)  # head snaps back
        self.assertLess(hit_peak["pelvis"]["pos"][1], -0.02)  # hips shoved back
        self.assertAlmostEqual(hit_end["pelvis"]["pos"][1], 0.0, places=4)  # damped recovery

        # 3. Death evaluation
        die_buckle = gait.evaluate_biped_death(0.15, hip_height=1.0, leg_length=0.9)
        die_fall = gait.evaluate_biped_death(0.60, hip_height=1.0, leg_length=0.9)
        die_ground = gait.evaluate_biped_death(1.00, hip_height=1.0, leg_length=0.9)

        self.assertGreater(die_buckle["legs"]["L"]["knee_pitch"], 10.0)  # knees buckle
        self.assertLess(die_fall["pelvis"]["rot"][0], -50.0)  # falling backward
        self.assertLess(die_ground["pelvis"]["pos"][2], -0.75)  # corpse on floor plane
        self.assertAlmostEqual(die_ground["pelvis"]["rot"][0], -88.0, places=2)

    def test_secondary_physics_chain(self):
        # 1. Basic properties
        self.assertEqual(gait.evaluate_secondary_chain(0, 0.0), [])
        angles = gait.evaluate_secondary_chain(4, phase=0.25, base_amplitude=10.0, amplitude_growth=1.2, phase_lag=0.3)
        self.assertEqual(len(angles), 4)

        # 2. Seamless cycle looping at t=0 and t=1
        a0 = gait.evaluate_secondary_chain(5, phase=0.0)
        a1 = gait.evaluate_secondary_chain(5, phase=1.0)
        for i in range(5):
            self.assertAlmostEqual(a0[i], a1[i], places=5)

        # 3. Amplitude growth: peak amplitude increases toward tip (segment 3 > segment 0)
        # Check maximum theoretical amplitude growth
        growth = 1.3
        base_amp = 8.0
        angles_t = [gait.evaluate_secondary_chain(4, phase=p * 0.01, base_amplitude=base_amp, amplitude_growth=growth) for p in range(100)]
        max_root = max(abs(row[0]) for row in angles_t)
        max_tip = max(abs(row[3]) for row in angles_t)
        self.assertAlmostEqual(max_root, base_amp, places=1)
        self.assertAlmostEqual(max_tip, base_amp * (growth ** 3), places=1)
        self.assertGreater(max_tip, max_root)

        # 4. Phase lag: tip lags root in periodic wave
        # At phase=0.0: root angle is sin(0) = 0.
        # Segment 1 has phase = 0 - phase_lag, so sin(-0.35) < 0
        a_lag = gait.evaluate_secondary_chain(3, phase=0.0, base_amplitude=10.0, phase_lag=0.35)
        self.assertAlmostEqual(a_lag[0], 0.0, places=5)
        self.assertLess(a_lag[1], 0.0)

        # 5. Impulse with damping
        imp0 = gait.evaluate_secondary_chain(3, phase=0.0, impulse_time=0.1, damping=2.0)
        imp_later = gait.evaluate_secondary_chain(3, phase=0.0, impulse_time=2.0, damping=2.0)
        # Settles/decays over time
        self.assertGreater(abs(imp0[0]), abs(imp_later[0]))

    def test_walker_clips_under_blender(self):
        blender_bin = blender.find(required=False)
        if not blender_bin:
            raise unittest.SkipTest("Blender not installed")
        # Run make_clips on sample model under headless Blender
        cmd = [blender_bin, "-b", "--python", os.path.join(REPO, "autorig", "steps", "make_clips.py"), "--", "wolf"]
        env = dict(os.environ, AUTORIG_MODELS=os.path.join(REPO, "samples"))
        r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        # If wolf has no clips section, test exits gracefully; check it imports gait without error
        self.assertNotIn("No module named 'gait'", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()

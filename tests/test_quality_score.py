# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the quality score (autorig/core/quality_score.py) on made-up measures, its body-part reading of
# bone names, and the run.py score command's wiring. No Blender.
#
#   python -m unittest tests.test_quality_score -v
import json, os, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import quality_score as QS  # noqa: E402

CLEAN_CLIP = {"name": "walk", "stretch3_permille": 0.0, "max_stretch": 1.5, "squash_permille": 0.5,
              "arm_in_torso_pct": 0.0, "leg_cross_pct": 0.2, "foot_skate": 2.0, "hover_pct": 0.0, "sink_pct": 0.5}
CLEAN = {"rest": {"mean_pct": 0.05, "p99_pct": 0.3}, "clips": [CLEAN_CLIP]}


def with_clip(**over):
    return {"rest": CLEAN["rest"], "clips": [CLEAN_CLIP, dict(CLEAN_CLIP, name="trot", **over)]}


class Score(unittest.TestCase):
    def test_clean_model_scores_100(self):
        s = QS.score(CLEAN)
        self.assertEqual((s["score"], s["grade"], s["worst"]), (100.0, "PASS", []))

    def test_one_big_fault_fails_whatever_else_is_clean(self):
        for key in ("rest_p99_pct", "stretch3_permille", "arm_in_torso_pct"):
            bad = QS.CHECKS[key][1] * 2
            m = {"rest": {"mean_pct": 0.05, "p99_pct": bad}, "clips": [CLEAN_CLIP]} if key == "rest_p99_pct" \
                else with_clip(**{key: bad})
            s = QS.score(m)
            self.assertLess(s["score"], QS.PASS_SCORE, key)
            self.assertEqual(s["worst"][0], key)

    def test_worst_clip_decides(self):
        s = QS.score(with_clip(stretch3_permille=QS.CHECKS["stretch3_permille"][1]))
        self.assertEqual(s["values"]["stretch3_permille"], QS.CHECKS["stretch3_permille"][1])

    def test_penalty_is_linear_between_good_and_bad(self):
        good, bad, w = QS.CHECKS["foot_skate"]
        self.assertEqual(QS.penalty("foot_skate", good), 0)
        self.assertAlmostEqual(QS.penalty("foot_skate", (good + bad) / 2), w / 2)
        self.assertEqual(QS.penalty("foot_skate", bad * 9), w)
        self.assertEqual(QS.penalty("foot_skate", None), 0)

    def test_audit_numbers(self):
        audit = {"verdict": {"checks": {"bleed_pct": {"value": 20.0}, "head_pct": {"value": 0.0}}}}
        s = QS.score(CLEAN, audit)
        self.assertEqual(s["penalties"]["audit_bleed_pct"], QS.CHECKS["audit_bleed_pct"][2])
        self.assertGreater(s["penalties"]["audit_head_short"], 0)

    def test_rest_by_design_skips_the_rest_checks(self):
        m = {"rest": {"mean_pct": 30.0, "p99_pct": 60.0}, "clips": [CLEAN_CLIP]}
        self.assertLess(QS.score(m)["score"], QS.PASS_SCORE)
        s = QS.score(m, rest_by_design=True)
        self.assertEqual((s["score"], s["values"]["rest_p99_pct"]), (100.0, None))

    def test_no_clips_is_check_at_best(self):
        s = QS.score({"rest": CLEAN["rest"], "clips": []})
        self.assertEqual((s["grade"], s["worst"]), ("CHECK", ["no_clips"]))

    def test_no_feet_no_sinking(self):
        m = {"rest": CLEAN["rest"], "feet": [], "clips": [dict(CLEAN_CLIP, sink_pct=80.0)]}
        self.assertIsNone(QS.score(m)["values"]["sink_pct"])

    def test_grades(self):
        self.assertEqual([QS.grade(x) for x in (95, 90, 89.9, 75, 74.9, None)],
                         ["PASS", "PASS", "CHECK", "CHECK", "FAIL", None])


class Parts(unittest.TestCase):
    def test_sides(self):
        for n, s in (("Ear_L", "L"), ("Front_Leg_Upper_R", "R"), ("mixamorig:LeftForeArm", "L"), ("thigh.R", "R"),
                     ("upperarm_l", "L"), ("LHand", "L"), ("leg", None), ("Hips", None), ("Spine_1", None)):
            self.assertEqual(QS.side_of(n), s, n)

    def test_parts_of_a_person(self):
        bones = [("Hips", None), ("Spine", "Hips"), ("mixamorig:LeftArm", "Spine"),
                 ("mixamorig:LeftForeArm", "mixamorig:LeftArm"), ("mixamorig:LeftHand", "mixamorig:LeftForeArm"),
                 ("bone.012", "mixamorig:LeftHand"), ("LeftUpLeg", "Hips"), ("LeftLeg", "LeftUpLeg"),
                 ("Neck", "Spine"), ("Head", "Neck")]
        p = QS.parts(bones)
        self.assertEqual(p["Spine"][0], "torso")
        self.assertEqual(p["mixamorig:LeftArm"][:3], ("arm", "L", False))
        self.assertEqual(p["mixamorig:LeftForeArm"][:3], ("arm", "L", True))
        self.assertEqual(p["bone.012"][:3], ("arm", "L", True))        # a finger with no telling name: its hand's
        self.assertEqual(p["LeftUpLeg"][:2], ("leg", "L"))
        self.assertEqual(p["Head"][0], "head")

    def test_parts_of_a_quadruped(self):
        bones = [("root", None), ("Hips", "root"), ("Back_Leg_Pelvis_L", "Hips"),
                 ("Back_Leg_Upper_L", "Back_Leg_Pelvis_L"), ("Front_Leg_Shoulder_R", "Hips"),
                 ("Front_Leg_Upper_R", "Front_Leg_Shoulder_R"), ("Tail_Base", "Hips")]
        p = QS.parts(bones)
        self.assertEqual(p["Back_Leg_Pelvis_L"], ("leg", "L", False, "back"))
        self.assertEqual(p["Back_Leg_Upper_L"], ("leg", "L", True, "back"))
        self.assertEqual(p["Front_Leg_Upper_R"], ("leg", "R", True, "front"))
        self.assertEqual(p["Tail_Base"][0], "tail")


class Command(unittest.TestCase):
    def test_rescore_writes_score_and_table(self):
        out = tempfile.mkdtemp()
        os.makedirs(os.path.join(out, "Cow"))
        with open(os.path.join(out, "Cow", "measure.json"), "w") as fh: json.dump(CLEAN, fh)
        env = dict(os.environ, AUTORIG_MODELS=out)
        r = subprocess.run([sys.executable, os.path.join(REPO, "autorig", "cli", "run.py"), "score", "all", "--rescore",
                            "--out", out, "--audit", "skip"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(out, "Cow", "score.json"), encoding="utf-8") as fh: s = json.load(fh)
        self.assertEqual(s["grade"], "PASS")
        with open(os.path.join(out, "_ranked.md"), encoding="utf-8") as fh: self.assertIn("| Cow | 100.0 | PASS |", fh.read())

    def test_table_keeps_earlier_runs_skipped_models(self):
        out = tempfile.mkdtemp()
        os.makedirs(os.path.join(out, "Cow"))
        with open(os.path.join(out, "Cow", "measure.json"), "w") as fh: json.dump(CLEAN, fh)
        with open(os.path.join(out, "_ranked.json"), "w") as fh: json.dump({"models": [], "skipped": {"Barn": "not rigged"}}, fh)
        env = dict(os.environ, AUTORIG_MODELS=out)
        subprocess.run([sys.executable, os.path.join(REPO, "autorig", "cli", "run.py"), "score", "Cow", "--rescore",
                        "--out", out, "--audit", "skip"], capture_output=True, text=True, env=env)
        with open(os.path.join(out, "_ranked.md"), encoding="utf-8") as fh: self.assertIn("Not rigged (1): Barn", fh.read())

    def test_airborne_clips_skip_the_ground_checks(self):
        m = {"rest": CLEAN["rest"], "clips": [CLEAN_CLIP, dict(CLEAN_CLIP, name="roll", sink_pct=40.0, foot_skate=900.0)]}
        self.assertEqual(QS.score(m)["score"], 100.0)


if __name__ == "__main__":
    unittest.main()

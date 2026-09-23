# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for closed-loop auto-tune optimizer.
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from autorig.core import auto_tune


class TestAutoTuneCore(unittest.TestCase):
    def test_audit_score_clean_pass(self):
        audit = {
            "verdict": {"grade": "PASS", "pass": True, "checks": {"bleed_pct": {"value": 0.5}}},
            "tears": {"combined": 0, "bend_max": 0, "worst_gap_pct": 0.0, "bones_tearing": 0},
        }
        score = auto_tune.audit_score(audit)
        self.assertEqual(score, 0.0)

    def test_audit_score_penalties(self):
        audit_check = {
            "verdict": {"grade": "CHECK", "pass": False, "checks": {"bleed_pct": {"value": 1.2}}},
            "tears": {"combined": 2, "bend_max": 1, "worst_gap_pct": 1.5, "bones_tearing": 1},
        }
        score = auto_tune.audit_score(audit_check)
        # CHECK: 500, comb: 2*20=40, bend: 1*15=15, gap: 1.5*5=7.5, bones: 1*10=10 -> 572.5
        self.assertAlmostEqual(score, 572.5)

        audit_fail = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {"bleed_pct": {"value": 3.0}}},
            "tears": {"combined": 5, "bend_max": 3, "worst_gap_pct": 4.0, "bones_tearing": 2},
        }
        # FAIL: 1000, comb: 5*20=100, bend: 3*15=45, gap: 4*5=20, bones: 2*10=20, bleed: (3.0-2.0)*50=50 -> 1235.0
        score_fail = auto_tune.audit_score(audit_fail)
        self.assertAlmostEqual(score_fail, 1235.0)

    def test_diagnose_audit(self):
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {"bleed_pct": {"value": 0.4}}},
            "tears": {
                "combined": 8,
                "bend_max": 4,
                "worst_gap_pct": 3.5,
                "worst_bone": "leg_front_1.L",
                "by_bone": [{"bone": "leg_front_1.L", "bend": 4, "bend_gap_pct": 3.5}],
            },
            "tear_sites": [
                {
                    "bone": "leg_front_1.L",
                    "points": [[0.8, 0.3, 0.4, 3.5]],
                    "clusters": [{"owners": ["leg_front_1.L", "body"]}],
                }
            ],
        }
        diag = auto_tune.diagnose_audit(audit)
        self.assertEqual(diag["grade"], "FAIL")
        self.assertFalse(diag["pass"])
        self.assertEqual(diag["worst_bone"], "leg_front_1.L")
        self.assertEqual(diag["combined_tears"], 8)
        self.assertEqual(diag["primary_issue"], "tears")
        self.assertEqual(diag["competing_pairs"], [("body", "leg_front_1.L")])

    def test_calculate_tear_centroid(self):
        tear_sites = [
            {
                "bone": "arm.L",
                "points": [
                    [1.0, 2.0, 3.0, 2.0],
                    [2.0, 4.0, 5.0, 1.5],
                ],
            }
        ]
        c = auto_tune.calculate_tear_centroid(tear_sites, "arm.L")
        self.assertEqual(c, [1.5, 3.0, 4.0])

    def test_propose_candidate_joint_blend(self):
        spec = {"rig": {"kind": "placed", "joint_blend": 0.4}}
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {}},
            "tears": {"combined": 6, "bend_max": 3, "worst_gap_pct": 2.0, "worst_bone": "spine_1"},
        }
        cand = auto_tune.propose_tuning_candidate(spec, audit, iteration=1, history=[])
        self.assertIsNotNone(cand)
        self.assertEqual(cand["delta_type"], "joint_blend")
        self.assertAlmostEqual(cand["spec"]["rig"]["joint_blend"], 0.55)

    def test_propose_candidate_smoothing(self):
        spec = {"rig": {"kind": "placed", "joint_blend": 0.55, "smooth": 0}}
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {}},
            "tears": {"combined": 4, "bend_max": 2, "worst_gap_pct": 1.5, "worst_bone": "spine_1"},
        }
        history = [{"param": "joint_blend_inc_1"}]
        cand = auto_tune.propose_tuning_candidate(spec, audit, iteration=2, history=history)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["delta_type"], "smooth")
        self.assertEqual(cand["spec"]["rig"]["smooth"], 1)

    def test_propose_candidate_limb_radius(self):
        spec = {"rig": {"kind": "placed", "joint_blend": 0.55, "smooth": 1, "limb_radius": 1.0}}
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {}},
            "tears": {"combined": 3, "bend_max": 2, "worst_gap_pct": 1.5, "worst_bone": "arm_2.L"},
        }
        history = [{"param": "joint_blend_inc_1"}, {"param": "smooth_1"}]
        cand = auto_tune.propose_tuning_candidate(spec, audit, iteration=3, history=history)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["delta_type"], "limb_radius")
        self.assertAlmostEqual(cand["spec"]["rig"]["limb_radius"], 1.25)

    def test_propose_candidate_station_nudge(self):
        spec = {
            "rig": {
                "kind": "placed",
                "joint_blend": 0.7,
                "smooth": 2,
                "chains": [
                    {
                        "name": "leg.L",
                        "points": [[0.5, 0.5, 0.5], [0.6, 0.6, 0.2]],
                        "names": ["leg_1.L", "leg_2.L"],
                    }
                ],
            }
        }
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {}},
            "tears": {"combined": 3, "bend_max": 2, "worst_gap_pct": 1.5, "worst_bone": "leg_1.L"},
            "tear_sites": [
                {
                    "bone": "leg_1.L",
                    "points": [[0.55, 0.55, 0.55, 2.0]],
                }
            ],
        }
        history = [{"param": "joint_blend_inc_1"}, {"param": "smooth_1"}, {"param": "limb_radius_inc"}]
        cand = auto_tune.propose_tuning_candidate(spec, audit, iteration=4, history=history)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["delta_type"], "joint_nudge")
        pts = cand["spec"]["rig"]["chains"][0]["points"]
        # Point should have nudged towards [0.55, 0.55, 0.55]
        self.assertGreater(pts[0][0], 0.5)

    def test_propose_candidate_rip_welds(self):
        spec = {
            "rig": {
                "kind": "placed",
                "joint_blend": 0.7,
                "smooth": 2,
                "rip_welds": [],
            }
        }
        audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {}},
            "tears": {"combined": 3, "bend_max": 2, "worst_gap_pct": 1.5, "worst_bone": "wing_1.L"},
            "tear_sites": [
                {
                    "bone": "wing_1.L",
                    "clusters": [{"owners": ["wing_1.L", "tail_2"]}],
                }
            ],
        }
        history = [
            {"param": "joint_blend_inc_1"},
            {"param": "smooth_1"},
            {"param": "limb_radius_inc"},
            {"param": "joint_nudge"},
        ]
        cand = auto_tune.propose_tuning_candidate(spec, audit, iteration=5, history=history)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["delta_type"], "rip_welds")
        self.assertIn(["tail_2", "wing_1.L"], cand["spec"]["rig"]["rip_welds"])

    def test_format_tuning_report(self):
        history = [
            {
                "iteration": 0,
                "action": "Baseline",
                "score": 1200.0,
                "grade": "FAIL",
                "pass": False,
                "combined_tears": 10,
                "bend_tears": 4,
                "worst_gap_pct": 3.0,
                "bleed_pct": 0.2,
                "accepted": None,
            },
            {
                "iteration": 1,
                "action": "Increase joint_blend from 0.4 to 0.55",
                "score": 530.0,
                "grade": "CHECK",
                "pass": True,
                "combined_tears": 1,
                "bend_tears": 1,
                "worst_gap_pct": 1.0,
                "bleed_pct": 0.2,
                "accepted": True,
            },
            {
                "iteration": 2,
                "action": "Enable weight smoothing passes (smooth=1)",
                "score": 0.0,
                "grade": "PASS",
                "pass": True,
                "combined_tears": 0,
                "bend_tears": 0,
                "worst_gap_pct": 0.0,
                "bleed_pct": 0.2,
                "accepted": True,
            },
        ]
        report = auto_tune.format_tuning_report(history)
        self.assertIn("Iter | Grade | Comb | Bend", report)
        self.assertIn("Baseline", report)
        self.assertIn("PASS", report)
        self.assertIn("Summary:", report)
        self.assertIn("Initial FAIL -> Final PASS", report)


class TestAutoTuneStepRunner(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-autotune-")
        self.models_dir = os.path.join(self.tmp, "models")
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.models_dir, "test_creature"), exist_ok=True)
        os.makedirs(os.path.join(self.work_dir, "audit"), exist_ok=True)
        self.env = dict(os.environ, AUTORIG_MODELS=self.models_dir, AUTORIG_WORK=self.work_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_tune_model_already_optimal(self):
        from unittest.mock import patch
        from autorig.steps import auto_tune as auto_tune_step

        model_name = "test_creature"
        spec = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "joint_blend": 0.4}}
        spec_file = os.path.join(self.models_dir, model_name, "rig.json")
        import json
        with open(spec_file, "w") as fh: json.dump(spec, fh)

        optimal_audit = {
            "verdict": {"grade": "PASS", "pass": True, "checks": {"bleed_pct": {"value": 0.1}}},
            "tears": {"combined": 0, "bend_max": 0, "worst_gap_pct": 0.0, "bones_tearing": 0},
        }

        with patch("autorig.steps.auto_tune.layout.pack_dir", return_value=os.path.join(self.models_dir, model_name)), \
             patch("autorig.steps.auto_tune.layout.work_dir", return_value=os.path.join(self.work_dir, "audit")), \
             patch("autorig.steps.auto_tune.run_pipeline_eval", return_value=optimal_audit):
            ok, score, hist, best_spec = auto_tune_step.auto_tune_model(model_name, max_iterations=3, work_dir=os.path.join(self.work_dir, "audit"), log_fn=lambda _: None)

        self.assertTrue(ok)
        self.assertEqual(score, 0.0)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["grade"], "PASS")

    def test_auto_tune_model_convergence(self):
        from unittest.mock import patch
        from autorig.steps import auto_tune as auto_tune_step

        model_name = "test_creature"
        spec = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "joint_blend": 0.4}}
        spec_file = os.path.join(self.models_dir, model_name, "rig.json")
        import json
        with open(spec_file, "w") as fh: json.dump(spec, fh)

        initial_audit = {
            "verdict": {"grade": "FAIL", "pass": False, "checks": {"bleed_pct": {"value": 0.5}}},
            "tears": {"combined": 8, "bend_max": 4, "worst_gap_pct": 3.0, "bones_tearing": 1, "worst_bone": "spine_1"},
        }
        improved_audit = {
            "verdict": {"grade": "PASS", "pass": True, "checks": {"bleed_pct": {"value": 0.5}}},
            "tears": {"combined": 0, "bend_max": 0, "worst_gap_pct": 0.0, "bones_tearing": 0},
        }

        eval_calls = [initial_audit, improved_audit]

        def mock_eval(*args, **kw):
            return eval_calls.pop(0) if eval_calls else improved_audit

        with patch("autorig.steps.auto_tune.layout.pack_dir", return_value=os.path.join(self.models_dir, model_name)), \
             patch("autorig.steps.auto_tune.layout.work_dir", return_value=os.path.join(self.work_dir, "audit")), \
             patch("autorig.steps.auto_tune.run_pipeline_eval", side_effect=mock_eval):
            ok, score, hist, best_spec = auto_tune_step.auto_tune_model(model_name, max_iterations=3, work_dir=os.path.join(self.work_dir, "audit"), log_fn=lambda _: None)

        self.assertTrue(ok)
        self.assertEqual(score, 0.0)
        self.assertGreaterEqual(len(hist), 2)
        self.assertTrue(hist[1]["accepted"])
        # Should have saved improved spec
        with open(spec_file, "r") as fh:
            saved_spec = json.load(fh)
        self.assertEqual(saved_spec["rig"]["joint_blend"], 0.55)


if __name__ == "__main__":
    unittest.main()


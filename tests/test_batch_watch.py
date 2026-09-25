# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for Batch Rigger and Watch Folder Daemon.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]

import batch_runner
import watch_daemon
import layout
import spec_store


class BatchWatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-test-batch-watch-")
        cls.orig_models = os.environ.get("AUTORIG_MODELS")
        cls.orig_work = os.environ.get("AUTORIG_WORK")
        cls.test_models = os.path.join(cls.tmp, "models")
        cls.test_work = os.path.join(cls.tmp, "work")
        shutil.copytree(os.path.join(REPO, "samples"), cls.test_models)
        os.environ["AUTORIG_MODELS"] = cls.test_models
        os.environ["AUTORIG_WORK"] = cls.test_work
        layout.ROOT = os.path.abspath(cls.test_models)
        layout.WORK = os.path.abspath(cls.test_work)
        spec_store.reload()

    @classmethod
    def tearDownClass(cls):
        if cls.orig_models:
            os.environ["AUTORIG_MODELS"] = cls.orig_models
        elif "AUTORIG_MODELS" in os.environ:
            del os.environ["AUTORIG_MODELS"]
        if cls.orig_work:
            os.environ["AUTORIG_WORK"] = cls.orig_work
        elif "AUTORIG_WORK" in os.environ:
            del os.environ["AUTORIG_WORK"]
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_resolve_models(self):
        # Resolve all
        all_models = batch_runner.resolve_models(["all"])
        self.assertIn("canine", all_models)
        self.assertIn("biped", all_models)

        # Resolve explicit names
        explicit = batch_runner.resolve_models(["canine", "nonexistent"])
        self.assertEqual(explicit, ["canine"])

        # Resolve with wildcard pattern
        pattern_models = batch_runner.resolve_models(pattern="*p*")
        self.assertIn("biped", pattern_models)
        self.assertIn("pedestal", pattern_models)

        # Resolve riggable
        riggable = batch_runner.resolve_models(["riggable"])
        self.assertIn("canine", riggable)
        riggable_flag = batch_runner.resolve_models(["all"], riggable_only=True)
        self.assertEqual(riggable, riggable_flag)

    def test_format_batch_table(self):
        summary = {
            "models_count": 2,
            "results": [
                {
                    "model": "wolf",
                    "group": "Creatures",
                    "steps": ["rig", "trim", "audit"],
                    "grade": "GOLD",
                    "duration": 4.2,
                    "status": "OK",
                    "error": None,
                },
                {
                    "model": "broken",
                    "group": "(root)",
                    "steps": ["rig"],
                    "grade": "-",
                    "duration": 1.1,
                    "status": "ERROR",
                    "error": "rig step failed",
                },
            ],
            "passed": 1,
            "failed": 1,
            "elapsed": 5.3,
        }
        table = batch_runner.format_batch_table(summary)
        self.assertIn("wolf", table)
        self.assertIn("broken", table)
        self.assertIn("GOLD", table)
        self.assertIn("rig step failed", table)
        self.assertIn("1 Passed | 1 Failed", table)

    def test_run_batch_mocked(self):
        with patch("batch_runner.run_model_pipeline") as mock_pipeline:
            mock_pipeline.side_effect = [
                {"model": "canine", "group": "Creatures", "steps": ["rig"], "grade": "PASS", "duration": 1.0, "status": "OK", "error": None},
                {"model": "biped", "group": "Characters", "steps": ["rig"], "grade": "FAIL", "duration": 1.5, "status": "FAIL", "error": "Audit failed"},
            ]
            summary = batch_runner.run_batch(models=["canine", "biped"], continue_on_error=True)
            self.assertEqual(summary["models_count"], 2)
            self.assertEqual(summary["passed"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(len(summary["results"]), 2)

    def test_is_file_stable(self):
        test_file = os.path.join(self.tmp, "test_file.obj")
        # 0 bytes: not stable
        with open(test_file, "w") as fh:
            pass
        self.assertFalse(watch_daemon.is_file_stable(test_file))

        # Has content but mtime is current (age < 0.2s): not stable with min_age=1.0
        with open(test_file, "w") as fh:
            fh.write("v 0 0 0\n")
        self.assertFalse(watch_daemon.is_file_stable(test_file, min_age=2.0))

        # Set old mtime: stable
        past = time.time() - 10.0
        os.utime(test_file, (past, past))
        self.assertTrue(watch_daemon.is_file_stable(test_file, min_age=1.0))

    def test_place_incoming_and_archive(self):
        incoming_dir = os.path.join(self.tmp, "incoming")
        os.makedirs(incoming_dir, exist_ok=True)

        asset_file = os.path.join(incoming_dir, "test_cube.obj")
        with open(asset_file, "w") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        past = time.time() - 5.0
        os.utime(asset_file, (past, past))

        res = watch_daemon.process_incoming_file(asset_file, auto_rig=False)
        self.assertIsNotNone(res)
        self.assertEqual(res["model"], "test_cube")
        self.assertIn("doctor", res)

        # Check placed folder in models root
        placed_dir = os.path.join(layout.ROOT, "test_cube")
        self.assertTrue(os.path.isdir(placed_dir))
        self.assertTrue(os.path.isfile(os.path.join(placed_dir, "test_cube.obj")))

        # Check moved to _processed
        self.assertFalse(os.path.exists(asset_file))
        proc_file = os.path.join(incoming_dir, "_processed", "test_cube.obj")
        self.assertTrue(os.path.isfile(proc_file))

    def test_cli_subprocesses(self):
        # Test batch CLI --help
        r_batch = subprocess.run([sys.executable, os.path.join(REPO, "autorig", "steps", "batch.py"), "--help"],
                                 capture_output=True, text=True)
        self.assertEqual(r_batch.returncode, 0)
        self.assertIn("--auto-tune", r_batch.stdout)

        # Test watch CLI --help
        r_watch = subprocess.run([sys.executable, os.path.join(REPO, "autorig", "steps", "watch.py"), "--help"],
                                 capture_output=True, text=True)
        self.assertEqual(r_watch.returncode, 0)
        self.assertIn("--once", r_watch.stdout)

    def test_batch_clips_skips_non_clip_models(self):
        # A model with no clips section: pipeline should skip clips without failing
        with patch("batch_runner.spec_store.model", return_value={"schema": "autorig-spec/1", "rig": {"kind": "placed"}}):
            res = batch_runner.run_model_pipeline("biped", steps=["clips"])
            self.assertEqual(res["status"], "OK")
            self.assertNotIn("clips", res["steps"])

    def test_run_clips_expansion(self):
        # run.py clips all should expand to models with clips in spec_store
        from autorig.cli import run
        with patch("autorig.cli.run.blender_step", return_value=0) as mock_step:
            rc = run.main(["clips", "all"])
            self.assertEqual(rc, 0)
            # canine has clips in samples/
            mock_step.assert_any_call("make_clips.py", "canine")

    def test_clippable_models_filtering(self):
        import autorig.gui.server as server
        server.layout = layout
        server.spec_store = spec_store
        clippable = server.clippable_models()
        self.assertIsInstance(clippable, list)
        for g, n in clippable:
            st = server.status(g, n)
            self.assertIsNotNone(st["clips_spec"])
            self.assertIsNone(st["steps"]["clips"])


if __name__ == "__main__":
    unittest.main()


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

    # ---- watch folder: what a drop brings with it, specs, failures

    def _incoming(self, name):
        d = os.path.join(self.tmp, name)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        return d

    @staticmethod
    def _age(*paths):
        past = time.time() - 10.0
        for p in paths:
            for root, dirs, files in os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]:
                for f in files:
                    os.utime(os.path.join(root, f), (past, past))

    def test_obj_brings_its_material_and_textures(self):
        inc = self._incoming("incoming_sidecars")
        os.makedirs(os.path.join(inc, "tex"))
        with open(os.path.join(inc, "crate.obj"), "w") as fh:
            fh.write("mtllib crate.mtl\nv 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        with open(os.path.join(inc, "crate.mtl"), "w") as fh:
            fh.write("newmtl wood\nmap_Kd -s 1 1 1 tex/wood.png\n")
        with open(os.path.join(inc, "tex", "wood.png"), "wb") as fh:
            fh.write(b"\x89PNG....")
        self._age(inc)
        res = watch_daemon.scan_incoming(inc, auto_rig=False, log=lambda *_: None)
        self.assertEqual([r["model"] for r in res], ["crate"])     # the .mtl is not a model of its own
        placed = os.path.join(layout.ROOT, "crate")
        self.assertTrue(os.path.isfile(os.path.join(placed, "crate.mtl")))
        self.assertTrue(os.path.isfile(os.path.join(placed, "tex", "wood.png")))
        self.assertTrue(res[0]["archived"])
        self.assertFalse(os.path.exists(os.path.join(inc, "crate.mtl")))  # archived with its model

    def test_dropped_folder_is_taken_whole(self):
        inc = self._incoming("incoming_folder")
        src = os.path.join(inc, "Lamp Post")
        os.makedirs(os.path.join(src, "textures"))
        with open(os.path.join(src, "lamp.obj"), "w") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        with open(os.path.join(src, "textures", "metal.png"), "wb") as fh:
            fh.write(b"png")
        self._age(src)
        self.assertTrue(watch_daemon.is_file_stable(src))              # a folder: its files decide, not st_size
        res = watch_daemon.scan_incoming(inc, auto_rig=False, log=lambda *_: None)
        self.assertEqual(len(res), 1)
        placed = os.path.join(layout.ROOT, res[0]["model"])
        self.assertTrue(os.path.isfile(os.path.join(placed, "lamp.obj")))
        self.assertTrue(os.path.isfile(os.path.join(placed, "textures", "metal.png")))

    def test_suggested_spec_is_written_and_rigged(self):
        inc = self._incoming("incoming_suggest")
        asset = os.path.join(inc, "blob.obj")
        with open(asset, "w") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        self._age(asset)
        runs = []

        class R:
            returncode, stdout, stderr = 0, "", ""

        def fake_run(script, *args, **kw):
            runs.append(script)
            if script == "suggest.py":                    # what steps/suggest.py writes with -out
                out = args[args.index("-out") + 1]
                os.makedirs(out, exist_ok=True)
                with open(os.path.join(out, args[0] + "_suggest.json"), "w") as fh:
                    json.dump({"archetype": "quadruped", "confidence": "high",
                               "spec": {"schema": "autorig-spec/1", "rig": {"kind": "placed", "chains": []}}}, fh)
            return R()

        with patch.object(watch_daemon.blender, "find", return_value="blender"), \
             patch.object(watch_daemon.blender, "run", fake_run), \
             patch("watch_daemon.run_model_pipeline", return_value={"status": "OK", "steps": ["rig"]}) as pipe:
            res = watch_daemon.process_incoming_file(asset, auto_rig=True, log=lambda *_: None)
        self.assertEqual(runs, ["survey.py", "suggest.py"])
        self.assertEqual(res["suggested"], "quadruped")
        spec_store.reload()
        self.assertEqual(spec_store.model("blob")["rig"]["kind"], "placed")
        pipe.assert_called_once()
        self.assertNotIn("survey", pipe.call_args.kwargs["steps"])     # surveyed already, above

    def test_custom_builder_from_a_drop_is_not_run(self):
        inc = self._incoming("incoming_custom")
        src = os.path.join(inc, "sneaky")
        os.makedirs(src)
        with open(os.path.join(src, "sneaky.obj"), "w") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        with open(os.path.join(src, "rig.json"), "w") as fh:
            json.dump({"schema": "autorig-spec/1", "rig": {"kind": "custom", "builder": "evil.py"}}, fh)
        with open(os.path.join(src, "evil.py"), "w") as fh:
            fh.write("raise SystemExit('must never run')\n")
        self._age(src)
        with patch("watch_daemon.run_model_pipeline") as pipe:
            res = watch_daemon.scan_incoming(inc, auto_rig=True, log=lambda *_: None)
        pipe.assert_not_called()
        self.assertEqual(res[0]["pipeline"]["status"], "SKIPPED")

    def test_unarchivable_or_failing_items_are_not_taken_again(self):
        inc = self._incoming("incoming_stuck")
        stuck = os.path.join(inc, "stuck.obj")
        boom = os.path.join(inc, "boom.obj")
        for p in (stuck, boom):
            with open(p, "w") as fh:
                fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
        self._age(stuck, boom)
        real = watch_daemon.place_incoming

        def place(src, *a, **k):
            if src.endswith("boom.obj"):
                raise OSError("locked by the writer")
            return real(src, *a, **k)

        seen = set()
        with patch("watch_daemon.archive", return_value=False), patch("watch_daemon.place_incoming", place):
            first = watch_daemon.scan_incoming(inc, auto_rig=False, seen=seen, log=lambda *_: None)
            second = watch_daemon.scan_incoming(inc, auto_rig=False, seen=seen, log=lambda *_: None)
        self.assertEqual([r["model"] for r in first], ["stuck"])        # boom failed, stuck still went through
        self.assertEqual(second, [])                                    # neither is ingested again
        self.assertTrue(os.path.isdir(os.path.join(layout.ROOT, "stuck")))
        self.assertFalse(os.path.isdir(os.path.join(layout.ROOT, "stuck_1")))

    def test_group_name_cannot_climb_out(self):
        self.assertEqual(watch_daemon.clean_group("../../etc"), "etc")
        self.assertEqual(watch_daemon.clean_group(""), "")

    # ---- batch: spec lookups, bad specs, stale audits

    def _pipe(self, model, spec, run_codes, steps):
        calls = []

        class R:
            def __init__(self, code):
                self.returncode, self.stdout, self.stderr = code, "", "boom" if code else ""

        def fake_run(script, *args, **kw):
            calls.append(script)
            return R(run_codes.get(os.path.basename(script), 0))

        with patch("batch_runner.spec_store.model", return_value=spec), patch.object(batch_runner.blender, "run", fake_run):
            return batch_runner.run_model_pipeline(model, steps=steps), calls

    def test_custom_builder_is_read_from_rig_section(self):
        builder = os.path.join(layout.pack_dir("biped"), "build_biped.py")
        with open(builder, "w") as fh:
            fh.write("# builder\n")
        try:
            spec = {"schema": "autorig-spec/1", "rig": {"kind": "custom", "builder": "build_biped.py"}}
            res, calls = self._pipe("biped", spec, {}, ["rig"])
            self.assertEqual(res["status"], "OK", res["error"])
            self.assertEqual(calls, [builder])
            res, _ = self._pipe("biped", {"rig": {"kind": "custom", "builder": "missing.py"}}, {}, ["rig"])
            self.assertEqual(res["status"], "ERROR")
            self.assertIn("missing.py", res["error"])
        finally:
            os.remove(builder)

    def test_bad_rig_json_fails_that_model_only(self):
        with patch("batch_runner.spec_store.model", side_effect=ValueError("schema is 'x'")):
            res = batch_runner.run_model_pipeline("biped", steps=["rig"])
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("rig.json", res["error"])

    def test_failed_audit_never_reports_the_old_grade(self):
        audit_dir = layout.work_dir("audit")
        os.makedirs(audit_dir, exist_ok=True)
        stale = os.path.join(audit_dir, "biped.json")
        with open(stale, "w") as fh:
            json.dump({"verdict": {"grade": "PASS", "pass": True, "checks": {}}}, fh)
        res, _ = self._pipe("biped", {"rig": {"kind": "placed"}}, {"audit.py": 1}, ["audit"])
        self.assertEqual(res["status"], "ERROR")
        self.assertEqual(res["grade"], "-")
        self.assertFalse(os.path.exists(stale))

    def test_clips_use_the_inferred_archetype(self):
        spec = {"schema": "autorig-spec/1", "rig": {"kind": "placed", "skeleton": "quadruped"}}
        res, calls = self._pipe("biped", spec, {}, ["clips"])
        self.assertEqual(calls, ["make_clips.py"])
        self.assertIn("clips", res["steps"])

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


# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for Process Watchdog, Timeouts, and Resource Guard.

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "autorig", "core")]

import autorig_watchdog as watchdog
import blender


class WatchdogTest(unittest.TestCase):
    def test_get_timeout_for_script(self):
        # Defaults, by step (a script maps to its step: rerig.py and a builder are the rig step)
        T = watchdog.DEFAULT_TIMEOUTS
        self.assertEqual(watchdog.get_timeout_for_script("survey.py"), T["survey"])
        self.assertEqual(watchdog.get_timeout_for_script("rerig.py"), T["rig"])
        self.assertEqual(watchdog.get_timeout_for_script("rerig_humanoid.py"), T["rig"])
        self.assertEqual(watchdog.get_timeout_for_script("decimate.py"), T["trim"])
        self.assertEqual(watchdog.get_timeout_for_script("audit_all.py"), 0.0)          # a wrapper: no limit of its own
        self.assertEqual(watchdog.get_timeout_for_script("unknown_step.py"), T["default"])
        self.assertGreaterEqual(T["rig"], 600.0)                                        # real rigs take minutes

        # A command line: the first .py in it decides, whatever the job's label says
        argv = ["blender", "-b", "--python-exit-code", "1", "--python", "/x/steps/run_builder.py", "--", "/m/build.py"]
        self.assertEqual(watchdog.step_of(argv), "rig")
        self.assertEqual(watchdog.get_timeout_for_script(argv), T["rig"])
        self.assertEqual(watchdog.step_of([sys.executable, "-u", "/x/steps/publish.py", "."]), "publish")
        # model names that contain another step's name no longer borrow its limit
        self.assertEqual(watchdog.get_timeout_for_script("trimmer"), T["default"])
        self.assertEqual(watchdog.get_timeout_for_script("survey_drone"), T["default"])

        # the global override never cuts a wrapper's whole multi-step run short
        os.environ["AUTORIG_STEP_TIMEOUT"] = "300"
        try:
            self.assertEqual(watchdog.get_timeout_for_script("auto_tune.py"), 0.0)
            self.assertEqual(watchdog.get_timeout_for_script("rerig.py"), 300.0)
        finally:
            del os.environ["AUTORIG_STEP_TIMEOUT"]

        # Per-step env override by step name, for any script of that step
        os.environ["AUTORIG_TIMEOUT_RIG"] = "1234"
        try:
            self.assertEqual(watchdog.get_timeout_for_script("rerig_humanoid.py"), 1234.0)
        finally:
            del os.environ["AUTORIG_TIMEOUT_RIG"]

        # Explicit timeout parameter
        self.assertEqual(watchdog.get_timeout_for_script("survey.py", explicit_timeout=15.0), 15.0)

        # Global env override
        os.environ["AUTORIG_STEP_TIMEOUT"] = "45.0"
        try:
            self.assertEqual(watchdog.get_timeout_for_script("survey.py"), 45.0)
        finally:
            del os.environ["AUTORIG_STEP_TIMEOUT"]

        # Per-step env override
        os.environ["AUTORIG_TIMEOUT_SURVEY"] = "99.0"
        try:
            self.assertEqual(watchdog.get_timeout_for_script("survey.py"), 99.0)
        finally:
            del os.environ["AUTORIG_TIMEOUT_SURVEY"]

    def test_get_max_memory_mb(self):
        self.assertEqual(watchdog.get_max_memory_mb(), 4096.0)
        os.environ["AUTORIG_MAX_MEMORY_MB"] = "2048"
        try:
            self.assertEqual(watchdog.get_max_memory_mb(), 2048.0)
        finally:
            del os.environ["AUTORIG_MAX_MEMORY_MB"]

    def test_get_process_rss_mb(self):
        rss = watchdog.get_process_rss_mb(os.getpid())
        if sys.platform.startswith("linux"):
            self.assertGreater(rss, 0.0)
        else:
            self.assertGreaterEqual(rss, 0.0)

    def test_run_with_watchdog_success(self):
        cmd = [sys.executable, "-c", "print('watchdog_ok')"]
        r = watchdog.run_with_watchdog(cmd, timeout=10.0)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(r.timed_out)
        self.assertFalse(r.memory_exceeded)
        self.assertIn("watchdog_ok", r.stdout)
        self.assertGreaterEqual(r.duration, 0.0)

    def test_run_with_watchdog_timeout(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        r = watchdog.run_with_watchdog(cmd, timeout=0.3, poll_interval=0.05)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.returncode, 124)
        self.assertLess(r.duration, 2.0)
        self.assertIn("TIMEOUT", r.stderr)

    def test_run_with_watchdog_large_output_no_deadlock(self):
        # Outputs 1 MB of text (substantially exceeding standard 64KB OS pipe buffer)
        cmd = [sys.executable, "-c", "import sys; sys.stdout.write('x' * (1024 * 1024)); sys.stdout.flush()"]
        r = watchdog.run_with_watchdog(cmd, timeout=10.0)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(r.timed_out)
        self.assertEqual(len(r.stdout), 1024 * 1024)

    def test_kill_process_tree(self):
        # Spawn a sleeping process and verify kill
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        self.assertIsNone(proc.poll())
        watchdog.kill_process_tree(proc)
        proc.wait(timeout=2.0)
        self.assertIsNotNone(proc.poll())

    def test_kill_process_tree_takes_children(self):
        # a wrapper (auto_tune.py, retarget.py) and the Blender it started: killing the wrapper's tree kills both
        code = ("import subprocess, sys, time\n"
                "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "print(c.pid, flush=True)\n"
                "time.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        child = int(proc.stdout.readline())
        self.assertTrue(watchdog._alive(child))
        watchdog.kill_process_tree(proc)
        proc.wait(timeout=10.0)
        for _ in range(50):
            if not watchdog._alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(watchdog._alive(child), "the child outlived its parent's tree kill")

    def test_reaper_only_touches_what_it_abandoned(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            self.assertEqual(watchdog.reap_orphaned_blender_processes(), [])
            self.assertIsNone(other.poll())                   # a process it did not abandon is left alone
            watchdog.abandon(other)
            self.assertEqual(watchdog.reap_orphaned_blender_processes(), [other.pid])
            other.wait(timeout=10.0)
        finally:
            if other.poll() is None:
                other.kill()

    def test_job_watchdog(self):
        class DummyJob:
            def __init__(self):
                self.lines = []
            def add(self, line):
                self.lines.append(line)

        job = DummyJob()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        guard = watchdog.JobWatchdog(job, proc, "test_job", timeout_seconds=0.3)
        guard.start()
        proc.wait(timeout=3.0)
        guard.stop()

        self.assertIsNotNone(proc.poll())
        self.assertTrue(any("timed out" in line for line in job.lines))

    def test_reap_orphaned_blender_processes_safe(self):
        reaped = watchdog.reap_orphaned_blender_processes()
        self.assertIsInstance(reaped, list)

    def test_blender_run_integrated(self):
        # blender.run with watchdog
        if not blender.find(required=False):
            self.skipTest("Blender not found")
        r = blender.run("survey.py", "-h")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(getattr(r, "timed_out", False))


if __name__ == "__main__":
    unittest.main()

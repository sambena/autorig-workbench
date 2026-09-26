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

import watchdog
import blender


class WatchdogTest(unittest.TestCase):
    def test_get_timeout_for_script(self):
        # Defaults
        self.assertEqual(watchdog.get_timeout_for_script("survey.py"), 60.0)
        self.assertEqual(watchdog.get_timeout_for_script("rerig.py"), 180.0)
        self.assertEqual(watchdog.get_timeout_for_script("decimate.py"), 120.0)
        self.assertEqual(watchdog.get_timeout_for_script("audit_all.py"), 120.0)
        self.assertEqual(watchdog.get_timeout_for_script("unknown_step.py"), 180.0)

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
        r = blender.run("survey.py", "-h")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(getattr(r, "timed_out", False))


if __name__ == "__main__":
    unittest.main()

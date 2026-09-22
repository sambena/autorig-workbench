# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: automated test for headless Blender audit thresholds.
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender
from scripts import ci_audit_thresholds


class TestAuditThresholdsCI(unittest.TestCase):
    def test_ci_headless_audit_pipeline(self):
        b_exe = blender.find(required=False)
        if not b_exe:
            self.skipTest("Blender not installed")
        code = ci_audit_thresholds.run_ci()
        self.assertEqual(code, 0, "CI audit pipeline did not return 0 (audit failed thresholds)")


if __name__ == "__main__":
    unittest.main()

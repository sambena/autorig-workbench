# SPDX-License-Identifier: GPL-3.0-or-later
# core/gpu_setup.py: the AUTORIG_GPU switch (the Blender side is checked by run.py gpu).
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autorig", "core"))
import gpu_setup  # noqa: E402


class GpuSetupTest(unittest.TestCase):
    def test_on_unless_switched_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTORIG_GPU", None)
            self.assertTrue(gpu_setup.wanted())
        for off in ("0", "off", "CPU", "no"):
            with mock.patch.dict(os.environ, {"AUTORIG_GPU": off}):
                self.assertFalse(gpu_setup.wanted(), off)

    def test_not_named_like_blenders_gpu_module(self):
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(gpu_setup.__file__), "gpu.py")))


if __name__ == "__main__":
    unittest.main()

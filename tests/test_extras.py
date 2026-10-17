# SPDX-License-Identifier: GPL-3.0-or-later
# core/extras.py: the optional-package folder, without installing anything.
import os, shutil, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autorig", "core"))
import extras  # noqa: E402


class ExtrasTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"AUTORIG_EXTRAS": self.dir})
        env.start(); self.addCleanup(env.stop)

    def dist(self, name):
        os.makedirs(os.path.join(self.dir, name + ".dist-info"))

    def test_installed_reads_dist_info_names(self):
        self.dist("pymeshlab-2025.7.post1")
        self.dist("robust_laplacian-1.1.0")
        self.dist("open_clip_torch-3.1.0")
        self.assertTrue(extras.installed("pymeshlab"))
        self.assertTrue(extras.installed("robust-laplacian"))
        self.assertTrue(extras.installed("open-clip"))
        self.assertFalse(extras.installed("trimesh"))

    def test_activate_appends_so_blenders_own_packages_win(self):
        before = list(sys.path)
        try:
            self.assertEqual(extras.activate(), self.dir)
            self.assertEqual(sys.path[-1], self.dir)
            self.assertEqual(sys.path[0], before[0])
        finally:
            sys.path[:] = before

    def test_every_extra_has_a_gpl_compatible_licence(self):
        for name, (req, mod, licence, use) in extras.EXTRAS.items():
            self.assertIn(licence, ("MIT", "BSD-2", "BSD-3", "Apache-2.0", "LGPL-3.0", "GPL-3.0", "MPL-2.0"), name)
            self.assertTrue(use, name)

    def test_open_clip_installs_the_cpu_build_of_pytorch(self):
        calls = []
        with mock.patch.object(extras.subprocess, "call", lambda cmd: calls.append(cmd) or 0):
            extras.install(["open-clip"], python=sys.executable)
        cmd = calls[0]
        self.assertIn("https://download.pytorch.org/whl/cpu", cmd)
        self.assertIn("open_clip_torch", cmd)
        self.assertIn("torch", cmd)

    def test_unknown_extra(self):
        with self.assertRaises(ValueError):
            extras.install(["not-a-thing"], python=sys.executable)


if __name__ == "__main__":
    unittest.main()

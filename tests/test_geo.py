# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: unit tests for pinch crease detection and geometry helpers in geo.py.
import math, os, subprocess, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import geo
import blender


class TestGeoAlgorithms(unittest.TestCase):
    def test_find_pinches_detects_crease(self):
        # Generate an hourglass-shaped limb from Z=0 to Z=1 with a pinch at Z=0.5
        coords = []
        for z in [i / 40.0 for i in range(41)]:
            r = 0.8 - 0.5 * math.sin(z * math.pi)
            for theta in [j * math.pi / 8.0 for j in range(16)]:
                coords.append((r * math.cos(theta), r * math.sin(theta), z))

        pinches = geo.find_pinches(coords, (0, 0, 0), (0, 0, 1), slices=30, min_t=0.2, max_t=0.8)
        self.assertTrue(len(pinches) >= 1)
        top_pinch = pinches[0]
        self.assertAlmostEqual(top_pinch["t"], 0.5, delta=0.03)
        self.assertAlmostEqual(top_pinch["radius"], 0.3, delta=0.05)
        self.assertGreater(top_pinch["prominence"], 0.15)
        self.assertAlmostEqual(top_pinch["pos"].x, 0.0, places=3)
        self.assertAlmostEqual(top_pinch["pos"].y, 0.0, places=3)
        self.assertAlmostEqual(top_pinch["pos"].z, 0.5, delta=0.03)


if "bpy" not in sys.modules:
    class TestGeoUnderBlender(unittest.TestCase):
        @unittest.skipUnless(blender.find(required=False), "Blender not found")
        def test_run_geo_under_blender(self):
            blender_bin = blender.find()
            script = os.path.abspath(__file__)
            r = subprocess.run([blender_bin, "-b", "--factory-startup", "--python", script, "--", "-v"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stderr + r.stdout)


if __name__ == "__main__":
    clean_argv = [sys.argv[0]]
    if "--" in sys.argv:
        clean_argv.extend(sys.argv[sys.argv.index("--") + 1:])
    unittest.main(argv=clean_argv)

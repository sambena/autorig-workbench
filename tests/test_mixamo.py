# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the Mixamo steps. mixamo_import's checks on made-up files, and (with Blender) mixamo_upload on
# the biped sample followed by mixamo_import on its result, standing in for a Mixamo download.
#
#   python -m unittest tests.test_mixamo -v
import json, os, re, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps")]
import blender  # noqa: E402
import mixamo_import  # noqa: E402

HAVE_BLENDER = blender.find(required=False) is not None and not os.environ.get("AUTORIG_NO_BLENDER")
FOOTER = bytes.fromhex("f85a8c6adef5d97eece90ce3758f290b")


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-mixamo-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_a_whole_binary_fbx_whatever_its_name(self):
        # a browser's unfinished-looking .tmp is fine when the bytes are whole
        self.assertTrue(mixamo_import.is_fbx_binary(self.write("abc.tmp", b"Kaydara FBX Binary  \x00" + b"x" * 64 + FOOTER)))

    def test_a_cut_off_download_or_an_ascii_fbx_is_refused(self):
        self.assertFalse(mixamo_import.is_fbx_binary(self.write("cut.fbx", b"Kaydara FBX Binary  \x00" + b"x" * 64)))
        self.assertFalse(mixamo_import.is_fbx_binary(self.write("ascii.fbx", b"; FBX 7.4.0 project file\n" + FOOTER)))
        self.assertFalse(mixamo_import.is_fbx_binary(os.path.join(self.tmp, "missing.fbx")))
        result = mixamo_import.run(os.path.join(self.tmp, "cut.fbx"), "src.glb", "X", self.tmp)
        self.assertIn("error", result)

    def test_texture_paths_are_read_out_of_the_file(self):
        path = self.write("t.fbx", b"..\x00IceAdult.fbm\\IceAdult_basecolor.png\x00..C:\\work\\IceAdult_normal.png\x00")
        found = mixamo_import.texture_paths(path)
        self.assertTrue(any(p.endswith("IceAdult.fbm\\IceAdult_basecolor.png") for p in found), found)
        self.assertTrue(any(p.endswith("IceAdult_normal.png") for p in found), found)


@unittest.skipUnless(HAVE_BLENDER, "needs Blender")
class UploadThenImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-test-mixamo-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_biped_sample_round_trip(self):
        src = os.path.join(REPO, "samples", "biped", "biped.obj")
        upload = os.path.join(self.tmp, "up", "Biped.fbx")
        r = blender.run("mixamo_upload.py", "--in", src, "--out", upload, "--height", "1.7", "--triangles", "2000")
        m = re.search(r"MIXAMO_UPLOAD (\{.*\})", r.stdout)
        self.assertIsNotNone(m, r.stdout[-800:] + r.stderr[-800:])
        report = json.loads(m.group(1))
        self.assertTrue(os.path.isfile(upload))
        self.assertLessEqual(report["triangles"], 2000)
        self.assertTrue(mixamo_import.is_fbx_binary(upload))
        # the upload stands in for the download: maps from the sample, Unity's axes, the game file beside its .fbm
        out = os.path.join(self.tmp, "game")
        result = mixamo_import.run(upload, src, "Biped", out)
        self.assertNotIn("error", result, result)
        self.assertTrue(result["ok"], result)          # no maps to point at: an untextured sample
        self.assertTrue(mixamo_import.is_fbx_binary(os.path.join(out, "Biped.fbx")))


if __name__ == "__main__":
    unittest.main()

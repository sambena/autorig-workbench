# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a rigged sample for the tests that read one (retarget, export).
#
# rigged/ and *.blend are not committed, so a fresh clone (and CI) has no rig to read. rig() builds one with the rig
# step under headless Blender, once per test class, into the class's own copy of samples/:
#
#   cls.rig_error = rigged_sample.rig("biped", cls.test_work)      # in setUpClass, after AUTORIG_MODELS is set
#   rigged_sample.need(self, self.rig_error)                       # first line of a test that reads the rig
#
# need() skips the test when Blender is missing and fails it when Blender is there but the rig step failed: a broken
# rig step is a failure, never a skip.
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]

import blender
import layout

NO_BLENDER = "Blender not found (the test reads a rigged model)"


def rig(name, work):
    """Rigs `name` under the current models root (rerig.py, its QA picture into <work>/qa). Returns None when the
    rig's .blend and .fbx are there, NO_BLENDER without Blender, else what went wrong."""
    blend = os.path.join(layout.rigged_dir(name), name + ".blend")
    fbx = os.path.join(layout.rigged_dir(name), name + ".fbx")
    if os.path.isfile(blend) and os.path.isfile(fbx):
        return None
    if not blender.find(required=False):
        return NO_BLENDER
    r = blender.run("rerig.py", "-only", name, "-qa", os.path.join(work, "qa"))
    if r.returncode != 0 or not os.path.isfile(blend) or not os.path.isfile(fbx):
        return "rigging %s failed (exit %s):\n%s" % (name, r.returncode, ((r.stdout or "") + (r.stderr or ""))[-3000:])
    return None


def need(test, error):
    """Skips `test` without Blender, fails it when the rig step failed, else returns."""
    if error == NO_BLENDER:
        test.skipTest(NO_BLENDER)
    if error:
        test.fail(error)

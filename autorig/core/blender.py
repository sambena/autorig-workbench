# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: finding and running Blender, for the drivers that run under ordinary Python (cli/, gui/).
#
# Order: the AUTORIG_BLENDER or BLENDER environment variable, `blender` on PATH, then the usual install folders
# (newest version first).
import glob, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = os.path.join(os.path.dirname(HERE), "steps")

_CANDIDATES = [
    r"C:\Program Files\Blender Foundation\Blender *\blender.exe",           # Windows installer
    "/Applications/Blender.app/Contents/MacOS/Blender",                     # macOS
    "/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",      # Linux
]


def _version_key(path):
    nums = [int(n) for n in "".join(c if c.isdigit() else " " for c in path).split()]
    return nums


def find(required=True):
    for var in ("AUTORIG_BLENDER", "BLENDER"):
        if os.environ.get(var):
            return os.environ[var]
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    for pattern in _CANDIDATES:
        hits = sorted(glob.glob(pattern), key=_version_key, reverse=True)
        if hits:
            return hits[0]
    if required:
        sys.exit("Blender not found: install Blender (the steps are tested on 5.2) or set AUTORIG_BLENDER to its "
                 "executable.")
    return None


def command(script, *args):
    """blender -b --python <steps/script> -- args. A script given as a path (a model's own builder) runs through
    run_builder.py, which puts the tool's steps and core on its import path first."""
    if os.path.isabs(script) or os.sep in script or "/" in script:
        return [find(), "-b", "--python", os.path.join(STEPS, "run_builder.py"), "--", script] + list(args)
    return [find(), "-b", "--python", os.path.join(STEPS, script), "--"] + list(args)


def run(script, *args, **kw):
    """Runs a step to the end, output captured as text."""
    return subprocess.run(command(script, *args), capture_output=True, text=True, errors="replace", **kw)

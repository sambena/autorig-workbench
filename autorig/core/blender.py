# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: finding and running Blender, for the drivers that run under ordinary Python (cli/, gui/).
#
# Order: the AUTORIG_BLENDER or BLENDER environment variable, `blender` on PATH, then the usual install folders
# (newest version first). AUTORIG_NO_BLENDER=1 hides it (the test suite then skips its Blender tests).
import glob, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = os.path.join(os.path.dirname(HERE), "steps")

TESTED_MAJOR_MINOR = (5, 2)
TESTED_VERSION_STR = "5.2"
TESTED_RANGE_DESC = "5.2 LTS"

_VERSION_CACHE = {}

_CANDIDATES = [
    r"C:\Program Files\Blender Foundation\Blender *\blender.exe",           # Windows installer
    "/Applications/Blender.app/Contents/MacOS/Blender",                     # macOS
    "/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",      # Linux
]


def _version_key(path):
    nums = [int(n) for n in "".join(c if c.isdigit() else " " for c in path).split()]
    return nums


def version(path=None):
    """Returns (major, minor, patch) as ints, or None if unknown/unreachable."""
    if "bpy" in sys.modules:
        try:
            import bpy
            return tuple(bpy.app.version[:3])
        except Exception:
            pass
    b_path = path or find(required=False)
    if not b_path:
        return None
    if b_path in _VERSION_CACHE:
        return _VERSION_CACHE[b_path]
    try:
        r = subprocess.run([b_path, "--version"], capture_output=True, text=True, errors="replace", timeout=10)
        m = re.search(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?", r.stdout)
        if m:
            v = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
            _VERSION_CACHE[b_path] = v
            return v
    except Exception:
        pass
    return None


def version_string(path=None):
    """Returns e.g. '5.2.2' or empty string if unknown."""
    v = version(path)
    return f"{v[0]}.{v[1]}.{v[2]}" if v else ""


def is_tested_version(path=None):
    """True when Blender matches the tested major and minor version (5.2)."""
    v = version(path)
    return bool(v and v[:2] == TESTED_MAJOR_MINOR)


def version_warning(path=None):
    """Returns a warning string if Blender is outside the tested range (5.2 LTS), else None."""
    v = version(path)
    if not v:
        return None
    if v[:2] != TESTED_MAJOR_MINOR:
        vs = f"{v[0]}.{v[1]}.{v[2]}"
        return (f"Blender {vs} detected. Autorig Workbench is tested on Blender {TESTED_RANGE_DESC}; "
                f"other versions may exhibit API drift or differing armature/mesh behavior.")
    return None


def warn_if_untested(path=None, file=sys.stderr):
    """Prints a warning to stderr if Blender is outside tested range (5.2 LTS). Returns warning or None."""
    w = version_warning(path)
    if w:
        print("WARNING: %s" % w, file=file, flush=True)
        return w
    return None


def find(required=True):
    if os.environ.get("AUTORIG_NO_BLENDER"):  # tests: behave as if Blender is not installed
        if required:
            sys.exit("Blender disabled by AUTORIG_NO_BLENDER.")
        return None
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
    # --python-exit-code: a step whose script raises exits 1 (Blender's own default is 0 even after a traceback)
    head = [find(), "-b", "--python-exit-code", "1", "--python"]
    if os.path.isabs(script) or os.sep in script or "/" in script:
        return head + [os.path.join(STEPS, "run_builder.py"), "--", script] + list(args)
    return head + [os.path.join(STEPS, script), "--"] + list(args)


def run(script, *args, timeout=None, memory_limit_mb=None, **kw):
    """Runs a step to the end with watchdog timeout and memory protection, output captured as text."""
    try:
        import autorig_watchdog as watchdog
    except ImportError:
        from autorig.core import autorig_watchdog as watchdog
    argv = command(script, *args)
    step_timeout = watchdog.get_timeout_for_script(argv, timeout)      # a builder path counts as the rig step
    return watchdog.run_with_watchdog(argv, timeout=step_timeout,
                                      memory_limit_mb=memory_limit_mb, capture_output=True, **kw)

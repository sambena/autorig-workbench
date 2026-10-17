# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: optional Python packages ("extras") for Blender's own Python.
#
#   python autorig/cli/run.py extras                     what is installed, and what each extra is for
#   python autorig/cli/run.py extras install pymeshlab trimesh     (or: install all)
#   python autorig/cli/run.py extras install gltfpack gltf-validator   programs for the export passes
#
# The tool itself needs nothing from pip. Some steps do better with a library (texture-safe decimation, mesh
# repair, UV atlases, a picture classifier); those are extras. They are installed with Blender's bundled Python
# (pip install --target) into one folder per Python version, so the same packages import inside a Blender step
# (call extras.activate() first) and in Blender's Python run on its own (extras.run_python). A step must still
# work, or say plainly what to install, when an extra is missing: check with extras.have(name).
#
# The folder: AUTORIG_EXTRAS, else %LOCALAPPDATA%\autorig\extras\py<ver> on Windows and
# ~/.local/share/autorig/extras/py<ver> elsewhere. Every extra's licence must fit the tool's GPL-3.0.
import glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# name: (pip requirement, import name, licence, what it is for). Steps add their own extras here.
EXTRAS = {
    "pymeshlab": ("pymeshlab", "pymeshlab", "GPL-3.0",
                  "texture-safe decimation, mesh repair, isotropic remesh, connected parts"),
    "pymeshfix": ("pymeshfix", "pymeshfix", "GPL-3.0", "makes a messy multi-shell mesh one watertight surface"),
    "trimesh": ("trimesh", "trimesh", "MIT", "mesh measures: parts, stable poses, symmetry, convex hulls"),
    "xatlas": ("xatlas", "xatlas", "MIT", "automatic UV unwrap and atlas packing"),
    "coacd": ("coacd", "coacd", "MIT", "convex pieces for game colliders and rough part splits"),
    "manifold3d": ("manifold3d", "manifold3d", "Apache-2.0", "robust booleans: carving parts, merging shells"),
    "robust-laplacian": ("robust-laplacian", "robust_laplacian", "MIT",
                         "weight smoothing that copes with non-manifold meshes"),
    "open-clip": ("open_clip_torch", "open_clip", "MIT",
                  "a picture classifier (CPU) for telling a dog from a winged creature in the suggest step"),
}


def data_root():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "autorig", "extras")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "autorig", "extras")


def blender_python():
    """Blender's bundled Python, next to the Blender found by core/blender.py; None without Blender."""
    sys.path.insert(0, HERE)
    import blender
    exe = blender.find(required=False)
    if not exe: return None
    exe = os.path.realpath(exe)
    base = os.path.dirname(exe)
    names = ("python.exe",) if os.name == "nt" else ("python3*", "python")
    for name in names:
        hits = [h for h in glob.glob(os.path.join(base, "*", "python", "bin", name))
                if re.match(r"^python(3(\.\d+)?)?(\.exe)?$", os.path.basename(h))]
        if hits: return sorted(hits)[-1]
    return None


def python_tag(python=None):
    """py313 for Python 3.13: the extras folder is per version, since compiled wheels are."""
    if python is None:
        return "py%d%d" % sys.version_info[:2]
    out = subprocess.run([python, "-c", "import sys;print('py%d%d' % sys.version_info[:2])"], capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    return out.stdout.strip() or "py3"


def folder(python=None):
    """The extras folder for this Python (inside Blender: Blender's), or for the Python given."""
    if os.environ.get("AUTORIG_EXTRAS"): return os.environ["AUTORIG_EXTRAS"]
    return os.path.join(data_root(), python_tag(python))


def activate():
    """Put the extras folder on sys.path (call it at the top of a Blender step that may use an extra). At the end:
    Blender's own packages (its numpy above all, which bpy is built against) stay first."""
    d = folder()
    if os.path.isdir(d) and d not in sys.path: sys.path.append(d)
    return d


def _dist_name(req):
    return re.split(r"[<>=!~\[ ;]", req, 1)[0].lower().replace("-", "_")


def installed(name, where=None):
    """Whether an extra is in the folder: its .dist-info is there (no import, so it works from any Python)."""
    req = EXTRAS[name][0] if name in EXTRAS else name
    d = where or folder()
    want = _dist_name(req)
    # <name>-<version>.dist-info, the name with - and . as _
    names = {os.path.basename(p)[:-len(".dist-info")].rsplit("-", 1)[0].lower().replace("-", "_").replace(".", "_")
             for p in glob.glob(os.path.join(d, "*.dist-info"))}
    return want.replace(".", "_") in names


def have(name):
    """Inside a Blender step: whether the extra imports (activate() first)."""
    activate()
    mod = EXTRAS[name][1] if name in EXTRAS else name
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def need(name, why=""):
    """have(name), or stop the step with a plain message saying how to install it."""
    if have(name): return
    sys.exit("This needs the optional package %s%s. Install it with:  python autorig/cli/run.py extras install %s"
             % (name, " (" + why + ")" if why else "", name))


def status():
    """Every extra, whether it is installed for Blender's Python, and where the folder is."""
    py = blender_python()
    where = folder(py) if py else None
    return {"python": py, "folder": where,
            "extras": [{"name": n, "pip": e[0], "licence": e[2], "use": e[3],
                        "installed": bool(where and installed(n, where))} for n, e in EXTRAS.items()]}


def install(names, python=None):
    """pip install --target the extras folder, with Blender's Python. Returns pip's exit code."""
    py = python or blender_python()
    if not py: raise RuntimeError("Blender's Python was not found: install Blender 5.2 or set AUTORIG_BLENDER")
    if names == ["all"]: names = list(EXTRAS)
    bad = [n for n in names if n not in EXTRAS]
    if bad: raise ValueError("unknown extras: %s (known: %s)" % (", ".join(bad), ", ".join(EXTRAS)))
    where = folder(py)
    os.makedirs(where, exist_ok=True)
    cmd = [py, "-m", "pip", "install", "--upgrade", "--target", where, "--no-warn-script-location"] + \
          [EXTRAS[n][0] for n in names]
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def run_python(args, **kw):
    """Run Blender's Python (no bpy) with the extras on its path: for helpers that use an extra outside Blender."""
    py = blender_python()
    if not py: raise RuntimeError("Blender's Python was not found")
    env = dict(kw.pop("env", None) or os.environ)
    env["PYTHONPATH"] = os.pathsep.join([folder(py), HERE] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run([py] + list(args), env=env, **kw)


def _passes():
    sys.path.insert(0, HERE)
    import export_passes
    return export_passes


def main(argv):
    if argv[:1] == ["install"]:
        if len(argv) < 2: sys.exit("usage: run.py extras install <name ...|all>")
        # programs (core/export_passes.py TOOLS: release binaries into the tools folder) and pip extras
        tools = _passes().TOOLS
        names = argv[1:]
        progs = list(tools) if names == ["all"] else [n for n in names if n in tools]
        pips = ["all"] if names == ["all"] else [n for n in names if n not in tools]
        try:
            for n in progs:
                print("installed %s -> %s" % (n, _passes().install_tool(n)))
            return install(pips) if pips else 0
        except (RuntimeError, ValueError, OSError) as e:
            sys.exit(str(e))
    if argv:
        sys.exit("usage: run.py extras [install <name ...|all>]")
    st = status()
    print("Blender's Python: %s" % (st["python"] or "not found"))
    print("Extras folder:    %s" % (st["folder"] or "-"))
    for e in st["extras"]:
        print("  [%s] %-17s %-10s %s" % ("x" if e["installed"] else " ", e["name"], e["licence"], e["use"]))
    p = _passes()
    print("Tools folder:     %s" % p.tools_dir())
    for t in p.tool_status():
        print("  [%s] %-17s %-10s %s" % ("x" if t["path"] else " ", t["name"], t["licence"], t["use"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

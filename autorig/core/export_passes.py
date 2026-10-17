# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the passes an engine package runs on its models (core/exporter.py).
#
#   unity_fbx      every FBX in a Unity package is checked as Unity reads it (core/fbx_inspect.py) and, when a root
#                  would show a rotation or a scale, rewritten by steps/unity_fbx.py (EdyJ's axis fix) under Blender.
#                  The rewrite is checked again: a file Unity would still import rotated stops the export.
#   gltfpack       optional: a GLB in a Web or Godot package is optimised with gltfpack (meshoptimizer, MIT).
#   gltf-validator optional gate: every GLB in a package goes through the Khronos glTF-Validator (Apache-2.0); a file
#                  with errors stops the export, warnings are reported.
#
# gltfpack and the validator are programs, not Python packages: official release binaries from each project's
# GitHub releases, downloaded into a tools folder beside the extras (no Node, nothing system-wide):
#   python autorig/cli/run.py extras install gltfpack gltf-validator
# A program on PATH with the same name is used too. Without one, the pass is skipped and the package says so.
import io, json, os, shutil, stat, subprocess, sys, tarfile, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)

try:
    import extras
    import fbx_inspect
except ImportError:
    from autorig.core import extras, fbx_inspect


class GateError(ValueError):
    """A package that failed a check it must pass (a GLB with validator errors, an FBX Unity would import rotated)."""


# name: version, licence, what for, executable, {platform: release asset URL}
TOOLS = {
    "gltfpack": {
        "version": "1.3", "licence": "MIT", "exe": "gltfpack",
        "use": "optimises Web and Godot GLBs: merged meshes, quantised and (Web) meshopt-compressed vertices",
        "home": "https://github.com/zeux/meshoptimizer",
        "urls": {"windows": "https://github.com/zeux/meshoptimizer/releases/download/v1.3/gltfpack-windows.zip",
                 "linux": "https://github.com/zeux/meshoptimizer/releases/download/v1.3/gltfpack-ubuntu.zip",
                 "macos": "https://github.com/zeux/meshoptimizer/releases/download/v1.3/gltfpack-macos.zip"},
    },
    "gltf-validator": {
        "version": "2.0.0-dev.3.10", "licence": "Apache-2.0", "exe": "gltf_validator",
        "use": "the Khronos glTF-Validator: a package whose GLB has errors is not written",
        "home": "https://github.com/KhronosGroup/glTF-Validator",
        "urls": {"windows": "https://github.com/KhronosGroup/glTF-Validator/releases/download/2.0.0-dev.3.10/"
                            "gltf_validator-2.0.0-dev.3.10-win64.zip",
                 "linux": "https://github.com/KhronosGroup/glTF-Validator/releases/download/2.0.0-dev.3.10/"
                          "gltf_validator-2.0.0-dev.3.10-linux64.tar.xz",
                 "macos": "https://github.com/KhronosGroup/glTF-Validator/releases/download/2.0.0-dev.3.10/"
                          "gltf_validator-2.0.0-dev.3.10-macos64.tar.xz"},
    },
}


def platform():
    return "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"


def tools_dir():
    """AUTORIG_TOOLS, else <extras data root>/tools: beside the per-Python extras folders."""
    return os.environ.get("AUTORIG_TOOLS") or os.path.join(extras.data_root(), "tools")


def _exe_name(name):
    return TOOLS[name]["exe"] + (".exe" if os.name == "nt" else "")


def find_tool(name):
    """The tool's program: the tools folder first, then PATH. None when neither has it (AUTORIG_NO_TOOLS=1 hides
    both, for tests of the skipped path)."""
    if os.environ.get("AUTORIG_NO_TOOLS"): return None
    p = os.path.join(tools_dir(), name, _exe_name(name))
    if os.path.isfile(p): return p
    return shutil.which(TOOLS[name]["exe"])


def tool_status():
    return [{"name": n, "version": t["version"], "licence": t["licence"], "use": t["use"], "home": t["home"],
             "path": find_tool(n)} for n, t in TOOLS.items()]


def install_tool(name, opener=urllib.request.urlopen):
    """Downloads the project's own release archive for this platform and keeps only its program, in
    <tools>/<name>/. Returns the program's path."""
    t = TOOLS[name]
    url = t["urls"].get(platform())
    if not url: raise RuntimeError("%s has no release build for %s" % (name, platform()))
    print("downloading %s %s from %s" % (name, t["version"], url), flush=True)
    with opener(url) as r:
        data = r.read()
    want = _exe_name(name)
    blob = None
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            hit = next((n for n in z.namelist() if os.path.basename(n) == want), None)
            if hit: blob = z.read(hit)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            hit = next((m for m in tf.getmembers() if m.isfile() and os.path.basename(m.name) == want), None)
            if hit: blob = tf.extractfile(hit).read()
    if blob is None: raise RuntimeError("%s: no %s in %s" % (name, want, url))
    d = os.path.join(tools_dir(), name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, want)
    with open(path, "wb") as fh: fh.write(blob)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(os.path.join(d, "VERSION"), "w") as fh: fh.write("%s %s\n%s\n" % (name, t["version"], url))
    return path


def _run(argv, timeout=600):
    return subprocess.run(argv, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
                          timeout=timeout)


# ---- Unity FBX

def _copy(src, dst):
    if os.path.abspath(src) != os.path.abspath(dst): shutil.copy2(src, dst)


def unity_fbx(src, dst, run_blender=None):
    """Copies src to dst as Unity should import it. Returns the pass record {pass, file, status, detail}; status is
    "ok" (already right, copied), "fixed" (rewritten), or "skipped" (needs a fix but Blender is missing: copied as
    is). Raises GateError when the rewrite still has problems."""
    rec = {"pass": "unity-fbx", "file": os.path.basename(dst)}
    try:
        problems = fbx_inspect.unity_problems(fbx_inspect.summary(src))
    except (ValueError, OSError) as e:                   # ASCII FBX, unreadable: leave it alone
        _copy(src, dst)
        return dict(rec, status="skipped", detail="could not read the FBX (%s); copied as is" % e)
    if not problems:
        _copy(src, dst)
        return dict(rec, status="ok", detail="roots import with rotation (0,0,0) and scale 1")
    if run_blender is None:
        try:
            import blender
        except ImportError:
            from autorig.core import blender
        if not blender.find(required=False):
            _copy(src, dst)
            return dict(rec, status="skipped", detail="Blender not found, copied as is: " + "; ".join(problems))
        run_blender = blender.run
    tmp = dst + ".unity.fbx"
    r = run_blender("unity_fbx.py", os.path.abspath(src), os.path.abspath(tmp))
    if r.returncode != 0 or not os.path.isfile(tmp):
        raise GateError("the Unity FBX rewrite of %s failed:\n%s" % (os.path.basename(src),
                        ((r.stdout or "") + (r.stderr or ""))[-2000:]))
    left = fbx_inspect.unity_problems(fbx_inspect.summary(tmp))
    if left:
        os.remove(tmp)
        raise GateError("%s would still import wrong in Unity: %s" % (os.path.basename(dst), "; ".join(left)))
    os.replace(tmp, dst)
    return dict(rec, status="fixed", detail="was: " + "; ".join(problems))


# ---- glTF

GLTFPACK_ARGS = {
    # meshopt compression (EXT_meshopt_compression): three.js and <model-viewer> decode it
    "web": ["-cc", "-kn", "-km"],
    # Godot's glTF importer reads neither meshopt compression nor quantised positions: merge and reorder only
    "godot": ["-noq", "-kn", "-km"],
}


def optimise_glb(path, target, enabled=True):
    """gltfpack over path, in place. The pass record's status: "done" (with sizes), "skipped" (off, or no gltfpack)."""
    rec = {"pass": "gltfpack", "file": os.path.basename(path)}
    if not enabled: return dict(rec, status="skipped", detail="turned off")
    exe = find_tool("gltfpack")
    if not exe:
        return dict(rec, status="skipped",
                    detail="gltfpack is not installed (python autorig/cli/run.py extras install gltfpack)")
    out = path + ".packed.glb"
    r = _run([exe, "-i", path, "-o", out] + GLTFPACK_ARGS.get(target, ["-kn", "-km"]))
    if r.returncode != 0 or not os.path.isfile(out):
        if os.path.exists(out): os.remove(out)
        raise GateError("gltfpack failed on %s:\n%s" % (os.path.basename(path), (r.stderr or r.stdout)[-2000:]))
    before, after = os.path.getsize(path), os.path.getsize(out)
    os.replace(out, path)
    return dict(rec, status="done", detail="%d KB -> %d KB (%s)" % (before // 1024, after // 1024,
                                                                   " ".join(GLTFPACK_ARGS.get(target, []))))


def validate_glb(path, enabled=True):
    """The Khronos glTF-Validator over path. status "passed", "failed" (errors; the caller stops the export) or
    "skipped". The record carries the counts and the first messages."""
    rec = {"pass": "gltf-validator", "file": os.path.basename(path)}
    if not enabled: return dict(rec, status="skipped", detail="turned off")
    exe = find_tool("gltf-validator")
    if not exe:
        return dict(rec, status="skipped",
                    detail="the glTF-Validator is not installed (python autorig/cli/run.py extras install "
                           "gltf-validator)")
    r = _run([exe, "-o", path], timeout=300)                    # -o: the JSON report to stdout
    text = r.stdout.strip()
    try:
        report = json.loads(text[text.index("{"):])
    except ValueError:
        raise GateError("the glTF-Validator gave no report for %s (exit %s):\n%s"
                        % (os.path.basename(path), r.returncode, (r.stderr or r.stdout)[-2000:]))
    return validation_record(rec, report)


def validation_record(rec, report):
    issues = report.get("issues", {})
    errors, warnings = issues.get("numErrors", 0), issues.get("numWarnings", 0)
    msgs = ["%s %s: %s" % ({0: "error", 1: "warning", 2: "info", 3: "hint"}.get(m.get("severity"), "?"),
                           m.get("code", ""), m.get("message", "")) + (" (%s)" % m["pointer"] if m.get("pointer") else "")
            for m in issues.get("messages", []) if m.get("severity", 3) <= 1][:12]
    return dict(rec, status="failed" if errors else "passed", errors=errors, warnings=warnings, messages=msgs,
                detail="%d errors, %d warnings" % (errors, warnings))

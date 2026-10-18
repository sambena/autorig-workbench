# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a Mixamo download made into an engine's game file.
#
#   python autorig/steps/mixamo_import.py --rig <download>.fbx --source Ice.glb --name IceAdult --out <folder>
#   python autorig/cli/run.py mixamo-import <the same arguments>
#
# After mixamo_upload.py's file has been rigged on mixamo.com and downloaded (FBX Binary, Original Pose; a browser may
# leave it as a .tmp until its save prompt is answered, which is fine: the bytes are checked here, not the name):
#   1. fbx_maps.py gives it the source's full-size colour and normal maps (the upload carried a small JPEG only);
#   2. unity_fbx.py --relative writes it so every root imports at rotation 0 and scale 1, run inside the folder that
#      holds <Name>.fbm/ so the texture paths come out as <Name>.fbm/<Name>_basecolor.png. Run anywhere else, the
#      relative path pointed back at the work folder and Unity found no texture (2026-10-17);
#   3. <out>/<Name>.fbx and <out>/<Name>.fbm/ are written, replacing what was there.
# In Unity, import <Name>.fbm/ before <Name>.fbx (or reimport the FBX after), or its material is made before its
# textures exist. Prints MIXAMO_IMPORT {json}.
import argparse, json, os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import blender  # noqa: E402

FBX_HEAD = b"Kaydara FBX Binary"


def is_fbx_binary(path):
    """True for a whole binary FBX: its header, and the fixed footer every exporter ends the file with."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(FBX_HEAD))
            fh.seek(-16, os.SEEK_END)
            tail = fh.read(16)
    except OSError:
        return False
    return head == FBX_HEAD and tail == bytes.fromhex("f85a8c6adef5d97eece90ce3758f290b")


def texture_paths(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return sorted({m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e]{3,}\.(?:png|jpg|jpeg)", data)})


def run(rig, source, name, out):
    if not is_fbx_binary(rig):
        return {"error": "not a whole binary FBX (still downloading, or not the FBX Binary format): " + rig}
    work = tempfile.mkdtemp(prefix="mixamo-import-")
    try:
        r = blender.run("fbx_maps.py", "--fbx", rig, "--source", source, "--name", name, "--out", work)
        made = os.path.join(work, name + ".fbx")
        log = r.stdout + r.stderr
        if not os.path.isfile(made):
            if "no base colour image" not in log:
                return {"error": "fbx-maps wrote nothing", "log": log[-600:]}
            shutil.copyfile(rig, made)   # an untextured source: the rig as it came, axes still fixed below
        unity = os.path.join(work, name + "_unity.fbx")
        r = blender.run("unity_fbx.py", made, unity, "--relative")
        if not os.path.isfile(unity):
            return {"error": "unity_fbx wrote nothing", "log": (r.stdout + r.stderr)[-600:]}
        os.makedirs(out, exist_ok=True)
        shutil.copyfile(unity, os.path.join(out, name + ".fbx"))
        fbm = os.path.join(work, name + ".fbm")
        target = os.path.join(out, name + ".fbm")
        if os.path.isdir(fbm):
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(fbm, target)
        paths = texture_paths(os.path.join(out, name + ".fbx"))
        relative = [p for p in paths if p.replace("\\", "/").startswith(name + ".fbm/")]
        maps = sorted(os.listdir(target)) if os.path.isdir(target) else []
        return {"out": os.path.join(out, name + ".fbx"), "maps": maps, "relative_paths": relative,
                "ok": bool(relative) or not maps}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Autorig Workbench: a Mixamo download made into a game file.")
    p.add_argument("--rig", required=True, help="the FBX downloaded from Mixamo (FBX Binary, Original Pose)")
    p.add_argument("--source", required=True, help="the generated model the upload was made from (its full maps)")
    p.add_argument("--name", required=True, help="the engine's resource name")
    p.add_argument("--out", required=True, help="folder for <Name>.fbx and <Name>.fbm/")
    a = p.parse_args(argv)
    result = run(os.path.abspath(a.rig), os.path.abspath(a.source), a.name, os.path.abspath(a.out))
    print("MIXAMO_IMPORT " + json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

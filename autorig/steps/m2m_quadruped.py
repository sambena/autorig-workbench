# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a four-legged animal on Mesh2Motion's horse, fox or bird skeleton, playing its clips.
#
#   blender -b --python autorig/steps/m2m_quadruped.py -- --in Cow.glb --rig horse-animations.glb --name CowHolstein
#           --height 1.6 --out <folder> [--triangles 15000] [--clips Idle=idle,Walk=walk,Trot=trot,Run=gallop]
#           [--facing auto] [--swing 1] [--tail-swing 0.4] [--voxel 0]
#   python autorig/cli/run.py m2m-quadruped <the same arguments>
#
# The first form of steps/m2m_rig.py, kept for the commands that already call it: the --rig file names the template
# (horse-animations.glb is the horse) and its folder is the Mesh2Motion animations folder. Everything else, the
# shape fit, legs from the backbone, the leg confine and the leg-direction bake included, is m2m_rig's: see there.
# Prints M2M_QUADRUPED {json} (and M2M_RIG {json} before it).
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]


def translate(argv):
    """m2m_quadruped's arguments as m2m_rig's."""
    out, rig = [], None
    it = iter(argv)
    for x in it:
        if x == "--rig":
            rig = next(it)
        else:
            out.append(x)
    if rig is None: raise SystemExit("m2m_quadruped: --rig <kind>-animations.glb is needed")
    kind = os.path.basename(rig).split("-")[0].lower()
    anims = os.path.dirname(os.path.abspath(rig))
    root = os.path.dirname(anims)
    if os.path.basename(root) == "static": root = os.path.dirname(root)
    if "--clips" not in out: out += ["--clips", "Idle=idle,Walk=walk,Trot=trot,Run=gallop" if kind == "horse" else "idle,walk"]
    return out + ["--template", kind, "--m2m", root]


if __name__ == "__main__":
    import json
    import m2m_rig
    a = m2m_rig.args(translate(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
    try:
        code = m2m_rig.run(a)
    except SystemExit as e:
        print("M2M_QUADRUPED " + json.dumps({"error": str(e.code)})); sys.stdout.flush(); os._exit(2)
    print("M2M_QUADRUPED " + json.dumps({"out": os.path.join(a.out, a.name + ".fbx"),
                                          "blend": os.path.join(a.out, a.name + ".blend")}))
    sys.stdout.flush()
    os._exit(code)

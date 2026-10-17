# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: checks a rigged, animated GLB the way an audience sees it (no rendering).
#
#   python autorig/cli/run.py clip-check <model | file.glb> [--every N] [--loop walk] [--report quality.json]
#   blender -b --python autorig/steps/clip_check.py -- <file.glb> [--every N] [--loop walk] [--report quality.json]
#
# A model name reads <rig folder>/preview.glb, the viewer's file. Three checks, each one a problem that reached a
# viewer unnoticed once:
#   moves   every animation has channels that change (a re-retarget once saved a still pose)
#   seams   vertices that sit together at rest (a glTF splits them on texture seams) stay together while it plays:
#           otherwise the model cracks open where the seams are
#   hands   how often a hand is inside the body (a dance learned on dancers in tight clothes puts hands into a robe)
# Evaluates each take separately across all meshes; samples every frame by default.
# Reports compression, triangle collapse, floor penetration, foot speed and foot skate as well (--max-skate S fails a
# clip whose skate score is above S; --speed walk=0.7 sets an in-place clip's ground speed, by default the clips
# manifest's: core/foot_contact.foot_skate).
# Exits 1 for still clips, cracks, stretched edges, or a requested loop mismatch.
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))


def main(argv):
    from clip_quality import check
    return check(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]))

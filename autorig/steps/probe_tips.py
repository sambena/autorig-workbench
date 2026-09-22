# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: lists where a boneless model's limbs end (geodesic extrema), in the 0..1 coordinates a spec uses.
#   blender -b --python autorig/steps/probe_tips.py -- a,b,c
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import rerig, geo
from spec_store import SPECS
keys = sys.argv[sys.argv.index("--") + 1].split(",")
for key in keys:
    spec = dict(SPECS[key])
    mesh, joints = rerig.load(rerig.find_fbx(key, spec))
    spec_n = dict(spec, kind="build")
    rerig.normalise(mesh, {}, spec_n)
    s = geo.Surface(mesh)
    print("TIPS", key, "size", [round(v, 3) for v in s.size])
    for i, (v, prom) in enumerate(s.tips(most=18, least=0.12)):
        print("TIPS   %2d  %s  prominence %.2f" % (i, s.norm(s.co[v]), prom))
print("TIPS_DONE")

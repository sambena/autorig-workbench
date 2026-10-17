# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: print which GPU Blender draws and computes with (run.py gpu; core/gpu_setup.py).
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import gpu_setup  # noqa: E402

r = gpu_setup.report()
print("GPU_REPORT " + json.dumps(r))
print("OpenGL (Workbench, EEVEE): %s" % r["opengl"])
print("Cycles (bakes):            %s" % r["cycles"])
for d in r["devices"]:
    print("  " + d)

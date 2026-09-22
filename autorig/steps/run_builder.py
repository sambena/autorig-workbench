# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: runs a model's own rig builder (spec kind="custom", `builder`: a script beside the model).
#
#   blender -b --python autorig/steps/run_builder.py -- <builder.py> [its own arguments]
#
# A custom builder is a hand-written rig for one model that the spec kinds cannot express yet. It lives with the
# model's data, not in the tool, and imports the tool's modules (rerig, layout, spec_store, geo, skeletons) by name:
# this puts steps/ and core/ on the import path, then runs the builder as __main__ with its own arguments after "--".
import os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    sys.exit("usage: blender -b --python run_builder.py -- <builder.py> [args]")
builder = os.path.abspath(argv[0])
sys.argv = [builder, "--"] + argv[1:]
print("BUILDER " + builder)
runpy.run_path(builder, run_name="__main__")

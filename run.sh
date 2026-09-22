#!/bin/sh
# Autorig Workbench: starts the GUI and opens it in the browser.  ./run.sh [--models DIR] [--work DIR] [--port N]
# Needs Python 3.9+ and Blender (found automatically, or set AUTORIG_BLENDER).
cd "$(dirname "$0")" || exit 1
exec python3 -m autorig "$@"

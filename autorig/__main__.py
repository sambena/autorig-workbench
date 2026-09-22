# SPDX-License-Identifier: GPL-3.0-or-later
# python -m autorig  starts the GUI (see autorig/gui/server.py for the options).
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui import server

# "gui" is accepted as a first word too, to match `python -m autorig.cli.run gui`.
if len(sys.argv) > 1 and sys.argv[1] == "gui": del sys.argv[1]
server.main()

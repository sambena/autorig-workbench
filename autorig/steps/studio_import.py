# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy a Studio output into a new model, retaining its local dependencies."""
import argparse
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from studio_assets import promote_output


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('model')
    ap.add_argument('artifact', help='Path relative to the source model, starting with _studio/')
    ap.add_argument('--name', required=True, help='New model name; existing models are never replaced')
    ap.add_argument('--group', default='')
    a = ap.parse_args(argv)
    print('STUDIO_IMPORT ' + json.dumps(promote_output(a.model, a.artifact, a.name, a.group)))
    return 0


if __name__ == '__main__': sys.exit(main())

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: packages a rigged model for engine export (Unreal, Unity, Godot, Web).
#
#   python autorig/steps/export.py <model> [--target unreal|unity|godot|web|all] [--out <dir>]
#
# Writes engine package folder and archive to <work>/export/<model>_<target>.zip.

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(PKG, "core")]

import exporter
import layout


def main(argv=None):
    parser = argparse.ArgumentParser(description="Package a rigged model for engine export.")
    parser.add_argument("model", help="Name of the model to package.")
    parser.add_argument("--target", default="all", choices=["unreal", "unity", "godot", "web", "all"],
                        help="Target engine or platform preset (default: all).")
    parser.add_argument("--out", default=None, help="Custom output directory.")
    args = parser.parse_args(argv)

    try:
        res = exporter.create_export_package(args.model, target=args.target, out_dir=args.out)
        print(f"EXPORT {args.model} {args.target} -> {res['zip_path']} ({res['zip_size']} bytes)")
        if "presets" in res:
            for t, p in res["presets"].items():
                print(f"  PRESET {t:8s} {len(p['files'])} files -> {p['zip_path']}")
        else:
            for f in res.get("files", []):
                print(f"  FILE   {f}")
        print("EXPORT_DONE")
        return 0
    except Exception as e:
        sys.stderr.write(f"EXPORT_ERROR: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

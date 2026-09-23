# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: External Motion Capture / BVH & FBX Clip Retargeter CLI step.
#
#   python autorig/steps/retarget.py <model> <mocap_file> [--clip-name <name>]
#                                    [--no-root-motion] [--no-scale] [--export-glb] [--dry-run]
#
# Maps motion capture or standard animation clip files (BVH, FBX) directly onto
# an autorigged character, preserving rest poses, bone twists, and proportions.

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import retargeter


def main(argv=None):
    parser = argparse.ArgumentParser(description="Autorig Workbench: mocap clip retargeter.")
    parser.add_argument("model", help="Target rigged model name (e.g. 'biped', 'canine').")
    parser.add_argument("mocap_file", help="Path to source mocap or animation file (.bvh or .fbx).")
    parser.add_argument("--clip-name", default=None, help="Name for the generated animation action/clip.")
    parser.add_argument("--no-root-motion", action="store_true", help="Disable root bone translation transfer.")
    parser.add_argument("--no-scale", action="store_true", help="Do not scale root displacement by character height.")
    parser.add_argument("--export-glb", action="store_true", help="Export rigged model + animated clip to .glb preview.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and display mapping plan without running Blender.")
    args = parser.parse_args(argv)

    model = args.model.strip()
    mocap_file = os.path.abspath(args.mocap_file)

    print(f"RETARGET_PLAN: Analyzing {mocap_file} for model '{model}'...")
    plan = retargeter.plan_retarget(
        model_name=model,
        mocap_file=mocap_file,
        clip_name=args.clip_name,
        root_motion=not args.no_root_motion,
    )

    print("\n" + retargeter.format_retarget_summary(plan) + "\n")

    if args.dry_run:
        print("RETARGET_DRY_RUN: Dry run complete. No modifications made.")
        return 0

    print(f"RETARGET_EXEC: Running Blender retargeting worker on '{model}'...")
    res = retargeter.retarget_clip(
        model_name=model,
        mocap_file=mocap_file,
        clip_name=plan["clip_name"],
        root_motion=plan["root_motion"],
        scale_proportions=not args.no_scale,
        export_glb=args.export_glb,
    )

    print(f"RETARGET_DONE: Baked action '{res.get('clip_name')}' ({res.get('frames')} frames @ {res.get('fps')} fps, {res.get('duration')}s)")
    if res.get("export_glb"):
        print(f"  EXPORT_GLB: {res.get('export_glb')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

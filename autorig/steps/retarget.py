# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: External Motion Capture / BVH & FBX Clip Retargeter CLI step.
#
#   python autorig/steps/retarget.py <models> <mocap_file_or_dir> [--clip-name <name>]
#                                    [--no-root-motion] [--no-scale] [--no-solve-offsets]
#                                    [--fps <N>] [--frame-range <start:end>]
#                                    [--export-glb] [--preview] [--dry-run]
#
# Maps motion capture or standard animation clip files (BVH, FBX) directly onto
# autorigged characters, preserving rest poses, bone twists, orientations, and proportions.

import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import retargeter


def parse_frame_range(val):
    if not val:
        return None
    parts = val.split(":")
    if len(parts) == 2:
        return [int(parts[0]), int(parts[1])]
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Autorig Workbench: mocap clip retargeter.")
    parser.add_argument("model", help="Target model name(s), comma-separated (e.g. 'biped', 'player_blue,armoured_guard').")
    parser.add_argument("mocap_file", help="Path to source mocap or animation file (.bvh or .fbx), or folder of clips.")
    parser.add_argument("--clip-name", default=None, help="Name for the generated animation action/clip.")
    parser.add_argument("--action", "-a", default=None, help="Name of specific source animation action/take to retarget.")
    parser.add_argument("--list-actions", action="store_true", help="List all actions embedded in the mocap file and exit.")
    parser.add_argument("--all-actions", action="store_true", help="Retarget all actions embedded in the mocap file onto the model.")
    parser.add_argument("--no-root-motion", action="store_true", help="Disable root bone translation transfer.")
    parser.add_argument("--no-scale", action="store_true", help="Do not scale root displacement by character height.")
    parser.add_argument("--no-solve-offsets", action="store_true", help="Do not solve orientation offsets between rest poses.")
    parser.add_argument("--fps", type=int, default=None, help="Conform motion to specific frame rate (e.g. 24, 30, 60).")
    parser.add_argument("--frame-range", default=None, help="Trim frame range to bake, format 'START:END' (e.g. '1:120').")
    parser.add_argument("--export-glb", action="store_true", help="Export rigged model + animated clip to .glb preview.")
    parser.add_argument("--preview", action="store_true", help="Update preview.glb with the new clip for the 3D viewer.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and display mapping plan without running Blender.")
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.model.split(",") if m.strip()]
    mocap_target = os.path.abspath(args.mocap_file)

    if os.path.isdir(mocap_target):
        files = sorted(glob.glob(os.path.join(mocap_target, "*.bvh")) +
                       glob.glob(os.path.join(mocap_target, "*.fbx")))
        if not files:
            print(f"RETARGET_ERROR: No .bvh or .fbx animation files found in '{mocap_target}'", file=sys.stderr)
            return 1
    else:
        if not os.path.isfile(mocap_target):
            print(f"RETARGET_ERROR: Mocap file not found: '{mocap_target}'", file=sys.stderr)
            return 1
        files = [mocap_target]

    if args.list_actions:
        meta = retargeter.inspect_mocap_file(files[0])
        actions = meta.get("actions", [])
        print(f"\nACTIONS IN {meta['file']} ({meta['format']}): {len(actions)} total")
        print("-" * 68)
        print(f"{'#':<4} {'Action Name':<42} {'Frames':<8} {'Duration':<10}")
        print("-" * 68)
        for idx, act in enumerate(actions):
            dur = f"{round(act['frames'] / (meta['fps'] or 30), 2)}s"
            is_act = " *" if act["name"] == meta.get("active_action") else ""
            print(f"{idx + 1:<4} {act['name']:<42} {act['frames']:<8} {dur:<10}{is_act}")
        print("-" * 68)
        if meta.get("active_action"):
            print("(* indicates default / active take)")
        return 0

    frame_range = parse_frame_range(args.frame_range)
    solve_offsets = not args.no_solve_offsets

    if args.all_actions:
        for m in models:
            for f in files:
                meta = retargeter.inspect_mocap_file(f)
                actions = meta.get("actions", [])
                if not actions:
                    print(f"RETARGET_ERROR: No actions found in {f}", file=sys.stderr)
                    continue
                print(f"RETARGET_ALL_ACTIONS: Baking {len(actions)} actions from {os.path.basename(f)} onto '{m}'...")
                for idx, act in enumerate(actions):
                    cname = retargeter.clean_action_name(act["name"])
                    print(f"  [{idx + 1}/{len(actions)}] Action '{act['name']}' -> '{cname}'...")
                    retargeter.retarget_clip(
                        model_name=m,
                        mocap_file=f,
                        source_action=act["name"],
                        clip_name=cname,
                        root_motion=not args.no_root_motion,
                        scale_proportions=not args.no_scale,
                        solve_offsets=solve_offsets,
                        fps=args.fps,
                        frame_range=frame_range,
                        export_glb=args.export_glb,
                        preview=args.preview and (idx == len(actions) - 1),
                    )
        print("RETARGET_ALL_ACTIONS: Complete!")
        return 0

    # If single model and single file, print full summary
    if len(models) == 1 and len(files) == 1:
        model = models[0]
        f = files[0]
        print(f"RETARGET_PLAN: Analyzing {f} for model '{model}'...")
        plan = retargeter.plan_retarget(
            model_name=model,
            mocap_file=f,
            clip_name=args.clip_name,
            source_action=args.action,
            root_motion=not args.no_root_motion,
            solve_offsets=solve_offsets,
        )

        print("\n" + retargeter.format_retarget_summary(plan) + "\n")

        if args.dry_run:
            print("RETARGET_DRY_RUN: Dry run complete. No modifications made.")
            return 0

        print(f"RETARGET_EXEC: Running Blender retargeting worker on '{model}'...")
        res = retargeter.retarget_clip(
            model_name=model,
            mocap_file=f,
            clip_name=plan["clip_name"],
            source_action=plan.get("source_action"),
            root_motion=plan["root_motion"],
            scale_proportions=not args.no_scale,
            solve_offsets=solve_offsets,
            fps=args.fps,
            frame_range=frame_range,
            export_glb=args.export_glb,
            preview=args.preview,
        )

        print(f"RETARGET_DONE: Baked action '{res.get('clip_name')}' ({res.get('frames')} frames @ {res.get('fps')} fps, {res.get('duration')}s)")
        if res.get("export_glb"):
            print(f"  EXPORT_GLB: {res.get('export_glb')}")
        return 0

    # Multi-model or multi-file batch execution
    print(f"RETARGET_BATCH: Processing {len(models)} model(s) x {len(files)} clip(s)...")
    if args.dry_run:
        for m in models:
            for f in files:
                try:
                    plan = retargeter.plan_retarget(m, f, clip_name=args.clip_name,
                                                    root_motion=not args.no_root_motion,
                                                    solve_offsets=solve_offsets)
                    print(f"  PLAN OK: {m} <- {os.path.basename(f)} (clip: {plan['clip_name']}, {plan['mapping_result']['mapped_count']} bones)")
                except Exception as e:
                    print(f"  PLAN FAIL: {m} <- {os.path.basename(f)}: {e}")
        print("RETARGET_DRY_RUN: Dry run complete.")
        return 0

    results = retargeter.retarget_batch(
        models=models,
        mocap_files=files,
        clip_names=[args.clip_name] if args.clip_name else None,
        root_motion=not args.no_root_motion,
        scale_proportions=not args.no_scale,
        solve_offsets=solve_offsets,
        fps=args.fps,
        frame_range=frame_range,
        export_glb=args.export_glb,
        preview=args.preview,
    )

    successes = sum(1 for r in results if r["status"] == "OK")
    print(f"\nRETARGET_BATCH_DONE: {successes}/{len(results)} clips successfully baked.")
    return 0 if successes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

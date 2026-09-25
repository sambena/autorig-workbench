# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Batch Rigger CLI step.
#
#   python autorig/steps/batch.py [models|all] [--group <group>] [--filter <pattern>]
#                                 [--steps survey,rig,trim,audit,clips,publish,preview]
#                                 [--auto-tune] [--export unreal|unity|godot|web|all]
#                                 [--stop-on-error]
#
# Runs pipeline across collections with progress logs and formatted status summary.

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import batch_runner
import layout


def main(argv=None):
    parser = argparse.ArgumentParser(description="Autorig Workbench: batch pipeline rigger.")
    parser.add_argument("models", nargs="*", default=["all"], help="Model names, comma-separated lists, 'all', or 'riggable'.")
    parser.add_argument("--group", default=None, help="Filter to models in a specific group.")
    parser.add_argument("--filter", default=None, help="Wildcard pattern filter (e.g. '*biped*').")
    parser.add_argument("--riggable-only", action="store_true", help="Only process models that have a rig spec (rig.json 'rig').")
    parser.add_argument("--steps", default="rig,trim,audit",
                        help="Comma-separated pipeline steps to run (default: rig,trim,audit).")
    parser.add_argument("--auto-tune", action="store_true", help="Run closed-loop auto-tune optimizer if audit fails.")
    parser.add_argument("--export", choices=["unreal", "unity", "godot", "web", "all"], default=None,
                        help="Package engine exports for successful models.")
    parser.add_argument("-j", "--jobs", "--workers", type=int, default=1, dest="workers",
                        help="Number of concurrent models to run in parallel (default: 1).")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop batch on first error.")
    args = parser.parse_args(argv)

    step_list = [s.strip() for s in args.steps.split(",") if s.strip()]
    models_arg = [m for sub in args.models for m in sub.split(",") if m.strip()]

    print(f"BATCH_START models={models_arg} steps={step_list} auto_tune={args.auto_tune} export={args.export} workers={args.workers}")
    summary = batch_runner.run_batch(
        models=models_arg,
        group=args.group,
        pattern=args.filter,
        steps=step_list,
        auto_tune=args.auto_tune,
        export_target=args.export,
        continue_on_error=not args.stop_on_error,
        riggable_only=args.riggable_only,
        workers=args.workers,
    )

    print("\n" + batch_runner.format_batch_table(summary) + "\n")
    print("BATCH_DONE")
    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

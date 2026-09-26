# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Watch Folder Ingestion Daemon CLI step.
#
#   python autorig/steps/watch.py <incoming_dir> [--group <group>] [--interval 2.0]
#                                 [--once] [--no-rig] [--auto-tune] [--export <target>]

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]

import watch_daemon


def on_model_processed(res):
    print(f"WATCH_INGEST file={res['file']} -> model={res['model']}")
    if res.get("suggested"):
        print(f"  SUGGESTED: rig.json written ({res['suggested']})")
    if res.get("spec_error"):
        print(f"  SPEC: {res['spec_error']}")
    if not res.get("archived"):
        print("  NOTE: could not move it into _processed; it will not be taken again until it changes")
    if res.get("doctor"):
        d = res["doctor"]
        print(f"  DOCTOR: {d.get('grade')} ({d.get('health_score')}/100) verts={d.get('verts')}")
    if res.get("pipeline"):
        p = res["pipeline"]
        print(f"  PIPELINE: {p.get('status')} grade={p.get('grade')} steps={','.join(p.get('steps', []))} in {p.get('duration')}s")
        if p.get("error"):
            print(f"  ERROR: {p['error']}")
        if p.get("export"):
            print(f"  EXPORT: {p.get('export')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Watch folder ingestion daemon.")
    parser.add_argument("folder", help="Directory to monitor for incoming 3D assets.")
    parser.add_argument("--group", default=None, help="Target model group (default: root).")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (default: 2.0).")
    parser.add_argument("--once", action="store_true", help="Process existing files once and exit immediately.")
    parser.add_argument("--no-rig", action="store_true", help="Import only; do not auto-rig imported assets.")
    parser.add_argument("--auto-tune", action="store_true", help="Run auto-tuning on imported models.")
    parser.add_argument("--export", choices=["unreal", "unity", "godot", "web", "all"], default=None,
                        help="Auto-export engine package after rigging.")
    args = parser.parse_args(argv)

    incoming_dir = os.path.abspath(args.folder)
    print(f"WATCH_START folder={incoming_dir} interval={args.interval} once={args.once}")
    results = watch_daemon.run_watch_loop(
        incoming_dir=incoming_dir,
        interval=args.interval,
        once=args.once,
        target_group=args.group,
        auto_rig=not args.no_rig,
        auto_tune=args.auto_tune,
        export_target=args.export,
        callback=on_model_processed,
    )

    print(f"WATCH_DONE processed={len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: set up AI generation on this machine (docs/GENERATE.md). The HTTP API only reads these settings,
# so the hosted copy's visitors can't turn generation on, raise a cap or read a key; run this on the server itself.
#
#   python scripts/generate_settings.py                      show the settings (keys only as saved / not set)
#   python scripts/generate_settings.py --key tripo          ask for Tripo's key without echoing it, and save it
#   python scripts/generate_settings.py --on | --off         turn generation on or off
#   python scripts/generate_settings.py --cap tripo_daily=40 --cap tripo_monthly=200
import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autorig", "core"))
import generators  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", choices=sorted(generators.PROVIDERS), help="save this provider's API key (asked, not echoed)")
    ap.add_argument("--clear-key", choices=sorted(generators.PROVIDERS))
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--cap", action="append", default=[], metavar="NAME=CREDITS", help="one of " + ", ".join(generators.CAPS))
    a = ap.parse_args(argv)
    changes = {}
    if a.on and a.off: ap.error("--on or --off")
    if a.on: changes["enabled"] = True
    if a.off: changes["enabled"] = False
    if a.key:
        k = getpass.getpass("%s API key: " % generators.PROVIDERS[a.key].LABEL).strip()
        if not k: ap.error("no key given")
        changes[a.key + "_key"] = k
    if a.clear_key: changes[a.clear_key + "_key"] = ""
    for c in a.cap:
        name, _, v = c.partition("=")
        if name not in generators.CAPS: ap.error("a cap is one of " + ", ".join(generators.CAPS))
        try:
            changes[name] = max(0.0, float(v))
        except ValueError:
            ap.error("%s needs a number" % name)
    out = generators.save_settings(changes) if changes else generators.public_settings()
    print(json.dumps(out, indent=2))
    print("settings file:", generators.settings_file())


if __name__ == "__main__":
    main()

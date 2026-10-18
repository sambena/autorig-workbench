# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what the person running the server lets the page reach. Plain Python.
#
# The page can switch the collection it works on (the models root) and pick files that are already on the server.
# Which folders those may be is the server's call, not the page's: a hosted copy (autorig.cougarcave.dev) must not
# let anyone with the link point it at any folder on the machine. So the folders are listed here, in a JSON file next
# to the repo (.autorig_server.json, or AUTORIG_CONFIG), which the page cannot write unless allow_any_folder is on:
#
#   {
#     "collections": [ {"name": "asset-packs", "path": "/home/cosmo/src/asset-packs"},
#                      {"name": "Samples",     "path": "samples"} ],
#     "browse":      [ "/home/cosmo/incoming" ],
#     "allow_any_folder": false
#   }
#
#   collections       models roots the page may switch to. The root the server was started with (--models) is
#                     always one, and so are its groups (one-pack views of it).
#   browse            more folders whose files the page may list and copy in as models. The collections are always
#                     browsable.
#   allow_any_folder  true lets the page add a collection or browse anywhere this machine's user can read: for a
#                     copy only you use (your own computer, 127.0.0.1). Also on with --allow-any-folder or
#                     AUTORIG_ALLOW_ANY_FOLDER=1. Off by default.
#
# A relative path is taken from the repo's parent folder (where the collections usually sit beside the tool).
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def path():
    return os.environ.get("AUTORIG_CONFIG") or os.path.join(REPO, ".autorig_server.json")


def _abs(p):
    p = os.path.expanduser(str(p))
    return os.path.realpath(p if os.path.isabs(p) else os.path.join(os.path.dirname(REPO), p))


def load():
    """The config with its paths made absolute. A missing or broken file is an empty config."""
    data = {}
    try:
        with open(path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    cols = []
    for c in data.get("collections") or []:
        if isinstance(c, str): c = {"path": c}
        if isinstance(c, dict) and c.get("path"):
            p = _abs(c["path"])
            cols.append({"name": str(c.get("name") or os.path.basename(p.rstrip("/\\")) or p), "path": p})
    browse = [_abs(b) for b in data.get("browse") or [] if isinstance(b, str) and b.strip()]
    any_folder = bool(data.get("allow_any_folder")) or os.environ.get("AUTORIG_ALLOW_ANY_FOLDER") == "1"
    return {"collections": cols, "browse": browse, "allow_any_folder": any_folder}


def add_collection(p, name=None):
    """Adds a collection to the file (the page does this only when allow_any_folder is on)."""
    try:
        with open(path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    cols = [c for c in data.get("collections") or [] if not (
        (c.get("path") if isinstance(c, dict) else c) and _abs(c.get("path") if isinstance(c, dict) else c) == _abs(p))]
    cols.append({"name": name or os.path.basename(os.path.normpath(p)), "path": p})
    data["collections"] = cols
    with open(path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def inside(p, roots):
    """True when path p is one of the roots or under one."""
    p = os.path.normcase(os.path.realpath(p))
    for r in roots:
        r = os.path.normcase(os.path.realpath(r))
        if p == r or p.startswith(r.rstrip(os.sep) + os.sep):
            return True
    return False

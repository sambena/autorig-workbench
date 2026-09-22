# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the results viewer's side of the local server (gui/server.py routes to it).
#
#   GET /viewer.html?model=<name>&t=<token>   the 3D viewer page (token required, as for the main page)
#   GET /viewer.js, /vendor/...               the viewer's code and the vendored three.js: static files from gui/,
#                                             no user data, so no token (a module import cannot carry a header);
#                                             the Host check still applies
#   GET /api/previews                         every rigged model, with its preview.glb (when it has one) and the
#                                             preview.json the preview step wrote beside it
#
# preview.glb itself is served through the ordinary /files/models/... route, under the token.
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
# The viewer's page: three.js loads GLB textures through blob: URLs.
CSP = ("default-src 'self'; img-src 'self' data: blob:; connect-src 'self' data: blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")


def static_file(path):
    """(bytes, content type) for a viewer file under gui/ (viewer.js or vendor/...), or None."""
    rel = path.lstrip("/")
    if not (rel == "viewer.js" or rel.startswith("vendor/")):
        return None
    parts = [p for p in rel.split("/") if p]
    if any(p in ("..", ".") or p.startswith(".") or ":" in p or "\\" in p for p in parts):
        return None
    p = os.path.realpath(os.path.join(HERE, *parts))
    if os.path.commonpath([os.path.normcase(p), os.path.normcase(os.path.realpath(HERE))]) != os.path.normcase(os.path.realpath(HERE)):
        return None
    ext = os.path.splitext(p)[1].lower()
    if ext not in STATIC_TYPES or not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        return fh.read(), STATIC_TYPES[ext]


def page(token):
    with open(os.path.join(HERE, "viewer.html"), encoding="utf-8") as fh:
        return fh.read().replace("__AUTORIG_TOKEN__", token)


def _mtime(p):
    return os.path.getmtime(p) if p and os.path.exists(p) else 0


def previews(layout, quiet):
    """Every rigged model (a .blend or FBX in its rig folder), in the models list's order, with its preview."""
    out = []
    for group, name in layout.all_models():
        rd = quiet(layout.rigged_dir, name)
        blend, fbx = (os.path.join(rd, name + ext) for ext in (".blend", ".fbx"))
        if not (os.path.exists(blend) or os.path.exists(fbx)):
            continue
        glb = os.path.join(rd, "preview.glb")
        clips_blend = os.path.join(layout.pack_dir(name), "clips", name + "_clips.blend")
        item = {"name": name, "group": group, "rig_folder": os.path.basename(rd), "preview": None, "info": None,
                "stale": False}
        if os.path.exists(glb):
            rel = os.path.relpath(glb, layout.ROOT).replace("\\", "/")
            item["preview"] = "/files/models/" + rel
            item["stale"] = _mtime(glb) + 1 < max(_mtime(blend), _mtime(fbx), _mtime(clips_blend))
            info = os.path.join(rd, "preview.json")
            if os.path.exists(info):
                try:
                    with open(info, encoding="utf-8") as fh: item["info"] = json.load(fh)
                except Exception as e:
                    item["info"] = {"error": "preview.json cannot be read: %r" % e}
        out.append(item)
    return out


def has_preview(layout, name):
    return os.path.exists(os.path.join(layout.rigged_dir(name), "preview.glb"))

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the results viewer's side of the local server (gui/server.py routes to it).
#
#   GET /viewer.html?model=<name>&t=<token>   the 3D viewer page (token required, as for the main page)
#   GET /viewer.js, /viewer_logic.js,
#       /vendor/...                           the viewer's code and the vendored three.js: static files from gui/,
#                                             no user data, so no token (a module import cannot carry a header);
#                                             the Host check still applies
#   GET /api/previews                         every rigged model, with its preview.glb (when it has one), the
#                                             preview.json the preview step wrote beside it, and a digest of its
#                                             audit (<AUTORIG_WORK>/audit/<model>.json): the verdict, the worst
#                                             bones and why, the bleed pairs and the islands
#
# preview.glb itself is served through the ordinary /files/models/... route, under the token.
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = ("/viewer.js", "/viewer_logic.js")        # the viewer's own modules, served like vendor/ (no token)
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
# The viewer's page: three.js loads GLB textures through blob: URLs.
CSP = ("default-src 'self'; img-src 'self' data: blob:; connect-src 'self' data: blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")


def static_file(path):
    """(bytes, content type) for a viewer file under gui/ (viewer.js, viewer_logic.js or vendor/...), or None."""
    rel = path.lstrip("/")
    if not ("/" + rel in CODE or rel.startswith("vendor/")):
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
        item["audit"] = audit_digest(layout, name, max(_mtime(blend), _mtime(fbx)))
        out.append(item)
    return out


def audit_digest(layout, name, rig_mtime=0):
    """What the viewer shows of a model's audit, or None when it has none: see worst_bones()."""
    p = os.path.join(layout.WORK, "audit", name + ".json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh: full = json.load(fh)
    except Exception as e:
        return {"error": "the audit cannot be read: %r" % e}
    v = full.get("verdict") or {}
    return {
        "pass": v.get("pass"),
        "grade": v.get("grade") or ("PASS" if v.get("pass") else "FAIL"),
        "checks": v.get("checks", {}),
        "warnings": v.get("warnings", []),
        "stale": _mtime(p) + 1 < rig_mtime,
        "bones": worst_bones(full),
        "meshes": [m for m in full.get("meshes", []) if m.get("verts_over_4")],
        "bleed_total_pct": full.get("bleed_total_pct"),
        "bleed_pairs": full.get("bleed_pairs", []),
        "islands": full.get("islands"), "rigid_islands": full.get("rigid_islands"),
        "largest_islands": full.get("largest_islands", []),
        "unweighted_verts": full.get("unweighted_verts", 0),
    }


def worst_bones(audit, limit=12):
    """The bones an audit (steps/audit.py's JSON) blames, worst first: [{bone, level, score, reasons}].

    level is "bad" when the bone is behind a check that failed (bend tears, bleed, the head's share), else "warn".
    Blamed: a bone whose 40-degree bend tears edges; the bone that owns another bone's surface (each bleed pair);
    a head that owns too little; twist tears, a bend that drags surface outside the bone's own limb, a limb bone
    reaching far, a hard joint. Too many influences is per mesh in the audit, not per bone, so it is in "meshes".
    """
    checks = (audit.get("verdict") or {}).get("checks", {})
    failed = lambda k: k in checks and checks[k].get("ok") is False
    rows = {}

    def add(bone, level, score, why):
        if not bone: return
        r = rows.setdefault(bone, {"bone": bone, "level": "warn", "score": 0.0, "reasons": []})
        if level == "bad": r["level"] = "bad"
        r["score"] += score
        r["reasons"].append(why)

    for b in audit.get("bends", []):
        t = b.get("tear_edges") or 0
        if t and b.get("mode") == "bend":
            add(b["bone"], "bad" if failed("bend_tears") else "warn", 100 + t,
                "tears %d edge%s bent 40° (stretch %sx)" % (t, "" if t == 1 else "s", b.get("max_stretch")))
        elif t:
            add(b["bone"], "warn", 10 + 0.2 * t, "tears %d edge%s twisted 60°" % (t, "" if t == 1 else "s"))
        c = b.get("collateral_pct") or 0
        if c >= 2 and b.get("mode") == "bend":
            add(b["bone"], "warn", 5 + c, "bending it drags %.1f%% of the surface outside its limb" % c)
    for pair in audit.get("bleed_pairs", []):
        owner, nearest, pct = pair[0], pair[1], pair[2]
        if pct < 0.05: continue
        add(owner, "bad" if failed("bleed_pct") and pct >= 0.25 else "warn", 50 + 40 * pct,
            "bleed: owns %.2f%% of the surface, nearer %s" % (pct, nearest))
    if failed("head_pct"):
        head = next((x for x in audit.get("hierarchy", []) if x["bone"].split(":")[-1].lower() == "head"), None)
        if head:
            add(head["bone"], "bad", 80, "owns only %s%% of the surface (at least %s%%)" %
                (checks["head_pct"].get("value"), checks["head_pct"].get("limit")))
    for w in audit.get("verdict", {}).get("warnings", []):
        m = re.match(r"^(\S+) reaches ([\d.]+)$", w)
        if m: add(m.group(1), "warn", 8, "reaches %s of the model from its bone (90th percentile)" % m.group(2))
    for j in audit.get("joints", []):
        if (j.get("hard_edges") or 0) >= 3:
            add(j["joint"], "warn", 3 + j["hard_edges"], "hard joint: %d edges jump from %s to it" % (j["hard_edges"], j["parent"]))
    out = sorted(rows.values(), key=lambda r: (r["level"] != "bad", -r["score"], r["bone"]))
    for r in out: r["score"] = round(r["score"], 2)
    return out[:limit]


def has_preview(layout, name):
    return os.path.exists(os.path.join(layout.rigged_dir(name), "preview.glb"))

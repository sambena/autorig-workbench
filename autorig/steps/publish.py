# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: writes each model's card (model.json) beside it, and an index of a group's cards (pack.json).
#
# The rigged .blend and the decimated .fbx already land beside the source export. The card carries what an engine
# importer needs to *use* them: how big the thing is in metres, what its rig is and which bone plays which role,
# which texture belongs to it, the audit's verdict and the clips. An importer reads the card rather than keeping its
# own copy of the numbers.
#
#   python autorig/steps/publish.py <group> [-only wolf,moth]
#   python autorig/steps/publish.py . [-only wolf]                     models directly under the root
#
# Card fields come from each model's rig.json ("card": metres, role, tint...) over the collection's defaults
# (autorig.json "card"; spec_store.CARD_DEFAULTS has the shape). Plain Python: no Blender needed.

import json, os, sys, glob, pathlib, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
from layout import ROOT, rig_folder, work_dir, SOURCE_EXTS
import spec_store

EXPORT_FORMAT = "autorig-export/"   # the winged export's manifest (make_clips.py, docs/FORMATS.md)


def _sources(d):
    """Source exports in the folder itself, the one with its own textures first (as layout.source_model)."""
    hits = [p for ext in SOURCE_EXTS for p in glob.glob(os.path.join(glob.escape(d), "*" + ext))
            if "rigged" not in os.path.basename(p)]
    return sorted(hits, key=lambda p: (not (os.path.isdir(os.path.splitext(p)[0] + ".fbm") or p.lower().endswith(".glb")),
                                       SOURCE_EXTS.index(os.path.splitext(p)[1].lower()), p))


def card(group, folder):
    d = os.path.join(ROOT, group, folder)
    rf = rig_folder(folder)                      # "rigged", or the spec's rig_folder
    rigged = os.path.join(d, rf)
    if not os.path.isdir(rigged):
        return None

    fbx = os.path.join(rigged, folder + ".fbx")
    blend = os.path.join(rigged, folder + ".blend")
    if not os.path.exists(fbx):
        return None

    settings = spec_store.COLLECTION["card"]
    tex = [os.path.relpath(p, d).replace("\\", "/")
           for p in glob.glob(os.path.join(glob.escape(d), "*.fbm", "*"))]
    # Same preference as layout.source_model: the export with its own textures. Kept local to this folder rather
    # than calling layout.pack_dir(), which resolves a name across groups.
    source = [os.path.basename(p) for p in _sources(d)]

    qa_src = os.path.join(work_dir("qa"), folder + ".json")
    # a custom builder keeps its own log beside the rig (<rig folder>/<model>_rig.json)
    own = os.path.join(rigged, folder + "_rig.json")
    if rf != "rigged" and os.path.exists(own): qa_src = own
    rig = {}
    if os.path.exists(qa_src):
        r = json.load(open(qa_src))
        rig = {k: r[k] for k in ("kind", "bones", "deform_bones", "rest_shift") if k in r}
        # which bone plays which role (skeletons.py, SKELETONS.md): what importers read instead of guessing from names
        if r.get("skeleton"): rig["skeleton"] = r["skeleton"]
        audit = os.path.join(work_dir("audit"), folder + ".json")
        if os.path.exists(audit):
            v = json.load(open(audit)).get("verdict")
            if v: rig["audit"] = {"pass": v["pass"], **{k: c["value"] for k, c in v["checks"].items()}}
        clips = os.path.join(d, "clips", folder + "_clips.json")
        if os.path.exists(clips):
            rig["clips"] = {"fbx": "clips/%s.fbx" % folder, "blend": "clips/%s_clips.blend" % folder,
                            "json": "clips/%s_clips.json" % folder,
                            "names": [c["name"] for c in json.load(open(clips, encoding="utf-8"))["clips"]]}
        png = qa_src[:-5] + ".png"
        if os.path.exists(png):
            shutil.copy2(png, os.path.join(rigged, folder + "_qa.png"))
        if os.path.exists(os.path.join(rigged, folder + "_qa.png")):
            rig["qa"] = "%s/%s_qa.png" % (rf, folder)
        if r.get("script"): rig["builder"] = r["script"]
        # the winged archetype's own export (make_clips.py): a folder with mesh, rig and takes in metres and its
        # manifest, beside the rig
        for m in glob.glob(os.path.join(glob.escape(rigged), "*", "*.json")):
            man = json.load(open(m, encoding="utf-8"))
            if man.get("format", "").startswith(EXPORT_FORMAT):
                sub = os.path.relpath(os.path.dirname(m), d).replace(os.sep, "/")
                rig["clips"] = {"export": "%s/%s" % (sub, man["fbx"]), "manifest": "%s/%s" % (sub, os.path.basename(m)),
                                "blend": "%s/%s.blend" % (rf, folder), "names": [c["name"] for c in man["clips"]]}
                if man.get("skeleton") and "skeleton" not in rig: rig["skeleton"] = {"archetype": man["skeleton"],
                                                                                     "bones": man.get("bones")}

    # The collection's fields, in its order, then this model's own values, then sidecar files beside the model.
    own_card = spec_store.model(folder).get("card", {})
    fields = {k: own_card.get(k, default) for k, default in settings["fields"].items()}
    fields.update({k: v for k, v in own_card.items() if k not in fields})
    for field, name in settings["sidecars"].items():
        p = os.path.join(d, name)
        if os.path.exists(p):
            fields[field] = json.load(open(p, encoding="utf-8"))

    ctx = dict(fields, rig_kind=rig.get("kind", "rig"), builder=rig.get("builder"), rig_folder=rf)
    notes = settings["notes"]
    hit = next((t for f, t in notes["when"].items() if fields.get(f)), None)
    if hit:
        note = hit.format(**ctx)
    elif rig.get("builder"):
        note = notes["builder"].format(**ctx)
    elif rig:
        note = notes["rigged"].format(**ctx)
    else:
        note = notes["static"].format(**ctx)

    return {
        "name": folder,
        settings["group_field"]: group or None,
        **fields,
        "source_fbx": source[0] if source else None,
        "textures": tex,
        "rigged_fbx": "%s/%s.fbx" % (rf, folder),
        "rigged_blend": "%s/%s.blend" % (rf, folder) if os.path.exists(blend) else None,
        "rig": rig,
        "note": note,
    }


def main():
    # python publish.py <group> [-only a,b]      ("." for the models directly under the root)
    # -only rewrites just those cards; pack.json is still rebuilt from every card, reading the others as they are,
    # so publishing one model never rewrites a card someone else is working on.
    a = sys.argv[1:]
    if not a or a[0].startswith("-"): sys.exit("usage: python publish.py <group|.> [-only a,b]")
    group = "" if a[0] in (".", "") else a[0]
    only = set(a[a.index("-only") + 1].split(",")) if "-only" in a else None
    folders = sorted(os.path.basename(d) for d in glob.glob(os.path.join(glob.escape(os.path.join(ROOT, group)), "*"))
                     if os.path.isdir(d) and not os.path.basename(d).startswith(("_", ".")))
    key = spec_store.COLLECTION["card"]["group_field"]

    cards = []
    for f in folders:
        path = pathlib.Path(ROOT, group, f, "model.json")
        if only is not None and f not in only:
            if path.exists(): cards.append(json.loads(path.read_text(encoding="utf-8")))
            continue
        c = card(group, f)
        if c is None:
            continue
        path.write_text(json.dumps(c, indent=2), encoding="utf-8")
        cards.append(c)
        print("PUBLISH %-16s %sm  %s" % (c["name"], c.get("metres"), c["rig"].get("bones", "-")))

    index = {key: group or None, "models": cards}
    pathlib.Path(ROOT, group, "pack.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print("PUBLISH_DONE %d cards, index at %s" % (len(cards), os.path.join(group or ".", "pack.json")))


if __name__ == "__main__":
    main()

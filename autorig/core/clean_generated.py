# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: delete the files the tool generates, to free space (File > Clean Up Generated Files…).
#
#   python autorig/core/clean_generated.py --root <models folder> [--work <work folder>]            sizes only
#   python autorig/core/clean_generated.py --root <models folder> --delete source,previews,renders
#
# Everything here is rebuilt by the tool when it is needed, except what the dialog leaves unticked: the audits
# (Audit all rebuilds them, slowly), the rig logs (Smeltdown's importer reads _autorig/qa/*.json), the undo
# history, and uploaded mocap clips (not rebuildable). Only the work folder and the files named
# <model>/rigged/preview.glb|json are ever touched; models, textures, rig.json, cards, rigs and clips never are.
import argparse, json, os, shutil, sys

# key, label, what it is, ticked by default, work-folder subfolders
CATEGORIES = [
    ("source", "Source views", "pictures of each model's source file; drawn again when you open a model", True,
     ["source"]),
    ("previews", "Rig previews", "each model's rigged/preview.glb and preview.json; the Preview step rebuilds them",
     True, []),
    ("renders", "QA and bend-test pictures", "the Rig step's QA renders and the humanoid bend tests", True,
     ["qa_humanoids"]),
    ("scratch", "Scratch and caches", "facing views, survey, clip previews, measure sheets, Mesh Doctor, uploads in "
     "progress, Studio jobs, auto-tune and AI Assist trial rigs", True,
     ["facing", "survey", "clips", "measure", "doctor", "_incoming", "studio_jobs", "tune", "ai_trials"]),
    ("export", "Engine exports", "File > Export's output; export again to rebuild", True, ["export"]),
    ("suggest", "Suggested skeletons", "Suggest's survey output; the spec it wrote into rig.json stays", True,
     ["suggest"]),
    ("audit", "Audits", "the grades in the model list; Audit all rebuilds them, slowly (Blender)", False, ["audit"]),
    ("riglogs", "Rig logs", "qa/<model>.json: Smeltdown's importer reads the skeleton roles from these; the Rig step "
     "rewrites them", False, []),
    ("undo", "Undo history", "AI Assist and spec editor undo: without it, Undo can't go back past now", False,
     ["ai", "spec_editor"]),
    ("mocap", "Uploaded mocap clips", "clips uploaded for retargeting: these can't be rebuilt", False, ["mocap"]),
]
KEYS = [c[0] for c in CATEGORIES]


def _walk_files(d):
    for base, dirs, files in os.walk(d):
        for f in files:
            yield os.path.join(base, f)


def _previews(root, work):
    """<model>/rigged/preview.glb|json anywhere under the models folder (not in the work folder or .git)."""
    skip = {os.path.normcase(os.path.realpath(work))}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git" and not d.startswith(".")
                   and os.path.normcase(os.path.realpath(os.path.join(base, d))) not in skip]
        if os.path.basename(base) == "rigged":
            for f in ("preview.glb", "preview.json"):
                if f in files: yield os.path.join(base, f)


def files_for(key, root, work):
    cat = next(c for c in CATEGORIES if c[0] == key)
    for sub in cat[4]:
        d = os.path.join(work, sub)
        if os.path.isdir(d): yield from _walk_files(d)
    qa = os.path.join(work, "qa")
    if key == "previews":
        yield from _previews(root, work)
    elif key in ("renders", "riglogs") and os.path.isdir(qa):
        for f in _walk_files(qa):
            is_log = os.path.dirname(f) == qa and f.endswith(".json")
            if is_log == (key == "riglogs"): yield f


def _size(f):
    try:
        return os.path.getsize(f)
    except OSError:
        return 0


def scan(root, work):
    out = []
    for key, label, note, default, _ in CATEGORIES:
        fs = list(files_for(key, root, work))
        out.append({"key": key, "label": label, "note": note, "default": default, "files": len(fs),
                    "bytes": sum(_size(f) for f in fs)})
    return out


def _inside(path, folder):
    p, f = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return p.startswith(f.rstrip(os.sep) + os.sep)


def clean(root, work, keys):
    """Delete the chosen categories; returns {key: {files, bytes}} and the files that could not go."""
    bad = [k for k in keys if k not in KEYS]
    if bad: raise ValueError("unknown kinds of file: %s" % ", ".join(bad))
    done, failed = {}, []
    for key in keys:
        n = size = 0
        for f in list(files_for(key, root, work)):
            ok_place = _inside(f, work) or (os.path.basename(f) in ("preview.glb", "preview.json")
                                            and os.path.basename(os.path.dirname(f)) == "rigged" and _inside(f, root))
            if not ok_place: continue
            s = _size(f)
            try:
                os.remove(f); n += 1; size += s
            except OSError as e:
                failed.append("%s: %s" % (f, e.strerror or e))
        done[key] = {"files": n, "bytes": size}
        cat = next(c for c in CATEGORIES if c[0] == key)
        for sub in cat[4]:                                     # the emptied folders too
            d = os.path.join(work, sub)
            if os.path.isdir(d) and not any(True for _ in _walk_files(d)): shutil.rmtree(d, ignore_errors=True)
    return {"deleted": done, "failed": failed[:50]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delete the files the tool generates, to free space.")
    ap.add_argument("--root", required=True, help="the models folder")
    ap.add_argument("--work", help="the work folder (default: <root>/_autorig)")
    ap.add_argument("--delete", help="comma list of kinds to delete: " + ",".join(KEYS))
    a = ap.parse_args(argv)
    work = a.work or os.path.join(a.root, "_autorig")
    if not a.delete:
        for c in scan(a.root, work):
            print("%-9s %6d files %9.1f MB  %s%s" % (c["key"], c["files"], c["bytes"] / 1e6, c["label"],
                                                     "" if c["default"] else "  (kept unless named)"))
        return 0
    print(json.dumps(clean(a.root, work, [k.strip() for k in a.delete.split(",") if k.strip()]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

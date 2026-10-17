# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: where a model's files live, and where the tool writes its working files.
#
# Two folders, both set from the environment so every step (and the GUI that runs them) agrees:
#
#   AUTORIG_MODELS  the models root. A model is a folder holding a source export (FBX with its .fbm textures, GLB,
#                   glTF or OBJ), either directly under the root (<root>/wolf) or one level down in a group
#                   (<root>/Creatures/wolf). Default: the repo's samples/ folder.
#   AUTORIG_WORK    QA pictures, rig logs, audits, measuring sheets. Default: <models root>/_autorig, or the
#                   parent's _autorig when only that exists (a pack chosen as the root shares its collection's)
#                   (folders starting with "_" or "." are never mistaken for models or groups).
#
# A model is named by its folder. Specs, logs and audits are keyed on that bare name, so a model can move between
# groups without anything being renamed; this resolves the name to wherever the folder is.

import json, os, glob, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))


def detect_work_dir(root):
    """The work folder for a models root: its own _autorig, else its parent's.

    A group or pack chosen as the root (asset-packs/Heartroot) shares the collection's work folder
    (asset-packs/_autorig), so its audits and logs are the ones already there.
    """
    root = os.path.abspath(root)
    for d in (os.path.join(root, "_autorig"), os.path.join(os.path.dirname(root), "_autorig")):
        if os.path.isdir(d):
            return d
    return os.path.join(root, "_autorig")


ROOT = os.path.abspath(os.environ.get("AUTORIG_MODELS") or os.path.join(REPO, "samples"))
WORK = os.path.abspath(os.environ.get("AUTORIG_WORK") or detect_work_dir(ROOT))

# Source formats, in order of preference when a folder holds more than one kind.
SOURCE_EXTS = (".fbx", ".glb", ".gltf", ".obj")
# Folders the tool writes inside a model folder; nothing under them is ever a source.
OUTPUT_DIRS = ("clips", "_studio")
# Model and group folder names the GUI will create (rename, duplicate, new group).
FOLDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


# ---------------------------------------------------------------------------------------------------------------
# Choosing the models root (the GUI's working folder)
#
# The GUI can switch ROOT while it runs, to narrow the model list to one pack or group. The last root and the recent
# ones are kept in .autorig_roots.json in the repo (AUTORIG_ROOTS_FILE overrides it; the tests point it elsewhere).
# Steps never read that file: the GUI hands them AUTORIG_MODELS and AUTORIG_WORK.
# ---------------------------------------------------------------------------------------------------------------

MAX_RECENT = 12


def roots_file():
    return os.environ.get("AUTORIG_ROOTS_FILE") or os.path.join(REPO, ".autorig_roots.json")


def _load_roots():
    try:
        with open(roots_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("recent", []), list):
            return data
    except (OSError, ValueError):
        pass
    return {"active_root": None, "active_work": None, "recent": []}


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _remember_root(root, work):
    data = _load_roots()
    recent = [r for r in data.get("recent", []) if isinstance(r, dict) and r.get("path") and not _same(r["path"], root)]
    data.update(active_root=root, active_work=work,
                recent=([{"path": root, "work": work}] + recent)[:MAX_RECENT])
    try:
        with open(roots_file(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError as e:
        print("LAYOUT could not save the recent roots to %s: %s" % (roots_file(), e))


def active_root():
    """The root the GUI used last, as (root, work), or None when there is none or it has gone."""
    data = _load_roots()
    root = data.get("active_root")
    if not root or not os.path.isdir(root):
        return None
    work = data.get("active_work")
    return os.path.abspath(root), (os.path.abspath(work) if work else None)


def recent_roots():
    """The recent roots that still exist, newest first: [{"path", "work"}]."""
    return [{"path": os.path.abspath(r["path"]), "work": r.get("work")}
            for r in _load_roots().get("recent", [])
            if isinstance(r, dict) and r.get("path") and os.path.isdir(r["path"])]


def resolve_root_path(raw):
    """A folder the user typed, as an absolute path.

    A full path is taken as it is. A short one ("asset-packs/Heartroot", "/asset-packs", "Heartroot") is looked up
    under the current root, then beside this repo, then in the repo. Returns the first that exists, else the path as
    typed made absolute (so the error names it).
    """
    clean = str(raw or "").strip().strip("\"'")
    if not clean:
        raise ValueError("enter a folder")
    clean = os.path.expanduser(clean)
    if os.path.isabs(clean) and os.path.isdir(clean):
        return os.path.abspath(clean)
    short = clean.replace("\\", "/").lstrip("/")
    for base in (ROOT, os.path.dirname(REPO), REPO):
        cand = os.path.abspath(os.path.join(base, short))
        if os.path.isdir(cand):
            return cand
    return os.path.abspath(clean)


def set_root(root, work=None, create=False, remember=True):
    """Switch the models root (and the work folder, detected from the root unless given). Returns its summary.

    Raises ValueError when the folder does not exist, unless create is set (the command line's --models).
    """
    global ROOT, WORK
    new_root = resolve_root_path(root)
    if not os.path.isdir(new_root):
        if not create:
            raise ValueError("no folder %s" % new_root)
        os.makedirs(new_root)
    new_work = os.path.abspath(resolve_root_path(work)) if work and str(work).strip() else detect_work_dir(new_root)
    os.makedirs(new_work, exist_ok=True)
    ROOT, WORK = new_root, new_work
    os.environ["AUTORIG_MODELS"], os.environ["AUTORIG_WORK"] = ROOT, WORK
    if remember:
        _remember_root(ROOT, WORK)
    return root_summary(ROOT)


def _folder_name(path):
    return os.path.basename(os.path.normpath(path)) or path


def count_models(path):
    """Models directly in a folder or one level down in its groups: what the model list would show as the root."""
    n = 0
    for d in glob.glob(os.path.join(glob.escape(path), "*")):
        if not os.path.isdir(d) or _hidden(os.path.basename(d)):
            continue
        if is_model_dir(d):
            n += 1
        else:
            n += sum(1 for m in glob.glob(os.path.join(glob.escape(d), "*")) if is_model_dir(m))
    return n


def is_collection(path):
    """A folder set up as a collection: it has an autorig.json or a work folder of its own."""
    return os.path.isfile(os.path.join(path, "autorig.json")) or os.path.isdir(os.path.join(path, "_autorig"))


def root_summary(path, kind=""):
    path = os.path.abspath(path)
    return {"path": path, "name": _folder_name(path), "kind": kind, "work": detect_work_dir(path),
            "models": count_models(path), "current": _same(path, ROOT)}


def discover_roots():
    """Folders worth offering as the root, each once, in this order: the current root and its groups, the
    collection above it, the recent roots, the bundled samples, and collections beside this repo with their packs."""
    out, seen = [], set()

    def add(path, kind, keep_empty=False):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen or not os.path.isdir(path):
            return
        seen.add(key)
        s = root_summary(path, kind)
        if s["models"] or keep_empty:
            out.append(s)

    add(ROOT, "current", keep_empty=True)
    for g in groups():
        add(os.path.join(ROOT, g), "group")
    parent = os.path.dirname(ROOT)
    if parent != ROOT and is_collection(parent):
        add(parent, "collection")
    for r in recent_roots():
        add(r["path"], "recent")
    add(os.path.join(REPO, "samples"), "samples")
    beside = os.path.dirname(REPO)
    for name in sorted(os.listdir(beside)) if os.path.isdir(beside) else ():
        d = os.path.join(beside, name)
        if _hidden(name) or not os.path.isdir(d) or _same(d, REPO) or not is_collection(d):
            continue
        add(d, "collection")
        for sub in sorted(os.listdir(d)):
            if not _hidden(sub):
                add(os.path.join(d, sub), "pack")
    return out


def work_dir(*parts):
    """A folder under AUTORIG_WORK (qa, audit, measure, facing, clips), created on first use."""
    d = os.path.join(WORK, *parts)
    os.makedirs(d, exist_ok=True)
    return d


def _hidden(name):
    return name.startswith(("_", "."))


def is_model_dir(d):
    """A folder is a model when it holds a source export or a rig.json directly."""
    if not os.path.isdir(d) or _hidden(os.path.basename(d)):
        return False
    if os.path.exists(os.path.join(d, "rig.json")):
        return True
    return any(os.path.splitext(f)[1].lower() in SOURCE_EXTS for f in os.listdir(d))


def groups():
    """Group folders under the root: folders that are not models themselves."""
    return sorted(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "*"))
                  if os.path.isdir(d) and not _hidden(os.path.basename(d)) and not is_model_dir(d))


def all_models():
    """(group, name) for every model: those at the root first (group ""), then each group's."""
    out = [("", m) for m in models_in(None) if is_model_dir(os.path.join(ROOT, m))]
    for g in groups():
        out += [(g, m) for m in models_in(g)]
    return out


def pack_dir(key):
    """The folder holding a model, at the root or one level down under a group."""
    direct = os.path.join(ROOT, key)
    if os.path.isdir(direct):
        return direct

    hits = sorted(
        d for d in glob.glob(os.path.join(ROOT, "*", key))
        if os.path.isdir(d) and not _hidden(os.path.basename(os.path.dirname(d)))
    )
    # Falls back to the direct path so a missing model is reported against a sensible one.
    return hits[0] if hits else direct


def group_of(key):
    """The group folder a model sits in ("" when it is directly under the root)."""
    parent = os.path.dirname(pack_dir(key))
    return "" if os.path.normcase(parent) == os.path.normcase(ROOT) else os.path.basename(parent)


def models_in(group=None):
    """Model folders under a group (or every folder directly under the root when group is None)."""
    base = os.path.join(ROOT, group) if group else ROOT
    return sorted(os.path.basename(d) for d in glob.glob(os.path.join(base, "*"))
                  if os.path.isdir(d) and not _hidden(os.path.basename(d)))


def has_own_textures(path):
    """True when the export carries its textures: an FBX with its <stem>.fbm folder beside it, or a GLB (embedded)."""
    if path.lower().endswith(".glb"):
        return True
    return os.path.isdir(os.path.splitext(path)[0] + ".fbm")


def _is_output(path, key):
    parts = os.path.relpath(path, pack_dir(key)).replace("\\", "/").split("/")[:-1]
    return any(p.startswith("rigged") or p in OUTPUT_DIRS for p in parts)


def source_model(key):
    """The source export, never one of the tool's own outputs (rigged*/, clips/).

    When a folder holds more than one, the one with its own textures wins. Generators often offer two downloads: a
    textured export (`Suit+shell.fbx` beside `Suit+shell.fbm`, with UVs the texture was baked against) and a bare
    mesh (`Suit shell mesh.fbx`, no UVs, one default material). Taking whichever sorted first once decimated a model
    from the bare mesh, and it reached the engine with no UVs and a texture that could never show. The two can also
    be separate generations with different proportions, so unwrapping the bare mesh afterwards would not line up.

    Folder depth, format (FBX, GLB, glTF, OBJ) and name break any remaining tie, and a choice is printed so an
    ambiguous folder is visible in the run log rather than settled silently.
    """
    hits = [
        p for ext in SOURCE_EXTS
        for p in glob.glob(os.path.join(glob.escape(pack_dir(key)), "**", "*" + ext), recursive=True)
        if not _is_output(p, key)
    ]
    rank = lambda p: SOURCE_EXTS.index(os.path.splitext(p)[1].lower())
    hits.sort(key=lambda p: (not has_own_textures(p), p.count(os.sep), rank(p), p))

    if len(hits) > 1:
        print("LAYOUT %s: %d source exports, using %s (textured=%s)" % (
            key, len(hits), os.path.relpath(hits[0], pack_dir(key)), has_own_textures(hits[0])))

    return hits[0] if hits else None


# The older name, kept for the steps that still call it.
source_fbx = source_model


# Work files are one level under a tool folder (audit/wolf.json, export/wolf/). A name matches the model when it
# is exactly the model's name, or the name plus an extension (wolf.glb, wolf.rig.json). A "_" suffix matches only
# the retarget logs and the export zips: "can" must not take "canine.json".
_EXPORT_ZIP_STEMS = ("all_presets", "unreal", "unity", "godot", "web")


def _inside(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return os.path.normcase(child).startswith(os.path.normcase(parent) + os.sep)


def _work_entry_belongs(entry, name):
    if entry == name or entry.startswith(name + "."):
        return True
    mark = name + "_"
    if not entry.startswith(mark):
        return False
    rest = entry[len(mark):]
    if rest == "retarget.json" or rest.endswith("_retarget.json"):
        return True
    return any(rest == stem + ".zip" for stem in _EXPORT_ZIP_STEMS)


def _one_model(name):
    """(group, folder) of the one model with this name, or ValueError."""
    name = str(name or "")
    if not name or name in (".", "..") or os.path.basename(name) != name:
        raise ValueError("bad model name")
    hits = [g for g, m in all_models() if m == name]
    if not hits:
        raise ValueError("no model " + name)
    if len(hits) != 1:
        raise ValueError("%s is in more than one group (%s); rename one first" % (name, ", ".join(g or "root" for g in hits)))
    group = hits[0]
    folder = os.path.join(ROOT, group, name) if group else os.path.join(ROOT, name)
    if not _inside(folder, ROOT) or not os.path.isdir(folder) or os.path.basename(os.path.realpath(folder)) != name:
        raise ValueError("refusing to touch a folder outside the models root")
    return group, folder


def _new_name(name, what="model"):
    name = str(name or "").strip()
    if not FOLDER_NAME.match(name):
        raise ValueError("%s names are letters, digits, _ and -, starting with a letter or digit: %r" % (what, name))
    return name


def _work_entries(name):
    """The work files keyed on a model's name: (folder, entry) pairs one level under WORK."""
    if not os.path.isdir(WORK) or _same(os.path.realpath(WORK), os.path.realpath(ROOT)):
        return []
    out = []
    for category in os.listdir(WORK):
        cat = os.path.join(WORK, category)
        if os.path.isdir(cat) and not os.path.islink(cat):
            out += [(cat, e) for e in os.listdir(cat) if _work_entry_belongs(e, name) and _inside(os.path.join(cat, e), WORK)]
    return out


def delete_model(name):
    """Remove one model folder and the work files keyed on its name.

    The folder has to be one model inside the models root. Two folders with the same name are left alone.
    Raises ValueError when the name is not one model.
    """
    group, raw = _one_model(name)
    if not is_model_dir(raw):
        raise ValueError("not a model folder")

    removed = []

    def _rm(path, base):
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        removed.append(os.path.relpath(path, base).replace("\\", "/"))

    _rm(raw, ROOT)
    for cat, entry in _work_entries(name):
        _rm(os.path.join(cat, entry), WORK)
    return {"name": name, "group": group, "removed": removed}


def create_group(group):
    """A new, empty group folder under the root. An existing group is not an error."""
    group = _new_name(group, "group")
    path = os.path.join(ROOT, group)
    if os.path.isdir(path):
        if is_model_dir(path):
            raise ValueError("%s is a model, not a group" % group)
        return {"group": group, "path": path, "created": False}
    if os.path.exists(path):
        raise ValueError("a file named %s is in the way" % group)
    os.makedirs(path)
    return {"group": group, "path": path, "created": True}


def move_model(name, group=""):
    """Move a model folder into another group ("" is the root). Its name, spec and work files stay as they are.

    A group that does not exist yet is created. The old group folder is removed when the move leaves it empty.
    """
    old_group, src = _one_model(name)
    group = str(group or "").strip()
    if group:
        create_group(group)
    if group == old_group:
        return {"name": name, "old_group": old_group, "group": group, "moved": False, "path": src}
    dest = os.path.join(ROOT, group, name) if group else os.path.join(ROOT, name)
    if os.path.exists(dest):
        raise ValueError("%s already exists" % os.path.relpath(dest, ROOT))
    shutil.move(src, dest)
    old_dir = os.path.join(ROOT, old_group)
    if old_group and os.path.isdir(old_dir) and not os.listdir(old_dir):
        os.rmdir(old_dir)
    return {"name": name, "old_group": old_group, "group": group, "moved": True, "path": dest}


def _rename_outputs(folder, old, new):
    """Inside a model folder, the outputs named after the model (rigged*/wolf.fbx, clips/wolf_clips.json) follow it."""
    for sub in os.listdir(folder):
        d = os.path.join(folder, sub)
        if not os.path.isdir(d) or not (sub.startswith("rigged") or sub == "clips"):
            continue
        for entry in os.listdir(d):
            if entry.startswith(old + ".") or entry.startswith(old + "_"):
                os.rename(os.path.join(d, entry), os.path.join(d, new + entry[len(old):]))


def rename_model(name, new_name):
    """Rename a model: its folder, the outputs in it named after it, and its work files."""
    new_name = _new_name(new_name)
    group, src = _one_model(name)
    if new_name == name:
        return {"name": name, "new_name": new_name, "renamed": False, "path": src}
    if any(m.lower() == new_name.lower() for _, m in all_models() if m != name):
        raise ValueError("a model named %s already exists" % new_name)
    dest = os.path.join(os.path.dirname(src), new_name)
    if os.path.exists(dest) and not _same(dest, src):            # a case-only rename is the same folder on Windows
        raise ValueError("%s already exists" % os.path.relpath(dest, ROOT))
    work = _work_entries(name)
    os.rename(src, dest)
    _rename_outputs(dest, name, new_name)
    for cat, entry in work:
        os.rename(os.path.join(cat, entry), os.path.join(cat, new_name + entry[len(name):]))
    return {"name": name, "new_name": new_name, "group": group, "renamed": True, "path": dest}


def duplicate_model(name, new_name):
    """Copy a model's source files and spec under a new name in the same group, to try another spec on.

    The tool's outputs (rigged*/, clips/, prepared/, _studio/) are not copied: the copy starts unrigged.
    """
    new_name = _new_name(new_name)
    group, src = _one_model(name)
    if any(m.lower() == new_name.lower() for _, m in all_models()):
        raise ValueError("a model named %s already exists" % new_name)
    dest = os.path.join(os.path.dirname(src), new_name)
    if os.path.exists(dest):
        raise ValueError("%s already exists" % os.path.relpath(dest, ROOT))

    def outputs(folder, entries):
        return [e for e in entries if folder == src and (e.startswith("rigged") or e in OUTPUT_DIRS)]

    shutil.copytree(src, dest, ignore=outputs)
    return {"name": new_name, "source": name, "group": group, "path": dest}


def rig_folder(key):
    """rigged/, unless the model's spec names another folder (`rig_folder`): a hand-built rig can live beside the
    automatic one, and every step (decimate, publish, make_clips) has to find the one that is current."""
    import spec_store
    return (spec_store.SPECS.get(leaf(key)) or {}).get("rig_folder", "rigged")


def rigged_dir(key):
    """Where this model's current rig lives (and where the pipeline writes it)."""
    return os.path.join(pack_dir(key), rig_folder(key))


def budget(key):
    """The engine triangle budget for a model: its own (`budget` in rig.json; null keeps full resolution), then
    full resolution when its group is listed in the collection's `full_resolution_groups`, then the collection's
    default `budget`. None means keep the full resolution."""
    import spec_store
    k = leaf(key)
    own = spec_store.model(k)
    if "budget" in own:
        return own["budget"]
    if group_of(k) in spec_store.COLLECTION["full_resolution_groups"]:
        return None
    return spec_store.COLLECTION["budget"]


def leaf(key):
    """The bare model name, for naming output files."""
    return os.path.basename(key.replace("\\", "/").rstrip("/"))

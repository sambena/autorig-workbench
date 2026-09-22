# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: where a model's files live, and where the tool writes its working files.
#
# Two folders, both set from the environment so every step (and the GUI that runs them) agrees:
#
#   AUTORIG_MODELS  the models root. A model is a folder holding a source export (FBX with its .fbm textures, GLB,
#                   glTF or OBJ), either directly under the root (<root>/wolf) or one level down in a group
#                   (<root>/Creatures/wolf). Default: the repo's samples/ folder.
#   AUTORIG_WORK    QA pictures, rig logs, audits, measuring sheets. Default: <models root>/_autorig
#                   (folders starting with "_" or "." are never mistaken for models or groups).
#
# A model is named by its folder. Specs, logs and audits are keyed on that bare name, so a model can move between
# groups without anything being renamed; this resolves the name to wherever the folder is.

import os, glob

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
ROOT = os.path.abspath(os.environ.get("AUTORIG_MODELS") or os.path.join(REPO, "samples"))
WORK = os.path.abspath(os.environ.get("AUTORIG_WORK") or os.path.join(ROOT, "_autorig"))

# Source formats, in order of preference when a folder holds more than one kind.
SOURCE_EXTS = (".fbx", ".glb", ".gltf", ".obj")
# Folders the tool writes inside a model folder; nothing under them is ever a source.
OUTPUT_DIRS = ("clips",)


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

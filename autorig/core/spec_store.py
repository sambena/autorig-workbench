# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the per-model data every step reads, from one place, so no step names a model.
#
# Two kinds of file, both JSON (it is in the standard library of system Python and of Blender's Python):
#
#   <model folder>/rig.json    one per model, beside its source export (docs/SPEC.md has every field):
#       {"schema": "autorig-spec/1",
#        "rig":      {...}      how to rig it: kind (tripo, build, humanoid, custom) and its options
#        "humanoid": {...}      joint heights and spans, for kind="humanoid"
#        "budget":   2000       engine triangle budget (null: keep full resolution)
#        "clips":    {...}      clip archetype and settings (make_clips.py)
#        "card":     {...}      fields for the model card (publish.py): metres, role, tint...
#        "notes":    {...}}     free text keyed by field ("rig.audit": "why the allowance"), for people and the GUI
#
#   <models root>/autorig.json settings for the whole collection:
#       {"schema": "autorig-collection/1",
#        "budget": null,                   default budget for a model with none of its own
#        "full_resolution_groups": [],     groups whose models keep full resolution unless they set a budget
#        "licence": "",                    the licence line written into clip manifests
#        "card": {...}}                    how cards are written (see CARD_DEFAULTS)
#
# Every section is optional. A model with no rig.json has no spec: survey and facing still run on it.
import json, os, sys
from collections.abc import Mapping

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
import layout

SCHEMA = "autorig-spec/1"
COLLECTION_SCHEMA = "autorig-collection/1"
SPEC_FILE = "rig.json"
COLLECTION_FILE = "autorig.json"

# How publish.py writes a card, unless the collection says otherwise.
#   group_field  the card key (and pack.json key) that names the model's group
#   fields       card fields between the group and the files, in order, with their defaults; a model's own
#                rig.json "card" section overrides them
#   sidecars     {field: file}: a JSON file beside the model whose contents fill that field
#   notes        the card's `note`, by case: `when` holds {field: text} used when that field is set, then
#                builder (a custom builder made the rig), rigged, static. Texts are str.format templates over the
#                card's fields plus rig_kind, builder and rig_folder.
CARD_DEFAULTS = {
    "group_field": "group",
    "fields": {"tint": None, "metres": None, "role": None},
    "sidecars": {},
    "notes": {
        "when": {},
        "builder": "Rig built by {builder} into {rig_folder}/ (SKELETONS.md). The .blend is full resolution with every "
                   "clip as an action; the .fbx is decimated for an engine.",
        "rigged": "Rigged by Autorig Workbench ({rig_kind}). The .blend is full resolution to animate against; the "
                  ".fbx is decimated for an engine.",
        "static": "Not rigged: nothing on it articulates. The .fbx is the source mesh decimated to an engine budget.",
    },
}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_collection(root=None):
    path = os.path.join(root or layout.ROOT, COLLECTION_FILE)
    c = _read(path) if os.path.exists(path) else {}
    card = dict(CARD_DEFAULTS, **c.get("card", {}))
    card["notes"] = dict(CARD_DEFAULTS["notes"], **c.get("card", {}).get("notes", {}))
    return {
        "budget": c.get("budget"),
        "full_resolution_groups": list(c.get("full_resolution_groups", [])),
        "licence": c.get("licence", ""),
        "card": card,
    }


COLLECTION = load_collection()
LICENCE = COLLECTION["licence"]

_cache = {}


def spec_path(key):
    return os.path.join(layout.pack_dir(layout.leaf(key)), SPEC_FILE)


def load_file(path):
    """A rig.json read and checked. Anything but a known schema is refused rather than half-read."""
    data = _read(path)
    if data.get("schema") != SCHEMA:
        raise ValueError("%s: schema is %r, expected %r" % (path, data.get("schema"), SCHEMA))
    return data


def override_env(key, path):
    """Environment for a step that should read `path` in place of <key>'s rig.json (auto-tune's candidates): the
    model's own rig.json is never written with a spec that has not been accepted."""
    return {"AUTORIG_SPEC_OVERRIDE": os.path.abspath(path), "AUTORIG_SPEC_OVERRIDE_MODEL": layout.leaf(key)}


def model(key):
    """Everything in a model's rig.json ({} when it has none), or the override file for this one model when
    AUTORIG_SPEC_OVERRIDE names one (see override_env)."""
    k = layout.leaf(key)
    ov = os.environ.get("AUTORIG_SPEC_OVERRIDE")
    if ov and os.environ.get("AUTORIG_SPEC_OVERRIDE_MODEL") == k:
        return load_file(ov)
    if k not in _cache:
        p = spec_path(k)
        _cache[k] = load_file(p) if os.path.exists(p) else {}
    return _cache[k]


def reload():
    _cache.clear()
    global COLLECTION, LICENCE
    COLLECTION = load_collection()
    LICENCE = COLLECTION["licence"]


def infer_clip_archetype(spec):
    """Infers an animation clip archetype from a model's rig spec when not explicitly configured."""
    if not isinstance(spec, dict):
        return None
    clips = spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}
    if isinstance(clips, dict) and clips.get("archetype"):
        return clips["archetype"]
    rig = spec.get("rig") or {}
    skel = (rig.get("skeleton") or ("humanoid" if rig.get("kind") == "humanoid" else "")).lower()
    if skel in ("winged", "flyer"):
        return "winged"
    if skel in ("serpent", "swimmer", "fish"):
        return "swimmer"
    if skel in ("floater",):
        return "flyer"
    if skel in ("turret", "rigid"):
        return "turret"
    if skel in ("machine",):
        return "machine"
    # Quadrupeds, hexapods, octopods, humanoids, tripo creatures default to walker
    if skel in ("quadruped", "hexapod", "octopod", "humanoid", "creature") or rig.get("kind") in ("humanoid", "tripo"):
        return "walker"
    # no skeleton named: what the chains it builds are (Heartroot's hand-measured specs name none, and their squid,
    # jellies, wisps and chests had no clips at all): legs walk, a lone body swims, anything else floats and trails
    names = [str(c.get("name") or "").lower() for c in rig.get("chains") or [] if isinstance(c, dict)]
    if any(n.startswith("leg") for n in names):
        return "walker"
    # a fish or an eel: a body with nothing but fins, a tail, a jaw, a lure or barbels on it, swimming; or a lone long
    # spine (the glass eel's eleven bones). A short lone one is an object (a crystal's)
    swims = ("spine", "body", "neck", "head", "tail", "jaw", "lure", "barbel")
    others = [n for n in names if n not in swims and not n.startswith("fin") and not n.startswith("tail_fin")]
    long_spine = any(str(c.get("name")) == "spine" and (c.get("bones") or 0) >= 4
                     for c in rig.get("chains") or [] if isinstance(c, dict))
    if names and not others and ("tail" in names or any(n.startswith("fin") for n in names) or long_spine):
        return "swimmer"
    if names:
        return "flyer"
    return None


def lifted_spec(key):
    """The rig.json of the model a lifted one was copied from (lift.json beside it: {"game", "model"}), or {}: a lift
    carries its source's rig and none of its spec (Smeltdown's floater is Heartroot's lantern squid)."""
    try:
        with open(os.path.join(layout.pack_dir(key), "lift.json"), encoding="utf-8") as fh:
            src = (json.load(fh) or {}).get("model")
    except (OSError, ValueError, TypeError):
        return {}
    return model(src) if src and layout.leaf(src) != layout.leaf(key) else {}


def clip_archetype(key):
    """The clip archetype a model's clips are made with: its own spec's, else its lift source's."""
    return infer_clip_archetype(model(key)) or infer_clip_archetype(lifted_spec(key))


class _Section(Mapping):
    """One section of every model's rig.json, read like a dict keyed on model name: SPECS["wolf"] is the wolf's
    "rig" section. Iterating walks the models root."""

    def __init__(self, section):
        self.section = section

    def __getitem__(self, key):
        m = model(key)
        v = m.get(self.section)
        if v is None and self.section == "clips":
            v = (m.get("rig") or {}).get("clips")
            if v is None:
                arch = clip_archetype(key)
                if arch:
                    v = {"archetype": arch}
        elif v is None and self.section == "humanoid":
            v = (m.get("rig") or {}).get("humanoid")
        if v is None:
            raise KeyError(key)
        return v

    def __iter__(self):
        def has(m_name):
            m = model(m_name)
            if self.section in m: return True
            if self.section in ("clips", "humanoid") and self.section in (m.get("rig") or {}): return True
            if self.section == "clips" and clip_archetype(m_name): return True
            return False
        return (m for _, m in layout.all_models() if has(m))

    def __len__(self):
        return sum(1 for _ in self)


SPECS = _Section("rig")          # rerig.py, measure.py, probe_tips.py, audit.py, the builders
HUMANOIDS = _Section("humanoid") # rerig_humanoid.py
CLIPS = _Section("clips")        # make_clips.py
CARDS = _Section("card")         # publish.py
NOTES = _Section("notes")

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


def model(key):
    """Everything in a model's rig.json ({} when it has none)."""
    k = layout.leaf(key)
    if k not in _cache:
        p = spec_path(k)
        _cache[k] = load_file(p) if os.path.exists(p) else {}
    return _cache[k]


def reload():
    _cache.clear()
    global COLLECTION, LICENCE
    COLLECTION = load_collection()
    LICENCE = COLLECTION["licence"]


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
            return False
        return (m for _, m in layout.all_models() if has(m))

    def __len__(self):
        return sum(1 for _ in self)


SPECS = _Section("rig")          # rerig.py, measure.py, probe_tips.py, audit.py, the builders
HUMANOIDS = _Section("humanoid") # rerig_humanoid.py
CLIPS = _Section("clips")        # make_clips.py
CARDS = _Section("card")         # publish.py
NOTES = _Section("notes")

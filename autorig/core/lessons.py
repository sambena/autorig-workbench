# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: lessons learnt from fixing one model, reapplied to the models like it. Plain Python.
#
# A fix made by hand to one model is often right for its kind: a cow's legs want less swing than the horse clips
# give them, whichever cow it is. So when a fix is made, it is written down here for the model's kind, and the next
# model of that kind starts from it. The kind is the first word of the model's name (Cow_Hereford_Tripo and
# Cow_Holstein are both "cow"), which is how the collection names its models.
#
# The lessons live with the models, in <collection>/autorig_lessons.json (the folder that holds the work folder), so
# they are committed with the collection and every copy of the tool that opens it uses them:
#
#   {"swing": {"cow": {"value": 0.75, "from": "Cow_Hereford_Tripo", "at": "2026-10-18"}}}
import datetime, json, os, re

import layout

FILE = "autorig_lessons.json"


def path():
    work = os.path.abspath(layout.WORK)
    base = os.path.dirname(work) if os.path.basename(work) == "_autorig" else os.path.abspath(layout.ROOT)
    return os.path.join(base, FILE)


def kind(name):
    """The model's kind: the first word of its name, lower case ("Cow_Hereford_Tripo" -> "cow"), or None."""
    words = [w for w in re.split(r"[_\-\s.]+", name or "") if w]
    if not words: return None
    w = re.sub(r"\d+$", "", words[0]).lower()
    return w if len(w) >= 3 and w.isalpha() else None


def load():
    try:
        with open(path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data):
    with open(path(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def for_model(name):
    """What the lessons say for this model: {"swing": 0.75, "swing_from": "Cow_Hereford_Tripo"}, or {}."""
    k = kind(name)
    entry = (load().get("swing") or {}).get(k) if k else None
    if not entry or not isinstance(entry.get("value"), (int, float)): return {}
    return {"swing": entry["value"], "swing_from": entry.get("from")}


def learn_swing(name, value):
    """Remembers a leg swing set for this model as the lesson for its kind; 1 (as the clips were made) forgets it."""
    k = kind(name)
    if not k or not isinstance(value, (int, float)): return
    data = load()
    swing = data.setdefault("swing", {})
    if abs(value - 1.0) < 1e-6:
        if swing.pop(k, None) is None: return
    else:
        old = swing.get(k) or {}
        if old.get("value") == value: return
        swing[k] = {"value": value, "from": name, "at": datetime.date.today().isoformat()}
    save(data)

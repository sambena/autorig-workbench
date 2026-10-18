# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what Mesh2Motion offers, read from its files. Plain Python (no bpy, no numpy).
#
# Mesh2Motion (https://github.com/Mesh2Motion/mesh2motion-app, Scott Petrovic and contributors; app MIT, rigs and
# animations CC0) has nine skeleton templates, each a rig GLB in static/rigs and one or more animation GLBs in
# static/animations. Its src/lib/RigConfig.ts says which files go with which template; FILES below follows it.
# The clip list comes from each animation GLB's JSON chunk only (names, and durations from the samplers' input
# accessors' max), so the API can list about 250 clips without loading any geometry.
#
# Root motion: Mesh2Motion keeps the root's position track on clips whose name ends in "RM". Its own test is
# name.toLowerCase().endsWith('rm'), which also catches "Cheer_One_arm" and "Help_One_Arm"; here a clip is root
# motion only when its name ends in " RM" or "_RM" (or is exactly "RM").
import json, os, re, struct, threading

FILES = {           # template: (rig file, [animation files], sample model)
    "human": ("rig-human.glb", ["human-base-animations.glb", "human-addon-animations.glb",
                                "human-mocap-animations.glb"], "model-human.glb"),
    "fox": ("rig-fox.glb", ["fox-animations.glb"], "model-fox.glb"),
    "horse": ("rig-horse.glb", ["horse-animations.glb"], "model-horse.glb"),
    "bird": ("rig-bird.glb", ["bird-animations.glb"], "model-bird.glb"),
    "dragon": ("rig-dragon.glb", ["dragon-animations.glb"], "model-dragon.glb"),
    "kaiju": ("rig-kaiju.glb", ["kaiju-animations.glb"], "model-kaiju.glb"),
    "spider": ("rig-spider.glb", ["spider-animations.glb"], "model-spider.glb"),
    "snake": ("rig-snake.glb", ["snake-animations.glb"], "model-snake.glb"),
    "shark": ("rig-shark.glb", ["shark-animations.glb"], "model-shark.glb"),
}
TITLES = {"human": "Human", "fox": "Fox (quadruped)", "horse": "Horse (quadruped)", "bird": "Bird",
          "dragon": "Dragon", "kaiju": "Kaiju (tailed biped)", "spider": "Spider (many legs)", "snake": "Snake",
          "shark": "Shark (fish)"}
ROOT_MOTION = re.compile(r"(^|[ _])RM$", re.I)

_cache = {}
_lock = threading.Lock()


def glb_json(path):
    """The JSON chunk of a .glb, reading only that chunk."""
    with open(path, "rb") as f:
        head = f.read(20)
        if len(head) < 20: raise ValueError(path + " is too short for a GLB")
        magic, _, _, jl, jt = struct.unpack("<IIIII", head)
        if magic != 0x46546C67 or jt != 0x4E4F534A: raise ValueError(path + " is not a GLB")
        return json.loads(f.read(jl))


def clips_in(path):
    """[{name, duration, root_motion, file}] of one animation GLB."""
    key = (path, os.path.getmtime(path))
    with _lock:
        if key in _cache: return _cache[key]
    j = glb_json(path)
    acc = j.get("accessors") or []
    out = []
    for a in j.get("animations") or []:
        dur = 0.0
        for s in a.get("samplers") or []:
            mx = (acc[s["input"]].get("max") or [0]) if s.get("input") is not None and s["input"] < len(acc) else [0]
            dur = max(dur, float(mx[0]))
        name = a.get("name") or "clip_%d" % len(out)
        out.append({"name": name, "duration": round(dur, 3), "root_motion": bool(ROOT_MOTION.search(name.strip())),
                    "file": os.path.basename(path)})
    with _lock:
        _cache[key] = out
    return out


def templates(app):
    """[{name, title, rig, sample, animation_files, clips: count}] for an M2M app checkout (its static/ folder)."""
    static = os.path.join(app, "static")
    out = []
    for t, (rig, anims, sample) in FILES.items():
        rig_p = os.path.join(static, "rigs", rig)
        anim_p = [os.path.join(static, "animations", a) for a in anims]
        if not os.path.isfile(rig_p) or not all(os.path.isfile(p) for p in anim_p):
            continue
        out.append({"name": t, "title": TITLES[t], "rig": "rigs/" + rig,
                    "sample": "models/" + sample if os.path.isfile(os.path.join(static, "models", sample)) else None,
                    "animation_files": ["animations/" + a for a in anims],
                    "clips": sum(len(clips_in(p)) for p in anim_p)})
    return out


def clips(app, template):
    """Every clip of one template, in file order. Raises ValueError for an unknown template."""
    if template not in FILES: raise ValueError("unknown template %r (one of %s)" % (template, ", ".join(FILES)))
    static = os.path.join(app, "static", "animations")
    out = []
    for a in FILES[template][1]:
        p = os.path.join(static, a)
        if os.path.isfile(p): out += clips_in(p)
    return out

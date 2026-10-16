# SPDX-License-Identifier: GPL-3.0-or-later
"""Model Studio settings shared by the CLI, queued jobs and browser."""
import copy
import json
import math
import os
import re
import tempfile
from pathlib import Path

TOOLS = ("lods", "vehicle", "reference", "materials", "foliage", "generate", "validate")
DEFAULTS = {
    "validate": {"every": 1, "loops": [], "seam_mm": 1.0},
    "lods": {"ratios": [1.0, 0.4, 0.15], "format": "glb", "protect_joints": True,
             "max_error": 0.02, "triangles": 0, "normalize": False, "length": 0.0, "height": 0.0, "front": "-y"},
    "vehicle": {"height": 0.0, "length": 6.0, "front": "-y", "assignments": {},
                "pivots": {}, "straighten": True, "carve_missing": False},
    "reference": {"folder": "", "pixels": 768, "turn": 0.0},
    "materials": {"asset": "bark_brown_02", "resolution": "2k", "packing": "orm"},
    "foliage": {"seed": 2718, "cell": 256, "height": 9.0, "wind_base": 0.0,
                "wind_tip": 1.0, "apply_wind": False},
    "generate": {"folder": "", "candidates": 1, "tier": "Gen-2.5-High", "quality": "30000"},
}


def atomic_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def inside(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def validate(tool, values):
    if tool not in TOOLS: raise ValueError("unknown Studio tool")
    if not isinstance(values, dict): raise ValueError("settings must be an object")
    unknown = set(values) - set(DEFAULTS[tool])
    if unknown: raise ValueError("unknown settings: " + ", ".join(sorted(unknown)))
    out = dict(copy.deepcopy(DEFAULTS[tool]), **values)
    def number(key, lo, hi, integer=False):
        v = out[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError("%s must be between %s and %s" % (key, lo, hi))
        if integer and int(v) != v: raise ValueError(key + " must be an integer")
    for k, default in DEFAULTS[tool].items():
        if isinstance(default, bool) and not isinstance(out[k], bool): raise ValueError(k + " must be a boolean")
        if isinstance(default, str) and not isinstance(out[k], str): raise ValueError(k + " must be text")
    if tool == "validate":
        number("every", 1, 120, True); number("seam_mm", 0.01, 100)
        if not isinstance(out["loops"], list) or any(not isinstance(v, str) or not v for v in out["loops"]): raise ValueError("loops must be a list of clip names")
    elif tool == "lods":
        rs = out["ratios"]
        if (not isinstance(rs, list) or not 1 <= len(rs) <= 6 or rs[0] != 1 or
            any(isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or not 0 < r <= 1 for r in rs) or
            any(a <= b for a, b in zip(rs, rs[1:]))):
            raise ValueError("ratios must descend from 1, with 1 to 6 levels")
        if out["format"] not in ("glb", "fbx"): raise ValueError("format must be glb or fbx")
        number("max_error", 0.001, 0.25)
        number("triangles", 0, 10000000, True); number("length", 0, 1000); number("height", 0, 1000)
        if out['height'] and out['length']: raise ValueError('choose height OR length for uniform scaling')
        if out['front'] not in ('-y','+y','+x','-x'): raise ValueError('invalid source front')
    elif tool == "vehicle":
        number("height", 0, 1000); number("length", 0.001, 1000)
        if out["front"] not in ("-y", "+y", "+x", "-x"): raise ValueError("invalid front")
        if not isinstance(out["assignments"], dict) or not set(out["assignments"]) <= {"FL", "FR", "RL", "RR"}:
            raise ValueError("assignments maps FL/FR/RL/RR to source object names")
        if any(not isinstance(v, str) or not v for v in out["assignments"].values()): raise ValueError("invalid wheel object")
        if len(set(out["assignments"].values())) != len(out["assignments"]): raise ValueError("a wheel cannot occupy two corners")
        if not isinstance(out["pivots"], dict) or not set(out["pivots"]) <= {"FL", "FR", "RL", "RR"}: raise ValueError("invalid pivot corners")
        for p in out["pivots"].values():
            if not isinstance(p, list) or len(p) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p):
                raise ValueError("pivots must be three finite coordinates in prepared metres")
    elif tool == "reference":
        number("pixels", 128, 4096, True); number("turn", -360, 360)
    elif tool == "materials":
        if not re.fullmatch(r"[a-z0-9_]{1,100}", out["asset"]): raise ValueError("invalid material asset ID")
        if out["resolution"] not in ("1k", "2k", "4k"): raise ValueError("invalid resolution")
        if out["packing"] not in ("orm", "rah"): raise ValueError("packing must be orm or rah")
    elif tool == "foliage":
        number("seed", 0, 2147483647, True); number("cell", 64, 1024, True)
        number("height", 0.001, 1000); number("wind_base", 0, 1); number("wind_tip", 0, 1)
    elif tool == "generate":
        number("candidates", 1, 8, True)
        for k in ("tier", "quality"):
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", out[k]): raise ValueError("invalid " + k)
    return out


def load(model_dir):
    path = os.path.join(model_dir, "studio.json")
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if os.path.isfile(path) else {}
    return {tool: validate(tool, raw.get(tool, {})) for tool in TOOLS}


def input_folder(folder, model_dir, roots=None):
    path = os.path.realpath(os.path.join(model_dir, folder or "references"))
    if roots and not any(inside(path, root) for root in roots):
        raise ValueError("reference folder must be under the models or work folder")
    if not os.path.isdir(path): raise ValueError("reference folder does not exist: " + path)
    return path


def artifacts(model_dir):
    root = Path(model_dir) / "_studio"
    if not root.exists(): return []
    return [{"path": str(p.relative_to(model_dir)).replace("\\", "/"), "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file() and inside(p, root) and not p.name.endswith(".tmp")]

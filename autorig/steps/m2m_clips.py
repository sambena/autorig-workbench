# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's human clips on a person already rigged on Mixamo, without rigging them again.
#
#   python autorig/cli/run.py m2m-clips <model> --clips "Idle,Walk,Run" [--root-motion] [--out <folder>] [--replace]
#                                    [--rig <download>.fbx] [--source <model>.glb] [--name <Name>] [--fps 30]
#                                    [--preview <file>.glb] [--no-check] [--list]
#
# <model> is a model folder (TornadoTitans/Ice_Age26_Tripo, or a path to one) whose model.json names its game file
# (game_name) and source (model_file), with Mixamo's download in mixamo/<Name>_mixamo.fbx, as mixamo_import.py left
# it; --rig, --source and --name say it outright. The clips come straight from Mesh2Motion's three human libraries,
# static/animations/human-{base,addon,mocap}-animations.glb (178 clips, CC0, (c) 2025 Scott Petrovic:
# https://github.com/Mesh2Motion/mesh2motion-app), in the submodule third_party/mesh2motion-app, the folder
# AUTORIG_M2M_ANIMATIONS names, or AUTORIG_M2M's static/animations (as the other m2m steps read it). A name is
# matched exactly, then ignoring case, spaces and underscores, then by the short names in ALIASES ("Idle" is Idle_A,
# "Run" is Jog); --list prints them all.
#
# Root motion: only a clip whose name ends in exactly " RM" or "_RM" travels (Mesh2Motion keys that travel on its root
# bone, above the pelvis). With --root-motion such a clip keeps it, on Hips, as Mixamo's own clips carry it; without,
# the travel is taken out and the take is named without the suffix. Every other clip plays in place, keeping its
# pelvis bob and sway.
#
# The way mixamo_import.py makes the game file, with one step between:
#   1. fbx_maps.py gives the Mixamo download the source's full-size maps;
#   2. m2m_clips_worker.py bakes each clip onto it (retarget_worker.bake_clip, the retargeter's bake) as a take, then
#      keeps the feet down (Hips raised or lowered to the clip's foot height) and plants the legs (two-bone IK onto
#      the clip's ankles), since a person's legs are not in the library's proportions;
#   3. unity_fbx.py --relative writes it with Unity's axes, texture paths <Name>.fbm/...
# Shipped files are never overwritten: the result is <out>/<Name>_m2m.fbx next to <Name>.fbx (out: the model's
# folder), and <Name>.fbm/ is only written when there is none. --replace writes <Name>.fbx and <Name>.fbm/ instead.
# A preview GLB of the same takes (default <work>/qa/m2m_clips/<Name>_m2m.glb) is then measured by clip_check.py
# (core/clip_quality.py: stretched edges, floor penetration, foot skate) unless --no-check.
# Prints M2M_CLIPS {json}; exits 1 when a step fails, or a clip explodes (EXPLODE_PCT).
import argparse, json, os, re, shutil, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(PKG, "core"), HERE]
import blender  # noqa: E402
import layout  # noqa: E402
from mixamo_import import is_fbx_binary, texture_paths  # noqa: E402

LIBRARIES = ("human-base-animations.glb", "human-addon-animations.glb", "human-mocap-animations.glb")
# A clip "explodes" when an edge opens by more than this share of the model's size (a limb flying off). Any stretched
# edge at all is not the test here: a Mixamo skin tears clip_check's way at the shoulders under Mixamo's own clips
# (Ice's jacket under Breathing Idle, 2026-10-18), so that is reported, not failed.
EXPLODE_PCT = 15.0
# short names for the clips people ask for first; the library's own names always work
ALIASES = {"idle": "Idle_A", "run": "Jog"}


def animations_dir():
    for d in (os.environ.get("AUTORIG_M2M_ANIMATIONS"),
              os.environ.get("AUTORIG_M2M") and os.path.join(os.environ["AUTORIG_M2M"], "static", "animations"),
              os.path.join(os.path.dirname(PKG), "third_party", "mesh2motion-app", "static", "animations")):
        if d and all(os.path.isfile(os.path.join(d, f)) for f in LIBRARIES):
            return d
    raise FileNotFoundError("Mesh2Motion's human clips not found: run `git submodule update --init "
                            "third_party/mesh2motion-app`, or set AUTORIG_M2M_ANIMATIONS to its static/animations")


def glb_json(path):
    """The JSON chunk of a GLB (no Blender needed)."""
    with open(path, "rb") as fh:
        head = fh.read(20)
        if head[:4] != b"glTF":
            raise ValueError("not a GLB: " + path)
        n = struct.unpack("<I", head[12:16])[0]
        return json.loads(fh.read(n))


def catalogue(folder=None):
    """[{name, library, glb, seconds, root_motion}] for every human clip, in the libraries' order."""
    folder = folder or animations_dir()
    out = []
    for lib in LIBRARIES:
        path = os.path.join(folder, lib)
        j = glb_json(path)
        for a in j.get("animations", []):
            ends = [j["accessors"][a["samplers"][c["sampler"]]["input"]].get("max", [0])[0] for c in a["channels"]]
            out.append({"name": a["name"], "library": lib, "glb": path, "seconds": round(max(ends or [0]), 3),
                        "root_motion": is_root_motion(a["name"])})
    return out


def is_root_motion(name):
    """Only a name ending in exactly " RM" or "_RM" travels (Hit_Knockback_RM, Crawl RM; not Storm, not Rm)."""
    return name.endswith(" RM") or name.endswith("_RM")


def in_place_name(name):
    return name[:-3] if is_root_motion(name) else name


def _key(name):
    return re.sub(r"[\s_]+", "", name).lower()


def resolve(names, clips):
    """The catalogue entry for each asked-for name, or ValueError naming the ones not found (with near misses)."""
    out, missing = [], []
    for want in names:
        hit = [c for c in clips if c["name"] == want] or [c for c in clips if _key(c["name"]) == _key(want)]
        if not hit and _key(want) in ALIASES:
            hit = [c for c in clips if c["name"] == ALIASES[_key(want)]]
        if hit:
            out.append(hit[0])
            continue
        near = [c["name"] for c in clips if _key(want) in _key(c["name"])][:8]
        missing.append("%s (%s)" % (want, ", ".join(near) if near else "no similar clip"))
    if missing:
        raise ValueError("no Mesh2Motion human clip named: " + "; ".join(missing) + ". `--list` shows them all")
    return out


def plan_takes(found, root_motion):
    """[{glb, action, take, strip_travel}] for the worker: travel kept only on *_RM clips with --root-motion."""
    out, seen = [], set()
    for c in found:
        strip = c["root_motion"] and not root_motion
        take = in_place_name(c["name"]) if strip else c["name"]
        take = re.sub(r"\s+", "_", take)
        if take in seen:
            continue
        seen.add(take)
        out.append({"glb": c["glb"], "action": c["name"], "take": take, "strip_travel": strip})
    return out


def find_model(model):
    """A model folder: the path itself, one under the models root, or a model of that name in any group."""
    for d in (model, os.path.join(layout.ROOT, model)):
        if os.path.isdir(d):
            return os.path.abspath(d)
    for g, m in layout.all_models():
        if m == layout.leaf(model):
            return layout.pack_dir(f"{g}/{m}" if g else m)
    raise FileNotFoundError("no model folder '%s' (under %s)" % (model, layout.ROOT))


def model_inputs(folder, rig=None, source=None, name=None):
    """(rig, source, name) for a model folder: model.json's game_name and model_file, mixamo/<Name>_mixamo.fbx."""
    card = {}
    try:
        with open(os.path.join(folder, "model.json"), encoding="utf-8") as fh:
            card = json.load(fh)
    except (OSError, ValueError):
        pass
    name = name or card.get("game_name")
    if not rig:
        mx = os.path.join(folder, "mixamo")
        cands = sorted(f for f in os.listdir(mx) if f.lower().endswith(".fbx")) if os.path.isdir(mx) else []
        pick = [f for f in cands if name and f == name + "_mixamo.fbx"] or (cands if len(cands) == 1 else [])
        if not pick:
            raise FileNotFoundError("no Mixamo download in %s (mixamo/<Name>_mixamo.fbx); give --rig" % folder)
        rig = os.path.join(mx, pick[0])
    if not name:
        name = re.sub(r"_mixamo$", "", os.path.splitext(os.path.basename(rig))[0])
    if not source and card.get("model_file") and os.path.isfile(os.path.join(folder, card["model_file"])):
        source = os.path.join(folder, card["model_file"])
    return os.path.abspath(rig), (os.path.abspath(source) if source else None), name


def output_names(out, name, replace):
    """(game FBX, its .fbm folder) to write: <Name>_m2m.fbx beside the shipped file, or <Name>.fbx with --replace."""
    return os.path.join(out, (name if replace else name + "_m2m") + ".fbx"), os.path.join(out, name + ".fbm")


def parse_checks(text):
    """Each CLIP_CHECK line clip_check.py printed, reduced to what this step reports."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("CLIP_CHECK "):
            continue
        e = json.loads(line[len("CLIP_CHECK "):])
        sk = e.get("foot_skate") or {}
        out[e["name"]] = {"skate": sk.get("score"), "skate_ratio": sk.get("ratio"),
                          "ground_speed": sk.get("ground_speed"), "tear_edges": e.get("tear_edges", 0),
                          "worst_gap_pct": round(e.get("worst_gap_pct", 0.0), 2),
                          "floor_penetration": round(e.get("floor_penetration", 0.0), 4),
                          "errors": e.get("errors", [])}
    return out


def run(rig, source, name, out, clips, root_motion=False, replace=False, fps=30.0, preview=None, check=True):
    if not is_fbx_binary(rig):
        return {"error": "not a whole binary FBX: " + rig}
    takes = plan_takes(resolve(clips, catalogue()), root_motion)
    fbx_out, fbm_out = output_names(out, name, replace)
    preview = preview or os.path.join(layout.work_dir("qa", "m2m_clips"), name + "_m2m.glb")
    work = tempfile.mkdtemp(prefix="m2m-clips-")
    result = {"rig": rig, "name": name, "takes": [t["take"] for t in takes], "out": fbx_out, "preview": preview}
    try:
        made = os.path.join(work, name + ".fbx")
        if source:
            r = blender.run("fbx_maps.py", "--fbx", rig, "--source", source, "--name", name, "--out", work)
            if not os.path.isfile(made) and "no base colour image" not in r.stdout + r.stderr:
                return {**result, "error": "fbx-maps wrote nothing", "log": (r.stdout + r.stderr)[-600:]}
        if not os.path.isfile(made):
            shutil.copyfile(rig, made)                 # an untextured source: the rig as it came
        clipped = os.path.join(work, name + "_clips.fbx")
        cfg = {"target_fbx": made, "out_fbx": clipped, "preview_glb": preview, "fps": fps, "clips": takes}
        r = blender.run("m2m_clips_worker.py", json.dumps(cfg))
        log = r.stdout + r.stderr
        worker = next((json.loads(l.split(" ", 1)[1]) for l in r.stdout.splitlines()
                       if l.startswith("M2M_CLIPS_WORKER {")), None)
        if worker is None or not os.path.isfile(clipped):
            return {**result, "error": "m2m_clips_worker wrote nothing", "log": log[-1200:]}
        result["baked"] = worker["takes"]
        unity = os.path.join(work, os.path.basename(fbx_out))
        r = blender.run("unity_fbx.py", clipped, unity, "--relative")
        if not os.path.isfile(unity):
            return {**result, "error": "unity_fbx wrote nothing", "log": (r.stdout + r.stderr)[-600:]}
        u = next((json.loads(l.split(" ", 1)[1]) for l in r.stdout.splitlines() if l.startswith("UNITY_FBX ")), {})
        result["fbx_takes"] = u.get("takes", [])
        os.makedirs(out, exist_ok=True)
        shutil.copyfile(unity, fbx_out)
        fbm = os.path.join(work, name + ".fbm")
        if os.path.isdir(fbm) and (replace or not os.path.isdir(fbm_out)):
            shutil.rmtree(fbm_out, ignore_errors=True)
            shutil.copytree(fbm, fbm_out)
        paths = texture_paths(fbx_out)
        result["relative_paths"] = [p for p in paths if p.replace("\\", "/").startswith(name + ".fbm/")]
        result["ok"] = set(result["fbx_takes"]) >= set(result["takes"]) and (bool(result["relative_paths"]) or
                                                                           not os.path.isdir(fbm_out))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if check and os.path.isfile(preview):
        report = os.path.splitext(preview)[0] + ".quality.json"
        r = blender.run("clip_check.py", preview, "--report", report, timeout=3600)
        result["quality"] = parse_checks(r.stdout)
        result["quality_report"] = report
        result["exploded"] = sorted(c for c, q in result["quality"].items() if q["worst_gap_pct"] > EXPLODE_PCT)
        if result["exploded"] or not result["quality"]:
            result["ok"] = False
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="Autorig Workbench: Mesh2Motion human clips on a Mixamo-rigged person.")
    p.add_argument("model", nargs="?", help="model folder (group/name or a path); optional with --rig and --name")
    p.add_argument("--clips", help='comma list of clip names, e.g. "Idle,Walk,Run" or "Idle_A,Walk,Jog,Roll_RM"')
    p.add_argument("--root-motion", action="store_true", help="*_RM clips keep their travel (on Hips)")
    p.add_argument("--out", help="folder to write in (default: the model's folder)")
    p.add_argument("--replace", action="store_true", help="write <Name>.fbx and <Name>.fbm/ over the shipped files")
    p.add_argument("--rig", help="the Mixamo download (FBX Binary); default <model>/mixamo/<Name>_mixamo.fbx")
    p.add_argument("--source", help="the generated model, for its full-size maps; default model.json's model_file")
    p.add_argument("--name", help="the engine's resource name; default model.json's game_name")
    p.add_argument("--fps", type=float, default=30.0, help="frame rate of the takes (Mixamo's clips are 30)")
    p.add_argument("--preview", help="preview GLB to write (default <work>/qa/m2m_clips/<Name>_m2m.glb)")
    p.add_argument("--no-check", action="store_true", help="skip clip_check.py on the preview")
    p.add_argument("--list", action="store_true", help="list Mesh2Motion's human clips and exit")
    a = p.parse_args(argv)
    if a.list:
        for c in catalogue():
            print("%-28s %6.2fs  %s%s" % (c["name"], c["seconds"], c["library"], "  (root motion)" if c["root_motion"] else ""))
        return 0
    if not a.clips or not (a.model or (a.rig and a.name)):
        p.error("give a model (or --rig and --name) and --clips")
    folder = find_model(a.model) if a.model else None
    rig, source, name = model_inputs(folder, a.rig, a.source, a.name) if folder else \
        (os.path.abspath(a.rig), os.path.abspath(a.source) if a.source else None, a.name)
    out = os.path.abspath(a.out or folder or os.path.dirname(rig))
    try:
        result = run(rig, source, name, out, [c.strip() for c in a.clips.split(",") if c.strip()],
                     root_motion=a.root_motion, replace=a.replace, fps=a.fps,
                     preview=os.path.abspath(a.preview) if a.preview else None, check=not a.no_check)
    except (ValueError, FileNotFoundError) as e:
        result = {"error": str(e)}
    for clip, q in sorted((result.get("quality") or {}).items()):
        print("M2M_CLIPS %-20s skate %-7s tears %-4s worst gap %5.2f%%  floor %-7s %s" % (
            clip, q["skate"], q["tear_edges"], q["worst_gap_pct"], q["floor_penetration"], ", ".join(q["errors"])))
    print("M2M_CLIPS " + json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

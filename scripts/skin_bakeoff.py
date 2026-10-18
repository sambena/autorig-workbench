# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the M2b skinning bake-off. Each model is fitted to its Mesh2Motion template once, then skinned
# both ways on that same skeleton: "autorig" (steps/m2m_rig.skin: bone heat on a voxel copy, the gate, smoothing,
# the leg confine) and "m2m" (Mesh2Motion's own, core/m2m_skin.py). Each result is graded by steps/audit.py, and its
# GLB checked by steps/clip_check.py on three of the template's clips. Results: docs/SKIN-BAKEOFF.md.
#
#   python3 scripts/skin_bakeoff.py --out ~/bakeoff [--only Horse_Bay,human_sample] [--jobs 3] [--models <asset-packs>]
#           [--methods autorig] [--rig-args "--blend 0.8"]       (one method only: quicker while working on it; the m2m run needs the autorig one)
#
# Run it on the autorig box (model work runs there). It writes nothing into asset-packs: every output goes under
# --out, <out>/<label>/<method>/. The m2m run is given the autorig run's fitted joints (--joints), so both skin the
# very same skeleton. <out>/results.json collects everything; the table it prints is the one in SKIN-BAKEOFF.md.
import argparse, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEPS = os.path.join(REPO, "autorig", "steps")
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import m2m_templates as T  # noqa: E402

SAMPLES = os.path.join(REPO, "third_party", "mesh2motion-app", "static", "models")
# (label, template, source under the asset-packs folder (or "sample:" for Mesh2Motion's own), height in metres
# (0: as it is), what it is)
MODELS = [
    ("human_sample", "human", "sample:model-human.glb", 0, "M2M sample"),
    ("Ice_Age26", "human", "TornadoTitans/Ice_Age26_Tripo/Ice_Age26_Tripo.glb", 1.8, "Tripo person"),
    ("Laree_Age32", "human", "TornadoTitans/Laree_Age32_Tripo/Laree_Age32_Tripo.glb", 1.7, "Tripo person"),
    ("horse_sample", "horse", "sample:model-horse.glb", 0, "M2M sample"),
    ("Horse_Bay", "horse", "TornadoTitans/Horse_Bay_Tripo/Horse_Bay_Tripo.glb", 1.6, "Tripo horse"),
    ("Cow_Holstein", "horse", "TornadoTitans/Cow_Holstein_Tripo/Cow_Holstein_Tripo.glb", 1.5, "Tripo cow"),
    ("fox_sample", "fox", "sample:model-fox.glb", 0, "M2M sample"),
    ("Dog_BlackTan", "fox", "TornadoTitans/Dog_BlackTan_Tripo/Dog_BlackTan_Tripo.glb", 0.6, "Tripo dog"),
    ("Cat_Tabby", "fox", "TornadoTitans/Cat_Tabby_Tripo/Cat_Tabby_Tripo.glb", 0.3, "Tripo cat"),
    ("bird_sample", "bird", "sample:model-bird.glb", 0, "M2M sample"),
    ("Chicken_Hen", "bird", "TornadoTitans/Chicken_Hen_Tripo/Chicken_Hen_Tripo.glb", 0.45, "Tripo hen"),
    ("Chicken_Rooster", "bird", "TornadoTitans/Chicken_Rooster_Tripo/Chicken_Rooster_Tripo.glb", 0.6, "Tripo rooster"),
    ("dragon_sample", "dragon", "sample:model-dragon.glb", 0, "M2M sample"),
    ("Dragon", "dragon", "Smeltdown/dragon/Dragon.fbx", 2.0, "Smeltdown dragon"),
    ("Drake", "dragon", "Smeltdown/drake/Drake.fbx", 1.5, "Smeltdown drake"),
    ("kaiju_sample", "kaiju", "sample:model-kaiju.glb", 0, "M2M sample"),
    ("spider_sample", "spider", "sample:model-spider.glb", 0, "M2M sample"),
    ("Crawler", "spider", "Smeltdown/crawler/Crawler.fbx", 0.5, "Smeltdown hexapod"),
    ("reef_crab", "spider", "Heartroot/reef_crab/tripo_convert_a26c3cfb-4b84-4c49-aed3-614135f3c96f.fbx", 0.4,
     "Tripo crab"),
    ("snake_sample", "snake", "sample:model-snake.glb", 0, "M2M sample"),
    ("glass_eel", "snake", "Heartroot/glass_eel/tripo_convert_8b3f2a4e-67b2-40a6-b6fc-e2ba3bfe9bd4.fbx", 0.3,
     "Tripo eel"),
    ("sea_serpent", "snake", "Heartroot/sea_serpent/tripo_convert_fa9b8772-bcb1-44f0-bdc5-af93e2744360.fbx", 0.6,
     "Tripo serpent (coiled)"),
    ("shark_sample", "shark", "sample:model-shark.glb", 0, "M2M sample"),
    ("Shark", "shark", "TornadoTitans/Shark_Tripo/Shark_Tripo.glb", 0.8, "Tripo shark"),
    ("megalodon", "shark", "Heartroot/the_megalodon/tripo_convert_1414b4ed-40c3-4662-b60f-070bdc5544c5.fbx", 1.5,
     "Tripo shark"),
]
METHODS = ("autorig", "m2m")
AUDIT_KEYS = ("bleed_pct", "combined_tears", "bend_tears", "head_pct", "stand_folds", "max_influences")


def clips_for(t):
    """Three of the template's clips, by role: idle, walk and one more (each a different clip)."""
    out = []
    for role, clip in T.TEMPLATES[t]["roles"].items():
        if clip not in [c for _, c in out]: out.append((role, clip))
    return ",".join(r for r, _ in out[:3])


def blender(script, argv, log):
    exe = os.environ.get("AUTORIG_BLENDER") or "blender"
    cmd = [exe, "-b", "--factory-startup", "--python", os.path.join(STEPS, script), "--"] + argv
    with open(log, "w") as fh:
        r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=3600)
    return r.returncode


def tagged(log, tag):
    for line in open(log, errors="replace"):
        if line.startswith(tag + " "):
            return json.loads(line[len(tag) + 1:])
    return None


def one(row, models, out, methods=METHODS, extra=()):
    label, t, src, height, what = row
    path = os.path.join(SAMPLES, src[7:]) if src.startswith("sample:") else os.path.join(models, src)
    res = {"label": label, "template": t, "source": src, "what": what, "methods": {}}
    if not os.path.exists(path):
        res["error"] = "source not found: " + path
        return res
    for method in methods:
        d = os.path.join(out, label, method)
        os.makedirs(d, exist_ok=True)
        argv = ["--in", path, "--name", label, "--out", d, "--template", t, "--height", str(height),
                "--clips", clips_for(t), "--skin", method, "--format", "glb,fbx", "--target", "generic",
                "--no-preview"] + list(extra)
        if method != METHODS[0]:
            joints = os.path.join(out, label, METHODS[0], label + "_m2m.json")
            if os.path.exists(joints): argv += ["--joints", joints]
        code = blender("m2m_rig.py", argv, os.path.join(d, "rig.log"))
        rig = tagged(os.path.join(d, "rig.log"), "M2M_RIG") or {}
        m = {"exit": code}
        if rig.get("error") or code != 0:
            m["error"] = rig.get("error") or "m2m_rig exited %d" % code
            res["methods"][method] = m
            continue
        aud = rig.get("audit") or {}
        m.update(grade=aud.get("grade"), **{k: (aud.get("checks") or {}).get(k) for k in AUDIT_KEYS},
                 skin_seconds=rig.get("skin_seconds"), bare=rig.get("bare_vertices"),
                 triangles=rig.get("triangles"), clips=[c["m2m"] for c in rig.get("clips", [])])
        glb = os.path.join(d, label + ".glb")
        if os.path.exists(glb):
            rep = os.path.join(d, "clips.json")
            blender("clip_check.py", [glb, "--report", rep], os.path.join(d, "clips.log"))
            if os.path.exists(rep):
                q = json.load(open(rep))
                m["clip_checks"] = [{"name": c["name"], "tear_edges": c.get("tear_edges"),
                                     "worst_gap_pct": c.get("worst_gap_pct"),
                                     "collapsed": c.get("collapsed_triangles"),
                                     "skate": (c.get("foot_skate") or {}).get("score"),
                                     "errors": c.get("errors")} for c in q.get("clips", [])]
        res["methods"][method] = m
    return res


def fmt(v, nd=1):
    if v is None: return "-"
    return ("%." + str(nd) + "f") % v if isinstance(v, float) else str(v)


def clip_summary(m):
    cs = m.get("clip_checks") or []
    if not cs: return "-"
    tears = sum(c.get("tear_edges") or 0 for c in cs)
    skate = [c["skate"] for c in cs if c.get("skate") is not None]
    return "%d / %s" % (tears, fmt(max(skate), 2) if skate else "-")


def table(rows):
    lines = ["| Model | Template | Method | Grade | Bleed % | Combined / bend tears | Head % | Stand folds | "
             "Clip tears / worst skate | Skin s |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for method in METHODS:
            m = r["methods"].get(method) or {"error": r.get("error", "not run")}
            if m.get("error"):
                lines.append("| %s | %s | %s | error: %s |  |  |  |  |  |  |" % (r["label"], r["template"], method,
                                                                              m["error"][:80]))
                continue
            lines.append("| %s | %s | %s | %s | %s | %s / %s | %s | %s | %s | %s |" % (
                r["label"], r["template"], method, m.get("grade"), fmt(m.get("bleed_pct"), 2),
                fmt(m.get("combined_tears")), fmt(m.get("bend_tears")), fmt(m.get("head_pct")),
                fmt(m.get("stand_folds")), clip_summary(m), fmt(m.get("skin_seconds"), 1)))
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--models", default=os.environ.get("AUTORIG_MODELS") or os.path.expanduser("~/src/asset-packs"))
    p.add_argument("--only", default="")
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--methods", default=",".join(METHODS))
    p.add_argument("--rig-args", dest="rig_args", default="", help="more m2m_rig arguments, e.g. \"--blend 0.8\"")
    a = p.parse_args()
    methods = [m for m in METHODS if m in a.methods.split(",")]
    out = os.path.abspath(os.path.expanduser(a.out))
    os.makedirs(out, exist_ok=True)
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    rows = [r for r in MODELS if not only or r[0] in only]
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        results = list(pool.map(lambda r: one(r, a.models, out, methods, a.rig_args.split()), rows))
    path = os.path.join(out, "results.json")
    old = json.load(open(path)) if os.path.exists(path) else []
    for r in results:            # a method not run this time keeps its last result
        prev = next((x for x in old if x["label"] == r["label"]), None)
        if prev:
            for m, v in prev.get("methods", {}).items(): r["methods"].setdefault(m, v)
    keep = [r for r in old if r["label"] not in {x["label"] for x in results}]
    order = [r[0] for r in MODELS]
    allr = sorted(keep + results, key=lambda r: order.index(r["label"]) if r["label"] in order else 999)
    with open(path, "w") as fh:
        json.dump(allr, fh, indent=1)
    print(table(allr))
    print("SKIN_BAKEOFF " + json.dumps({"models": len(allr), "out": out}))


if __name__ == "__main__":
    main()

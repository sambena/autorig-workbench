# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: CLI step to suggest an archetype and placed skeleton for a model.
#
#   blender -b --python autorig/steps/suggest_step.py -- <model> [-out <dir>]
#
import bpy, sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import rerig, geo, suggest, layout


def run_suggest(key):
    path = rerig.find_fbx(key)
    if not path:
        return {"model": key, "error": "no source export found"}

    mesh, joints = rerig.load(path)
    rerig.normalise(mesh, joints or {}, {"kind": "build", "forward": "auto"})
    s = geo.Surface(mesh)

    raw_tips = s.tips(most=18, least=0.12)
    norm_tips = [s.norm(s.co[v]) for v, _ in raw_tips]

    # Load survey data if available
    surv_path = os.path.join(layout.work_dir("survey"), key + ".json")
    surv = None
    if os.path.exists(surv_path):
        try:
            with open(surv_path, encoding="utf-8") as fh:
                surv = json.load(fh)
        except Exception:
            pass

    joint_list = [{"name": k, "head": [float(c) for c in v["pos"]], "parent": v.get("parent")} for k, v in (joints or {}).items()]

    res = suggest.suggest_skeleton(
        name=key,
        source_data={
            "size": [float(v) for v in s.size],
            "lo": [float(v) for v in s.lo],
            "hi": [float(v) for v in s.hi],
            "joints": joint_list
        },
        survey_data=surv,
        tips=norm_tips,
        proportions=[float(v) for v in s.size],
        forward="auto",                 # the tips are in the frame normalise's auto facing turned the mesh to
    )
    res["model"] = key
    res["tips_count"] = len(norm_tips)
    return res


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not a:
        sys.exit("usage: blender -b --python suggest_step.py -- <model>")
    key = a[0]
    out_dir = a[a.index("-out") + 1] if "-out" in a else None

    res = run_suggest(key)
    out_json = json.dumps(res, indent=2)
    print("SUGGEST " + out_json)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, key + "_suggest.json"), "w", encoding="utf-8") as fh:
            fh.write(out_json)


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: CLI step to suggest an archetype and placed skeleton for a model.
#
#   blender -b --python autorig/steps/suggest_step.py -- <model> [-out <dir>] [-no-vote]
#
# Beside the tips, two optional opinions on what the model is (core/suggest.py weighs them, and says which it used):
#   body plan     core/body_plan.py on the mesh, with the trimesh extra
#   picture vote  four renders scored by core/picture_vote.py, with the open-clip extra (in Blender's Python run on
#                 its own, so torch never loads into Blender); -no-vote skips it
# Without the extras the suggestion is the tips' alone, as before.
import bpy, sys, os, json, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import rerig, geo, suggest, layout, extras, body_plan

# picture vote views: the directions the camera looks along (the model faces -Y)
VOTE_VIEWS = [("front", (0, 1, 0)), ("side", (-1, 0, 0)), ("q1", (-0.55, 0.7, -0.45)), ("q2", (0.6, 0.7, -0.3))]


def log(msg):
    print("SUGGEST_LOG " + msg, flush=True)


def plan_of(mesh):
    """The body-plan measures (core/body_plan.py), or None without the trimesh extra."""
    extras.activate()
    if not extras.have("trimesh"):
        log("body plan: skipped (the trimesh extra is not installed)")
        return None
    import numpy as np
    me = mesh.data
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, dtype=np.float64); me.vertices.foreach_get("co", co)
    tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int64); me.loop_triangles.foreach_get("vertices", tri)
    return body_plan.measure(co, tri, log=log)


def picture_vote(mesh, key, out_dir):
    """Renders the model from four sides and scores the pictures with OpenCLIP (core/picture_vote.py) in Blender's
    Python run on its own. None without the open-clip extra or when the vote fails."""
    if not extras.installed("open-clip"):
        log("picture vote: skipped (the open-clip extra is not installed)")
        return None
    import facing
    pics = os.path.join(out_dir or layout.work_dir("suggest"), key + "_views")
    os.makedirs(pics, exist_ok=True)
    facing.scene(320)
    sh = bpy.context.scene.display.shading
    sh.color_type = 'TEXTURE'
    sh.background_type = 'VIEWPORT'             # a light backdrop: the classifier reads a black one as night/space
    sh.background_color = (0.82, 0.82, 0.82)
    paths = facing.shoot(mesh, pics, key, views=VOTE_VIEWS)
    try:
        r = extras.run_python([os.path.join(HERE, "..", "core", "picture_vote.py")] + paths,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    except Exception as e:
        log("picture vote: failed to run (%s)" % e)
        return None
    m = re.search(r"^PICTURE_VOTE (.*)$", r.stdout or "", re.M)
    vote = json.loads(m.group(1)) if m else {"error": (r.stderr or "no answer").strip()[-300:]}
    if vote.get("error"):
        log("picture vote: failed (%s)" % vote["error"])
        return None
    vote["views"] = [os.path.basename(p) for p in paths]
    return vote


def run_suggest(key, out_dir=None, vote=True):
    path = rerig.find_fbx(key)
    if not path:
        return {"model": key, "error": "no source export found"}

    mesh, joints = rerig.load(path)
    rerig.normalise(mesh, joints or {}, {"kind": "build", "forward": "auto"})
    s = geo.Surface(mesh)

    plan = plan_of(mesh)
    picture = picture_vote(mesh, key, out_dir) if vote else None

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
        body_plan=plan,
        picture=picture,
    )
    res["model"] = key
    res["tips_count"] = len(norm_tips)
    # which opinions this step could weigh (the spec editor measures again when an older file has no such record)
    res["opinions"] = {"tips": True, "body_plan": plan is not None, "picture": picture is not None}
    return res


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not a:
        sys.exit("usage: blender -b --python suggest_step.py -- <model>")
    key = a[0]
    out_dir = a[a.index("-out") + 1] if "-out" in a else None

    res = run_suggest(key, out_dir, vote="-no-vote" not in a)
    out_json = json.dumps(res, indent=2)
    print("SUGGEST " + out_json)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, key + "_suggest.json"), "w", encoding="utf-8") as fh:
            fh.write(out_json)


if __name__ == "__main__":
    main()

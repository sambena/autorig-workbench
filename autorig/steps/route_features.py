# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what Auto (m2m_rig --template auto) reads off a model before it picks a route, saved so the
# choice can be replayed in plain Python (cli/route_eval.py) while suggest.py, body_plan.py and m2m_templates.choose
# change, without a Blender run per try.
#
#   blender -b --python autorig/steps/route_features.py -- --out <dir> model[,model...]
#
# Writes <dir>/<model>.json: the source, its height, the tips and bounds suggest.py takes, the body plan, and the
# measures choose() takes (as m2m_rig.run computes them; never the model's own rig.json, which is the answer).
import json, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "core")]
import m2m_rig as M

A = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out = A[A.index("--out") + 1]
models = [m for a in A if not a.startswith("--") and a != out for m in a.split(",") if m]
os.makedirs(out, exist_ok=True)


def features(model):
    import layout, geo, suggest_step
    folder = layout.pack_dir(model)
    card = {}
    try: card = json.load(open(os.path.join(folder, "model.json"), encoding="utf-8"))
    except (OSError, ValueError): pass
    src = os.path.join(folder, card["model_file"]) if card.get("model_file") else layout.raw_source(model)
    if not src or not os.path.exists(src): return {"model": model, "error": "no source"}
    height = float(card.get("metres_high") or 0)
    mesh, src_joints = M.source_mesh(src, model, 15000)
    facing = M.face_minus_y(mesh, "auto", "anatomy", src_joints)
    top = M.stand(mesh, height)
    plan = None
    try: plan = suggest_step.plan_of(mesh)
    except Exception as e: plan = {"error": repr(e)}
    s = geo.Surface(mesh)
    tips = [list(s.norm(s.co[v])) for v, _ in s.tips(most=18, least=0.12)]
    pts = [v.co for v in mesh.data.vertices]
    meas = dict(M.body_measures(pts, top), tail=round(M.tail_share(pts, top), 3))
    if height: meas["metres"] = round(top, 3)
    r = M.tail_reach(pts)
    if r is not None: meas["tail_reach"] = r
    return {"model": model, "group": layout.group_of(model), "source": os.path.relpath(src, folder), "height": height,
            "top": round(top, 4), "facing": facing, "had_rig": bool(src_joints), "vertices": len(pts),
            "source_data": {"size": list(s.size), "lo": list(s.lo), "hi": list(s.hi), "joints": []},
            "tips": tips, "proportions": list(s.size), "plan": plan, "measures": meas}


def rigged_bones(model):
    """The committed rig's bone names (for the ground truth only; Auto never sees them)."""
    import bpy, layout, glob
    rd = layout.rigged_dir(model)
    blends = sorted(glob.glob(os.path.join(glob.escape(rd), "*.blend")))
    if not blends: return None
    with bpy.data.libraries.load(blends[0]) as (src, dst): dst.armatures = list(src.armatures)
    names = [b.name for arm in dst.armatures if arm for b in arm.bones]
    return {"file": os.path.basename(blends[0]), "bones": names}


for m in models:
    t = time.time()
    try:
        f = features(m)
    except BaseException as e:
        f = {"model": m, "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-1500:]}
    try: f["rigged"] = rigged_bones(m)
    except Exception as e: f["rigged"] = {"error": repr(e)}
    f["seconds"] = round(time.time() - t, 1)
    with open(os.path.join(out, m + ".json"), "w", encoding="utf-8", newline="\n") as fh: json.dump(f, fh, indent=1, default=str)
    print("ROUTE_FEATURES %s %s %.0fs" % (m, f.get("error") or "ok", f["seconds"]), flush=True)
print("ROUTE_FEATURES_DONE", flush=True)

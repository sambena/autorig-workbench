# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Meshy (https://www.meshy.ai) through its official HTTP API (https://docs.meshy.ai/en/api):
# text, one picture or up to four pictures of one object to a model.
#
#   POST /openapi/v2/text-to-3d {mode: preview}      spends: the mesh
#   POST /openapi/v2/text-to-3d {mode: refine}       spends: its texture (part of the same confirmed estimate)
#   POST /openapi/v1/image-to-3d, multi-image-to-3d  spend: mesh and texture in one task
#   GET  the same paths + /{id}                      free: status, progress, model_urls.glb, consumed_credits
#   GET  /openapi/v1/balance                         free
#
# Credits from https://docs.meshy.ai/en/api/pricing (checked 2026-10-18). Meshy refunds a failed task.
import os

from . import common

NAME, LABEL, UNIT = "meshy", "Meshy", "credits"
DOCS = "https://docs.meshy.ai/en/api"
MODELS = ("meshy-7.1", "meshy-6", "meshy-6-lite")
MESH = {"meshy-7.1": 20, "meshy-6": 20, "meshy-6-lite": 5}
# a picture's mesh with its texture, by texture resolution (None: that model can't)
TEXTURED = {"meshy-7.1": {"2k": 30, "4k": 30, "8k": 35}, "meshy-6": {"2k": 30, "4k": 30, "8k": 35},
            "meshy-6-lite": {"2k": 15, "4k": None, "8k": None}}
REFINE = {"2k": 10, "4k": 10, "8k": 15}
OPTIONS = ("ai_model", "texture", "enable_pbr", "texture_resolution", "geometry_resolution", "target_polycount",
           "should_remesh", "pose_mode", "texture_prompt")
DEFAULTS = {"ai_model": "meshy-7.1", "texture": True, "texture_resolution": "2k"}
PATHS = {"text": "/openapi/v2/text-to-3d", "image": "/openapi/v1/image-to-3d", "multi": "/openapi/v1/multi-image-to-3d"}


def base():
    return common.base_url("AUTORIG_MESHY_URL", "https://api.meshy.ai")


def estimate(prompt, images, options):
    o = dict(DEFAULTS, **common.pick(options, OPTIONS, "Meshy"))
    m = common.one_of(o["ai_model"], MODELS, "ai_model")
    for k in ("texture", "enable_pbr", "should_remesh"):
        if k in o: common.boolean(o[k], k)
    res = common.one_of(o["texture_resolution"], ("2k", "4k", "8k"), "texture_resolution")
    geo = common.one_of(o.get("geometry_resolution", "standard"), ("standard", "2k", "4k"), "geometry_resolution")
    if "pose_mode" in o: common.one_of(o["pose_mode"], ("", "a-pose", "t-pose"), "pose_mode")
    if "target_polycount" in o and (isinstance(o["target_polycount"], bool) or not isinstance(o["target_polycount"], int)):
        raise ValueError("target_polycount is a whole number")
    if geo != "standard" and m == "meshy-6-lite": raise ValueError("geometry_resolution needs meshy-7.1 or meshy-6")
    if images:
        if len(images) > 4: raise ValueError("Meshy takes up to 4 pictures")
        kind = "image" if len(images) == 1 else "multi"
        if o["texture"]:
            c = TEXTURED[m][res]
            if c is None: raise ValueError("%s textures at 2k only" % m)
            lines = [("%s to model with %s texture (%s)" % ("picture" if kind == "image" else "pictures", res, m), c)]
        else:
            lines = [("%s to model, mesh only (%s)" % ("picture" if kind == "image" else "pictures", m), MESH[m])]
    else:
        if not prompt or not isinstance(prompt, str): raise ValueError("give prompt or images")
        if len(prompt) > 800: raise ValueError("Meshy's prompt is up to 800 characters")
        kind = "text"
        lines = [("text to model, mesh (%s)" % m, MESH[m])]
        if o["texture"]:
            if res != "2k" and m == "meshy-6-lite": raise ValueError("%s textures at 2k only" % m)
            lines.append(("texture, %s" % res, REFINE[res]))
    if geo != "standard": lines.append(("%s geometry (Ultra)" % geo, 5))
    return {"kind": kind, "credits": sum(c for _, c in lines), "breakdown": lines, "options": o}


def _mesh_body(o):
    body = {"ai_model": o["ai_model"], "target_formats": ["glb"]}
    for k in ("geometry_resolution", "target_polycount", "should_remesh", "pose_mode"):
        if k in o: body[k] = o[k]
    return body


def _create(key, kind, body):
    tid = common.call("POST", base() + PATHS[kind], key, json_body=body).get("result")
    if not tid: raise common.GeneratorError("Meshy gave no task id")
    return tid


def submit(key, est, prompt, images):
    """Spends: the mesh now and, for text with a texture, the texture once the mesh is done (poll). Only
    gui/generate_api.py calls this, after a confirmed estimate."""
    o, kind = est["options"], est["kind"]
    body = _mesh_body(o)
    if kind == "text":
        body.update(mode="preview", prompt=prompt)
    else:
        body.update(should_texture=o["texture"])
        if o["texture"]:
            body["texture_resolution"] = o["texture_resolution"]
            if o.get("enable_pbr"): body["enable_pbr"] = True
            if o.get("texture_prompt"): body["texture_prompt"] = o["texture_prompt"]
        if kind == "image": body["image_url"] = images[0].data_uri()
        else: body["image_urls"] = [im.data_uri() for im in images]
    tid = _create(key, kind, body)
    return {"task_id": tid, "kind": kind, "refine": kind == "text" and bool(o["texture"]),
            "refine_body": ({"mode": "refine", "texture_resolution": o["texture_resolution"],
                             **({"enable_pbr": True} if o.get("enable_pbr") else {}),
                             **({"texture_prompt": o["texture_prompt"]} if o.get("texture_prompt") else {}),
                             "target_formats": ["glb"]} if kind == "text" and o["texture"] else None)}


def task(key, kind, task_id):
    if not isinstance(task_id, str) or not task_id.strip() or "/" in task_id:
        raise ValueError("task_id is the id Meshy gave the task")
    return common.call("GET", "%s%s/%s" % (base(), PATHS[kind], task_id.strip()), key)


def poll(key, state):
    """(status, progress, credits charged or None, reason). For text with a texture this also starts the texture
    task when the mesh is done: the second half of what was confirmed, recorded in state["refine_id"]."""
    tid = state.get("refine_id") or state["task_id"]
    d = task(key, state["kind"], tid)
    st, prog = d.get("status"), int(d.get("progress") or 0)
    if st in ("FAILED", "CANCELED", "EXPIRED"):
        why = (d.get("task_error") or {}).get("message") if isinstance(d.get("task_error"), dict) else ""
        return "failed", prog, state.get("charged", 0), "Meshy says the task %s%s" % (st.lower(), ": " + why if why else "")
    if st == "SUCCEEDED":
        charged = state.get("charged", 0) + float(d.get("consumed_credits") or 0)
        if state.get("refine") and not state.get("refine_id"):
            state["charged"] = charged
            state["refine_id"] = _create(key, "text", dict(state["refine_body"], preview_task_id=state["task_id"]))
            return "running", 50, None, ""
        return "done", 100, charged, ""
    if state.get("refine"):
        prog = 50 + prog // 2 if state.get("refine_id") else prog // 2
    return ("queued" if st == "PENDING" else "running"), prog, None, ""


def fetch(key, state, folder, d=None):
    tid = state.get("refine_id") or state["task_id"]
    d = d or task(key, state["kind"], tid)
    if d.get("status") != "SUCCEEDED": raise common.GeneratorError("Meshy's task is %s, not finished" % d.get("status"))
    url = (d.get("model_urls") or {}).get("glb")
    if not url: raise common.GeneratorError("Meshy's finished task has no GLB")
    name = "%s.glb" % tid
    return common.download(url, os.path.join(folder, name), base(), name)


def import_task(key, task_id, folder):
    """A model already made (and paid for): Meshy's task ids don't say which kind, so each kind is asked in turn."""
    last = None
    for kind in ("text", "image", "multi"):
        try:
            d = task(key, kind, task_id)
        except common.GeneratorError as e:
            if e.status in (400, 404): last = e; continue
            raise
        return fetch(key, {"task_id": task_id.strip(), "kind": kind}, folder, d), d.get("consumed_credits")
    raise common.GeneratorError("Meshy has no task %s on this key (%s)" % (task_id, last), 404)


def balance(key):
    return common.call("GET", base() + "/openapi/v1/balance", key, timeout=20).get("balance")

# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Tripo (https://www.tripo3d.ai) through its official HTTP API, v3
# (https://developers.tripo3d.com/en/docs/introduction): text, one picture, or front/left/back/right pictures to a
# model.
#
#   POST /v3/generation/{text,image,multiview}-to-model   spends credits (frozen at once, refunded when a task fails)
#   GET  /v3/tasks/{task_id}                              free: status, progress, output.model_url, credits_consumed
#   POST /v3/files                                        free: a picture -> file_token
#   GET  /v3/account/balance                              free
#
# Prices are H3.1's (https://developers.tripo3d.com/en/models/v3-1, checked 2026-10-18), so only that model is
# offered here: another model's price is not published in the same table and could not be capped.
import os

from . import common

NAME, LABEL, UNIT = "tripo", "Tripo", "credits"
DOCS = "https://developers.tripo3d.com/en/docs/introduction"
MODEL = "v3.1-20260211"
BASE = {"text": 10, "image": 20, "multiview": 20}
TEXTURE = {"fast": 10, "standard": 10, "detailed": 20, "extreme": 30}   # extreme: "more than detailed"; +10 assumed
OPTIONS = ("model", "texture", "pbr", "texture_quality", "geometry_quality", "face_limit", "quad", "smart_low_poly",
           "generate_parts", "auto_size", "negative_prompt", "model_seed", "texture_seed", "image_seed", "export_uv",
           "texture_version", "delight")
DEFAULTS = {"model": MODEL, "texture": True, "pbr": True, "texture_quality": "standard"}


def base():
    return common.base_url("AUTORIG_TRIPO_URL", "https://openapi.tripo3d.com") + "/v3"


def _data(reply):
    if not isinstance(reply, dict) or reply.get("code") not in (0, None):
        why = reply.get("message") if isinstance(reply, dict) else reply
        raise common.GeneratorError("Tripo: %s" % why)
    return reply.get("data") or {}


def estimate(prompt, images, options):
    o = dict(DEFAULTS, **common.pick(options, OPTIONS, "Tripo"))
    common.one_of(o["model"], (MODEL,), "model (only H3.1 has a published price)")
    for k in ("texture", "pbr", "quad", "smart_low_poly", "generate_parts", "auto_size", "export_uv", "delight"):
        if k in o: common.boolean(o[k], k)
    if o.get("pbr"): o["texture"] = True                  # Tripo forces texture on with PBR
    common.one_of(o["texture_quality"], tuple(TEXTURE), "texture_quality")
    if "geometry_quality" in o: common.one_of(o["geometry_quality"], ("standard", "detailed"), "geometry_quality")
    if "face_limit" in o and (isinstance(o["face_limit"], bool) or not isinstance(o["face_limit"], int) or o["face_limit"] < 48):
        raise ValueError("face_limit is a whole number of faces")
    if o.get("generate_parts") and (o.get("texture") or o.get("pbr")):
        raise ValueError("generate_parts needs texture and pbr false")
    if images:
        if prompt: raise ValueError("Tripo makes a model from text or from pictures, not both")
        kind = "multiview" if len(images) > 1 or images[0].view else "image"
        if kind == "multiview":
            views = [im.view for im in images]
            if any(views) and not all(views): raise ValueError("give every picture a view, or none (front, left, back, right in order)")
            if not any(views):
                if len(images) > 4: raise ValueError("at most 4 views")
                for im, v in zip(images, common.VIEWS): im.view = v
            if "front" not in [im.view for im in images]: raise ValueError("the front view is needed")
            if len({im.view for im in images}) != len(images): raise ValueError("each view once")
    else:
        if not prompt or not isinstance(prompt, str): raise ValueError("give prompt or images")
        if len(prompt) > 1024: raise ValueError("Tripo's prompt is up to 1024 characters")
        kind = "text"
    lines = [("%s to model (H3.1)" % kind, BASE[kind])]
    if o.get("texture"): lines.append(("%s texture" % o["texture_quality"], TEXTURE[o["texture_quality"]]))
    if o.get("geometry_quality") == "detailed": lines.append(("detailed geometry (Ultra)", 20))
    if o.get("quad"): lines.append(("quad mesh", 5))
    if o.get("smart_low_poly"): lines.append(("smart low-poly", 10))
    if o.get("generate_parts"): lines.append(("parts", 20))
    return {"kind": kind, "credits": sum(c for _, c in lines), "breakdown": lines, "options": o}


def _input(key, im):
    if im.url: return im.url
    reply = common.call("POST", base() + "/files", key, form=[("file", (im.filename, im.data, im.ctype))])
    token = _data(reply).get("file_token")
    if not token: raise common.GeneratorError("Tripo gave no file_token for an uploaded picture")
    return token


def submit(key, est, prompt, images):
    """Spends est["credits"]. Only gui/generate_api.py calls this, after a confirmed estimate."""
    body = dict(est["options"])
    if est["kind"] == "text":
        body["prompt"] = prompt
    elif est["kind"] == "image":
        body["input"] = _input(key, images[0])
    else:
        body["inputs"] = [{im.view: _input(key, im)} for im in images]
    task = _data(common.call("POST", "%s/generation/%s-to-model" % (base(), est["kind"]), key, json_body=body)).get("task_id")
    if not task: raise common.GeneratorError("Tripo gave no task_id")
    return {"task_id": task}


def task(key, task_id):
    if not isinstance(task_id, str) or not task_id.strip() or "/" in task_id:
        raise ValueError("task_id is the id Tripo gave the task")
    return _data(common.call("GET", "%s/tasks/%s" % (base(), task_id.strip()), key))


def poll(key, state):
    """(status, progress 0-100, credits charged or None, reason): status queued, running, done or failed. Free."""
    d = task(key, state["task_id"])
    st = d.get("status")
    if st == "success": return "done", 100, d.get("credits_consumed"), ""
    if st in ("failed", "cancelled", "banned", "expired", "unknown"):
        return "failed", d.get("progress") or 0, 0, "Tripo says the task %s" % st
    return ("queued" if st == "queued" else "running"), d.get("progress") or 0, None, ""


def _model_url(d):
    out = d.get("output") or {}
    return out.get("model_url") or out.get("pbr_model_url") or out.get("model") or out.get("base_model_url")


def fetch(key, state, folder, d=None):
    """Download the finished model (free) into folder. Returns its path."""
    d = d or task(key, state["task_id"])
    if d.get("status") != "success": raise common.GeneratorError("Tripo's task is %s, not finished" % d.get("status"))
    url = _model_url(d)
    if not url: raise common.GeneratorError("Tripo's finished task has no model link")
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    name = "%s%s" % (state["task_id"], ext if ext in (".glb", ".fbx") else ".glb")
    return common.download(url, os.path.join(folder, name), base(), name)


def import_task(key, task_id, folder):
    """A model already made (and paid for) on this account: look it up and download it. Free."""
    d = task(key, task_id)
    return fetch(key, {"task_id": task_id.strip()}, folder, d), d.get("credits_consumed")


def balance(key):
    d = _data(common.call("GET", base() + "/account/balance", key, timeout=20))
    return d.get("balance")

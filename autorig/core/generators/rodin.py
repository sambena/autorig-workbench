# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Hyper3D Rodin (https://hyper3d.ai) Gen-2.5 through its official HTTP API
# (https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5): text, or one to five pictures, to a model.
#
#   POST /api/v2/rodin      multipart; spends credits; -> uuid (for download) and jobs.subscription_key (for status)
#   POST /api/v2/status     free: {subscription_key} -> jobs[].status (Waiting, Generating, Done, Failed)
#   POST /api/v2/download   free: {task_uuid} -> list[{name, url}]
#
# core/studio_generation.py runs Rodin through the hyper3d CLI for the Studio's resumable photo sets; this adapter
# talks HTTP so the server needs no Node, and shares that module's check of the files a generation hands back.
# Credits (checked 2026-10-18): 0.5 a generation, +0.5 for tier Gen-2.5-Extreme-High, +2.0 for texture_mode
# extreme-high; the docs say other settings add nothing.
import os

from . import common

NAME, LABEL, UNIT = "rodin", "Hyper3D Rodin", "credits"
DOCS = "https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5"
TIERS = ("Gen-2.5-Extreme-Low", "Gen-2.5-Low", "Gen-2.5-Medium", "Gen-2.5-High", "Gen-2.5-Extreme-High")
TEXTURE_MODES = ("extreme-low", "low", "medium", "high", "extreme-high")
OPTIONS = ("tier", "texture_mode", "quality", "mesh_mode", "geometry_instruct_mode", "uhd_texture", "addons", "seed")
DEFAULTS = {"tier": "Gen-2.5-Medium"}


def base():
    return common.base_url("AUTORIG_RODIN_URL", "https://api.hyper3d.com") + "/api/v2"


def estimate(prompt, images, options):
    o = dict(DEFAULTS, **common.pick(options, OPTIONS, "Rodin"))
    common.one_of(o["tier"], TIERS, "tier")
    if "texture_mode" in o: common.one_of(o["texture_mode"], TEXTURE_MODES, "texture_mode")
    if "quality" in o: common.one_of(o["quality"], ("high", "medium", "low", "extra-low"), "quality")
    if "mesh_mode" in o: common.one_of(o["mesh_mode"], ("Raw", "Quad"), "mesh_mode")
    if "geometry_instruct_mode" in o: common.one_of(o["geometry_instruct_mode"], ("creative", "faithful"), "geometry_instruct_mode")
    if "uhd_texture" in o: common.boolean(o["uhd_texture"], "uhd_texture")
    if "addons" in o and o["addons"] not in (["HighPack"], "HighPack", []): raise ValueError("addons is [\"HighPack\"] or none")
    if "seed" in o and (isinstance(o["seed"], bool) or not isinstance(o["seed"], int)): raise ValueError("seed is a whole number")
    if images:
        kind = "image"                               # views go as plain pictures; the first sets the material
    else:
        if not prompt or not isinstance(prompt, str): raise ValueError("give prompt or images")
        kind = "text"
    if prompt is not None and not isinstance(prompt, str): raise ValueError("prompt is text")
    lines = [("%s to model (%s)" % (kind, o["tier"]), 0.5)]
    if o["tier"] == "Gen-2.5-Extreme-High": lines.append(("Extreme-High tier", 0.5))
    if o.get("texture_mode") == "extreme-high": lines.append(("extreme-high texture", 2.0))
    return {"kind": kind, "credits": sum(c for _, c in lines), "breakdown": lines, "options": o}


def submit(key, est, prompt, images):
    """Spends est["credits"]. Only gui/generate_api.py calls this, after a confirmed estimate."""
    o = est["options"]
    form = [("images", (im.filename if not im.view else "%d_%s" % (i, im.filename), im.bytes(), im.ctype))
            for i, im in enumerate(images or [])]
    if prompt: form.append(("prompt", prompt))
    form += [("tier", o["tier"]), ("geometry_file_format", "glb")]
    for k in ("texture_mode", "quality", "mesh_mode", "geometry_instruct_mode", "seed"):
        if k in o: form.append((k, o[k]))
    if o.get("uhd_texture"): form.append(("uhd_texture", "true"))
    if o.get("addons"): form.append(("addons", "HighPack"))
    r = common.call("POST", base() + "/rodin", key, form=form, timeout=300)
    if r.get("error") or not r.get("uuid"):
        raise common.GeneratorError("Rodin refused the generation: %s" % (r.get("error") or r.get("message") or "no uuid"))
    sub = (r.get("jobs") or {}).get("subscription_key")
    if not sub: raise common.GeneratorError("Rodin gave no subscription_key")
    return {"task_id": r["uuid"], "subscription_key": sub, "charged": r.get("consumed")}


def poll(key, state):
    r = common.call("POST", base() + "/status", key, json_body={"subscription_key": state["subscription_key"]})
    jobs = r.get("jobs") or []
    if not jobs: return "queued", 0, None, ""
    sts = [j.get("status") for j in jobs]
    if any(s == "Failed" for s in sts): return "failed", 0, 0, "Rodin says the generation failed"
    if all(s == "Done" for s in sts): return "done", 100, state.get("charged"), ""
    if all(s == "Waiting" for s in sts): return "queued", 0, None, ""
    return "running", int(100 * sum(s == "Done" for s in sts) / len(sts)), None, ""


def files(key, task_id):
    if not isinstance(task_id, str) or not task_id.strip(): raise ValueError("task_id is the task uuid Rodin gave")
    r = common.call("POST", base() + "/download", key, json_body={"task_uuid": task_id.strip()})
    return r.get("list") or []


def fetch(key, state, folder):
    glbs = [f for f in files(key, state["task_id"]) if str(f.get("name", "")).lower().endswith(".glb")]
    if not glbs:
        raise common.GeneratorError("Rodin has no GLB for %s yet (still generating, or made in another format)"
                                    % state["task_id"], 409)
    f = glbs[0]
    name = os.path.basename(str(f["name"]))
    return common.download(f.get("url"), os.path.join(folder, name), base(), name)


def import_task(key, task_id, folder):
    """A generation already made (and paid for): its task uuid (the uuid in the generate reply). Free."""
    return fetch(key, {"task_id": task_id}, folder), None


def balance(key):
    return None                                      # not in the Gen-2.5 docs; the page shows credits on the site

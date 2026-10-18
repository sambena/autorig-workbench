# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: generate a model with Tripo, Meshy or Hyper3D Rodin, under /api/v1 (gui/api_v1.py mounts it).
# docs/GENERATE.md has the whole story; core/generators/ has the providers.
#
#   POST /api/v1/generate {provider, prompt | images, options, name, group}  -> an estimate: id, credits, breakdown
#   POST /api/v1/generate {confirm: <estimate id>}                           -> SPENDS: a generation job
#   GET  /api/v1/generate                                                    -> settings (no keys), spend, recent jobs
#   GET  /api/v1/generate/{job}                                              -> state, progress, task id, the model
#   POST /api/v1/models {provider, task_id, name, group}                     -> import one made already (free)
#   POST /api/v1/pipeline {confirm | provider + task_id, template, clips...}  -> generate or import, rig, animate
#
# Spending: the only call to an adapter's submit() is in confirm(), which needs an estimate made by an earlier call
# (it lasts 15 minutes and works once), generation turned on in the settings, the provider's key, and room under the
# provider's daily and monthly caps. The confirm spends exactly what was estimated: the estimate keeps the request,
# and a confirm that sends a different one is refused.
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback

_core = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _core not in sys.path: sys.path.insert(0, _core)
import generators  # noqa: E402
from generators import common  # noqa: E402

ESTIMATE_TTL = 15 * 60
MAX_ESTIMATES = 100
_estimates = {}                       # id -> {provider, prompt, images, fingerprint, est, created, name, group, then}
_jobs = {}                            # id -> job dict
_lock = threading.Lock()
_srv_for_disk = []                    # the server, to read a job saved before a restart

ROUTES = [
    ("GET", "/api/v1/generate", "AI generation: on or off, providers with key set, caps and spend, recent jobs", None),
    ("POST", "/api/v1/generate", "AI generation (docs/GENERATE.md): {provider, prompt | images, options} -> an estimate; "
                                 "then {confirm: <estimate id>} spends it and returns a job",
     {"provider": "tripo", "prompt": "a brown cow, four legs, standing, side view", "name": "Cow"}),
    ("GET", "/api/v1/generate/{job}", "one generation: state, progress, provider task id, credits, the model", None),
]


def route(h, app, srv, api, method, parts, body=None):
    """parts after /api/v1: ["generate", ...]. Returns (code, body)."""
    _srv_for_disk[:] = [srv]
    if method == "GET" and len(parts) == 1:
        return 200, overview()
    if method == "GET" and len(parts) == 2:
        j = job(parts[1])
        if not j: raise api.ApiError(404, "no generation %r" % parts[1])
        return 200, j
    if method == "POST" and len(parts) == 1:
        body = h._json_body() if body is None else body
        if body.get("confirm"):
            return confirm(app, srv, api, body)
        return 200, estimate(api, body)
    raise api.ApiError(404, "no route %s /api/v1/%s" % (method, "/".join(parts)))


# ---------------------------------------------------------------------------------------------------------------
# Estimate (free) and confirm (spends)
# ---------------------------------------------------------------------------------------------------------------

def _adapter(api, provider):
    if provider not in generators.PROVIDERS:
        raise api.ApiError(400, "provider is one of %s" % ", ".join(generators.PROVIDERS))
    return generators.PROVIDERS[provider]


def _fingerprint(provider, prompt, images, options):
    """The request as sent (pictures hashed), to tell whether a confirm asks for what was estimated."""
    raw = json.dumps({"provider": provider, "prompt": (prompt or "").strip() if prompt is None or isinstance(prompt, str) else prompt,
                      "images": images or [], "options": options or {}}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prune(now):
    for k in [k for k, e in _estimates.items() if now - e["created"] > ESTIMATE_TTL]:
        del _estimates[k]
    while len(_estimates) >= MAX_ESTIMATES:
        del _estimates[min(_estimates, key=lambda k: _estimates[k]["created"])]


def estimate(api, body, then=None):
    """Price a generation without spending: the estimate id a confirm needs."""
    provider = body.get("provider")
    ad = _adapter(api, provider)
    prompt = body.get("prompt")
    if prompt is not None and not isinstance(prompt, str): raise ValueError("prompt is text")
    prompt = (prompt or "").strip() or None
    imgs = common.images(body.get("images"))
    raw_options = body.get("options") or {}
    est = ad.estimate(prompt, imgs, raw_options)
    s = generators.load_settings()
    key = generators.key_for(s, provider)
    ledger = generators.Ledger()
    ok, why = generators.check_cap(s, ledger, provider, est["credits"])
    if not s["enabled"]:
        ok, why = False, "generation is off on this server (enabled in its .autorig_generate.json; docs/GENERATE.md)"
    elif not key:
        ok, why = False, "no %s key on this server (docs/GENERATE.md)" % ad.LABEL
    bal = None
    if key:
        try:
            bal = ad.balance(key)
        except Exception:                                         # noqa: the balance is a courtesy
            bal = None
    if ok and isinstance(bal, (int, float)) and bal < est["credits"]:
        ok, why = False, "the %s account has %s %s, and this costs %s" % (ad.LABEL, generators.fmt(float(bal)), ad.UNIT,
                                                                         generators.fmt(est["credits"]))
    name = body.get("name") or ""
    if name and not isinstance(name, str): raise ValueError("name is text")
    group = body.get("group") or ""
    now = time.time()
    with _lock:
        _prune(now)
        eid = "est_" + secrets.token_urlsafe(12)
        _estimates[eid] = {"provider": provider, "prompt": prompt, "images": imgs, "options": raw_options,
                           "fingerprint": _fingerprint(provider, prompt, body.get("images"), raw_options), "est": est,
                           "created": now, "name": name, "group": group, "then": then, "raw_images": body.get("images")}
    spent = ledger.summary(now)[provider]
    return {"estimate": eid, "provider": provider, "label": ad.LABEL, "kind": est["kind"],
            "credits": est["credits"], "unit": ad.UNIT,
            "breakdown": [{"what": w, "credits": c} for w, c in est["breakdown"]],
            "options": est["options"], "balance": bal,
            "caps": {"daily": s[provider + "_daily"], "monthly": s[provider + "_monthly"],
                     "spent_today": spent["today"], "spent_month": spent["month"]},
            "can_spend": ok, "reason": why, "expires_in": ESTIMATE_TTL,
            "to_spend": "POST the same path with {\"confirm\": \"%s\"} (works once, for %d minutes)" % (eid, ESTIMATE_TTL // 60)}


def confirm(app, srv, api, body, then=None):
    """Spend a confirmed estimate: submit the generation, then poll, download and place the model in a thread."""
    eid = body.get("confirm")
    if not isinstance(eid, str): raise ValueError("confirm is the estimate id")
    with _lock:
        _prune(time.time())
        e = _estimates.get(eid)
        if not e:
            raise api.ApiError(404, "no estimate %r: estimates last %d minutes and work once; ask a new one"
                               % (eid, ESTIMATE_TTL // 60))
        # the confirm spends what was estimated; a request that changed needs its own estimate
        if any(k in body for k in ("provider", "prompt", "images", "options")):
            fp = _fingerprint(body.get("provider", e["provider"]), body.get("prompt", e["prompt"]),
                              body.get("images", e["raw_images"]), body.get("options", e["options"]))
            if fp != e["fingerprint"]:
                raise api.ApiError(409, "the request differs from the estimate's; ask a new estimate for it")
        del _estimates[eid]                                     # works once, whatever happens next
    provider, ad, est = e["provider"], generators.PROVIDERS[e["provider"]], e["est"]
    s = generators.load_settings()
    if not s["enabled"]:
        raise api.ApiError(403, "generation is off on this server (docs/GENERATE.md)")
    key = generators.key_for(s, provider)
    if not key: raise api.ApiError(412, "no %s key on this server (docs/GENERATE.md)" % ad.LABEL)
    then = then or e.get("then")
    name = _free_name(srv, body.get("name") or e["name"] or _name_from(e["prompt"], provider))
    group = body.get("group") or e["group"] or ""
    if group and not srv.NAME.match(group): raise ValueError("a group name is letters, digits, _ and -")
    ledger = generators.Ledger()
    jid = "gen_" + secrets.token_hex(6)
    with generators.Ledger.lock:
        ok, why = generators.check_cap(s, ledger, provider, est["credits"])
        if not ok: raise api.ApiError(402, why)
        ledger.add(provider, est["credits"], "held: %s" % est["kind"], job=jid)
        try:
            state = ad.submit(key, est, e["prompt"], e["images"])
        except Exception as ex:
            ledger.add(provider, -est["credits"], "released: the submit failed", job=jid)
            raise api.ApiError(502, "%s did not take the generation: %s" % (ad.LABEL, ex))
    ledger.add(provider, 0, "submitted", job=jid, task_id=state.get("task_id"))
    j = {"id": jid, "provider": provider, "label": ad.LABEL, "kind": est["kind"], "state": "submitted", "progress": 0,
         "task_id": state.get("task_id"), "credits_held": est["credits"], "credits_charged": None, "unit": ad.UNIT,
         "name": name, "group": group, "model": None, "error": "", "rig_job": None,
         "started": time.time(), "finished": None, "pipeline": bool(then),
         "links": {"self": "/api/v1/generate/" + jid}}
    with _lock:
        _jobs[jid] = j
    _save(srv, j)
    threading.Thread(target=_run, args=(app, srv, api, j, ad, key, state, s, then), daemon=True,
                     name="generate-" + jid).start()
    return 202, {"job": dict(j)}


def _run(app, srv, api, j, ad, key, state, s, then):
    ledger = generators.Ledger()
    held = j["credits_held"]
    poll = max(0.05, float(s.get("poll_seconds") or 5))
    deadline = time.time() + 60 * max(1.0, float(s.get("timeout_minutes") or 30))
    misses = 0
    try:
        while True:
            time.sleep(poll)
            try:
                status, progress, charged, why = ad.poll(key, state)
                misses = 0
            except common.GeneratorError as ex:
                misses += 1                                       # a dropped poll is retried; polls are free
                if misses >= 10: raise
                _update(srv, j, error="polling: %s (retrying)" % ex)
                continue
            # a final state is shown only once its ledger row is written and (for done) the model is placed
            if status == "failed":
                ledger.add(j["provider"], -held, "released: the task failed", job=j["id"], task_id=j["task_id"])
                _update(srv, j, state="failed", progress=progress, credits_charged=0,
                        error=why or "the generation failed", finished=time.time())
                return
            if status == "done":
                break
            _update(srv, j, state=status, progress=progress, error="")
            if time.time() > deadline:
                _update(srv, j, state="timed_out", finished=time.time(),
                        error="still not finished after %s minutes; when it is, import it: POST /api/v1/models "
                              "{\"provider\": \"%s\", \"task_id\": \"%s\"}" % (s.get("timeout_minutes"), j["provider"], j["task_id"]))
                return
        if charged is not None:
            ledger.add(j["provider"], float(charged) - held, "charged %s" % generators.fmt(float(charged)),
                       job=j["id"], task_id=j["task_id"])
        _update(srv, j, state="downloading", credits_charged=charged)
        stage = tempfile.mkdtemp(prefix="gen_", dir=api.incoming(srv))
        try:
            path = ad.fetch(key, state, stage)
            m = api.place(srv, stage, path, j["name"], j["group"])     # mends a GLB (core/tripo_glb.py), removes stage
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        _update(srv, j, state="done", progress=100, model=m["name"], group=m["group"], finished=time.time(),
                links=dict(j["links"], model="/api/v1/models/" + m["name"]))
        if then:
            code, rj = api.submit(app, srv, m["group"], m["name"], "m2m-rig", api.m2m_args(srv, m["name"], **then))
            _update(srv, j, rig_job=rj.get("id"), links=dict(j["links"], rig_job="/api/v1/jobs/%s" % rj.get("id")))
    except Exception as ex:                                       # noqa: the job reports it; the task id stays
        _update(srv, j, state="failed" if j["state"] != "done" else "done", finished=time.time(),
                error="%s (task %s on %s: import it by task id once fixed, at no cost)" % (ex, j["task_id"], j["label"]))
        traceback.print_exc()


def _update(srv, j, **changes):
    with _lock:
        j.update(changes)
    _save(srv, j)


def _jobs_dir(srv):
    return os.path.join(srv.layout.WORK, "generate_jobs")


def _save(srv, j):
    """Kept on disk too, so a restart never loses a paid task's id."""
    try:
        d = _jobs_dir(srv)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, j["id"] + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(j, fh, indent=2)
        os.replace(tmp, os.path.join(d, j["id"] + ".json"))
    except OSError:
        pass



def job(jid):
    with _lock:
        j = _jobs.get(jid)
        if j: return dict(j)
    if not re.match(r"^gen_[0-9a-f]{12}$", jid or "") or not _srv_for_disk: return None
    try:
        with open(os.path.join(_jobs_dir(_srv_for_disk[0]), jid + ".json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def overview():
    s = generators.load_settings()
    spent = generators.Ledger().summary()
    with _lock:
        recent = sorted((dict(j) for j in _jobs.values()), key=lambda j: -j["started"])[:20]
    pub = generators.public_settings(s)
    return {"enabled": s["enabled"],
            "providers": [{"name": p, "label": ad.LABEL, "unit": ad.UNIT, "docs": ad.DOCS, "key": pub[p + "_key"],
                           "daily": s[p + "_daily"], "monthly": s[p + "_monthly"],
                           "spent_today": spent[p]["today"], "spent_month": spent[p]["month"]}
                          for p, ad in generators.PROVIDERS.items()],
            "jobs": recent, "docs": "docs/GENERATE.md"}


def _name_from(prompt, provider):
    words = re.findall(r"[A-Za-z0-9]+", prompt or "")[:4]
    return "_".join(w.capitalize() for w in words) or "%s_model" % provider


def _free_name(srv, name):
    base = srv.clean_name(name) or "model"
    out, n = base, 2
    while srv.find_group(out) is not None:
        out = "%s_%d" % (base[:60], n); n += 1
    return out


# ---------------------------------------------------------------------------------------------------------------
# Import by task id (free) and the one-call pipeline
# ---------------------------------------------------------------------------------------------------------------

def import_model(srv, api, body):
    """POST /api/v1/models {provider, task_id, name, group}: download a model already generated (and paid for) on the
    provider's account this server has the key of, mend it and place it. Spends nothing."""
    provider, task_id = body.get("provider"), body.get("task_id")
    ad = _adapter(api, provider)
    if not isinstance(task_id, str) or not task_id.strip(): raise ValueError("task_id is the provider's task id")
    key = generators.key_for(generators.load_settings(), provider)
    if not key: raise api.ApiError(412, "no %s key on this server (docs/GENERATE.md)" % ad.LABEL)
    stage = tempfile.mkdtemp(prefix="gen_", dir=api.incoming(srv))
    try:
        try:
            path, _ = ad.import_task(key, task_id.strip(), stage)
        except common.GeneratorError as ex:
            raise api.ApiError(ex.status if ex.status in (404, 409) else 502, str(ex))
        name = body.get("name") or "%s_%s" % (provider, re.sub(r"[^A-Za-z0-9]", "", task_id)[:12])
        return api.place(srv, stage, path, name, body.get("group") or "")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def rig_options(srv, api, body):
    """The pipeline's rig and animate options, checked before anything is spent."""
    then = {"template": body.get("template") or "auto", "height": body.get("height"),
            "clips": body.get("clips") or "all", "naming": body.get("naming"), "target": body.get("target"),
            "fmt": body.get("format")}
    api.m2m_args(srv, "check", **then)
    return then


def pipeline(h, app, srv, api, body):
    """POST /api/v1/pipeline with a generator: {provider, task_id} imports and rigs; {provider, prompt|images} answers
    an estimate whose confirm also rigs; {confirm} spends it and rigs when the model arrives."""
    _srv_for_disk[:] = [srv]
    if body.get("confirm"):
        e = _estimates.get(body["confirm"])
        then = (e or {}).get("then") or rig_options(srv, api, body)
        return confirm(app, srv, api, body, then=then)
    then = rig_options(srv, api, body)
    if body.get("task_id"):
        m = import_model(srv, api, body)
        code, rj = api.submit(app, srv, m["group"], m["name"], "m2m-rig", api.m2m_args(srv, m["name"], **then))
        return code, dict(rj, model=m["name"])
    out = estimate(api, body, then=then)
    out["to_spend"] = "POST /api/v1/pipeline with {\"confirm\": \"%s\"}: generate, then rig and animate" % out["estimate"]
    return 200, out

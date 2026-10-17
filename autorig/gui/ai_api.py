# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the AI Assist routes; called only after the server's Host/token guard.
#   GET  /api/ai/settings                 the settings (keys only as "saved" / "from <env var>"), the models and prices
#   POST /api/ai/settings  {...}          change them; a key sent as "" keeps the saved one, "-" clears it
#   GET  /api/ai/status                   whether AI is on, the model, this month's spend and limit
#   GET  /api/ai/estimate?task=&model=&message=&conversation=     what a request could cost, and whether it may run
#   POST /api/ai/start     {task, model, message, conversation}   start a request; {job, conversation}
#   GET  /api/ai/job?id=                  the request's steps so far, its reply, its plan, its cost
#   POST /api/ai/apply     {plan, skip}   apply the plan (minus the change numbers in skip)
#   POST /api/ai/undo      {task, journal}
#   GET  /api/ai/usage                    the last calls and what they cost

try:
    from . import ai_rig
except ImportError:
    import ai_rig

_assistant = None


def assistant(app, srv):
    global _assistant
    if _assistant is None:
        import ai_assist
        _assistant = ai_assist.Assistant([ai_rig.FixRig(srv, app)])
    return _assistant


def _ctx(srv, body):
    name = str(body.get("model") or "")
    if srv.find_group(name) is None:
        raise ValueError("no model " + name)
    return {"model": name}


def get(h, app, srv, path, q):
    if not path.startswith("/api/ai/"):
        return False
    import ai_cost
    import ai_providers
    a = assistant(app, srv)
    one = lambda k: (q.get(k) or [""])[0]
    try:
        if path == "/api/ai/settings":
            h._send(200, {"settings": ai_cost.public_settings(), "catalog": ai_cost.catalog(), "status": a.status(),
                          "tasks": [{"name": t.name, "label": t.label} for t in a.tasks.values()]})
        elif path == "/api/ai/status":
            h._send(200, a.status())
        elif path == "/api/ai/estimate":
            _ctx(srv, {"model": one("model")})
            h._send(200, a.estimate(one("task") or "fix-rig", one("message"), one("conversation")))
        elif path == "/api/ai/job":
            j = a.job(one("id"))
            h._send(200, j) if j else h._send(404, {"error": "that request has run out"})
        elif path == "/api/ai/usage":
            h._send(200, {"spent": a.ledger.spent_this_month(), "limit": float(a.settings()["monthly_limit"]),
                          "recent": a.ledger.recent(30)})
        else:
            h._send(404, {"error": "not found"})
    except ai_providers.AIError as e:
        h._send(400, {"error": str(e)})
    return True


def post(h, app, srv, path, body):
    if not path.startswith("/api/ai/"):
        return False
    import ai_cost
    import ai_providers
    a = assistant(app, srv)
    try:
        if path == "/api/ai/settings":
            h._send(200, {"settings": ai_cost.save_settings(body), "status": a.status()})
        elif path == "/api/ai/start":
            h._send(200, a.start(str(body.get("task") or "fix-rig"), body.get("message", ""), _ctx(srv, body),
                                 str(body.get("conversation") or "")))
        elif path == "/api/ai/apply":
            h._send(200, a.apply(str(body.get("plan") or ""), body.get("skip") or []))
        elif path == "/api/ai/undo":
            h._send(200, a.undo(str(body.get("task") or "fix-rig"), body.get("journal")))
        else:
            h._send(404, {"error": "not found"})
    except ai_providers.AIError as e:
        h._send(400, {"error": str(e)})
    except spec_conflict() as e:
        h._send(409, {"error": str(e)})
    return True


def spec_conflict():
    try:
        from . import spec_api
    except ImportError:
        import spec_api
    return spec_api.Conflict

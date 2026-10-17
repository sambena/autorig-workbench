# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the AI assistant's loop (ported from Cougar Player's server/ai/agent.js). A conversation with the
# AI picked in AI Assist > Settings, which looks at a model with a task's read tools, may try changes with its trial
# tools, and suggests changes with its propose_* tools. Suggestions never happen on their own: they gather into a plan
# the person reads and applies (or not) from the page, and an applied plan can be undone.
#
# A task (the toolkit) supplies: name, label, system (the prompt), defs ([{name, description, schema}]), run(name, args,
# ctx) -> {"content", "images"?, "change"?}, step_label(name, args), apply(changes, ctx) -> {"results", "journal"},
# undo(journal) -> {"results"}. ai_rig.py is the first one ("Fix this rig").
#
# A request runs as a background thread and the page polls it: the model may take many steps, and a trial rig takes
# a while. Every model call is priced at its worst case and checked against the monthly limit first.

import json
import secrets
import threading
import time
import traceback

import ai_cost as cost
import ai_providers as providers

HOUR = 3600.0


def new_id():
    return secrets.token_urlsafe(9)


def _chars(messages):
    """The size of a conversation as the cost estimate counts it: its text, and its pictures counted apart."""
    chars, images = 0, 0
    for m in messages:
        for p in m["parts"]:
            if p.get("type") == "image":
                images += 1
                continue
            q = {k: v for k, v in p.items() if k not in ("images", "raw")}
            chars += len(json.dumps(q))
            images += len(p.get("images") or [])
    return chars, images


class Assistant:
    def __init__(self, tasks, ledger=None, settings=None):
        self.tasks = {t.name: t for t in tasks}
        self.ledger = ledger or cost.Ledger()
        self._settings = settings or cost.load_settings          # a function, so a test can hand in its own
        self.conversations, self.jobs, self.plans = {}, {}, {}
        self.lock = threading.Lock()

    # ---- what the page asks before it runs anything ---------------------------------------------------------

    def settings(self):
        return self._settings()

    def status(self):
        s = self.settings()
        spent, limit = self.ledger.spent_this_month(), float(s["monthly_limit"])
        out = {"provider": s["provider"], "spent": spent, "limit": limit, "enabled": False, "problem": ""}
        if s["provider"] == "off":
            out["problem"] = "AI is off. Pick an AI in AI Assist > Settings."
            return out
        try:
            provider, model, key, _ = cost.current(s)
            info = cost.model_info(provider, model, s)
            out.update(model=model, model_label=info["label"], free=info["free"],
                       provider_label=providers.PROVIDERS[provider]["label"])
            if providers.PROVIDERS[provider]["needs_key"] and not key:
                out["problem"] = "Add the %s API key in AI Assist > Settings." % providers.NAMES[provider]
        except providers.AIError as e:
            out["problem"] = str(e)
        out["enabled"] = not out["problem"]
        return out

    def _sizes(self, task, conversation, message):
        past = self.conversations.get(conversation)
        msgs = (past or {}).get("messages", [])
        chars, images = _chars(msgs)
        chars += len(message) + len(task.system) + len(json.dumps([dict(name=d["name"], description=d["description"],
                                                                         schema=d["schema"]) for d in task.defs]))
        return chars, images

    def estimate(self, task_name, message="", conversation=""):
        """What a request could cost: typical (about four model calls) and worst (every step, every token)."""
        task = self._task(task_name)
        s = self.settings()
        provider, model, _, _ = cost.current(s)
        info = cost.model_info(provider, model, s)
        chars, images = self._sizes(task, conversation, message)
        e = cost.estimate(info, chars, images, int(s["max_tokens"]), int(s["max_steps"]))
        spent, limit = self.ledger.spent_this_month(), float(s["monthly_limit"])
        allowed, reason = cost.check(info, e["worst"], spent, limit)
        return {"typical": round(e["typical"], 4), "worst": round(e["worst"], 4), "spent": round(spent, 4), "limit": limit,
                "free": info["free"], "model": model, "model_label": info["label"], "allowed": allowed, "reason": reason,
                "max_steps": int(s["max_steps"])}

    def _task(self, name):
        t = self.tasks.get(name)
        if not t:
            raise providers.AIError("There is no AI Assist task called %r." % name)
        return t

    # ---- a request ------------------------------------------------------------------------------------------

    def sweep(self):
        now = time.time()
        for table, ttl in ((self.conversations, 6 * HOUR), (self.jobs, HOUR), (self.plans, 6 * HOUR)):
            for k in [k for k, v in table.items() if now - v["at"] > ttl and not v.get("busy")]:
                del table[k]

    def start(self, task_name, message, context, conversation="", chat=None):
        """Starts a request in the background. Returns {job, conversation}. chat stands in for providers.chat in tests."""
        self.sweep()
        task = self._task(task_name)
        text = str(message or "").strip()[:4000]
        if not text:
            raise providers.AIError("Say what you'd like help with.")
        st = self.status()
        if not st["enabled"]:
            raise providers.AIError(st["problem"])
        est = self.estimate(task_name, text, conversation)
        if not est["allowed"]:
            raise providers.AIError(est["reason"])
        with self.lock:
            cid = conversation if conversation in self.conversations else new_id()
            convo = self.conversations.setdefault(cid, {"messages": [], "at": time.time(), "task": task_name,
                                                        "context": context})
            if convo.get("busy"):
                raise providers.AIError("Still working on the last message.")
            if convo["task"] != task_name or convo["context"] != context:
                raise providers.AIError("That conversation was about something else. Start a new one.")
            convo["busy"] = True
            job = {"id": new_id(), "done": False, "steps": [], "reply": "", "plan": None, "error": "", "cost": 0.0,
                   "at": time.time(), "busy": True}
            self.jobs[job["id"]] = job
        before = len(convo["messages"])

        def work():
            try:
                self._turn(task, job, convo, text, context, chat or providers.chat)
            except providers.AIError as e:
                job["error"] = str(e)
                del convo["messages"][before:]          # a failed turn leaves no half-finished tool calls behind
            except Exception as e:                      # a bug: say so plainly, keep the trace in the server log
                job["error"] = "Something went wrong: %s" % e
                traceback.print_exc()
                del convo["messages"][before:]
            finally:
                convo["busy"] = False
                convo["at"] = time.time()
                job.update(done=True, busy=False, at=time.time(), spent=self.ledger.spent_this_month())

        threading.Thread(target=work, name="ai-" + job["id"], daemon=True).start()
        return {"job": job["id"], "conversation": cid}

    def _turn(self, task, job, convo, text, context, chat):
        s = self.settings()
        provider, model, key, url = cost.current(s)
        info = cost.model_info(provider, model, s)
        max_steps, max_tokens = int(s["max_steps"]), int(s["max_tokens"])
        effort = "medium" if info.get("effort") else None
        defs = [{"name": d["name"], "description": d["description"], "schema": d["schema"]} for d in task.defs]
        note = task.context_note(context)
        convo["messages"].append({"role": "user", "parts": [{"type": "text", "text": "%s\n\n(%s)" % (text, note) if note else text}]})
        plan = {"id": new_id(), "changes": [], "at": time.time(), "applied": False, "task": task.name, "context": context}
        said = ""
        for step in range(max_steps):
            chars, images = _chars(convo["messages"])
            chars += len(task.system) + len(json.dumps(defs))
            worst = cost.estimate(info, chars, images, max_tokens, 1)["worst"]
            with self.ledger.lock:
                ok, reason = cost.check(info, worst, self.ledger.spent_this_month(), float(s["monthly_limit"]))
                if not ok:
                    raise providers.AIError(reason)
                res = chat(provider=provider, model=model, key=key, url=url, system=task.system,
                           messages=convo["messages"], tools=defs, max_tokens=max_tokens, effort=effort)
                spent = cost.cost(info, res["usage"]["in"], res["usage"]["out"])
                self.ledger.add(provider, model, res["usage"]["in"], res["usage"]["out"], spent, task.name)
            job["cost"] += spent
            convo["messages"].append({"role": "assistant", "parts": res["parts"], "raw": res.get("raw")})
            calls = [p for p in res["parts"] if p["type"] == "call"]
            said = "\n".join(p["text"] for p in res["parts"] if p["type"] == "text").strip()
            if res["stop"] == "refused":
                job["reply"] = said or "The AI declined to help with this."
                break
            if not calls:
                job["reply"] = said or ("The answer was cut off. Try asking for less at once." if res["stop"] == "max"
                                        else "(No answer.)")
                break
            if said:
                job["steps"].append({"tool": "", "summary": said[:400]})
            results = []
            for call in calls:
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                label = task.step_label(call["name"], args)
                job["steps"].append({"tool": call["name"], "summary": label, "running": True})
                try:
                    out = task.run(call["name"], args, context)
                    if out.get("change"):
                        change = dict(out["change"], n=len(plan["changes"]) + 1)
                        # a task may say a new proposal replaces its earlier one (one rig.json at a time)
                        if out.get("replaces"):
                            plan["changes"] = [c for c in plan["changes"] if c["type"] != change["type"]]
                            change["n"] = len(plan["changes"]) + 1
                        plan["changes"].append(change)
                        job["steps"][-1] = {"tool": call["name"], "summary": "Suggested: " + change["summary"]}
                        content = out.get("content") or "Added to the plan as change %d: %s" % (change["n"], change["summary"])
                    else:
                        job["steps"][-1] = {"tool": call["name"], "summary": out.get("label") or label}
                        content = out.get("content") or "Done."
                    results.append({"type": "result", "id": call["id"], "name": call["name"], "content": content,
                                    "error": False, "images": out.get("images") or []})
                except Exception as e:                    # the model hears what went wrong and can try another way
                    msg = str(e) or e.__class__.__name__
                    job["steps"][-1] = {"tool": call["name"], "summary": "%s: %s" % (label, msg), "error": True}
                    results.append({"type": "result", "id": call["id"], "name": call["name"], "content": msg, "error": True})
            convo["messages"].append({"role": "user", "parts": results})
            if step == max_steps - 1:
                job["reply"] = (said + "\n\n" if said else "") + ("I stopped after %d steps. Ask me to carry on if there's "
                                                                 "more to do." % max_steps)
        if plan["changes"]:
            self.plans[plan["id"]] = plan
            job["plan"] = {"id": plan["id"], "changes": [{k: v for k, v in c.items() if k in ("n", "type", "summary", "detail")}
                                                         for c in plan["changes"]]}

    def job(self, job_id):
        j = self.jobs.get(job_id)
        if not j:
            return None
        return {k: j.get(k) for k in ("done", "steps", "reply", "plan", "error", "cost", "spent")}

    # ---- applying a plan ------------------------------------------------------------------------------------

    def apply(self, plan_id, skip=()):
        plan = self.plans.get(plan_id)
        if not plan:
            raise providers.AIError("That plan has run out. Ask again.")
        if plan["applied"]:
            raise providers.AIError("That plan was already applied.")
        skipped = {int(n) for n in skip or ()}
        chosen = [c for c in plan["changes"] if c["n"] not in skipped]
        if not chosen:
            raise providers.AIError("Nothing is ticked.")
        plan["applied"] = True
        return self._task(plan["task"]).apply(chosen, plan["context"])

    def undo(self, task_name, journal):
        return self._task(task_name).undo(journal)

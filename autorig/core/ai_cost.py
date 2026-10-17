# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the AI settings, the price of every model, and the cost gate (ported from Deal Hunter and Cougar
# Player). The rule: no surprise paid calls. AI is off until it is picked in AI Assist > Settings; it runs only when a
# button is pressed; every model call is priced at its worst case before it is sent and refused when it could pass
# this month's limit; and what each call billed is kept, one JSON line a call.
#
# Settings live in .autorig_ai.json beside the repo (git ignores it; AUTORIG_AI_FILE moves it). An empty key falls
# back to ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY. The spend log is .autorig_ai_usage.jsonl
# (AUTORIG_AI_USAGE_FILE moves it).

import json
import os
import struct
import threading
import time

import ai_providers as providers

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))

# US $ per million tokens, checked 2026-10-17. Thinking tokens bill as output. A promo price holds until its date.
# effort: the model takes a reasoning-effort setting (the assistant asks for "medium").
MODELS = [
    {"provider": "claude", "id": "claude-opus-5-5", "label": "Claude Opus 5.5 (best)", "in": 4.00, "out": 20.00, "effort": True},
    {"provider": "claude", "id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "in": 2.00, "out": 10.00, "effort": True},
    {"provider": "claude", "id": "claude-haiku-5-5", "label": "Claude Haiku 5.5 (cheapest)", "in": 0.10, "out": 0.50, "effort": True},
    {"provider": "openai", "id": "gpt-6-astra", "label": "GPT-6 Astra (best)", "in": 10.00, "out": 50.00, "effort": False},
    {"provider": "openai", "id": "gpt-6.1-sol", "label": "GPT-6.1 Sol", "in": 2.00, "out": 10.00, "effort": False},
    {"provider": "openai", "id": "gpt-6-luna", "label": "GPT-6 Luna (cheapest)", "in": 0.10, "out": 0.50, "effort": False},
    {"provider": "gemini", "id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro (best, preview)", "in": 2.00, "out": 12.00, "effort": True},
    {"provider": "gemini", "id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "in": 1.50, "out": 7.50, "effort": True,
     "promo": {"until": "2027-01-01", "in": 0.75, "out": 3.75}},
    {"provider": "gemini", "id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite (cheapest)", "in": 0.30, "out": 2.50, "effort": True},
]
# Claude Haiku 5.5's price doubles past 100K prompt tokens; the assistant's prompts stay far under that.

ENV_KEYS = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}

DEFAULTS = {
    "provider": "off",
    "claude_key": "", "claude_model": "claude-opus-5-5",
    "openai_key": "", "openai_model": "gpt-6.1-sol",
    "gemini_key": "", "gemini_model": "gemini-3.8-flash", "gemini_free_tier": False,
    "ollama_url": providers.OLLAMA_URL, "ollama_model": "qwen3.5:4b",
    "monthly_limit": 5.00,      # dollars of paid AI a calendar month
    "max_steps": 12,            # model calls in one request at most
    "max_tokens": 8000,         # the answer's cap on each call; it also caps that call's cost
}
LIMITS = {"monthly_limit": (0.0, 1000.0), "max_steps": (1, 40), "max_tokens": (1000, 32000)}
IMAGE_TOKENS = 1600             # a picture, at the size the providers scale it to (Claude: width * height / 750)


def settings_file():
    return os.environ.get("AUTORIG_AI_FILE") or os.path.join(REPO, ".autorig_ai.json")


def usage_file():
    return os.environ.get("AUTORIG_AI_USAGE_FILE") or os.path.join(REPO, ".autorig_ai_usage.jsonl")


def load_settings():
    out = dict(DEFAULTS)
    try:
        with open(settings_file(), encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            out.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return out


def save_settings(changes):
    """Merge the changes into the saved settings. A key sent as "" keeps the one already saved (the page never sees
    keys); "-" clears it. Returns the public view."""
    cur = load_settings()
    for k, v in (changes or {}).items():
        if k not in DEFAULTS:
            continue
        if k.endswith("_key"):
            v = str(v or "").strip()
            if v == "":
                continue
            cur[k] = "" if v == "-" else v
        elif k == "provider":
            if v not in providers.PROVIDERS and v != "off":
                raise ValueError("pick one of: off, " + ", ".join(providers.PROVIDERS))
            cur[k] = v
        elif k in LIMITS:
            lo, hi = LIMITS[k]
            try:
                v = float(v) if k == "monthly_limit" else int(v)
            except (TypeError, ValueError):
                raise ValueError("%s must be a number" % k)
            cur[k] = min(hi, max(lo, v))
        elif k == "gemini_free_tier":
            cur[k] = bool(v)
        else:
            cur[k] = str(v or "").strip()
    with open(settings_file(), "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2)
    return public_settings(cur)


def key_for(s, provider):
    if provider == "ollama":
        return ""
    return (s.get(provider + "_key") or "").strip() or os.environ.get(ENV_KEYS[provider], "")


def public_settings(s=None):
    """The settings without the keys: whether each is set, and where from."""
    s = s or load_settings()
    out = {k: v for k, v in s.items() if not k.endswith("_key")}
    for p in ENV_KEYS:
        out[p + "_key"] = "saved" if s.get(p + "_key") else ("from %s" % ENV_KEYS[p] if os.environ.get(ENV_KEYS[p]) else "")
    return out


def _price_today(m, now=None):
    promo = m.get("promo")
    if promo and time.strftime("%Y-%m-%d", time.localtime(now)) < promo["until"]:
        return {"in": promo["in"], "out": promo["out"]}
    return {"in": m["in"], "out": m["out"]}


def model_info(provider, model, s=None, now=None):
    """The price card for one model. Ollama runs at home, so any model there is free."""
    s = s or {}
    if provider == "ollama":
        return {"provider": provider, "id": model, "label": "Ollama " + model, "in": 0.0, "out": 0.0, "free": True, "effort": False}
    for m in MODELS:
        if m["provider"] == provider and m["id"] == model:
            if provider == "gemini" and s.get("gemini_free_tier"):
                # Google bills nothing on a key without billing; its replies don't say which tier, so the user does
                return dict(m, label=m["label"] + ", free tier", **{"in": 0.0, "out": 0.0}, free=True)
            return dict(m, free=False, **_price_today(m, now))
    raise providers.AIError('Pick a model from the list in AI Assist > Settings ("%s" has no known price, so its cost '
                            "can't be capped)." % model)


def current(s=None, provider=None, model=None):
    """(provider, model, key, url) the assistant would use, from the settings or the one picked for a request."""
    s = s or load_settings()
    provider = provider or s.get("provider", "off")
    if provider not in providers.PROVIDERS:
        raise providers.AIError("AI is off. Pick an AI in AI Assist > Settings first.")
    model = model or s.get(provider + "_model") or providers.PROVIDERS[provider]["default_model"]
    return provider, model, key_for(s, provider), s.get("ollama_url") if provider == "ollama" else ""


def cost(info, tokens_in, tokens_out):
    if not info or info.get("free"):
        return 0.0
    return ((tokens_in or 0) * info["in"] + (tokens_out or 0) * info["out"]) / 1e6


def png_size(data):
    """(width, height) from a PNG's header bytes, or None."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    return None


def image_tokens(width, height):
    return min(IMAGE_TOKENS, max(85, int(width * height / 750))) if width and height else IMAGE_TOKENS


def input_tokens(chars, images=0):
    """About 3.5 characters a token, plus the provider's own wrapping; deliberately on the high side."""
    return int(chars / 3.5) + 400 + images * IMAGE_TOKENS


def estimate(info, chars, images=0, max_tokens=8000, turns=1, growth_chars=2500, growth_images=0.5):
    """What a request of several model calls may cost. The conversation is sent again each call and grows by what the
    tools return (text and pictures). typical: four calls at a third of the answer cap; worst: every call, every
    token of the cap."""
    typical = worst = 0.0
    for k in range(turns):
        t_in = input_tokens(chars + k * growth_chars, images + k * growth_images)
        worst += cost(info, t_in, max_tokens)
        if k < 4:
            typical += cost(info, t_in, max_tokens * 0.35)
    return {"typical": typical, "worst": worst}


def dollars(x):
    # a fraction of a cent would show as $0.00, which reads as free
    return "$%.3f" % x if 0 < x < 0.01 else "$%.2f" % x


class Ledger:
    """The spend log: one JSON line per model call. Months are local calendar months."""

    lock = threading.Lock()                 # one priced call at a time, so two clicks can't both slip under the limit

    def __init__(self, path=None):
        self.path = path or usage_file()

    def rows(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue                                         # a broken line is skipped, not fatal
            if isinstance(row, dict):
                out.append(row)
        return out

    def spent_this_month(self, now=None):
        t = time.localtime(now)
        start = time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        return sum(float(r.get("cost") or 0) for r in self.rows() if float(r.get("at") or 0) >= start)

    def add(self, provider, model, tokens_in, tokens_out, dollars_spent, task=""):
        row = {"at": time.time(), "provider": provider, "model": model, "in": tokens_in, "out": tokens_out,
               "cost": round(dollars_spent, 6), "task": task}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    def recent(self, n=20):
        return list(reversed(self.rows()[-n:]))


def check(info, worst, spent, limit):
    """May this go ahead under the monthly limit? (allowed, reason)"""
    if info.get("free"):
        return True, ""
    if spent + worst > limit:
        return False, ("That could cost up to %s, and this month's AI limit of %s has %s left. Raise it in "
                       "AI Assist > Settings, or pick a cheaper model." % (dollars(worst), dollars(limit),
                                                                          dollars(max(0.0, limit - spent))))
    return True, ""


def catalog():
    """What the settings dialog lists: the AIs and every priced model."""
    return {"providers": {k: v["label"] for k, v in providers.PROVIDERS.items()},
            "models": [dict({"provider": m["provider"], "id": m["id"], "label": m["label"]}, **_price_today(m)) for m in MODELS]}

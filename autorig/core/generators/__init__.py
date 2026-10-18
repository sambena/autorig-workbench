# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: AI 3D generators (Tripo, Meshy, Hyper3D Rodin), their keys, and the spending cap. docs/GENERATE.md.
#
# Each adapter (tripo.py, meshy.py, rodin.py) has the same functions:
#   estimate(prompt, images, options) -> {kind, credits, breakdown, options}     free, offline
#   submit(key, estimate, prompt, images) -> state                                SPENDS (gui/generate_api.py only)
#   poll(key, state) -> (status, progress, credits charged or None, reason)      free (Meshy's text texture: see there)
#   fetch(key, state, folder) -> path of the downloaded model                     free
#   import_task(key, task_id, folder) -> (path, credits it cost or None)          free: made and paid for already
#   balance(key) -> credits left, or None                                         free
#
# The rule (Sam's): nothing is spent without a priced estimate confirmed by a second call, generation is off until
# the settings turn it on, and each provider has a daily and a monthly cap in its own credits, because the hosted
# copy is reachable from the internet.
#
# Settings: .autorig_generate.json beside the repo (git ignores it; AUTORIG_GENERATE_FILE moves it). Edit it on the
# server, or `python scripts/generate_settings.py`; the API only reads it. An empty key falls back to TRIPO_API_KEY,
# MESHY_API_KEY or HYPER3D_API_KEY. Spend log: .autorig_generate_usage.jsonl (AUTORIG_GENERATE_USAGE_FILE).
import json
import os
import threading
import time

from . import meshy, rodin, tripo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))

PROVIDERS = {"tripo": tripo, "meshy": meshy, "rodin": rodin}
ENV_KEYS = {"tripo": "TRIPO_API_KEY", "meshy": "MESHY_API_KEY", "rodin": "HYPER3D_API_KEY"}

DEFAULTS = {
    "enabled": False,                 # off until someone who can edit the server's files turns it on
    "tripo_key": "", "meshy_key": "", "rodin_key": "",
    # caps in each provider's own credits; a generation that could pass one is refused
    "tripo_daily": 40, "tripo_monthly": 200,
    "meshy_daily": 60, "meshy_monthly": 300,
    "rodin_daily": 2.0, "rodin_monthly": 10.0,
    "poll_seconds": 5,
    "timeout_minutes": 30,
}
CAPS = tuple(k for k in DEFAULTS if k.endswith(("_daily", "_monthly")))


def settings_file():
    return os.environ.get("AUTORIG_GENERATE_FILE") or os.path.join(REPO, ".autorig_generate.json")


def usage_file():
    return os.environ.get("AUTORIG_GENERATE_USAGE_FILE") or os.path.join(REPO, ".autorig_generate_usage.jsonl")


def load_settings():
    out = dict(DEFAULTS)
    try:
        with open(settings_file(), encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            out.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    out["enabled"] = out.get("enabled") is True
    for k in CAPS:
        try:
            out[k] = max(0.0, float(out[k]))
        except (TypeError, ValueError):
            out[k] = 0.0                                           # a broken cap allows nothing
    return out


def save_settings(changes):
    """For scripts/generate_settings.py (run on the server), never the HTTP API."""
    cur = load_settings()
    for k, v in changes.items():
        if k not in DEFAULTS: raise ValueError("no setting %r" % k)
        cur[k] = v
    with open(settings_file(), "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2)
    return public_settings(cur)


def key_for(s, provider):
    return (s.get(provider + "_key") or "").strip() or os.environ.get(ENV_KEYS[provider], "").strip()


def public_settings(s=None):
    """The settings without the keys: whether each is set, and where from."""
    s = s or load_settings()
    out = {k: v for k, v in s.items() if not k.endswith("_key")}
    for p in PROVIDERS:
        out[p + "_key"] = "saved" if s.get(p + "_key") else ("from %s" % ENV_KEYS[p] if os.environ.get(ENV_KEYS[p]) else "")
    return out


def _day_start(now):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _month_start(now):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


class Ledger:
    """One JSON line per spend event: the estimate's credits held when a task is sent, then a correction when the
    provider reports what it charged (or refunded a failure). Days and months are local."""

    lock = threading.Lock()           # one cap check and submit at a time, so two confirms can't both slip under

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
                continue
            if isinstance(row, dict): out.append(row)
        return out

    def spent(self, provider, since):
        return sum(float(r.get("credits") or 0) for r in self.rows()
                   if r.get("provider") == provider and float(r.get("at") or 0) >= since)

    def add(self, provider, credits, what, **extra):
        row = dict({"at": time.time(), "provider": provider, "credits": round(float(credits), 4), "what": what}, **extra)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    def summary(self, now=None):
        now = now or time.time()
        return {p: {"today": round(self.spent(p, _day_start(now)), 4), "month": round(self.spent(p, _month_start(now)), 4)}
                for p in PROVIDERS}


def check_cap(s, ledger, provider, credits, now=None):
    """May a generation costing `credits` go ahead? (allowed, reason)"""
    now = now or time.time()
    unit = PROVIDERS[provider].UNIT
    for span, start in (("daily", _day_start(now)), ("monthly", _month_start(now))):
        cap = s[provider + "_" + span]
        spent = ledger.spent(provider, start)
        if spent + credits > cap:
            return False, ("that costs %s %s, and %s's %s cap of %s has %s left (raise %s_%s in the server's "
                           ".autorig_generate.json)" % (fmt(credits), unit, PROVIDERS[provider].LABEL, span, fmt(cap),
                                                        fmt(max(0.0, cap - spent)), provider, span))
    return True, ""


def fmt(x):
    return ("%.2f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)

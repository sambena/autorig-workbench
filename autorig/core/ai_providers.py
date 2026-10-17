# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: talks to the AI providers with native tool calling, over plain HTTP (urllib; nothing to install).
# Ported from Cougar Player's server/ai/providers.js. The rest of the tool speaks one neutral message format; this
# module turns it into each provider's own shape and turns the reply back.
#
# messages: [{"role": "user" | "assistant", "parts": [
#     {"type": "text", "text"} |
#     {"type": "image", "media_type", "data"}                  (base64; user turns only) |
#     {"type": "call", "id", "name", "args" (dict), "raw"?}    (raw: the provider's own part, echoed back) |
#     {"type": "result", "id", "name", "content" (str), "error" (bool), "images"?: [{"media_type", "data"}]}],
#   "raw"?: the provider's whole reply content, echoed back as it came (Claude's thinking blocks must be)}]
# Result parts are sent by the "user" role.
# tools: [{"name", "description", "schema"}]   (schema: a JSON Schema object of type "object")
#
# chat() returns {"parts": [text and call parts], "raw": ..., "usage": {"in", "out"}, "stop": "end"|"tool"|"max"|"refused"}

import json
import urllib.error
import urllib.request

PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "needs_key": True, "default_model": "claude-opus-5-5"},
    "openai": {"label": "OpenAI (ChatGPT)", "needs_key": True, "default_model": "gpt-6.1-sol"},
    "gemini": {"label": "Google Gemini", "needs_key": True, "default_model": "gemini-3.8-flash"},
    "ollama": {"label": "Ollama (free, runs on your own machine)", "needs_key": False, "default_model": "qwen3.5:4b"},
}
NAMES = {"claude": "Anthropic", "openai": "OpenAI", "gemini": "Google Gemini", "ollama": "Ollama"}
OLLAMA_URL = "http://localhost:11434"


class AIError(Exception):
    """A friendly sentence the page can show as it is."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def text_of(parts):
    return "\n".join(p["text"] for p in parts if p.get("type") == "text")


# ---- Claude -----------------------------------------------------------------------------------------------------

def _claude_request(model, key, system, messages, tools, max_tokens, effort):
    body = {"model": model, "max_tokens": max_tokens, "messages": []}
    if system:
        body["system"] = system
    if effort:
        body["output_config"] = {"effort": effort}       # thinking is adaptive on the 5.5 models; effort sets its depth
    for m in messages:
        if m["role"] == "assistant" and m.get("raw") is not None:
            body["messages"].append({"role": "assistant", "content": m["raw"]})   # thinking blocks go back unchanged
            continue
        content = []
        for p in m["parts"]:
            t = p.get("type")
            if t == "text":
                content.append({"type": "text", "text": p["text"]})
            elif t == "image":
                content.append({"type": "image", "source": {"type": "base64", "media_type": p["media_type"], "data": p["data"]}})
            elif t == "call":
                content.append({"type": "tool_use", "id": p["id"], "name": p["name"], "input": p.get("args") or {}})
            elif t == "result":
                inner = [{"type": "text", "text": str(p.get("content") or "")}]
                for im in p.get("images") or []:
                    inner.append({"type": "image", "source": {"type": "base64", "media_type": im["media_type"], "data": im["data"]}})
                content.append({"type": "tool_result", "tool_use_id": p["id"], "content": inner, "is_error": bool(p.get("error"))})
        body["messages"].append({"role": m["role"], "content": content})
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]
    return ("https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, body)


_CLAUDE_STOP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool", "max_tokens": "max", "refusal": "refused",
                "pause_turn": "end"}


def _claude_reply(data):
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return None
    parts = []
    for b in data["content"]:
        if b.get("type") == "text" and b.get("text"):
            parts.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "tool_use":
            parts.append({"type": "call", "id": b["id"], "name": b["name"], "args": b.get("input") or {}})
    u = data.get("usage") or {}
    tokens_in = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    return {"parts": parts, "raw": data["content"], "usage": {"in": tokens_in, "out": u.get("output_tokens") or 0},
            "stop": _CLAUDE_STOP.get(data.get("stop_reason"), "end")}


# ---- OpenAI -----------------------------------------------------------------------------------------------------

def _openai_tools(tools):
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}}
            for t in tools]


def _data_url(im):
    return "data:%s;base64,%s" % (im["media_type"], im["data"])


def _openai_request(model, key, system, messages, tools, max_tokens, effort):
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m["role"] == "assistant":
            calls = [p for p in m["parts"] if p.get("type") == "call"]
            msg = {"role": "assistant", "content": text_of(m["parts"]) or None}
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}} for c in calls]
            out.append(msg)
            continue
        pictures = []
        for p in m["parts"]:
            if p.get("type") == "result":
                out.append({"role": "tool", "tool_call_id": p["id"], "content": str(p.get("content") or "")})
                pictures += p.get("images") or []
            elif p.get("type") == "image":
                pictures.append(p)
        text = text_of(m["parts"])
        if text or pictures:
            # a tool message carries text only, so the pictures a tool returned follow in a user message
            content = ([{"type": "text", "text": text}] if text else []) + \
                      [{"type": "image_url", "image_url": {"url": _data_url(im)}} for im in pictures]
            out.append({"role": "user", "content": content})
    body = {"model": model, "messages": out, "max_completion_tokens": max_tokens}
    if tools:
        body["tools"] = _openai_tools(tools)
    return ("https://api.openai.com/v1/chat/completions",
            {"authorization": "Bearer " + key, "content-type": "application/json"}, body)


_OPENAI_STOP = {"stop": "end", "tool_calls": "tool", "function_call": "tool", "length": "max", "content_filter": "refused"}


def _openai_reply(data):
    choices = (data or {}).get("choices") if isinstance(data, dict) else None
    if not choices or not choices[0].get("message"):
        return None
    msg = choices[0]["message"]
    parts = []
    if isinstance(msg.get("content"), str) and msg["content"]:
        parts.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        fn = c.get("function")
        if not fn:
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        parts.append({"type": "call", "id": c.get("id"), "name": fn.get("name"), "args": args if isinstance(args, dict) else {}})
    stop = _OPENAI_STOP.get(choices[0].get("finish_reason"), "end")
    if msg.get("refusal"):
        stop = "refused"
    if stop == "end" and any(p["type"] == "call" for p in parts):
        stop = "tool"
    u = data.get("usage") or {}
    return {"parts": parts, "raw": None, "usage": {"in": u.get("prompt_tokens") or 0, "out": u.get("completion_tokens") or 0},
            "stop": stop}


# ---- Gemini -----------------------------------------------------------------------------------------------------

_GEMINI_DROP = {"additionalProperties", "$schema", "default", "examples"}


def gemini_schema(schema):
    """Gemini rejects some JSON Schema words, so they are taken out at every depth (field names are kept)."""
    if isinstance(schema, list):
        return [gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in _GEMINI_DROP:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: gemini_schema(sub) for name, sub in v.items()}
        else:
            out[k] = gemini_schema(v)
    return out


def _gemini_request(model, key, system, messages, tools, max_tokens, effort):
    calls = {p["id"]: p for m in messages for p in m["parts"] if p.get("type") == "call"}
    contents = []
    for m in messages:
        parts = []
        for p in m["parts"]:
            t = p.get("type")
            if t == "text":
                parts.append({"text": p["text"]})
            elif t == "image":
                parts.append({"inlineData": {"mimeType": p["media_type"], "data": p["data"]}})
            elif t == "call":
                parts.append(p.get("raw") or {"functionCall": {"name": p["name"], "args": p.get("args") or {}}})
            elif t == "result":
                fr = {"name": p["name"], "response": {"content": str(p.get("content") or "")}}
                given = ((calls.get(p["id"]) or {}).get("raw") or {}).get("functionCall", {}).get("id")
                if given:
                    fr["id"] = given
                parts.append({"functionResponse": fr})
                for im in p.get("images") or []:
                    parts.append({"inlineData": {"mimeType": im["media_type"], "data": im["data"]}})
        contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    body = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if effort:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low" if effort == "low" else "medium"}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                                    "parameters": gemini_schema(t["schema"])} for t in tools]}]
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % urllib.request.quote(model, safe="")
    return url, {"x-goog-api-key": key, "content-type": "application/json"}, body


_GEMINI_REFUSED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII", "IMAGE_SAFETY"}


def _gemini_reply(data):
    if not isinstance(data, dict):
        return None
    um = data.get("usageMetadata") or {}
    usage = {"in": um.get("promptTokenCount") or 0,
             "out": (um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0)}   # thinking bills as output
    cands = data.get("candidates") or []
    if not cands:
        if (data.get("promptFeedback") or {}).get("blockReason"):
            return {"parts": [], "raw": None, "usage": usage, "stop": "refused"}
        return None
    parts, n = [], 0
    for p in (cands[0].get("content") or {}).get("parts") or []:
        if p.get("functionCall"):
            n += 1
            fc = p["functionCall"]
            parts.append({"type": "call", "id": fc.get("id") or "g%d" % n, "name": fc.get("name"), "args": fc.get("args") or {}, "raw": p})
        elif isinstance(p.get("text"), str) and p["text"] and not p.get("thought"):
            parts.append({"type": "text", "text": p["text"]})
    stop = "end"
    if any(p["type"] == "call" for p in parts):
        stop = "tool"
    elif cands[0].get("finishReason") == "MAX_TOKENS":
        stop = "max"
    elif cands[0].get("finishReason") in _GEMINI_REFUSED:
        stop = "refused"
    return {"parts": parts, "raw": None, "usage": usage, "stop": stop}


# ---- Ollama -----------------------------------------------------------------------------------------------------

def ollama_base(url):
    return str(url or OLLAMA_URL).rstrip("/")


def _ollama_request(model, url, system, messages, tools, max_tokens):
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m["role"] == "assistant":
            calls = [p for p in m["parts"] if p.get("type") == "call"]
            msg = {"role": "assistant", "content": text_of(m["parts"])}
            if calls:
                msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c.get("args") or {}}} for c in calls]
            out.append(msg)
            continue
        for p in m["parts"]:
            if p.get("type") == "result":
                msg = {"role": "tool", "content": str(p.get("content") or ""), "tool_name": p["name"]}
                if p.get("images"):
                    msg["images"] = [im["data"] for im in p["images"]]
                out.append(msg)
        text = text_of(m["parts"])
        pics = [p["data"] for p in m["parts"] if p.get("type") == "image"]
        if text or pics:
            msg = {"role": "user", "content": text}
            if pics:
                msg["images"] = pics
            out.append(msg)
    body = {"model": model, "messages": out, "stream": False, "think": False, "options": {"num_predict": max_tokens}}
    if tools:
        body["tools"] = _openai_tools(tools)
    return ollama_base(url) + "/api/chat", {"content-type": "application/json"}, body


def _ollama_reply(data):
    if not isinstance(data, dict) or not data.get("message"):
        return None
    msg = data["message"]
    parts, n = [], 0
    if isinstance(msg.get("content"), str) and msg["content"]:
        parts.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        fn = c.get("function")
        if not fn:
            continue
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        n += 1
        parts.append({"type": "call", "id": "o%d" % n, "name": fn.get("name"), "args": args if isinstance(args, dict) else {}})
    stop = "max" if data.get("done_reason") == "length" else "end"
    if any(p["type"] == "call" for p in parts):
        stop = "tool"
    return {"parts": parts, "raw": None, "usage": {"in": data.get("prompt_eval_count") or 0, "out": data.get("eval_count") or 0},
            "stop": stop}


_REPLIES = {"claude": _claude_reply, "openai": _openai_reply, "gemini": _gemini_reply, "ollama": _ollama_reply}


def build_request(provider, model, key="", url="", system="", messages=(), tools=(), max_tokens=4096, effort=None):
    """(url, headers, body) for one provider. Split from chat() so the tests can check each shape without a network."""
    if provider == "claude":
        return _claude_request(model, key, system, messages, tools, max_tokens, effort)
    if provider == "openai":
        return _openai_request(model, key, system, messages, tools, max_tokens, effort)
    if provider == "gemini":
        return _gemini_request(model, key, system, messages, tools, max_tokens, effort)
    if provider == "ollama":
        return _ollama_request(model, url, system, messages, tools, max_tokens)
    raise AIError('Pick an AI in AI Assist > Settings ("%s" is not one).' % provider)


def read_reply(provider, data):
    return _REPLIES[provider](data)


def _error_text(data, raw):
    if isinstance(data, dict) and data.get("error"):
        e = data["error"]
        if isinstance(e, str):
            return e
        if isinstance(e, dict) and e.get("message"):
            return e["message"]
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        return data["message"]
    return (raw or "").strip()[:200]


def chat(provider, model, key="", url="", system="", messages=(), tools=(), max_tokens=4096, effort=None, timeout=180,
         opener=None):
    """One model call. opener(request, timeout) stands in for urllib.request.urlopen in the tests."""
    info = PROVIDERS.get(provider)
    if not info:
        raise AIError('Pick an AI in AI Assist > Settings ("%s" is not one).' % provider)
    name = NAMES[provider]
    if info["needs_key"] and not key:
        raise AIError("Add the %s API key in AI Assist > Settings." % name)
    req_url, headers, body = build_request(provider, model or info["default_model"], key, url, system, messages, tools,
                                           max_tokens, effort)
    req = urllib.request.Request(req_url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if e.code in (401, 403):
            raise AIError("%s rejected the API key. Check it in AI Assist > Settings." % name, e.code)
        if e.code == 429:
            raise AIError("%s says the rate limit or quota has been reached. Wait a minute, or check the account's "
                          "billing." % name, e.code)
        why = _error_text(data, raw)
        raise AIError("%s returned an error (%d)%s" % (name, e.code, ": " + why if why else "."), e.code)
    except (urllib.error.URLError, OSError) as e:
        if "timed out" in str(e).lower():
            raise AIError("%s took longer than %d seconds to answer. Try again." % (name, timeout))
        where = ("Couldn't reach Ollama at %s. Is it running?" % ollama_base(url) if provider == "ollama"
                 else "Couldn't reach %s. Check this computer's internet connection." % name)
        raise AIError(where)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    reply = read_reply(provider, data) if data is not None else None
    if not reply:
        raise AIError("%s sent back a reply that couldn't be read (HTTP %s). Try again." % (name, status))
    return reply

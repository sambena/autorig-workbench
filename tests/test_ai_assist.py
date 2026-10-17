# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Assist: the provider shapes, the cost gate, the assistant's loop (with a scripted AI, so nothing is paid for),
and Fix this rig's tools on a copy of the samples."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "gui")]
import ai_assist
import ai_cost
import ai_providers as providers

PNG_1x1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
TOOLS = [{"name": "look", "description": "look", "schema": {"type": "object", "properties": {}, "additionalProperties": False}}]
CONVO = [
    {"role": "user", "parts": [{"type": "text", "text": "fix it"}]},
    {"role": "assistant", "parts": [{"type": "text", "text": "Looking."}, {"type": "call", "id": "c1", "name": "look", "args": {}}]},
    {"role": "user", "parts": [{"type": "result", "id": "c1", "name": "look", "content": "a picture", "error": False,
                                "images": [{"media_type": "image/png", "data": PNG_1x1}]}]},
]


class ProviderShapes(unittest.TestCase):
    def test_claude_puts_pictures_inside_the_tool_result_and_echoes_raw_turns(self):
        convo = [dict(CONVO[0]), dict(CONVO[1], raw=[{"type": "thinking", "thinking": "", "signature": "s"},
                                                      {"type": "tool_use", "id": "c1", "name": "look", "input": {}}]), CONVO[2]]
        url, headers, body = providers.build_request("claude", "claude-opus-5-5", "k", "", "sys", convo, TOOLS, 8000, "medium")
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "k")
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertNotIn("thinking", body)                  # adaptive by default on the 5.5 models; never disabled
        self.assertEqual(body["messages"][1]["content"][0]["type"], "thinking")      # echoed back unchanged
        result = body["messages"][2]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual([c["type"] for c in result["content"]], ["text", "image"])
        self.assertEqual(body["tools"][0]["input_schema"]["type"], "object")

    def test_openai_sends_tool_pictures_in_a_user_message(self):
        _, _, body = providers.build_request("openai", "gpt-6.1-sol", "k", "", "sys", CONVO, TOOLS, 8000, None)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])
        self.assertTrue(body["messages"][-1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(json.loads(body["messages"][2]["tool_calls"][0]["function"]["arguments"]), {})

    def test_gemini_drops_schema_words_it_rejects_and_inlines_pictures(self):
        _, headers, body = providers.build_request("gemini", "gemini-3.8-flash", "k", "", "sys", CONVO, TOOLS, 8000, "medium")
        self.assertEqual(headers["x-goog-api-key"], "k")
        self.assertNotIn("additionalProperties", body["tools"][0]["functionDeclarations"][0]["parameters"])
        parts = body["contents"][2]["parts"]
        self.assertIn("functionResponse", parts[0])
        self.assertIn("inlineData", parts[1])

    def test_ollama_carries_pictures_on_the_tool_message(self):
        url, _, body = providers.build_request("ollama", "qwen3.5:4b", "", "http://box:11434/", "sys", CONVO, TOOLS, 8000, None)
        self.assertEqual(url, "http://box:11434/api/chat")
        tool = [m for m in body["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool["images"], [PNG_1x1])

    def test_replies_are_read_into_one_shape(self):
        claude = providers.read_reply("claude", {"content": [{"type": "text", "text": "hi"},
                                                             {"type": "tool_use", "id": "t", "name": "look", "input": {"a": 1}}],
                                                 "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5,
                                                                                      "cache_read_input_tokens": 3}})
        self.assertEqual((claude["stop"], claude["usage"]), ("tool", {"in": 13, "out": 5}))
        self.assertEqual(claude["parts"][1], {"type": "call", "id": "t", "name": "look", "args": {"a": 1}})
        oa = providers.read_reply("openai", {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "function": {"name": "look", "arguments": "{\"b\": 2}"}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}})
        self.assertEqual((oa["stop"], oa["parts"][0]["args"]), ("tool", {"b": 2}))
        gem = providers.read_reply("gemini", {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "MAX_TOKENS"}],
                                              "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1, "thoughtsTokenCount": 2}})
        self.assertEqual((gem["stop"], gem["usage"]["out"]), ("max", 3))

    def test_a_missing_key_is_said_plainly(self):
        with self.assertRaises(providers.AIError) as e:
            providers.chat("claude", "claude-opus-5-5", key="", messages=CONVO, tools=TOOLS)
        self.assertIn("API key", str(e.exception))


class CostGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-ai-")
        self.saved = {k: os.environ.get(k) for k in ("AUTORIG_AI_FILE", "AUTORIG_AI_USAGE_FILE", "ANTHROPIC_API_KEY")}
        os.environ["AUTORIG_AI_FILE"] = os.path.join(self.tmp, "ai.json")
        os.environ["AUTORIG_AI_USAGE_FILE"] = os.path.join(self.tmp, "usage.jsonl")
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prices_and_unknown_models(self):
        info = ai_cost.model_info("claude", "claude-opus-5-5")
        self.assertEqual((info["in"], info["out"], info["free"]), (4.0, 20.0, False))
        self.assertAlmostEqual(ai_cost.cost(info, 1_000_000, 100_000), 6.0)
        self.assertTrue(ai_cost.model_info("ollama", "anything")["free"])
        self.assertTrue(ai_cost.model_info("gemini", "gemini-3.8-flash", {"gemini_free_tier": True})["free"])
        with self.assertRaises(providers.AIError):
            ai_cost.model_info("claude", "claude-made-up")

    def test_estimate_grows_with_steps_and_pictures(self):
        info = ai_cost.model_info("claude", "claude-sonnet-5-5")
        one = ai_cost.estimate(info, 20000, 0, 8000, 1)
        many = ai_cost.estimate(info, 20000, 0, 8000, 12)
        self.assertGreater(many["worst"], 11 * one["worst"])
        self.assertLess(many["typical"], many["worst"])
        self.assertGreater(ai_cost.estimate(info, 20000, 4, 8000, 1)["worst"], one["worst"])

    def test_the_monthly_limit_holds(self):
        info = ai_cost.model_info("claude", "claude-opus-5-5")
        self.assertTrue(ai_cost.check(info, 0.5, 4.0, 5.0)[0])
        ok, why = ai_cost.check(info, 1.5, 4.0, 5.0)
        self.assertFalse(ok)
        self.assertIn("$1.50", why)
        self.assertTrue(ai_cost.check(ai_cost.model_info("ollama", "m"), 99, 99, 0)[0])

    def test_ledger_counts_this_month_only_and_skips_broken_lines(self):
        led = ai_cost.Ledger()
        led.add("claude", "claude-opus-5-5", 10, 10, 0.25, "fix-rig")
        with open(led.path, "a") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"at": time.time() - 90 * 86400, "cost": 9.0}) + "\n")
        self.assertAlmostEqual(led.spent_this_month(), 0.25)

    def test_keys_are_kept_and_never_shown(self):
        out = ai_cost.save_settings({"provider": "claude", "claude_key": "sk-secret", "monthly_limit": "2.5", "max_steps": 99})
        self.assertEqual(out["claude_key"], "saved")
        self.assertNotIn("sk-secret", json.dumps(out))
        self.assertEqual((out["monthly_limit"], out["max_steps"]), (2.5, 40))
        ai_cost.save_settings({"claude_key": ""})                       # empty keeps it
        self.assertEqual(ai_cost.load_settings()["claude_key"], "sk-secret")
        ai_cost.save_settings({"claude_key": "-"})                      # "-" clears it
        self.assertEqual(ai_cost.load_settings()["claude_key"], "")
        os.environ["ANTHROPIC_API_KEY"] = "from-env"
        self.assertEqual(ai_cost.current()[2], "from-env")
        self.assertEqual(ai_cost.public_settings()["claude_key"], "from ANTHROPIC_API_KEY")
        with self.assertRaises(ValueError):
            ai_cost.save_settings({"provider": "skynet"})


class FakeTask:
    name, label, system = "fake", "Fake", "You fix things."
    defs = [{"name": "look", "description": "look", "schema": TOOLS[0]["schema"]},
            {"name": "propose", "description": "propose", "schema": TOOLS[0]["schema"]},
            {"name": "broken", "description": "fails", "schema": TOOLS[0]["schema"]}]

    def __init__(self):
        self.applied = None

    def context_note(self, ctx):
        return "Model: " + ctx["model"]

    def step_label(self, name, args):
        return name

    def run(self, name, args, ctx):
        if name == "look":
            return {"content": "it tears at the knee", "images": [{"media_type": "image/png", "data": PNG_1x1}]}
        if name == "propose":
            return {"change": {"type": "spec", "summary": "longer shins", "detail": "-a\n+b"}, "replaces": True}
        raise ValueError("that tool is broken")

    def apply(self, changes, ctx):
        self.applied = changes
        return {"results": [{"n": c["n"], "ok": True} for c in changes], "journal": "j1"}

    def undo(self, journal):
        return {"results": [{"ok": True}], "undone": journal}


def scripted(replies):
    """A stand-in for providers.chat: hands back the replies in order, and keeps what it was sent."""
    sent = []

    def chat(**kw):
        sent.append(json.loads(json.dumps(kw)))          # as it was when sent: the conversation grows afterwards
        return replies.pop(0)
    chat.sent = sent
    return chat


def call(name, cid):
    return {"type": "call", "id": cid, "name": name, "args": {}}


class AssistantLoop(CostGate):
    def make(self, limit=5.0, provider="claude"):
        ai_cost.save_settings({"provider": provider, "claude_key": "k", "monthly_limit": limit, "max_steps": 4})
        self.task = FakeTask()
        return ai_assist.Assistant([self.task])

    def wait(self, a, job):
        for _ in range(200):
            j = a.job(job)
            if j["done"]:
                return j
            time.sleep(0.02)
        self.fail("the request never finished")

    def test_a_request_looks_proposes_and_is_priced(self):
        a = self.make()
        chat = scripted([
            {"parts": [call("look", "1"), call("broken", "2")], "raw": None, "usage": {"in": 1000, "out": 100}, "stop": "tool"},
            {"parts": [call("propose", "3")], "raw": None, "usage": {"in": 3000, "out": 200}, "stop": "tool"},
            {"parts": [{"type": "text", "text": "Longer shins should stop the knee tearing."}], "raw": None,
             "usage": {"in": 3500, "out": 50}, "stop": "end"},
        ])
        r = a.start("fake", "fix it", {"model": "wolf"}, chat=chat)
        j = self.wait(a, r["job"])
        self.assertEqual(j["error"], "")
        self.assertIn("Longer shins", j["reply"])
        self.assertEqual(j["plan"]["changes"][0]["summary"], "longer shins")
        self.assertTrue(any(s.get("error") for s in j["steps"]))          # the broken tool is reported, not fatal
        expect = ai_cost.cost(ai_cost.model_info("claude", "claude-opus-5-5"), 7500, 350)
        self.assertAlmostEqual(j["cost"], expect)
        self.assertAlmostEqual(a.ledger.spent_this_month(), expect)
        # the picture went back to the model inside the tool result; the error result says what failed
        results = chat.sent[1]["messages"][-1]["parts"]
        self.assertEqual(results[0]["images"][0]["media_type"], "image/png")
        self.assertEqual((results[1]["error"], results[1]["content"]), (True, "that tool is broken"))
        self.assertIn("Model: wolf", chat.sent[0]["messages"][0]["parts"][0]["text"])
        out = a.apply(j["plan"]["id"])
        self.assertEqual(out["journal"], "j1")
        self.assertEqual(self.task.applied[0]["summary"], "longer shins")
        with self.assertRaises(providers.AIError):
            a.apply(j["plan"]["id"])                                      # once only

    def test_the_limit_stops_a_request_before_it_is_sent(self):
        a = self.make(limit=0.01)
        chat = scripted([])
        with self.assertRaises(providers.AIError) as e:
            a.start("fake", "fix it", {"model": "wolf"}, chat=chat)
        self.assertIn("limit", str(e.exception))
        self.assertEqual(chat.sent, [])
        self.assertEqual(a.ledger.spent_this_month(), 0)

    def test_a_failed_request_leaves_the_conversation_as_it_was(self):
        a = self.make()

        def chat(**kw):
            raise providers.AIError("Anthropic rejected the API key.")
        r = a.start("fake", "fix it", {"model": "wolf"}, chat=chat)
        j = self.wait(a, r["job"])
        self.assertIn("rejected", j["error"])
        self.assertEqual(a.conversations[r["conversation"]]["messages"], [])

    def test_ai_off_says_where_to_turn_it_on(self):
        ai_cost.save_settings({"provider": "off"})
        a = ai_assist.Assistant([FakeTask()])
        with self.assertRaises(providers.AIError) as e:
            a.start("fake", "fix it", {"model": "wolf"}, chat=scripted([]))
        self.assertIn("Settings", str(e.exception))


class FixRigTools(unittest.TestCase):
    """The tools on a copy of the samples, through the server's own module (no network, no AI)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-ai-rig-")
        cls.saved = {k: os.environ.get(k) for k in ("AUTORIG_MODELS", "AUTORIG_WORK", "AUTORIG_ROOTS_FILE")}
        os.environ["AUTORIG_ROOTS_FILE"] = os.path.join(cls.tmp, "roots.json")
        models = os.path.join(cls.tmp, "models")
        shutil.copytree(os.path.join(REPO, "samples"), models, ignore=shutil.ignore_patterns("_autorig"))
        import layout, spec_store, blender, grades, exporter
        import server
        server.layout, server.spec_store, server.blender, server.grades, server.exporter = layout, spec_store, blender, grades, exporter
        cls.old_root = (layout.ROOT, layout.WORK)
        layout.set_root(models, os.path.join(cls.tmp, "work"), remember=False)
        spec_store.reload()
        cls.layout, cls.server = layout, server
        import ai_rig

        class Runner:
            current = None

            def submit(self, job):
                self.job = job
                return type("J", (), {"info": lambda s: {"id": 1, "label": job.step}})()

        class App:
            runner = Runner()
        cls.tool = ai_rig.FixRig(server, App())
        cls.ai_rig = ai_rig

    @classmethod
    def tearDownClass(cls):
        cls.layout.ROOT, cls.layout.WORK = cls.old_root
        for k, v in cls.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_tool(self, tool_name, **args):
        return self.tool.run(tool_name, args, {"model": "canine"})

    def test_reading(self):
        self.assertIn('"rig"', self.run_tool("get_spec")["content"])
        self.assertEqual(json.loads(self.run_tool("get_status")["content"])["name"], "canine")
        self.assertIn("no audit", self.run_tool("get_audit")["content"])
        self.assertIn("Headings", self.run_tool("spec_reference", topic="")["content"])
        self.assertIn("### Grades", self.run_tool("spec_reference", topic="grades")["content"])
        with self.assertRaises(ValueError):
            self.tool.run("get_spec", {}, {"model": "nobody"})

    def test_propose_checks_the_spec_then_apply_and_undo_round_trip(self):
        spec = json.loads(self.run_tool("get_spec")["content"])
        bad = dict(spec, schema="nope")
        with self.assertRaises(ValueError):
            self.run_tool("propose_spec", spec=bad, summary="bad")
        with self.assertRaises(ValueError):
            self.run_tool("propose_spec", spec=spec, summary="the same")
        new = json.loads(json.dumps(spec))
        new.setdefault("notes", {})["ai"] = "tried by a test"
        self.assertEqual(self.run_tool("propose_spec", spec=new, summary="")["change"]["summary"], "Change notes")
        out = self.run_tool("propose_spec", spec=new, summary="add a note")
        self.assertTrue(out["replaces"])
        self.assertIn("tried by a test", out["change"]["detail"])
        before = self.run_tool("get_spec")["content"]
        done = self.tool.apply([dict(out["change"], n=1)], {"model": "canine"})
        self.assertIn("tried by a test", self.run_tool("get_spec")["content"])
        self.assertEqual(done["job"]["label"], "AI fix: re-rig")
        self.tool.undo(done["journal"])
        self.assertEqual(self.run_tool("get_spec")["content"], before)
        with self.assertRaises(ValueError):
            self.tool.undo(done["journal"])                               # once only

    def test_a_trial_rigs_a_scratch_copy(self):
        import blender
        if os.environ.get("AUTORIG_NO_BLENDER") or not blender.find(required=False):
            self.skipTest("Blender is not installed")
        spec = json.loads(self.run_tool("get_spec")["content"])
        real = sorted(os.listdir(self.layout.pack_dir("canine")))
        out = self.run_tool("trial_rig", spec=spec, note="as it is")
        view = json.loads(out["content"].split("\nPictures:")[0])
        self.assertEqual(view["trial"], 1)
        self.assertIn(view["grade"], ("PASS", "CHECK", "FAIL"))
        self.assertEqual(sorted(os.listdir(self.layout.pack_dir("canine"))), real)    # the real model is untouched
        pics = json.loads(self.run_tool("list_pictures")["content"])
        self.assertIn("trial1_qa_render", pics)
        shown = self.run_tool("view_picture", name="trial1_qa_render")
        self.assertIn(shown["images"][0]["media_type"], ("image/png", "image/jpeg"))


if __name__ == "__main__":
    unittest.main()

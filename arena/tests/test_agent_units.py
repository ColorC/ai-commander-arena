"""arena-agent pieces on their own: context trimming, library, tool plumbing, chat client retries, route credentials."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_agent_fakes import FakeReferee  # noqa: E402  (also puts the repo root on sys.path)
from arena.agent import arena_agent, client, routes, tools  # noqa: E402
from arena.referee import rules  # noqa: E402


def tool_round(i, size):
    cid = f"call{i}"
    return [{"role": "assistant", "content": f"round {i}", "reasoning_content": "r" * 50,
             "tool_calls": [{"id": cid, "type": "function", "function": {"name": "get_summary", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": cid, "content": "x" * size}]


class Trim(unittest.TestCase):
    def test_drops_whole_old_rounds_and_keeps_notes_digest(self):
        conv = arena_agent.Conversation("SYSTEM", budget=6000, schema_tokens=500, keep_recent=6)
        conv.add({"role": "user", "content": "wake 1"})
        for i in range(30):
            for m in tool_round(i, 3000):
                conv.add(m)
        conv.notes["plan.md"] = "hold the ridge"
        info = conv.trim()
        self.assertGreater(info["dropped"], 0)
        self.assertLessEqual(conv.total(), 6000)
        ms = conv.messages
        self.assertTrue(ms[0]["content"].startswith(arena_agent.PLACEHOLDER) and "hold the ridge" in ms[0]["content"])
        self.assertEqual(ms[0]["role"], "user")
        self.assertEqual(ms[-1]["tool_call_id"], "call29")           # the newest round survives
        ids = set()
        for m in ms:                                                  # no orphan tool message, no unanswered tool call
            if m.get("tool_calls"):
                ids |= {c["id"] for c in m["tool_calls"]}
            if m["role"] == "tool":
                self.assertIn(m["tool_call_id"], ids)
        answered = {m["tool_call_id"] for m in ms if m["role"] == "tool"}
        self.assertEqual(ids, answered)
        self.assertEqual(conv.request()[0], {"role": "system", "content": "SYSTEM"})
        # a second trim keeps a single placeholder and counts cumulatively
        for m in tool_round(99, 20000):
            conv.add(m)
        conv.trim()
        self.assertEqual(sum(m["content"].startswith(arena_agent.PLACEHOLDER) for m in conv.messages if m["role"] == "user"), 1)

    def test_small_conversation_untouched_and_reasoning_dropped_on_new_wake(self):
        conv = arena_agent.Conversation("S", budget=50000, schema_tokens=100)
        conv.add({"role": "user", "content": "w1"})
        conv.add({"role": "assistant", "content": "a", "reasoning_content": "secret thoughts", "extra_field": 1})
        self.assertIsNone(conv.trim())
        self.assertNotIn("extra_field", conv.messages[-1])
        self.assertIn("reasoning_content", conv.messages[-1])
        conv.add({"role": "user", "content": "w2"})
        self.assertNotIn("reasoning_content", conv.messages[1])


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "sub"))
        Path(self.tmp.name, "FM01.txt").write_text(
            "Intro line\n\nMovement\nHasty movement is fast but tires units.\nDeliberate movement is slow.\n\nFire support\nArtillery needs a spotter.\n", encoding="utf-8")
        Path(self.tmp.name, "sub", "faq.txt").write_text("Q: artillery hasty?\nA: see FM01\n", encoding="utf-8")
        self.lib = tools.Library(self.tmp.name)

    def test_search_ranks_by_matched_keywords_with_file_and_lines(self):
        hits = self.lib.search("hasty movement")
        self.assertEqual((hits[0]["file"], hits[0]["lines"]), ("FM01.txt", "3-5"))
        self.assertIn("Hasty movement", hits[0]["snippet"])
        self.assertEqual(hits[0]["matched"], "2/2")
        self.assertEqual({h["file"] for h in self.lib.search("artillery")}, {"FM01.txt", "sub/faq.txt"})

    def test_read_ranges_and_listing_and_no_escape(self):
        self.assertTrue(self.lib.read("FM01.txt", 3, 4).splitlines()[1].startswith("3: Movement"))
        self.assertIn("faq", self.lib.read("faq", 1, 1))              # unique partial name
        with self.assertRaises(ValueError):
            self.lib.read("../FM01.txt")
        with self.assertRaises(ValueError):
            self.lib.read(os.path.join(self.tmp.name, "FM01.txt"))
        box = tools.ToolBox(None, self.lib)
        self.assertEqual({f["file"] for f in json.loads(box.run("library_read", {}).text)}, {"FM01.txt", "sub/faq.txt"})
        self.assertIn("没有叫", box.run("library_read", {"file": "../x"}).text)
        self.assertIn("FM01.txt", box.run("library_search", {"query": "spotter", "max_results": "3"}).text)


class ToolPlumbing(unittest.TestCase):
    def test_arguments_paths_and_errors(self):
        with FakeReferee("t") as ref:
            box = tools.ToolBox(tools.Referee(ref.url, "t"), tools.Library(tempfile.gettempdir()))
            self.assertIn("缺少必填参数", box.run("get_unit", {}).text)
            self.assertIn("get_terrain 需要", box.run("get_terrain", {}).text)
            self.assertEqual(ref.requests, [])                         # bad calls never reach the referee
            box.run("get_terrain", {"hexes": [412, 413]})
            self.assertEqual(ref.requests[-1][2], {"hexes": "412,413"})
            box.run("get_terrain", {"box": [1, 2, 3, 4]})
            self.assertEqual(ref.requests[-1][2], {"box": "1,2,3,4"})
            box.run("get_unit", {"name": "1 Pl A/Coy"})
            self.assertEqual(ref.requests[-1][1], "view/unit/1 Pl A/Coy")  # server side decodes the %2F back
            # object arguments sent as JSON strings are accepted
            r = box.run("submit_command", {"kind": "orders", "unit": "A", "orders": '[{"orders": "Screen"}]'})
            self.assertEqual(ref.requests[-1][3]["orders"], [{"orders": "Screen"}])
            self.assertEqual(r.status, 200)
            box.run("notes_put", {"path": "a/b.md", "content": "x"})
            self.assertEqual(ref.requests[-1][:2], ("PUT", "notes/a/b.md"))
            box.run("withdraw_command", {"cmd_id": "c1"})
            self.assertEqual(ref.requests[-1][:2], ("DELETE", "queue/c1"))
            box.run("get_events", {"since": 5})
            self.assertEqual(ref.requests[-1][2], {"since": "5"})
            ref.idle = True
            r = box.run("get_summary", {})
            self.assertTrue(r.turn_over)
            self.assertIn('"http_status": 409', r.text)
        down = tools.ToolBox(tools.Referee("http://127.0.0.1:9", "t", timeout=2), tools.Library(tempfile.gettempdir()))
        self.assertIn("error", down.run("get_summary", {}).text)       # unreachable referee becomes a tool error, not a crash

    def test_turn_over_detection_matches_the_referee_wording(self):
        for msg in ("side is idle: wait for a wake with /agent/wait", "deputy has no open turn", "turn_id does not match the open turn"):
            self.assertTrue(tools.is_turn_over(409, {"error": msg}), msg)
        self.assertFalse(tools.is_turn_over(409, {"error": "observation belongs to the commander's open turn"}))
        self.assertFalse(tools.is_turn_over(400, {"error": "side is idle"}))

    def test_both_sides_get_identical_tools(self):
        names = [t["function"]["name"] for t in tools.schemas()]
        self.assertEqual(len(names), len(set(names)))
        for want in ("get_summary", "get_unit", "get_contacts", "get_vp", "get_terrain", "get_queue", "get_thinking", "get_events", "get_briefing",
                     "list_units", "submit_command", "withdraw_command", "set_observe", "put_script", "delete_script", "list_scripts",
                     "run_compute", "notes_get", "notes_put", "library_search", "library_read", "spawn_subagent", "end_turn"):
            self.assertIn(want, names)
        self.assertEqual([n for n in names if n not in [t["function"]["name"] for t in tools.schemas(True)]], ["spawn_subagent", "check_subagents"])
        for t in tools.schemas():
            json.dumps(t)
            self.assertEqual(t["function"]["parameters"]["type"], "object")

    def test_exercise_tools_follow_the_training_referees_request(self):
        req = tools.ALL["request_exercise"]["function"]
        props = req["parameters"]["properties"]
        self.assertEqual(set(props), {"type", "opponent", "play_side", "pacing", "units"})
        self.assertEqual(req["parameters"]["required"], ["type", "opponent", "play_side"])
        self.assertEqual((props["type"]["enum"], props["opponent"]["enum"], props["play_side"]["enum"]),
                         (["full", "small"], ["ai", "self"], [0, 1]))
        for quota in ("4 场", f"{rules.EXERCISE_FULL_WALL_MIN} 分钟", "60 游戏分钟", "3 小时", "10 个", "额度"):   # the quotas are enforced, never shown
            self.assertNotIn(quota, req["description"])
        self.assertEqual(tools.BY_NAME["request_exercise"][1:4], ("POST", "exercise", "json"))
        self.assertEqual(tools.BY_NAME["list_exercises"][1:4], ("GET", "exercises", None))


class FakeModelServer:
    """OpenAI-compatible chat completions endpoint that plays a list of (status, headers, body) answers."""

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                status, headers, out = outer.answers.pop(0) if outer.answers else (500, {}, {})
                raw = json.dumps(out).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


OK_BODY = {"id": "cmpl-1", "model": "reported-model-7", "usage": {"prompt_tokens": 12, "completion_tokens": 5},
           "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "refusal": None,
                        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "end_turn", "arguments": "{}"}}]}}]}


class ChatClientTests(unittest.TestCase):
    def route(self, base, key=lambda: "K", extra=None):
        return routes.Route("t", base, "wanted-model", key, headers={"x-session": "s"}, extra=extra or {})

    def test_retries_then_reports_what_the_provider_said(self):
        srv = FakeModelServer([(429, {"Retry-After": "0"}, {"error": "slow down"}), (503, {}, {"error": "busy"}),
                               (200, {"x-ratelimit-remaining": "41", "Content-Type": "application/json"}, OK_BODY)])
        self.addCleanup(srv.close)
        retries, slept = [], []
        c = client.ChatClient(self.route(srv.base, extra={"reasoning_effort": "high"}), on_retry=retries.append, sleep=slept.append, backoff=0.01)
        r = c.chat([{"role": "user", "content": "hi"}], tools.schemas())
        self.assertEqual((r.attempts, r.model, r.usage["prompt_tokens"], r.finish_reason), (3, "reported-model-7", 12, "tool_calls"))
        self.assertEqual(r.headers, {"x-ratelimit-remaining": "41"})
        self.assertNotIn("refusal", r.message)
        self.assertEqual([x["error"][:8] for x in retries], ["HTTP 429", "HTTP 503"])
        path, headers, body = srv.seen[-1]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual((headers["authorization"], headers["x-session"], body["model"], body["reasoning_effort"]), ("Bearer K", "s", "wanted-model", "high"))
        self.assertEqual(len(body["tools"]), len(tools.schemas()))

    def test_client_errors_are_not_retried_and_deadline_is_respected(self):
        srv = FakeModelServer([(400, {}, {"error": "bad request"}), (500, {}, {}), (500, {}, {})])
        self.addCleanup(srv.close)
        c = client.ChatClient(self.route(srv.base), sleep=lambda s: None, backoff=0.01)
        with self.assertRaises(client.ModelError) as e:
            c.chat([], [])
        self.assertEqual((e.exception.status, len(srv.seen)), (400, 1))
        with self.assertRaises(client.DeadlineExceeded):
            client.ChatClient(self.route(srv.base), sleep=lambda s: None, backoff=50, backoff_max=50).chat([], [], deadline=time.time() + 5)

    def test_unavailable_credential_is_retried_until_it_comes_back(self):
        srv = FakeModelServer([(200, {}, OK_BODY)])
        self.addCleanup(srv.close)
        calls = []

        def key():
            calls.append(1)
            if len(calls) < 3:
                raise routes.TokenExpired("expired")
            return "fresh"
        c = client.ChatClient(self.route(srv.base, key=key), sleep=lambda s: None, key_wait=0)
        self.assertEqual(c.chat([], []).attempts, 3)
        self.assertEqual(srv.seen[0][1]["authorization"], "Bearer fresh")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = routes.HOME, os.environ.pop("KIMI_API_KEY", None)
        routes.HOME = self.tmp.name
        self.addCleanup(self.restore)
        self.cred = os.path.join(self.tmp.name, ".kimi-code", "credentials", "kimi-code.json")
        os.makedirs(os.path.dirname(self.cred))

    def restore(self):
        routes.HOME = self.old[0]
        if self.old[1] is not None:
            os.environ["KIMI_API_KEY"] = self.old[1]

    def test_kimi_expired_token_is_reported_not_refreshed(self):
        Path(self.cred).write_text(json.dumps({"access_token": "A", "refresh_token": "R", "expires_at": time.time() - 5}), encoding="utf-8")
        before = hashlib.sha256(Path(self.cred).read_bytes()).hexdigest()
        with self.assertRaises(routes.TokenExpired) as e:
            routes.kimi_token()
        self.assertIn("kimi -p ok", str(e.exception))
        self.assertEqual(hashlib.sha256(Path(self.cred).read_bytes()).hexdigest(), before)
        Path(self.cred).write_text(json.dumps({"access_token": "A", "expires_at": time.time() + 600}), encoding="utf-8")
        self.assertEqual(routes.kimi_token(), "A")

    def test_route_table(self):
        self.assertEqual(set(routes.ROUTES), {"kimi-code", "deepseek-go", "deepseek-proxy", "deepseek-1017-dashscope", "fake"})
        self.assertEqual(routes.ROUTES["kimi-code"].model, "k3.1-pro")
        r = routes.get_route("kimi-code", {"reasoning_effort": "high"})
        self.assertEqual(r.extra["reasoning_effort"], "high")
        with self.assertRaises(SystemExit):
            routes.get_route("nope")

    def test_prompt_is_neutral(self):
        text = Path(arena_agent.__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8")
        for word in ("Kimi", "DeepSeek", "kimi", "deepseek"):
            self.assertNotIn(word, text)
        for ph in ("{{side_label}}", "{{opponent_line}}", "{{force_name}}", "{{scenario_name}}"):
            self.assertIn(ph, text)

    def test_prompt_names_the_opponent(self):
        text = Path(arena_agent.__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8")
        me = types.SimpleNamespace(template=text, side=0)
        match = arena_agent.Agent.build_system(me, {"opponent": "commander", "scenario": "S"})
        exam = arena_agent.Agent.build_system(me, {"opponent": "game_ai", "scenario": "S"})
        self.assertIn("完全相同", match)
        self.assertNotIn("内置 AI", match.split("# 这是一场计分比赛")[0])
        self.assertIn("游戏内置 AI", exam)
        self.assertNotIn("完全相同", exam)
        self.assertNotIn("{{", exam)
        self.assertNotIn("训练期", match)
        p5 = arena_agent.Agent.build_system(me, {"opponent": "commander", "scenario": "S", "workspace": "carried"})
        self.assertIn("训练期写的笔记和登记的脚本", p5)
        self.assertIn("以 brief show 为准", p5)
        self.assertNotIn("{{", p5)

    def test_training_and_exercises_have_their_own_framing(self):
        text = Path(arena_agent.__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8")
        me = types.SimpleNamespace(template=text, side=1)
        training = arena_agent.Agent.build_system(me, {"scenario": "S", "sides": {"0": {}, "1": {}}})
        self.assertIn("训练期", training)
        self.assertIn("参赛方 B", training)
        self.assertNotIn("{{", training)
        self.assertNotIn("补训", training)
        sup = arena_agent.Agent.build_system(me, {"scenario": "S", "sides": {"0": {}, "1": {}}, "supplement": "RULING_TEXT"})
        self.assertTrue(sup.startswith(training.rstrip("\n")))
        self.assertIn("# 补训", sup)
        self.assertTrue(sup.rstrip().endswith("RULING_TEXT"))
        # the notes round between the games: the ruling brings its own title
        notes = arena_agent.Agent.build_system(me, {"scenario": "S", "sides": {"0": {}, "1": {}},
                                                    "supplement": "# 局间整理\n\nNOTES_ROUND"})
        self.assertTrue(notes.startswith(training.rstrip("\n")))
        self.assertIn("\n# 局间整理\n\nNOTES_ROUND", notes)
        self.assertNotIn("补训", notes)
        for word in ("Kimi", "DeepSeek", "kimi", "deepseek", "波军的指挥官"):
            self.assertNotIn(word, training)
        small = arena_agent.Agent.build_system(me, {"opponent": "commander", "scenario": "S", "exercise": {
            "scored": False, "pacing": "turn", "opponent": "self",
            "small_experiment": {"units": ["1/2/9 MR", "2/2/9 MR"], "game_minutes": 60}}})
        head = small.split("# 这是一场计分比赛")[0]
        self.assertIn("训练期的演习", head)
        self.assertIn("陪练", head)  # season 2: the sparring partner, not a clone
        self.assertIn("1/2/9 MR、2/2/9 MR", head)
        self.assertIn("回合制", head)
        self.assertNotIn("考核局", small)
        vs_ai = arena_agent.Agent.build_system(me, {"opponent": "game_ai", "scenario": "S", "exercise": {
            "scored": False, "pacing": "formal", "opponent": "game_ai"}})
        self.assertIn("游戏内置 AI", vs_ai)
        self.assertNotIn("考核局", vs_ai)
        self.assertNotIn("回合制", vs_ai)


if __name__ == "__main__":
    unittest.main()

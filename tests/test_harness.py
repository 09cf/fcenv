"""Tests for fcenv.harness, with a scripted stand-in for the API client."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic.types import Message  # noqa: E402

from fcenv import harness  # noqa: E402
from fcenv.session import Notebook  # noqa: E402
from tests.test_edit import LEVEL  # noqa: E402


class FakeClient:
    """Replies with scripted turns: each a list of (tool name, input) calls,
    or a string for a text-only reply. Keeps each request's messages."""

    def __init__(self, turns, stop_reason=None):
        self.turns = list(turns)
        self.stop_reason = stop_reason
        self.requests = []
        self.messages = self

    def stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        turn = self.turns.pop(0) if self.turns else "done"
        n = len(self.requests)
        if isinstance(turn, str):
            content = [{"type": "text", "text": turn}]
        else:
            content = [{"type": "thinking", "thinking": "hmm", "signature": "sig"}] + [
                {"type": "tool_use", "id": f"t{n}_{i}", "name": name, "input": args}
                for i, (name, args) in enumerate(turn)]
        stop = self.stop_reason or ("end_turn" if isinstance(turn, str) else "tool_use")
        msg = Message.model_validate({
            "id": f"m{n}", "type": "message", "role": "assistant", "model": "fake",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}})
        return _Stream(msg)


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __iter__(self):
        return iter(())

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


def play(turns, **kw):
    out = Path(tempfile.mkdtemp())
    client = FakeClient(turns, kw.pop("stop_reason", None))
    r = harness.play(LEVEL, out, client=client, log=lambda m: None, **kw)
    return r, out, client


class TestHarness(unittest.TestCase):
    def test_build_run_submit(self):
        r, out, client = play([
            [("describe", {}), ("view", {})],
            [("add_rod", {"kind": "wood", "a": "0:1", "b": {"from": "0:1", "offset": [60, 0]}})],
            [("run", {"timeout": 30}), ("run_frame", {"tick": 10})],
            [("submit", {})],
        ])
        self.assertEqual(r["stop"], "submitted")
        self.assertTrue(r["submitted_by_agent"])
        self.assertEqual((r["tool_calls"], r["turns"]), (6, 4))
        self.assertEqual(r["usage"]["input_tokens"], 40)
        self.assertEqual(json.loads((out / "result.json").read_text()), r)

        # the prompt, then turns; the history only grows
        first = client.requests[0]["messages"]
        self.assertIn("experimental evaluation", first[0]["content"][0]["text"])
        self.assertEqual(len(client.requests[-1]["messages"]), 7)
        self.assertEqual(client.requests[1]["messages"][:2], client.requests[-1]["messages"][:2])
        results = client.requests[1]["messages"][2]["content"]
        self.assertEqual([b["tool_use_id"] for b in results], ["t1_0", "t1_1"])
        self.assertEqual([b["type"] for b in results[1]["content"]], ["text", "image"])

        # the transcript has image files instead of base64
        lines = [json.loads(s) for s in (out / "transcript.jsonl").read_text().splitlines()]
        self.assertEqual([m["role"] for m in lines], ["user"] + ["assistant", "user"] * 4)
        img = lines[2]["content"][1]["content"][1]
        self.assertEqual(img, {"type": "image", "file": "images/0000.png"})
        self.assertTrue((out / img["file"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(lines[1]["content"][0]["type"], "thinking")
        log = [json.loads(s) for s in (out / "tools.jsonl").read_text().splitlines()]
        self.assertEqual([(e["turn"], e["tool_use_id"]) for e in log],
                         [(1, "t1_0"), (1, "t1_1"), (2, "t2_0"), (3, "t3_0"), (3, "t3_1"),
                          (4, "t4_0")])
        md = (out / "transcript.md").read_text()
        self.assertTrue(md.startswith("# "))
        self.assertIn("**Not solved** (submitted); 6 tool calls, 4 turns", md)
        self.assertIn("## Turn 4", md)
        self.assertIn('**→ add_rod**(kind="wood", a="0:1", b={"from": "0:1", "offset": [60, 0]})',
                      md)
        self.assertIn("![](images/0000.png)", md)
        self.assertIn("<summary><i>thinking</i></summary>", md)
        self.assertNotIn("base64", md)
        for f in ("final.xml", "final_save.xml", "final_view.png", "final_trails.png"):
            self.assertTrue((out / f).stat().st_size > 0, f)
        self.assertIn('id="1"', (out / "final.xml").read_text())

    def test_the_record_replays(self):
        """tools.jsonl and versions.jsonl are the whole build: replaying the
        calls on a fresh session gives the same versions, and each entry's
        version_after is the design after that call."""
        r, out, _ = play([
            [("build", {"pieces": [{"rod": "wood", "a": "0:1", "b": [60, -20]},
                                   {"rod": "water", "a": "@0:1", "b": [60, 40]}]})],
            [("add_wheel", {"spin": "cw", "center": "1:1"}), ("save", {"name": "a"})],
            [("move_joint", {"joint": "2:1", "to": [80, 40]}), ("undo", {}), ("redo", {})],
            [("delete", {"blocks": [3]}), ("load", {"name": "a"}), ("run", {"timeout": 20})],
            [("submit", {})]])
        log = [json.loads(s) for s in (out / "tools.jsonl").read_text().splitlines()]
        versions = [json.loads(s) for s in (out / "versions.jsonl").read_text().splitlines()]
        self.assertEqual([v["version"] for v in versions], list(range(len(versions))))
        self.assertEqual(versions[0]["xml"], LEVEL)
        s = harness.Session(LEVEL)
        for e in log:
            harness.tools.dispatch(s, e["tool"], e["args"])
            self.assertEqual(s.xml, versions[e["version_after"]]["xml"], e["tool"])
        self.assertEqual(s.versions, [v["xml"] for v in versions])
        self.assertEqual([e["version_after"] for e in log], [1, 2, 2, 3, 2, 3, 4, 5, 5, 5])

    def test_errors_are_marked(self):
        r, out, client = play([[("delete", {"blocks": [0]}), ("nope", {})], [("submit", {})]])
        results = client.requests[1]["messages"][2]["content"]
        self.assertEqual([b["is_error"] for b in results], [True, True])

    def test_tool_limit_evaluates_the_current_design(self):
        r, _, client = play([[("describe", {})]] * 5, max_tool_calls=3)
        self.assertEqual((r["stop"], r["tool_calls"], r["turns"]), ("tool_limit", 3, 3))
        self.assertFalse(r["submitted_by_agent"])
        self.assertFalse(r["solved"])

    def test_nudges_then_gives_up(self):
        r, _, client = play(["thinking out loud", [("describe", {})], "done?", "really done"],
                            max_nudges=2)
        self.assertEqual((r["stop"], r["nudges"], r["turns"]), ("gave_up", 2, 4))
        self.assertIn("No one is here", client.requests[1]["messages"][-1]["content"][0]["text"])

    def test_turn_limit_and_refusal(self):
        r, _, _ = play([[("describe", {})]] * 5, max_turns=2)
        self.assertEqual((r["stop"], r["turns"]), ("turn_limit", 2))
        r, _, _ = play(["no"], stop_reason="refusal")
        self.assertEqual((r["stop"], r["turns"]), ("refusal", 1))

    def test_cut_off_without_calls(self):
        r, _, client = play(["thinking and thinking"], stop_reason="max_tokens", max_turns=2)
        self.assertEqual(r["nudges"], 2)  # both fake turns are cut off
        self.assertIn("hit the output limit (32000 tokens",
                      client.requests[1]["messages"][-1]["content"][0]["text"])

    def test_cut_off_calls_are_not_run(self):
        r, _, client = play([[("add_rod", {"kind": "wood", "a": [0, 0], "b": [0, 0]})]],
                            stop_reason="max_tokens", max_turns=2)
        self.assertEqual(r["tool_calls"], 0)
        res = client.requests[1]["messages"][2]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertIn("cut off", res["content"][0]["text"])

    def test_notes_are_off_by_default(self):
        _, out, client = play([[("write_notes", {"text": "x"})], [("submit", {})]])
        self.assertNotIn("write_notes", [t["name"] for t in client.requests[0]["tools"]])
        self.assertNotIn("notebook", client.requests[0]["messages"][0]["content"][0]["text"])
        res = client.requests[1]["messages"][2]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertFalse((out / "notes.md").exists())

    def test_notes_are_free_and_work_after_the_level(self):
        nb = Notebook(cap=20)
        r, out, client = play([
            [("write_notes", {"text": "a"}), ("describe", {})],  # describe: the one call
            # the level is over (out of calls): the notes phase
            [("write_notes", {"text": "far too long for the cap"})],
            [("write_notes", {"text": "b"})],
            "done"], max_tool_calls=1, notebook=nb)
        self.assertEqual((r["stop"], r["tool_calls"], r["notes_turns"]), ("tool_limit", 1, 3))
        self.assertEqual(nb.text, "b")
        self.assertEqual((out / "notes.md").read_text(), "b")
        self.assertIn("write_notes", [t["name"] for t in client.requests[0]["tools"]])
        # told the outcome after the last tool results; a rejected write says why
        msgs = client.requests[1]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "user"])
        self.assertIn("You ran out of tool calls, so your design as it was got evaluated: "
                      "Not solved.", msgs[3]["content"][0]["text"])
        md = (out / "transcript.md").read_text()
        self.assertIn("**Harness:**\n\n> The level is over.", md)
        self.assertIn("**→ write_notes**()\n\n```text\nb\n```", md)
        msgs = client.requests[2]["messages"]
        self.assertIn("Too long", msgs[5]["content"][0]["content"][0]["text"])
        log = [json.loads(s) for s in (out / "tools.jsonl").read_text().splitlines()]
        self.assertEqual([(e["tool"], e["ok"]) for e in log],
                         [("write_notes", True), ("describe", True), ("write_notes", False),
                          ("write_notes", True)])

    def test_notes_written_as_text_get_a_reminder(self):
        nb = Notebook()
        r, _, client = play([[("submit", {})], "write_notes: lesson one",
                             [("write_notes", {"text": "lesson one"})], "done"], notebook=nb)
        self.assertEqual((nb.text, r["notes_turns"]), ("lesson one", 3))
        self.assertIn("Your notes are unchanged",
                      client.requests[2]["messages"][-1]["content"][0]["text"])
        # leaving them as they are is fine too: one reminder, then done
        r, _, client = play([[("submit", {})], "nothing new", "done"], notebook=Notebook("x"))
        self.assertEqual(r["notes_turns"], 2)
        self.assertEqual(len(client.requests), 3)

    def test_series_carries_notes_over(self):
        d = Path(tempfile.mkdtemp())
        (d / "lvl.xml").write_text(LEVEL)
        client = FakeClient([
            [("submit", {})], [("write_notes", {"text": "rods fold; triangulate"})], "done",
            [("submit", {})], "nothing new"])
        nb = Notebook()
        summary = harness.run_series([d / "lvl.xml"] * 2, d / "out", notebook=nb,
                                     client=client, log=lambda m: None)
        self.assertEqual([r["level"] for r in summary["levels"]], ["lvl", "lvl-2"])
        self.assertEqual((summary["played"], summary["solved"]), (2, 0))
        first = client.requests[0]["messages"][0]["content"][0]["text"]
        self.assertIn("level 1 of 2", first)
        self.assertIn("<notes>\n(empty)\n</notes>", first)
        second = client.requests[3]["messages"][0]["content"][0]["text"]
        self.assertIn("level 2 of 2", second)
        self.assertIn("<notes>\nrods fold; triangulate\n</notes>", second)
        self.assertEqual(len(client.requests[3]["messages"]), 1)  # a fresh conversation
        self.assertIn("You submitted: Not solved.",
                      client.requests[1]["messages"][-1]["content"][0]["text"])
        self.assertEqual((d / "out" / "notes.md").read_text(), "rods fold; triangulate")
        self.assertEqual((d / "out" / "lvl" / "notes.md").read_text(), "rods fold; triangulate")
        self.assertEqual(json.loads((d / "out" / "summary.json").read_text()), summary)

    def test_budget(self):
        d = Path(tempfile.mkdtemp())
        (d / "lvl.xml").write_text(LEVEL)
        client = FakeClient([[("describe", {})]] * 5)
        # each fake turn costs 10 input and 5 output tokens: $0.000175 on Opus 5
        self.assertAlmostEqual(harness.cost("claude-opus-5",
                                            {"input_tokens": 10, "output_tokens": 5}), 0.000175)
        self.assertIsNone(harness.cost("fake", {}))
        summary = harness.run_series([d / "lvl.xml"] * 2, d / "out", budget=0.0003,
                                     client=client, model="claude-opus-5", log=lambda m: None)
        self.assertEqual(summary["played"], 1)
        self.assertEqual((summary["levels"][0]["stop"], summary["levels"][0]["turns"]),
                         ("budget", 2))

    def test_request_parameters(self):
        _, _, client = play([[("submit", {})]], effort="high", model="m")
        p = client.requests[0]
        self.assertEqual((p["model"], p["output_config"]), ("m", {"effort": "high"}))
        self.assertEqual(p["thinking"]["type"], "adaptive")
        self.assertEqual(p["cache_control"], {"type": "ephemeral"})
        self.assertEqual(p["tools"], harness.tools.TOOLS)


if __name__ == "__main__":
    unittest.main()

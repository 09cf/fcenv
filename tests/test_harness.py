"""Tests for fcenv.harness, with a scripted stand-in for the API client."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic.types import Message  # noqa: E402

from fcenv import harness  # noqa: E402
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
        for f in ("final.xml", "final_save.xml", "final_view.png", "final_trails.png"):
            self.assertTrue((out / f).stat().st_size > 0, f)
        self.assertIn('id="1"', (out / "final.xml").read_text())

    def test_the_record_replays(self):
        """tools.jsonl and versions.jsonl are the whole build: replaying the
        calls on a fresh session gives the same versions, and each entry's
        version_after is the design after that call."""
        r, out, _ = play([
            [("add_rods", {"rods": [{"kind": "wood", "a": "0:1", "b": [60, -20]},
                                    {"kind": "water", "a": "@0:1", "b": [60, 40]}]})],
            [("add_wheel", {"spin": "cw", "center": "1:1"}), ("save", {"name": "a"})],
            [("move_joint", {"joint": "2:1", "to": [80, 40]}), ("undo", {}), ("redo", {})],
            [("delete", {"block": 3}), ("load", {"name": "a"}), ("run", {"timeout": 20})],
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
        r, out, client = play([[("delete", {"block": 0}), ("nope", {})], [("submit", {})]])
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

    def test_cut_off_calls_are_not_run(self):
        r, _, client = play([[("add_rod", {"kind": "wood", "a": [0, 0], "b": [0, 0]})]],
                            stop_reason="max_tokens", max_turns=2)
        self.assertEqual(r["tool_calls"], 0)
        res = client.requests[1]["messages"][2]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertIn("cut off", res["content"][0]["text"])

    def test_request_parameters(self):
        _, _, client = play([[("submit", {})]], effort="high", model="m")
        p = client.requests[0]
        self.assertEqual((p["model"], p["output_config"]), ("m", {"effort": "high"}))
        self.assertEqual(p["thinking"]["type"], "adaptive")
        self.assertEqual(p["cache_control"], {"type": "ephemeral"})
        self.assertIs(p["tools"], harness.tools.TOOLS)


if __name__ == "__main__":
    unittest.main()

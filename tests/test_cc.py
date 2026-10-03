"""Tests for fcenv.cc (and fcenv.toolserver), with tests/fake_claude.py
standing in for Claude Code."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fcenv import cc, harness  # noqa: E402
from fcenv.session import Notebook  # noqa: E402
from tests.test_edit import LEVEL  # noqa: E402

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_claude.py")]


def play(runs, usage=(0.1, 0.0), delay=0, model="fake-model", **kw):
    d = Path(tempfile.mkdtemp())
    script = d / "script.json"
    script.write_text(json.dumps({"runs": runs, "usage": list(usage), "delay": delay,
                                  "model": model, "msg_usage": [1000, 100]}))
    import os
    os.environ["FAKE_CLAUDE_SCRIPT"] = str(script)
    r = cc.play(LEVEL, d / "out", claude=FAKE, log=lambda m: None, **kw)
    args = [json.loads(p.read_text()) for p in sorted(d.glob("script.args*"))]
    return r, d / "out", args


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines()]


class TestClaudeCode(unittest.TestCase):
    def test_a_level(self):
        r, out, args = play([[
            [["describe", {}], ["view", {}]],
            [["add_rod", {"kind": "wood", "a": "0:1", "b": [80, -20]}]],
            [["submit", {}]]]], usage=(0.1, 0.05), max_level_usage=None)
        self.assertEqual((r["stop"], r["runner"], r["model"]), ("submitted", "claude-code",
                                                                "fake-model"))
        self.assertEqual((r["tool_calls"], r["turns"], r["nudges"]), (4, 3, 0))
        self.assertEqual(r["plan_usage"], {"five_hour": [0.15, 0.25], "seven_day": [0.3, 0.3]})
        self.assertEqual(r["usage"], {"input_tokens": 10, "output_tokens": 5})
        # isolated: our tools only, no system prompt or settings, a fresh session
        a = args[0]
        self.assertEqual(a[a.index("--system-prompt") + 1], "")
        self.assertEqual(a[a.index("--tools") + 1], "")
        self.assertEqual(a[a.index("--setting-sources") + 1], "")
        self.assertIn("mcp__fc__submit", a[a.index("--allowedTools") + 1].split(","))
        self.assertIn("--session-id", a)
        self.assertIn("experimental evaluation", a[a.index("-p") + 1])
        # the record: calls linked to the transcript, which has names without
        # the MCP prefix and images as files; one message per turn
        log = lines(out / "tools.jsonl")
        self.assertEqual([(e["tool"], e["turn"], e["tool_use_id"]) for e in log],
                         [("describe", 1, "t1_0"), ("view", 1, "t1_1"),
                          ("add_rod", 2, "t2_0"), ("submit", 3, "t3_0")])
        transcript = lines(out / "transcript.jsonl")
        self.assertEqual([m["role"] for m in transcript],
                         ["user"] + ["assistant", "user"] * 3)
        first = transcript[1]["content"]
        self.assertEqual([b["type"] for b in first], ["thinking", "tool_use", "tool_use"])
        self.assertEqual(first[2]["name"], "view")
        self.assertEqual(transcript[2]["content"][1]["content"][1],
                         {"type": "image", "file": "images/0000.png"})
        self.assertEqual(len(lines(out / "versions.jsonl")), 2)
        self.assertTrue((out / "final_view.png").exists())
        self.assertTrue((out / "cc" / "stream.jsonl").exists())

    def test_nudges_and_notes(self):
        nb = Notebook()
        r, out, args = play([
            [[["describe", {}]], "let me think"],  # stops without submitting
            [[["submit", {}]]],  # after the nudge
            ["write_notes: a lesson"],  # notes phase: notes written as text
            [[["write_notes", {"text": "a lesson"}]], "done"],  # after the reminder
        ], notebook=nb)
        self.assertEqual((r["stop"], r["nudges"], r["turns"], r["notes_turns"]),
                         ("submitted", 1, 3, 3))
        self.assertEqual(nb.text, "a lesson")
        self.assertEqual(len(args), 4)
        self.assertIn("--resume", args[1])
        self.assertEqual(args[1][args[1].index("--resume") + 1],
                         args[0][args[0].index("--session-id") + 1])
        self.assertIn("No one is here", args[1][args[1].index("-p") + 1])
        self.assertIn("You submitted", args[2][args[2].index("-p") + 1])
        self.assertIn("notes are unchanged", args[3][args[3].index("-p") + 1])
        self.assertIn("<notes>", args[0][args[0].index("-p") + 1])

    def test_usage_limit(self):
        r, _, args = play([[[["describe", {}]], [["describe", {}]], [["submit", {}]]]],
                          usage=(0.75, 0.04), delay=1, max_usage=0.8)
        self.assertEqual(r["stop"], "usage_limit")
        self.assertLess(r["tool_calls"], 3)
        self.assertFalse(r["submitted_by_agent"])

    def test_the_90_percent_warning_is_not_a_limit(self):
        r, _, _ = play([[[["describe", {}]], [["describe", {}]], [["submit", {}]]]],
                       usage=(0.88, 0.01), max_usage=1.0)
        self.assertEqual((r["stop"], r["plan_usage"]["five_hour"]), ("submitted", [0.89, 0.91]))

    def test_level_usage_limit(self):
        nb = Notebook()
        r, _, args = play([[[["describe", {}]]] * 6, [[["write_notes", {"text": "x"}]], "ok"]],
                          usage=(0.1, 0.04), delay=1, max_level_usage=0.1, notebook=nb)
        # first seen 0.14, then 0.18, 0.22, 0.26: stopped at the 4th turn's report,
        # before its call
        self.assertEqual((r["stop"], r["tool_calls"]), ("level_usage", 3))
        self.assertEqual(nb.text, "x")  # the notes phase still ran
        self.assertIn("used up its share", args[1][args[1].index("-p") + 1])
        self.assertTrue(r["usage_partial"])  # killed: no result event

    def test_level_budget(self):
        # each fake message: 1000 input and 100 output tokens, $0.006 on Opus 5.5
        r, out, args = play([[[["describe", {}]]] * 6], delay=1, model="claude-opus-5-5",
                            level_budget=0.02)
        self.assertIn("--include-partial-messages", args[0])
        self.assertIn("This level has a budget", args[0][args[0].index("-p") + 1])
        # 0.006, 0.012, 0.018, then 0.024: over, before the 4th call
        self.assertEqual((r["stop"], r["tool_calls"]), ("level_budget", 3))
        self.assertEqual((r["list_price_cost"], r["level_budget"]), (0.024, 0.02))
        self.assertEqual(r["usage"], {"input_tokens": 4000, "output_tokens": 400})  # exact
        notes = [b["content"][-1]["text"] for m in lines(out / "transcript.jsonl")
                 if m["role"] == "user" for b in m["content"] if b["type"] == "tool_result"]
        self.assertEqual(notes, ["[Budget: 30% of this level's used]",
                                 "[Budget: 60% of this level's used]",
                                 "[Budget: 90% of this level's used; less than one more turn at "
                                 "the current rate] When it runs out, the level ends and your "
                                 "design is evaluated as it is."])
        # hidden: the same limit, but nothing shown
        r, out, args = play([[[["describe", {}]]] * 6], delay=1, model="claude-opus-5-5",
                            level_budget=0.02, show_budget=False)
        self.assertEqual(r["stop"], "level_budget")
        self.assertNotIn("budget", args[0][args[0].index("-p") + 1])
        self.assertNotIn("[Budget", (out / "transcript.jsonl").read_text())

    def test_time_limit(self):
        r, _, _ = play([[[["describe", {}]], "sleep", [["submit", {}]]]], max_minutes=0.05)
        self.assertEqual((r["stop"], r["tool_calls"]), ("time_limit", 1))

    def test_relative_output_and_claude_failing(self):
        import os
        d = Path(tempfile.mkdtemp())
        cwd = os.getcwd()
        os.chdir(d)
        try:
            (d / "script.json").write_text(json.dumps({"runs": [[[["submit", {}]]]]}))
            os.environ["FAKE_CLAUDE_SCRIPT"] = str(d / "script.json")
            r = cc.play(LEVEL, "out", claude=FAKE, log=lambda m: None)
            self.assertEqual(r["stop"], "submitted")
            fail = [sys.executable, "-c", "import sys; print('no login', file=sys.stderr); "
                                          "sys.exit(1)"]
            r = cc.play(LEVEL, "out2", claude=fail, log=lambda m: None)
            self.assertEqual((r["stop"], r["nudges"]), ("error", 0))
            self.assertEqual(r["error"], "claude exited with 1: no login")
        finally:
            os.chdir(cwd)

    def test_series(self):
        d = Path(tempfile.mkdtemp())
        (d / "lvl.xml").write_text(LEVEL)
        script = d / "script.json"
        script.write_text(json.dumps({"runs": [[[["submit", {}]]], [[["submit", {}]]]],
                                      "usage": [0.2, 0.01]}))
        import os
        os.environ["FAKE_CLAUDE_SCRIPT"] = str(script)
        summary = harness.run_series([d / "lvl.xml"] * 2, d / "out", player=cc.play,
                                     claude=FAKE, log=lambda m: None)
        self.assertEqual(summary["played"], 2)
        self.assertEqual(summary["levels"][1]["plan_usage"]["five_hour"], [0.22, 0.22])


if __name__ == "__main__":
    unittest.main()

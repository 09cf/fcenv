"""Tests for fcenv.session and the tool layer (fcenv.tools)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fcenv.session import Session  # noqa: E402
from fcenv.tools import TOOLS, dispatch  # noqa: E402
from tests.test_edit import LEVEL  # noqa: E402


def call(s, tool, **args):
    ok, blocks = dispatch(s, tool, args)
    return ok, blocks[0]["text"], [b for b in blocks if b["type"] == "image"]


class TestTools(unittest.TestCase):
    def test_schemas_and_argument_free_tools(self):
        names = [t["name"] for t in TOOLS]
        self.assertEqual(len(set(names)), len(names))
        for t in TOOLS:
            self.assertEqual(t["input_schema"]["type"], "object")
        s = Session(LEVEL)
        for n in ("describe", "view", "run", "help", "list_saves"):
            ok, text, _ = call(s, n)
            self.assertTrue(ok, (n, text))
        ok, text, _ = call(s, "no_such_tool")
        self.assertEqual((ok, text), (False, "Unknown tool 'no_such_tool'."))

    def test_build_with_names_and_relative_points(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "add_rod", kind="wood", a="0:1",
                           b={"from": "0:1", "offset": [60, 0]})
        self.assertTrue(ok)
        self.assertEqual(text, "Added wood rod #1 (1:0 = 0:1, 1:1 at (80, -20)).")
        ok, text, _ = call(s, "build", pieces=[
            {"rod": "water", "a": [60, 60], "b": [120, 60]},
            {"rod": "water", "a": "@0:1", "b": {"from": "@0:1", "length": 40, "angle": 90}}])
        self.assertIn("water rod #3 (3:0 = 2:1, 3:1 at (120, 100))", text)
        ok, text, _ = call(s, "add_wheel", spin="cw", center="1:1")
        self.assertEqual(text, "Added cw wheel #4 (centre 4:0 = 1:1).")

    def test_build_a_car_in_one_call(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "build", pieces=[
            {"rod": "wood", "a": [-60, 100], "b": [60, 100]},
            {"wheel": "cw", "center": "@0:0"},
            {"wheel": "none", "center": "@0:1"},
            {"rod": "water", "a": "@2:4", "b": "0:3"}])  # the rim joint above wheel #3
        self.assertTrue(ok, text)
        self.assertEqual(text, "Added wood rod #1 (1:0 at (-60, 100), 1:1 at (60, 100)); "
                               "cw wheel #2 (centre 2:0 = 1:0); wheel #3 (centre 3:0 = 1:1); "
                               "water rod #4 (4:0 = 3:4, 4:1 = 0:3).")
        ok, text, _ = call(s, "build", pieces=[{"rod": "wood", "a": [0, 0]}])
        self.assertIn("Bad arguments", text)
        ok, text, _ = call(s, "build", pieces=[{"spring": "x"}])
        self.assertIn("neither a rod", text)

    def test_joints_given_as_numbers(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "add_wheel", spin="cw", center=0.1)
        self.assertEqual((ok, text), (False, 'Bad arguments: joint names are strings like "3:1", not numbers (0.1)'))
        ok, text, _ = call(s, "move_joint", joint=0.0, to=[0, 0])
        self.assertIn("not numbers (0.0)", text)
        ok, text, _ = call(s, "add_wheel", spin="cw", center="0.1")  # the old format
        self.assertEqual(text, 'Bad arguments: \'0.1\' is not a joint like "3:1"')

    def test_rejections_and_bad_arguments(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "add_rod", kind="wood", a=[-50, 0], b=[50, 0])
        self.assertEqual((ok, text[:18]), (False, "Rejected (overlap)"))
        ok, text, _ = call(s, "add_rod", kind="wood", a="three", b=[50, 0])
        self.assertFalse(ok)
        self.assertIn("not a joint", text)
        self.assertEqual(s.current, 0)  # nothing changed

    def test_undo_redo_and_checkpoints(self):
        s = Session(LEVEL)
        call(s, "add_rod", kind="water", a=[60, 60], b=[120, 60])
        call(s, "save", name="one rod")
        call(s, "add_rod", kind="water", a=[60, 100], b=[120, 100])
        two = s.xml
        call(s, "undo")
        self.assertNotEqual(s.xml, two)
        call(s, "redo")
        self.assertEqual(s.xml, two)
        ok, _, _ = call(s, "load", name="one rod")
        self.assertTrue(ok)
        self.assertEqual(s.xml, s.versions[s.checkpoints["one rod"]])
        call(s, "undo")  # loading is undoable
        self.assertEqual(s.xml, two)
        ok, text, _ = call(s, "load", name="nope")
        self.assertEqual((ok, text),
                         (False, "No checkpoint 'nope' (saved: 'start', 'one rod')."))
        # "start" is the level as given: a reset in one call
        ok, text, _ = call(s, "list_saves")
        self.assertTrue(text.startswith("start: 1 blocks (the level as given)\none rod: 2"))
        self.assertFalse(call(s, "save", name="start")[0])
        call(s, "load", name="start")
        self.assertEqual(s.xml, LEVEL)

    def test_rejections_say_nothing_changed_and_reruns_are_noted(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "build", pieces=[{"rod": "wood", "a": [-50, 0], "b": [50, 0]}])
        self.assertTrue(text.endswith("Nothing was built."), text)
        ok, text, _ = call(s, "build", pieces=[{"rod": "wood", "a": [-50, 0], "b": [50, 0]}] * 2)
        self.assertTrue(text.endswith("Nothing was built: the call is all or none."), text)
        call(s, "add_rod", kind="wood", a=[60, 60], b=[120, 60])
        ok, text, _ = call(s, "move_joint", joint="1:0", to=[0, 0])  # into the goal rect
        self.assertTrue(text.endswith(". Nothing changed."), text)
        self.assertFalse(call(s, "run", timeout=5)[1].startswith("Note"))  # the first run
        call(s, "add_rod", kind="wood", a=[-50, 0], b=[50, 0])  # rejected
        self.assertTrue(call(s, "run", timeout=5)[1].startswith(
            "Note: this is the same design as your last run (your last change was rejected).\n"))
        self.assertTrue(call(s, "run", timeout=5)[1].startswith(
            "Note: this is the same design as your last run.\n"))
        call(s, "add_rod", kind="water", a=[60, 60], b=[120, 60])
        self.assertFalse(call(s, "run", timeout=5)[1].startswith("Note"))

    def test_delete_several(self):
        s = Session(LEVEL)
        call(s, "build", pieces=[{"rod": "wood", "a": [-60, 100], "b": [60, 100]},
                                 {"wheel": "cw", "center": "@0:0"},
                                 {"wheel": "cw", "center": "@0:1"}])
        ok, text, _ = call(s, "delete", blocks=[0, 2])  # all or none: 0 is the goal
        self.assertFalse(ok)
        self.assertIn("goal", text)
        self.assertEqual(len(s.versions), 2)
        ok, text, _ = call(s, "delete", blocks=[3, 2, 2])
        self.assertEqual((ok, text), (True, "Deleted #3, #2."))
        self.assertEqual([b["id"] for b in __import__("fcenv").edit.describe(s.xml)["blocks"]],
                         [0, 1])
        call(s, "undo")  # one step
        self.assertEqual(len(__import__("fcenv").edit.describe(s.xml)["blocks"]), 4)
        self.assertTrue(call(s, "delete", block=1)[0])  # the old form, in older records

    def test_runs_and_images(self):
        s = Session(LEVEL)
        ok, text, _ = call(s, "run_frame", tick=5)
        self.assertEqual((ok, text), (False, "No run yet: call run first."))
        ok, text, _ = call(s, "run", timeout=60)
        self.assertTrue(text.startswith("Not solved"))
        for tool, args in (("run_frame", {"tick": 30, "motion": True}),
                           ("run_trails", {"start": 10}),
                           ("run_filmstrip", {"stride": 20, "region": "action"}),
                           ("view", {"region": "level"})):
            ok, _, images = call(s, tool, **args)
            self.assertTrue(ok, tool)
            self.assertTrue(images and images[0]["source"]["media_type"] == "image/png")
        call(s, "add_rod", kind="water", a=[60, 60], b=[120, 60])
        ok, text, _ = call(s, "run_frame", tick=5)
        self.assertIn("the design has changed since this run", text)

    def test_submit_ends_the_session_and_everything_is_logged(self):
        s = Session(LEVEL, max_tool_calls=10)
        call(s, "add_rod", kind="water", a=[60, 60], b=[120, 60])
        ok, text, _ = call(s, "submit")
        self.assertTrue(text.startswith("Submitted. Not solved."))
        ok, text, _ = call(s, "describe")
        self.assertFalse(ok)
        self.assertEqual(s.tool_calls, 3)
        log = [json.loads(line) for line in s.log_jsonl().splitlines()]
        self.assertEqual([e["tool"] for e in log], ["add_rod", "submit", "describe"])
        self.assertEqual((log[0]["version_before"], log[0]["version_after"]), (0, 1))

    def test_tool_call_limit(self):
        s = Session(LEVEL, max_tool_calls=2)
        call(s, "describe")
        call(s, "describe")
        ok, text, _ = call(s, "describe")
        self.assertEqual((ok, text), (False, "Out of tool calls (limit 2)."))


class TestDocs(unittest.TestCase):
    def test_task_prompt_describes_the_level(self):
        from fcenv.docs import task_prompt

        p = task_prompt(Session(LEVEL, max_tool_calls=40))
        self.assertIn("experimental evaluation", p)
        self.assertIn("build area: x -200 to 200, y -200 to 200", p)
        self.assertIn("goal area: x 250 to 350, y -50 to 50", p)
        self.assertIn("goal pieces: 1 (#0)", p)
        self.assertIn("You have 40 tool calls.", p)
        self.assertIn("up to 200 seconds", p)

    def test_help_topics(self):
        from fcenv.docs import HELP

        s = Session(LEVEL)
        ok, text, _ = call(s, "help")
        self.assertEqual(text, "Topics: " + ", ".join(HELP))
        for topic in HELP:
            ok, text, _ = call(s, "help", topic=topic)
            self.assertEqual((ok, text), (True, HELP[topic]))
        ok, text, _ = call(s, "help", topic="nope")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

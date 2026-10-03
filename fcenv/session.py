"""A play session: one level, the agent's design versions, and a log.

Everything goes through the pure functions in edit/view/render/run; the
session only keeps:
- versions: every design XML produced, append-only, plus which is current
  (undo/redo move between them; edits and loads add new ones);
- checkpoints: named designs the agent saved;
- the latest run (earlier runs aren't kept; checkpoints cover comparing);
- log: one entry per tool call, for recreating the whole session.
An optional Notebook carries the agent's notes from one session to the next.
"""

import json
import time
from dataclasses import dataclass, field

from . import edit, render, run as runs, view
from .design import joint_name


# calls that work after submit and don't use up the tool-call budget
_FREE = {"write_notes"}


@dataclass
class Notebook:
    """The agent's notes, kept across sessions (levels); at most `cap`
    characters."""
    text: str = ""
    cap: int = 15000


@dataclass
class Budget:
    """A level's allowance, in list-price dollars, as the agent sees it: tool
    results end with how much is used (when `shown`). The runner keeps
    `spent` up to date and ends the level when it runs out."""
    total: float
    spent: float = 0.0
    last: float = 0.0  # what the latest turn cost
    shown: bool = True

    def update(self, spent):
        self.last, self.spent = spent - self.spent, spent

    @property
    def over(self):
        return self.spent >= self.total

    def note(self):
        used = round(self.spent / self.total, 6)  # 0.9 is 90%, not 89.99...
        text = f"[Budget: {used:.0%} of this level's used"
        if used >= 0.75 and self.last > 0:
            n = max(0, int((self.total - self.spent) // self.last))
            text += (f"; about {n} more turn{'' if n == 1 else 's'} at the current rate" if n
                     else "; less than one more turn at the current rate")
        text += "]"
        if used >= 0.9:
            text += (" When it runs out, the level ends and your design is evaluated as it is"
                     " (an unsolved one scores nothing either way, so keep trying).")
        return text


@dataclass
class Result:
    ok: bool
    text: str
    images: list = field(default_factory=list)  # PNG bytes


class Session:
    def __init__(self, level_xml, rules=edit.Rules(), submit_timeout=6000,
                 max_tool_calls=None, notebook=None, budget=None):
        if isinstance(level_xml, bytes):
            level_xml = level_xml.decode()
        self.rules = rules
        self.submit_timeout = submit_timeout
        self.max_tool_calls = max_tool_calls
        self.notebook = notebook
        self.budget = budget
        self.versions = [level_xml]
        self.current = 0
        self._undo, self._redo = [], []
        self.checkpoints = {"start": 0}  # name -> version index; "start": the level
        self.last_run = None  # (Run, version index)
        self._rejected = False  # an edit was rejected since the last run
        self.log = []
        self.done = False
        self.final = None  # the submitted evaluation run

    @property
    def xml(self):
        return self.versions[self.current]

    @property
    def tool_calls(self):
        """Calls counted against max_tool_calls (writing notes is free)."""
        return sum(e["tool"] not in _FREE for e in self.log)

    # ------------------------------------------------------------ plumbing

    def call(self, tool, args, fn, **meta):
        """Run one tool call: check the session is open and within budget,
        run it, log it (with `meta`, e.g. the caller's ids for the call)."""
        before = self.current
        free = tool in _FREE
        if self.done and not free:
            res = Result(False, "The session has ended (the design was submitted).")
        elif (not free and self.max_tool_calls is not None
              and self.tool_calls >= self.max_tool_calls):
            res = Result(False, f"Out of tool calls (limit {self.max_tool_calls}).")
        else:
            try:
                res = fn()
            except (ValueError, TypeError, KeyError) as e:
                res = Result(False, f"Bad arguments: {e}")
        self.log.append({"call": len(self.log), "time": time.time(), "tool": tool,
                         "args": args, "ok": res.ok, "text": res.text,
                         "version_before": before, "version_after": self.current,
                         **meta})
        return res

    def _commit(self, result, describe_change, unchanged="Nothing changed."):
        if isinstance(result, edit.Err):
            e = result.error
            self._rejected = True
            return Result(False, f"Rejected ({e.code.value}): {e.message}. {unchanged}")
        self._set(result.xml)
        return Result(True, describe_change(result))

    def _set(self, xml):
        self._undo.append(self.current)
        self._redo.clear()
        self.versions.append(xml)
        self.current = len(self.versions) - 1

    def _joints_text(self, refs):
        """'5:0 = 1:1, 5:1 at (80, -20)': each slot's joint, by name."""
        joints = edit.describe(self.xml)["joints"]
        out = []
        for r in refs:
            j = next(j for j in joints if r in j["members"])
            name = j["name"]
            if name != r:
                out.append(f"{joint_name(r)} = {joint_name(name)}")
            else:
                out.append(f"{joint_name(r)} at ({view._n(j['x'])}, {view._n(j['y'])})")
        return ", ".join(out)

    # ------------------------------------------------------------ build

    def build(self, pieces):
        """Add edit.Rod and edit.Wheel pieces, all or none."""
        def text(r):
            parts = []
            for p, b, js in zip(pieces, r.info["blocks"], r.info["joints"]):
                if isinstance(p, edit.Rod):
                    parts.append(f"{p.kind} rod #{b} ({self._joints_text(js)})")
                else:
                    kind = "wheel" if p.spin == "none" else f"{p.spin} wheel"
                    parts.append(f"{kind} #{b} (centre {self._joints_text(js[:1])})")
            return "Added " + "; ".join(parts) + "."
        return self._commit(edit.build(self.xml, pieces, self.rules), text,
                            "Nothing was built." if len(pieces) == 1
                            else "Nothing was built: the call is all or none.")

    def delete(self, blocks):
        """Delete pieces (block IDs), all or none."""
        blocks = list(dict.fromkeys(blocks))
        if not blocks:
            return Result(False, "No pieces given.")
        xml = self.xml
        for b in blocks:
            r = edit.delete(xml, b, self.rules)
            if isinstance(r, edit.Err):
                break
            xml = r.xml
        return self._commit(r if isinstance(r, edit.Err) else edit.Ok(xml, {}),
                            lambda r: f"Deleted #{', #'.join(map(str, blocks))}.")

    def rewire(self, rod, end, to):
        return self._commit(
            edit.rewire(self.xml, rod, end, to, self.rules),
            lambda r: f"Rewired rod #{rod}: {self._joints_text(r.info['joints'])}.")

    def move_joint(self, joint, to):
        return self._commit(
            edit.move_joint(self.xml, joint, to, self.rules),
            lambda r: (f"Moved joint {joint_name(joint)} by "
                       f"({view._n(r.info['delta'][0])}, {view._n(r.info['delta'][1])})"
                       + (f"; moved #{', #'.join(map(str, r.info['moved']))}"
                          if r.info["moved"] else "")
                       + (f"; stretched #{', #'.join(map(str, r.info['stretched']))}"
                          if r.info["stretched"] else "") + "."))

    def move_component(self, block, dx, dy):
        return self._commit(
            edit.move_component(self.xml, block, dx, dy, self.rules),
            lambda r: f"Moved #{', #'.join(map(str, r.info['moved']))} by "
                      f"({view._n(dx)}, {view._n(dy)}).")

    # ------------------------------------------------------------ history

    def undo(self):
        if not self._undo:
            return Result(False, "Nothing to undo.")
        self._redo.append(self.current)
        self.current = self._undo.pop()
        return Result(True, "Undone.")

    def redo(self):
        if not self._redo:
            return Result(False, "Nothing to redo.")
        self._undo.append(self.current)
        self.current = self._redo.pop()
        return Result(True, "Redone.")

    def save(self, name):
        if name == "start":
            return Result(False, "'start' is the level as given; save under another name.")
        self.checkpoints[name] = self.current
        return Result(True, f"Saved the current design as {name!r}.")

    def load(self, name):
        if name not in self.checkpoints:
            known = ", ".join(repr(n) for n in self.checkpoints)
            return Result(False, f"No checkpoint {name!r} (saved: {known}).")
        self._set(self.versions[self.checkpoints[name]])
        return Result(True, f"Loaded {name!r}.")

    def list_checkpoints(self):
        lines = []
        for name, v in self.checkpoints.items():
            n = len(edit.describe(self.versions[v])["blocks"])
            same = self.versions[v] == self.xml
            lines.append(f"{name}: {n} blocks" + (" (the level as given)" if name == "start"
                                                   else "")
                         + (" (same as the current design)" if same else ""))
        return Result(True, "\n".join(lines))

    # ------------------------------------------------------------ inspect

    def describe(self):
        return Result(True, view.summary(self.xml))

    def view(self, region="build"):
        r = render.render(self.xml, region)
        return Result(True, r.notes() or "All joints labelled.", [r.png])

    # ------------------------------------------------------------ runs

    def run(self, timeout=3000):
        same = (self.last_run is not None
                and self.versions[self.last_run[1]] == self.xml)
        note = ""
        if same:
            note = ("Note: this is the same design as your last run"
                    + (" (your last change was rejected)" if self._rejected else "") + ".\n")
        self._rejected = False
        r = runs.simulate(self.xml, timeout=timeout)
        self.last_run = (r, self.current)
        return Result(True, note + r.summary())

    def _latest(self):
        r, v = self.last_run
        note = "" if v == self.current else " (the design has changed since this run)"
        return r, note

    def _no_run(self):
        return Result(False, "No run yet: call run first.") if self.last_run is None else None

    def run_frame(self, tick, region="level", motion=False):
        if self._no_run():
            return self._no_run()
        r, note = self._latest()
        return Result(True, f"Tick {tick}{note}.", [r.frame(tick, view=region, motion=motion).png])

    def run_trails(self, start=0, end=None, region="level"):
        if self._no_run():
            return self._no_run()
        r, note = self._latest()
        return Result(True, f"Goal-piece trails{note}.",
                      [r.trails(start, end, view=region).png])

    def run_filmstrip(self, start=0, end=None, stride=None, region="level", motion=False):
        if self._no_run():
            return self._no_run()
        r, note = self._latest()
        pages = r.filmstrip(start, end, stride=stride, view=region, motion=motion)
        return Result(True, f"{len(pages)} page(s){note}.", [p.png for p in pages])

    # ------------------------------------------------------------ notes

    def write_notes(self, text):
        nb = self.notebook
        if nb is None:
            return Result(False, "There are no notes in this session.")
        if len(text) > nb.cap:
            return Result(False, f"Too long: {len(text)} characters (at most {nb.cap}). "
                                 "The notes are unchanged.")
        nb.text = text
        return Result(True, f"Notes saved ({len(text)} of {nb.cap} characters).")

    # ------------------------------------------------------------ end

    def submit(self):
        r = runs.simulate(self.xml, timeout=self.submit_timeout)
        self.final = r
        self.done = True
        verdict = (f"Solved at tick {r.solve_tick}." if r.solve_tick is not None
                   else "Not solved.")
        return Result(True, f"Submitted. {verdict} The session has ended.")

    def log_jsonl(self):
        return "\n".join(json.dumps(e) for e in self.log)

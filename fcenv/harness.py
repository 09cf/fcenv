"""An agent loop: Claude plays levels through the tools, via the Messages API.

    python3 -m fcenv.harness levels/official/03-mind-the-gap.xml --out runs/try1
    python3 -m fcenv.harness levels/official/0[1-5]-*.xml --out runs/s1 --notes

Levels are played in order, each as a fresh conversation, into OUT/<level
name>/ (one level is just a series of one). The agent gets the task prompt as
its first message and the tools in fcenv.tools. Each turn's tool calls go
through tools.dispatch, in order, and their results (text and images) go back
as tool results. A level ends when the agent submits, runs out of tool calls,
or hits the turn limit; if the agent never submitted, the current design is
evaluated as if it had (result.json says which).

With notes on (--notes, or --notes-from FILE to start from earlier notes),
the agent has a notebook that carries over between levels: it's in each
level's prompt, write_notes replaces it, and after each level the agent is
told the outcome and gets a few turns to update it.

The history is append-only (images included), so the prompt cache and the
model's earlier thinking stay valid for the whole level.

Output, per level:
- result.json: outcome, counts, token usage, estimated cost;
- transcript.md: the readable version (see readable.py);
- transcript.jsonl: every message, timestamped, with images replaced by
  paths under images/;
- tools.jsonl: the session log, one entry per tool call: tool, arguments,
  result text, the design version before and after, the time, and the turn
  and tool_use_id linking it to the transcript;
- versions.jsonl: every design version's XML, by version number. With
  tools.jsonl this is the whole build process, step by step; runs can be
  recomputed from the version they ran on;
- final.xml, final_save.xml (FC's saveDesign format), final_view.png and
  final_trails.png: the design as evaluated;
- notes.md: the notebook after the level, when notes are on.
And for the series: OUT/summary.json, and OUT/notes.md (the latest notes).
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

from . import docs, levels, readable, render, tools
from .session import Notebook, Session

MODEL = "claude-opus-5"

# $ per million tokens (input, output), for cost estimates only. Cache reads
# are 0.1x input and (5-minute) cache writes 1.25x.
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0),
          "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}

_NUDGE = ("(No one is here to reply. Keep going with the tools, and call submit "
          "when you're done.)")
_CUT_OFF = "Not run: your message was cut off (max_tokens) before this call was complete."
_UNCHANGED = ("(Your notes are unchanged: that was a reply, not a write_notes call. "
              "Call write_notes to save notes; if you meant to leave them as they "
              "are, just reply done.)")
_TOO_LONG = ("(Your last message hit the output limit ({max_tokens} tokens, thinking "
             "included) before you called a tool, so it was cut off. Work in smaller "
             "steps: build, run, look, adjust.)")
_REFLECT = ("The level is over. {outcome} Now update your notes with anything that will "
            "help you on later levels, which are different puzzles in the same game: "
            "write_notes replaces the whole notebook. Reply without a tool call when "
            "you're done.")


def _block_dict(b):
    return b if isinstance(b, dict) else b.to_dict()


class _Record:
    """The episode's files, written as it goes: transcript.jsonl (images
    written to files), and the session's log and design versions."""

    def __init__(self, out):
        self.out = out
        (out / "images").mkdir(parents=True, exist_ok=True)
        self.files = {n: open(out / n, "w")
                      for n in ("transcript.jsonl", "tools.jsonl", "versions.jsonl")}
        self.images = self.calls = self.versions = 0

    def _line(self, name, obj):
        self.files[name].write(json.dumps(obj) + "\n")
        self.files[name].flush()

    def _strip(self, block):
        block = dict(block)
        if block.get("type") == "image":
            path = f"images/{self.images:04d}.png"
            (self.out / path).write_bytes(base64.b64decode(block["source"]["data"]))
            self.images += 1
            return {"type": "image", "file": path}
        if block.get("type") == "tool_result":
            block["content"] = [self._strip(c) for c in block["content"]]
        return block

    def message(self, role, content, **extra):
        content = [self._strip(_block_dict(b)) for b in content]
        self._line("transcript.jsonl",
                   {"role": role, "time": time.time(), "content": content, **extra})

    def sync(self, session):
        """Write the session's new log entries and design versions."""
        for v in session.versions[self.versions:]:
            self._line("versions.jsonl", {"version": self.versions, "xml": v})
            self.versions += 1
        for e in session.log[self.calls:]:
            self._line("tools.jsonl", e)
            self.calls += 1

    def close(self):
        for f in self.files.values():
            f.close()


def _usage(total, u):
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens"):
        total[k] = total.get(k, 0) + (getattr(u, k, None) or 0)


def cost(model, usage):
    """Estimated dollars for `usage` (as in result.json), or None for a model
    not in PRICES."""
    if model not in PRICES:
        return None
    i, o = PRICES[model]
    return (usage.get("input_tokens", 0) * i
            + usage.get("cache_creation_input_tokens", 0) * i * 1.25
            + usage.get("cache_read_input_tokens", 0) * i * 0.1
            + usage.get("output_tokens", 0) * o) / 1e6


def _outcome(stop, run):
    verdict = (f"Solved at tick {run.solve_tick}." if run.solve_tick is not None
               else "Not solved.")
    if stop == "submitted":
        return f"You submitted: {verdict}"
    why = {"tool_limit": "You ran out of tool calls", "turn_limit": "You ran out of turns",
           "gave_up": "You stopped"}.get(stop, "The level ended")
    return f"{why}, so your design as it was got evaluated: {verdict}"


def play(level_xml, out, client=None, model=MODEL, effort=None, max_tool_calls=None,
         max_turns=300, max_nudges=2, max_tokens=32000, notebook=None, level=None,
         levels_total=None, budget=None, spent=0.0, reflect_turns=4, log=print):
    """Play one level into directory `out`; returns the result dict (also
    written to result.json). Tool calls are counted, not limited, unless
    `max_tool_calls` is given (the prompt then says so); `budget` is the
    limit that matters, and `max_turns` a backstop. `notebook` (a session.Notebook) turns notes on
    and is updated in place. `budget` (dollars, with `spent` already spent)
    stops the level once the estimated cost goes over it."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic(max_retries=6)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    session = Session(level_xml, max_tool_calls=max_tool_calls, notebook=notebook)
    record = _Record(out)
    params = dict(model=model, max_tokens=max_tokens, tools=tools.tools_for(session),
                  thinking={"type": "adaptive", "display": "summarized"},
                  cache_control={"type": "ephemeral"})
    if effort:
        params["output_config"] = {"effort": effort}
    messages = []
    usage = {}

    def say(text):
        content = [{"type": "text", "text": text}]
        messages.append({"role": "user", "content": content})
        record.message("user", content)

    def send(label):
        with client.messages.stream(messages=messages, **params) as s:
            t0 = last = time.time()
            for _ in s:  # events as they arrive: report on long turns
                if time.time() - last >= 30:
                    last = time.time()
                    log(f"{label}: still going ({last - t0:.0f} s)")
            response = s.get_final_message()
        _usage(usage, response.usage)
        messages.append({"role": "assistant", "content": response.content})
        record.message("assistant", response.content, stop_reason=response.stop_reason,
                       usage=response.usage.to_dict())
        calls = [b for b in response.content if b.type == "tool_use"]
        log(f"{label}: {response.stop_reason}, "
            f"{', '.join(c.name for c in calls) or 'no tool calls'}")
        return response, calls

    def answer(response, calls, turn):
        results = []
        for c in calls:
            if response.stop_reason == "max_tokens":
                ok, blocks = False, [{"type": "text", "text": _CUT_OFF}]
            else:
                ok, blocks = tools.dispatch(session, c.name, c.input,
                                            turn=turn, tool_use_id=c.id)
                record.sync(session)
            results.append({"type": "tool_result", "tool_use_id": c.id,
                            "content": blocks, "is_error": not ok})
        messages.append({"role": "user", "content": results})
        record.message("user", results)

    def over_budget():
        c = cost(model, usage)
        return budget is not None and c is not None and spent + c > budget

    say(docs.task_prompt(session, level, levels_total))
    record.sync(session)
    turns = nudges = reflected = 0
    stop = None
    start = time.time()
    try:
        while stop is None:
            if turns >= max_turns:
                stop = "turn_limit"
                break
            response, calls = send(f"turn {turns + 1}")
            turns += 1
            if response.stop_reason == "refusal":
                stop = "refusal"
            elif calls:
                answer(response, calls, turns)
                if session.done:
                    stop = "submitted"
                elif (session.max_tool_calls is not None
                      and session.tool_calls >= session.max_tool_calls):
                    stop = "tool_limit"
            elif response.stop_reason == "pause_turn":
                continue  # resend as is; the model picks up where it left off
            elif nudges < max_nudges:
                nudges += 1
                say(_TOO_LONG.format(max_tokens=max_tokens)
                    if response.stop_reason == "max_tokens" else _NUDGE)
            else:
                stop = "gave_up"
            if stop is None and over_budget():
                stop = "budget"
        submitted = session.done
        if not submitted:
            session.submit()  # evaluate the current design, outside the log
        if notebook is not None and stop not in ("refusal", "budget"):
            say(_REFLECT.format(outcome=_outcome(stop, session.final)))
            first, reminded = len(session.log), False
            while reflected < reflect_turns and not over_budget():
                response, calls = send(f"notes {reflected + 1}")
                reflected += 1
                if response.stop_reason == "refusal":
                    break
                if not calls:
                    # a reply isn't a save (agents have written their notes out
                    # as text): say so, once
                    wrote = any(e["tool"] == "write_notes" and e["ok"]
                                for e in session.log[first:])
                    if wrote or reminded:
                        break
                    reminded = True
                    say(_UNCHANGED)
                    continue
                answer(response, calls, turns + reflected)
    finally:
        record.close()
        if stop is None:
            stop = "error"
        submitted = any(e["tool"] == "submit" and e["ok"] for e in session.log)
        result = finish(out, session, {
            "runner": "api", "model": model, "effort": effort, "stop": stop, "submitted_by_agent": submitted,
            "max_tool_calls": max_tool_calls, "turns": turns, "nudges": nudges,
            "notes_turns": reflected, "usage": usage, "cost": cost(model, usage),
            "seconds": round(time.time() - start, 1)})
    return result


def finish(out, session, fields):
    """Evaluate the design if the agent didn't submit, and write the level's
    result.json (`fields` plus the outcome), final design files and
    readable transcript (transcript.md)."""
    if session.final is None:
        session.submit()  # evaluate the current design, outside the log
    run = session.final
    result = dict(fields, solved=run.solve_tick is not None, solve_tick=run.solve_tick,
                  tool_calls=session.tool_calls)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "final.xml").write_text(session.xml)
    (out / "final_save.xml").write_text(levels.to_save_design(session.xml, "final"))
    (out / "final_view.png").write_bytes(render.render(session.xml, "build").png)
    (out / "final_trails.png").write_bytes(run.trails(view="action").png)
    if session.notebook is not None:
        (out / "notes.md").write_text(session.notebook.text)
    if (out / "transcript.jsonl").exists():
        readable.write(out)
    return result


def run_series(paths, out, notebook=None, budget=None, log=print, player=None, **kw):
    """Play the level files `paths` in order into OUT/<level name>/, with
    `notebook` (if any) carrying over. Stops starting levels once `budget`
    dollars are spent, or after a level stopped for a budget or usage limit.
    `player` plays a level (default: play, through the API; see cc.play for
    Claude Code). Returns the summary (also written to summary.json)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results, spent, names = [], 0.0, set()
    summary = {"levels": results, "solved": 0, "played": 0, "cost": 0.0}
    for k, path in enumerate(paths, 1):
        name, n = Path(path).stem, 2
        while name in names:  # the same level more than once
            name, n = f"{Path(path).stem}-{n}", n + 1
        names.add(name)
        if budget is not None and spent >= budget:
            log(f"budget of ${budget:.2f} reached: not playing {name} or later levels")
            break
        log(f"== {name} ({k}/{len(paths)})")
        if player is None:
            r = play(Path(path).read_text(), out / name, notebook=notebook, level=k,
                     levels_total=len(paths), budget=budget, spent=spent,
                     log=lambda m: log("  " + m), **kw)
        else:
            r = player(Path(path).read_text(), out / name, notebook=notebook, level=k,
                       levels_total=len(paths), log=lambda m: log("  " + m), **kw)
        spent += r["cost"] or 0.0
        results.append({"level": name, **r})
        if r.get("plan_usage"):
            used = ", ".join(f"{w} {a:.0%}->{b:.0%}" for w, (a, b) in r["plan_usage"].items())
        else:
            used = f"${r['cost']:.2f}" if r["cost"] is not None else "cost unknown"
        log(f"   {'solved at tick %d' % r['solve_tick'] if r['solved'] else 'not solved'} "
            f"({r['stop']}), {r['tool_calls']} calls, {used}")
        summary = {"levels": results, "solved": sum(r["solved"] for r in results),
                   "played": len(results), "cost": spent}
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        if notebook is not None:
            (out / "notes.md").write_text(notebook.text)
        if r["stop"] in ("budget", "usage_limit"):
            log(f"stopped ({r['stop']}): not playing the rest")
            break
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("levels", nargs="+", help="level XML files, played in order")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-tool-calls", type=int, help="per level; none by default (the "
                   "agent is told about a limit when there is one)")
    p.add_argument("--max-turns", type=int, default=300, help="per level")
    p.add_argument("--notes", action="store_true", help="keep a notebook across levels")
    p.add_argument("--notes-from", metavar="FILE", help="start the notebook from FILE "
                   "(e.g. an earlier series' notes.md); implies --notes")
    p.add_argument("--notes-cap", type=int, default=15000, help="notebook size, characters")
    p.add_argument("--budget", type=float, metavar="DOLLARS",
                   help="stop once the estimated cost passes this")
    p.add_argument("--print-prompt", action="store_true",
                   help="print the first level's prompt and exit (no API calls)")
    a = p.parse_args(argv)
    notebook = None
    if a.notes or a.notes_from:
        notebook = Notebook(Path(a.notes_from).read_text() if a.notes_from else "",
                            a.notes_cap)
    if a.print_prompt:
        s = Session(Path(a.levels[0]).read_text(), max_tool_calls=a.max_tool_calls,
                    notebook=notebook)
        print(docs.task_prompt(s, 1, len(a.levels)))
        return 0
    summary = run_series(a.levels, a.out, notebook=notebook, budget=a.budget,
                         model=a.model, effort=a.effort, max_tool_calls=a.max_tool_calls,
                         max_turns=a.max_turns, log=lambda m: print(m, file=sys.stderr))
    print(f"{summary['solved']}/{summary['played']} solved, "
          f"estimated cost ${summary['cost']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""An agent loop: Claude plays one level through the tools, via the Messages API.

    python3 -m fcenv.harness level.xml --out runs/try1

The agent gets the task prompt as its first message and the tools in
fcenv.tools. Each turn's tool calls go through tools.dispatch, in order,
and their results (text and images) go back as tool results. The episode
ends when the agent submits, runs out of tool calls, or hits the turn limit;
if the agent never submitted, the current design is evaluated as if it had
(result.json says which).

The history is append-only (images included), so the prompt cache and the
model's earlier thinking stay valid for the whole episode.

Output directory:
- result.json: outcome, counts, token usage;
- transcript.jsonl: every message, timestamped, with images replaced by
  paths under images/;
- tools.jsonl: the session log, one entry per tool call: tool, arguments,
  result text, the design version before and after, the time, and the turn
  and tool_use_id linking it to the transcript;
- versions.jsonl: every design version's XML, by version number. With
  tools.jsonl this is the whole build process, step by step; runs can be
  recomputed from the version they ran on;
- final.xml, final_save.xml (FC's saveDesign format), final_view.png and
  final_trails.png: the design as evaluated.
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

from . import docs, levels, render, tools
from .session import Session

MODEL = "claude-opus-5"

_NUDGE = ("(No one is here to reply. Keep going with the tools, and call submit "
          "when you're done.)")
_CUT_OFF = "Not run: your message was cut off (max_tokens) before this call was complete."


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


def play(level_xml, out, client=None, model=MODEL, effort=None, max_tool_calls=200,
         max_turns=300, max_nudges=2, max_tokens=32000, log=print):
    """Play one episode; returns the result dict (also written to result.json)."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic(max_retries=6)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    session = Session(level_xml, max_tool_calls=max_tool_calls)
    record = _Record(out)
    params = dict(model=model, max_tokens=max_tokens, tools=tools.TOOLS,
                  thinking={"type": "adaptive", "display": "summarized"},
                  cache_control={"type": "ephemeral"})
    if effort:
        params["output_config"] = {"effort": effort}

    prompt = [{"type": "text", "text": docs.task_prompt(session)}]
    messages = [{"role": "user", "content": prompt}]
    record.message("user", prompt)
    record.sync(session)
    usage, turns, nudges, stop = {}, 0, 0, None
    start = time.time()
    try:
        while stop is None:
            if turns >= max_turns:
                stop = "turn_limit"
                break
            with client.messages.stream(messages=messages, **params) as s:
                response = s.get_final_message()
            turns += 1
            _usage(usage, response.usage)
            messages.append({"role": "assistant", "content": response.content})
            record.message("assistant", response.content, stop_reason=response.stop_reason,
                           usage=response.usage.to_dict())
            calls = [b for b in response.content if b.type == "tool_use"]
            log(f"turn {turns}: {response.stop_reason}, "
                f"{', '.join(c.name for c in calls) or 'no tool calls'}")

            if response.stop_reason == "refusal":
                stop = "refusal"
            elif calls:
                results = []
                for c in calls:
                    if response.stop_reason == "max_tokens":
                        ok, blocks = False, [{"type": "text", "text": _CUT_OFF}]
                    else:
                        ok, blocks = tools.dispatch(session, c.name, c.input,
                                                    turn=turns, tool_use_id=c.id)
                        record.sync(session)
                    results.append({"type": "tool_result", "tool_use_id": c.id,
                                    "content": blocks, "is_error": not ok})
                messages.append({"role": "user", "content": results})
                record.message("user", results)
                if session.done:
                    stop = "submitted"
                elif (session.max_tool_calls is not None
                      and session.tool_calls >= session.max_tool_calls):
                    stop = "tool_limit"
            elif response.stop_reason == "pause_turn":
                continue  # resend as is; the model picks up where it left off
            elif nudges < max_nudges:
                nudges += 1
                nudge = [{"type": "text", "text": _NUDGE}]
                messages.append({"role": "user", "content": nudge})
                record.message("user", nudge)
            else:
                stop = "gave_up"
    finally:
        record.close()
        if stop is None:
            stop = "error"
        submitted = session.done
        if not submitted:
            session.submit()  # evaluate the current design, outside the log
        run = session.final
        result = {
            "model": model, "effort": effort, "stop": stop,
            "submitted_by_agent": submitted,
            "solved": run.solve_tick is not None, "solve_tick": run.solve_tick,
            "tool_calls": session.tool_calls, "max_tool_calls": max_tool_calls,
            "turns": turns, "nudges": nudges, "usage": usage,
            "seconds": round(time.time() - start, 1),
        }
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        (out / "final.xml").write_text(session.xml)
        (out / "final_save.xml").write_text(levels.to_save_design(session.xml, "final"))
        (out / "final_view.png").write_bytes(render.render(session.xml, "build").png)
        (out / "final_trails.png").write_bytes(run.trails(view="action").png)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("level", help="level XML file")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-tool-calls", type=int, default=200)
    p.add_argument("--max-turns", type=int, default=300)
    p.add_argument("--print-prompt", action="store_true",
                   help="print the task prompt and exit (no API calls)")
    a = p.parse_args(argv)
    xml = Path(a.level).read_text()
    if a.print_prompt:
        print(docs.task_prompt(Session(xml, max_tool_calls=a.max_tool_calls)))
        return 0
    r = play(xml, a.out, model=a.model, effort=a.effort, max_tool_calls=a.max_tool_calls,
             max_turns=a.max_turns, log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Levels played through Claude Code (claude -p): e.g. on a Claude
subscription instead of API credits.

    python3 -m fcenv.cc levels/official/03-mind-the-gap.xml --out runs/cc1
    python3 -m fcenv.cc levels/official/0[1-5]-*.xml --out runs/cc2 --notes

The same tools, prompts, notes and output as the API harness (harness.py);
Claude Code runs the agent loop instead. The tools are served over MCP from
this process (toolserver.py), so the session survives across the claude
runs of a level: the first, then resumed runs for nudges and the notes
phase. Claude Code gets our prompt with an empty system prompt, no built-in
tools, no settings files and an empty working directory: only our tools.

Limits: wall clock per level (--max-minutes; the notes phase gets
--notes-minutes more), and plan usage: Claude Code reports how much of the
5-hour and 7-day windows is used. A level stops once it has used
--max-level-usage of either window itself (its notes phase still runs, and
the series goes on), and everything stops once either window passes
--max-usage. result.json has plan_usage (each window before and after the
level) instead of a cost.

Output per level, as with the API harness, plus cc/: Claude Code's event
stream (stream.jsonl, images left out), stderr and the MCP config.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import docs
from .harness import (_NUDGE, _REFLECT, _UNCHANGED, _Record, _outcome, finish,
                      run_series)
from .session import Notebook, Session
from .toolserver import ToolServer

_WINDOWS = ("five_hour", "seven_day")


def _strip_images(obj):
    """A copy of a stream event with image data left out."""
    if isinstance(obj, dict):
        if obj.get("type") == "image":
            return {"type": "image"}
        return {k: _strip_images(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_images(v) for v in obj]
    return obj


def _tool_result(b):
    c = b.get("content")
    if isinstance(c, str):
        c = [{"type": "text", "text": c}]
    return {"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": c or [],
            "is_error": bool(b.get("is_error"))}


def play(level_xml, out, model=None, effort=None, max_tool_calls=None, notebook=None,
         level=None, levels_total=None, max_minutes=30.0, notes_minutes=5.0,
         max_usage=0.8, max_level_usage=0.1, max_nudges=2, claude="claude", log=print):
    """Play one level through Claude Code into directory `out`; returns the
    result dict (also written to result.json). `claude` is the command (a
    string, or a list for a command with arguments)."""
    out = Path(out).resolve()  # claude runs in out/cc/work
    (out / "cc" / "work").mkdir(parents=True, exist_ok=True)
    session = Session(level_xml, max_tool_calls=max_tool_calls, notebook=notebook)
    record = _Record(out)
    stream_file = open(out / "cc" / "stream.jsonl", "w")
    stderr_file = open(out / "cc" / "stderr.txt", "w")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # the subscription
    sid = str(uuid.uuid4())
    st = {"model": None, "turns": 0, "usage": {}, "cc_cost": 0.0, "windows": {},
          "error": None, "over_usage": False, "level_over": False, "partial": False,
          "msgs": {}, "got_result": False}
    assistant = []  # content of each assistant message (= turn), for linking calls

    def over(windows):
        return any((windows.get(w) or {}).get("utilization", 0) >= max_usage
                   for w in _WINDOWS)

    def event(e, pending):
        """Handle one stream event; returns the pending assistant message."""
        kind = e.get("type")
        if kind == "system" and e.get("subtype") == "init":
            st["model"] = st["model"] or e.get("model")
        elif kind == "assistant":
            msg = e["message"]
            if msg.get("usage"):
                st["msgs"][msg.get("id")] = msg["usage"]  # in case no result comes
            if pending is None or pending["id"] != msg.get("id"):
                flush(pending)
                pending = {"id": msg.get("id"), "content": [], "usage": msg.get("usage")}
            for b in msg.get("content", []):
                if b.get("type") == "tool_use":
                    b = dict(b, name=b["name"].removeprefix("mcp__fc__"))
                pending["content"].append(b)
        elif kind == "user":
            flush(pending)
            pending = None
            content = e["message"].get("content")
            if isinstance(content, list):
                blocks = [_tool_result(b) if b.get("type") == "tool_result"
                          else b for b in content]
                record.message("user", blocks)
        elif kind == "rate_limit_event":
            info = e.get("rate_limit_info") or {}
            windows = info.get("unifiedWindows") or {}
            for w in _WINDOWS:
                if w in windows:
                    st["windows"].setdefault(w, [windows[w]["utilization"]] * 2)[1] = \
                        windows[w]["utilization"]
            # "allowed_warning" (from 90% full) is still allowed
            if info.get("status", "allowed") not in ("allowed", "allowed_warning") \
                    or over(windows):
                st["over_usage"] = True
            if max_level_usage is not None and any(
                    b - a >= max_level_usage - 1e-9 for a, b in st["windows"].values()):
                st["level_over"] = True
        elif kind == "result":
            flush(pending)
            pending = None
            st["got_result"] = True
            for k, v in (e.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    st["usage"][k] = st["usage"].get(k, 0) + v
            st["cc_cost"] += e.get("total_cost_usd") or 0.0
            if e.get("is_error"):
                st["error"] = str(e.get("result") or e.get("subtype"))
        return pending

    def flush(pending):
        if pending is None:
            return
        st["turns"] += 1
        assistant.append(pending["content"])
        calls = [b["name"] for b in pending["content"] if b.get("type") == "tool_use"]
        log(f"turn {st['turns']}: {', '.join(calls) or 'no tool calls'}")
        record.message("assistant", pending["content"], usage=pending["usage"])

    def invoke(message, first, seconds, level_limit=True):
        """One claude run: `message` as the new user turn. Returns whether it
        was stopped (time or usage; the level's own usage limit only if
        `level_limit`)."""
        record.message("user", [{"type": "text", "text": message}])
        st["msgs"], st["got_result"] = {}, False
        args = ([claude] if isinstance(claude, str) else list(claude)) + [
                "-p", message, "--system-prompt", "", "--setting-sources", "",
                "--mcp-config", str(out / "cc" / "mcp.json"), "--strict-mcp-config",
                "--tools", "", "--allowedTools", ",".join(srv.tool_names()),
                "--output-format", "stream-json", "--verbose"]
        args += ["--session-id", sid] if first else ["--resume", sid]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        proc = subprocess.Popen(args, cwd=out / "cc" / "work", env=env, text=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=stderr_file)
        lines = queue.Queue()
        threading.Thread(target=lambda: ([lines.put(x) for x in proc.stdout], lines.put(None)),
                         daemon=True).start()
        deadline, pending, stopped = time.time() + seconds, None, None
        while True:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line.strip():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stream_file.write(json.dumps(_strip_images(e)) + "\n")
                stream_file.flush()
                pending = event(e, pending)
            limited = st["over_usage"] or (level_limit and st["level_over"])
            if (time.time() > deadline or limited) and stopped is None:
                proc.terminate()
                stopped = time.time()
            elif stopped is not None and time.time() > stopped + 10:
                proc.kill()
        flush(pending)
        if not st["got_result"]:  # killed: count what the messages reported
            st["partial"] = True
            for u in st["msgs"].values():
                for k, v in u.items():
                    if isinstance(v, (int, float)):
                        st["usage"][k] = st["usage"].get(k, 0) + v
        if proc.wait() and stopped is None and not st["error"]:
            stderr_file.flush()
            tail = (out / "cc" / "stderr.txt").read_text().strip().splitlines()[-3:]
            st["error"] = f"claude exited with {proc.returncode}: " + " / ".join(tail)
        return stopped is not None

    start = time.time()
    turns = nudges = 0
    stop = None
    with ToolServer(session, on_call=lambda: record.sync(session)) as srv:
        (out / "cc" / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"fc": {"type": "http", "url": srv.url}}}))
        record.sync(session)
        try:
            deadline = start + max_minutes * 60
            stopped = invoke(docs.task_prompt(session, level, levels_total), True,
                             deadline - time.time())
            while stop is None:
                if session.done:
                    stop = "submitted"
                elif st["over_usage"]:
                    stop = "usage_limit"
                elif st["level_over"]:
                    stop = "level_usage"
                elif stopped or time.time() >= deadline:
                    stop = "time_limit"
                elif st["error"]:
                    stop = "error"
                elif (max_tool_calls is not None
                      and session.tool_calls >= max_tool_calls):
                    stop = "tool_limit"
                elif nudges < max_nudges:
                    nudges += 1
                    stopped = invoke(_NUDGE, False, deadline - time.time())
                else:
                    stop = "gave_up"
            turns = st["turns"]
            if session.final is None:
                session.submit()  # evaluate the current design, outside the log
            if notebook is not None and stop not in ("usage_limit", "error"):
                first = len(session.log)
                invoke(_REFLECT.format(outcome=_outcome(stop, session.final)), False,
                       notes_minutes * 60, level_limit=False)
                if not st["over_usage"] and not any(
                        e["tool"] == "write_notes" and e["ok"] for e in session.log[first:]):
                    invoke(_UNCHANGED, False, notes_minutes * 60, level_limit=False)
        finally:
            record.close()
            stream_file.close()
            stderr_file.close()
            if stop is None:
                stop = "error"
            _link(session.log, assistant)
            (out / "tools.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in session.log))
            submitted = any(e["tool"] == "submit" and e["ok"] for e in session.log)
            result = finish(out, session, {
                "runner": "claude-code", "model": st["model"], "effort": effort,
                "stop": stop, "submitted_by_agent": submitted,
                "max_tool_calls": max_tool_calls, "turns": turns or st["turns"],
                "nudges": nudges, "notes_turns": st["turns"] - (turns or st["turns"]),
                "usage": st["usage"], "usage_partial": st["partial"], "cost": None,
                "cc_cost_estimate": round(st["cc_cost"], 4),
                "plan_usage": {w: v for w, v in st["windows"].items()},
                "error": st["error"], "seconds": round(time.time() - start, 1)})
    return result


def _link(log, assistant):
    """Give each log entry the turn and tool_use_id of the call that made it,
    matching tool calls in the transcript to log entries by tool and
    arguments, in order."""
    used = set()
    for turn, content in enumerate(assistant, 1):
        for b in content:
            if b.get("type") != "tool_use":
                continue
            for k, e in enumerate(log):
                if k not in used and e["tool"] == b["name"] and e["args"] == b.get("input"):
                    used.add(k)
                    e["turn"], e["tool_use_id"] = turn, b["id"]
                    break


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("levels", nargs="+", help="level XML files, played in order")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--model", help="default: Claude Code's (your plan's) default")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-minutes", type=float, default=30.0, help="per level")
    p.add_argument("--notes-minutes", type=float, default=5.0, help="per notes phase")
    p.add_argument("--max-usage", type=float, default=0.8,
                   help="stop everything once the 5-hour or 7-day window is this full (0-1)")
    p.add_argument("--max-level-usage", type=float, default=0.1,
                   help="stop a level once it has used this much of either window "
                        "(0-1; its notes phase still runs)")
    p.add_argument("--max-tool-calls", type=int, help="per level; none by default")
    p.add_argument("--notes", action="store_true", help="keep a notebook across levels")
    p.add_argument("--notes-from", metavar="FILE", help="start the notebook from FILE; "
                   "implies --notes")
    p.add_argument("--notes-cap", type=int, default=15000, help="notebook size, characters")
    a = p.parse_args(argv)
    notebook = None
    if a.notes or a.notes_from:
        notebook = Notebook(Path(a.notes_from).read_text() if a.notes_from else "",
                            a.notes_cap)
    summary = run_series(a.levels, a.out, notebook=notebook, player=play, model=a.model,
                         effort=a.effort, max_tool_calls=a.max_tool_calls,
                         max_minutes=a.max_minutes, notes_minutes=a.notes_minutes,
                         max_usage=a.max_usage, max_level_usage=a.max_level_usage,
                         log=lambda m: print(m, file=sys.stderr))
    print(f"{summary['solved']}/{summary['played']} solved")
    return 0


if __name__ == "__main__":
    sys.exit(main())

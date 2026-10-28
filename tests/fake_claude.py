#!/usr/bin/env python3
"""A stand-in for `claude -p ... --output-format stream-json`, for testing
fcenv.cc without Claude Code: it plays a script through the real MCP tool
server and prints Claude Code-shaped events.

The script (JSON, path in $FAKE_CLAUDE_SCRIPT) is {"runs": [...], "usage":
[five_hour, step]}: one entry per claude run (the first, then each resume),
each a list of turns: a list of [tool, args] calls, or a string (a text
reply, which ends the run). "sleep" as a turn waits (for time limits);
"hang" starts a child process that keeps stdout open, then waits, as
Claude Code did when it hung on API retries. "resets" lists the 5-hour
window's resetsAt per turn (the last one repeats). "run_usage" lists the
5-hour utilization each run starts from (e.g. a fresh window after a wait;
the last one repeats), instead of carrying on from the last run's.
Each assistant message is followed by a rate_limit_event whose five_hour
utilization grows by `step`, then a pause of "delay" seconds (as a real
turn takes time; lets the driver act on the event before the calls run).
With --include-partial-messages, each message also comes with its usage, as
message_start and message_delta stream events ("msg_usage": [input,
output] tokens). "model" names the model (default fake-model).
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp.client import Client


def emit(e):
    print(json.dumps(e), flush=True)


async def main(argv):
    script_path = Path(os.environ["FAKE_CLAUDE_SCRIPT"])
    script = json.loads(script_path.read_text())
    state_path = script_path.with_suffix(".state")
    state = json.loads(state_path.read_text()) if state_path.exists() else {"run": 0, "n": 0}
    runs, (usage, step) = script["runs"], script.get("usage", [0.1, 0.0])
    args = {"argv": argv}
    Path(script_path.with_suffix(f".args{state['run']}")).write_text(json.dumps(argv))
    url = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())[
        "mcpServers"]["fc"]["url"]
    if "run_usage" in script:
        ru = script["run_usage"]
        usage = ru[min(state["run"], len(ru) - 1)]
    turns = runs[state["run"]] if state["run"] < len(runs) else ["done"]
    state["run"] += 1

    def save():  # as it goes: the driver may kill this run
        script["usage"] = [usage, step]
        script_path.write_text(json.dumps(script))
        state_path.write_text(json.dumps(state))
    save()
    partial = "--include-partial-messages" in argv
    msg_in, msg_out = script.get("msg_usage", [1000, 100])
    emit({"type": "system", "subtype": "init", "model": script.get("model", "fake-model"),
          "session_id": "s"})
    async with Client(url) as client:
        for turn in turns:
            state["n"] += 1
            mid = f"msg{state['n']}"
            if turn == "sleep":
                time.sleep(30)
                continue
            if turn == "hang":
                subprocess.Popen(["sleep", "300"])  # inherits stdout
                time.sleep(300)
                continue
            if isinstance(turn, str):
                emit({"type": "assistant", "message": {
                    "id": mid, "content": [{"type": "text", "text": turn}],
                    "usage": {"output_tokens": 1}}})
                break
            uses = [{"type": "tool_use", "id": f"t{state['n']}_{i}",
                     "name": f"mcp__fc__{name}", "input": a}
                    for i, (name, a) in enumerate(turn)]
            # thinking and each call as separate events of one message, as
            # Claude Code streams them
            if partial:
                emit({"type": "stream_event", "event": {"type": "message_start", "message": {
                    "id": mid, "usage": {"input_tokens": msg_in, "output_tokens": 1}}}})
            emit({"type": "assistant", "message": {
                "id": mid, "content": [{"type": "thinking", "thinking": "hmm"}],
                "usage": {"output_tokens": 3}}})
            for u in uses:
                emit({"type": "assistant", "message": {"id": mid, "content": [u]}})
            if partial:
                emit({"type": "stream_event", "event": {"type": "message_delta",
                                                         "usage": {"output_tokens": msg_out}}})
            usage += step
            resets = script.get("resets", [1e10])
            emit({"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed_warning" if usage >= 0.9 else "allowed",
                "unifiedWindows": {
                    "five_hour": {"utilization": round(usage, 4),
                                  "resetsAt": resets[min(state["n"] - 1, len(resets) - 1)]},
                    "seven_day": {"utilization": 0.3}}}})
            save()
            time.sleep(script.get("delay", 0))
            results = []
            for u in uses:
                r = await client.call_tool(u["name"].removeprefix("mcp__fc__"), u["input"])
                content = []
                for c in r.content:
                    if c.type == "text":
                        content.append({"type": "text", "text": c.text})
                    else:
                        content.append({"type": "image", "source": {
                            "type": "base64", "media_type": c.mime_type, "data": c.data}})
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": content, "is_error": r.is_error})
            emit({"type": "user", "message": {"role": "user", "content": results}})
    save()
    emit({"type": "result", "subtype": "success", "is_error": False,
          "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))

#!/usr/bin/env python3
"""A stand-in for `claude -p ... --output-format stream-json`, for testing
fcenv.cc without Claude Code: it plays a script through the real MCP tool
server and prints Claude Code-shaped events.

The script (JSON, path in $FAKE_CLAUDE_SCRIPT) is {"runs": [...], "usage":
[five_hour, step]}: one entry per claude run (the first, then each resume),
each a list of turns: a list of [tool, args] calls, or a string (a text
reply, which ends the run). "sleep" as a turn waits (for time limits).
Each assistant message is followed by a rate_limit_event whose five_hour
utilization grows by `step`, then a pause of "delay" seconds (as a real
turn takes time; lets the driver act on the event before the calls run).
"""

import asyncio
import json
import os
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
    turns = runs[state["run"]] if state["run"] < len(runs) else ["done"]
    state["run"] += 1

    def save():  # as it goes: the driver may kill this run
        script["usage"] = [usage, step]
        script_path.write_text(json.dumps(script))
        state_path.write_text(json.dumps(state))
    save()
    emit({"type": "system", "subtype": "init", "model": "fake-model", "session_id": "s"})
    async with Client(url) as client:
        for turn in turns:
            state["n"] += 1
            mid = f"msg{state['n']}"
            if turn == "sleep":
                time.sleep(30)
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
            emit({"type": "assistant", "message": {
                "id": mid, "content": [{"type": "thinking", "thinking": "hmm"}],
                "usage": {"output_tokens": 3}}})
            for u in uses:
                emit({"type": "assistant", "message": {"id": mid, "content": [u]}})
            usage += step
            emit({"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "unifiedWindows": {
                    "five_hour": {"utilization": round(usage, 4)},
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

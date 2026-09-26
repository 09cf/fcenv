"""A readable version of a level's record: transcript.md, made from
transcript.jsonl and result.json.

    python3 -m fcenv.readable runs/s1            # every level in a series
    python3 -m fcenv.readable runs/s1/03-mind-the-gap

Turn by turn: the agent's thinking (summaries, where the runner has them)
and words, its tool calls written compactly, and their results, with long
results folded and images inline. Messages from the harness (nudges, the
notes phase) are marked as such. The harnesses write it at the end of each
level.
"""

import json
import sys
from pathlib import Path

_FOLD = 12  # results longer than this many lines are folded


def _value(v):
    return json.dumps(v, separators=(", ", ": "))


def _call(b):
    """A tool call, e.g. `add_rod(kind="wood", a="0:1", b=[80, -20])`, with
    list-of-object arguments (build's pieces) one per line."""
    args, lines = [], []
    for k, v in (b.get("input") or {}).items():
        if k == "text" and b["name"] == "write_notes":
            lines.append("```text\n" + str(v).rstrip() + "\n```")
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            args.append(f"{k}=[…{len(v)}]")
            lines += [f"    {i}: {_value(x)}" for i, x in enumerate(v)]
        else:
            args.append(f"{k}={_value(v)}")
    head = f"**→ {b['name']}**(" + ", ".join(args) + ")"
    if lines and not lines[0].startswith("```"):
        return head + "\n\n```text\n" + "\n".join(lines) + "\n```"
    return "\n\n".join([head] + lines)


def _result(b):
    parts = []
    for c in b.get("content") or []:
        if c.get("type") == "text":
            text = c["text"].rstrip()
            mark = "✗ " if b.get("is_error") else ""
            if text.count("\n") + 1 > _FOLD:
                first = text.splitlines()[0]
                parts.append(f"<details><summary>{mark}{first}</summary>\n\n```text\n"
                             f"{text}\n```\n\n</details>")
            elif "\n" in text:
                parts.append(f"{mark}\n```text\n{text}\n```" if mark else
                             f"```text\n{text}\n```")
            else:
                parts.append(f"{mark}`{text}`" if len(text) < 100 else f"{mark}{text}")
        elif c.get("type") == "image" and "file" in c:
            parts.append(f"![]({c['file']})")
    return "\n\n".join(parts)


def _quote(text):
    return "\n".join("> " + line if line else ">" for line in text.strip().splitlines())


def markdown(level_dir):
    d = Path(level_dir)
    msgs = [json.loads(s) for s in (d / "transcript.jsonl").read_text().splitlines()]
    out = [f"# {d.name}"]
    rf = d / "result.json"
    if rf.exists():
        r = json.loads(rf.read_text())
        outcome = f"solved at tick {r['solve_tick']}" if r["solved"] else "not solved"
        extra = []
        if r.get("cost") is not None:
            extra.append(f"${r['cost']:.2f}")
        for w, (a, b) in (r.get("plan_usage") or {}).items():
            extra.append(f"{w.replace('_', '-')} window {a:.0%}→{b:.0%}")
        out.append(f"**{outcome.capitalize()}** ({r['stop']}); {r['tool_calls']} tool calls, "
                   f"{r['turns']} turns; {r.get('model')}"
                   + (f" ({r['runner']})" if r.get("runner") else "")
                   + ("; " + ", ".join(extra) if extra else ""))
    calls = {}  # tool_use_id -> call block, to title results
    turn = 0
    for i, m in enumerate(msgs):
        if m["role"] == "assistant":
            turn += 1
            out.append(f"## Turn {turn}")
            for b in m["content"]:
                if b.get("type") == "thinking" and b.get("thinking", "").strip():
                    out.append("<details><summary><i>thinking</i></summary>\n\n"
                               + _quote(b["thinking"]) + "\n\n</details>")
                elif b.get("type") == "text" and b.get("text", "").strip():
                    out.append(b["text"].strip())
                elif b.get("type") == "tool_use":
                    calls[b["id"]] = b
            continue
        texts = [b["text"] for b in m["content"] if b.get("type") == "text"]
        results = [b for b in m["content"] if b.get("type") == "tool_result"]
        if i == 0:
            out.append("<details><summary><b>Prompt</b></summary>\n\n```text\n"
                       + "\n\n".join(texts).strip() + "\n```\n\n</details>")
            continue
        for b in results:
            call = calls.get(b.get("tool_use_id"))
            head = _call(call) if call else "**→ (a call)**"
            out.append(head + "\n\n" + _result(b))
        for t in texts:
            out.append(f"**Harness:**\n\n{_quote(t)}")
    return "\n\n".join(out) + "\n"


def write(level_dir):
    """Write level_dir/transcript.md; returns its path."""
    path = Path(level_dir) / "transcript.md"
    path.write_text(markdown(level_dir))
    return path


def main(argv=None):
    dirs = argv if argv is not None else sys.argv[1:]
    if not dirs:
        sys.exit("usage: python3 -m fcenv.readable DIR... (a level's or a series' output)")
    for d in map(Path, dirs):
        levels = [d] if (d / "transcript.jsonl").exists() else sorted(
            p.parent for p in d.glob("*/transcript.jsonl"))
        for lvl in levels:
            print(write(lvl))
    return 0


if __name__ == "__main__":
    sys.exit(main())

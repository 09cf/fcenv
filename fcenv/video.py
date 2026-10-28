"""Videos of recorded episodes: the build process step by step, then the runs.

    python3 -m fcenv.video runs/mtg-1            # writes runs/mtg-1/episode.mp4

Everything comes from the harness's record (tools.jsonl, versions.jsonl,
transcript.jsonl, images/, result.json):
- each change to the design (build calls, undo/redo, load) shows the design
  after it, in the build view with joints labelled;
- a rejected call shows the unchanged design, with the error in red;
- calls that returned images (view, run_frame, ...) show what the agent saw;
- run and submit (refused or not: either way it ran the design) are
  re-simulated from the version they ran on and played at 30 ticks a second
  (real time), sped up past `max_run` seconds. If the agent never submitted,
  the harness's final evaluation is played at the end.
A bar on top shows the call and its result; the bottom shows the agent's
words for that turn.
"""

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

import cairo

from . import render
from . import run as runs

_BG = (0.13, 0.14, 0.16)
_TOP, _BOTTOM = 64, 58
_CHANGED = {"add_rod", "add_rods", "build", "add_wheel", "delete", "rewire", "move_joint",
            "move_component", "undo", "redo", "load"}


def _png(data):
    return cairo.ImageSurface.create_from_png(io.BytesIO(data))


def _words(transcript):
    """The agent's words for each turn: its text, or else its thinking summary."""
    out = []
    for m in transcript:
        if m["role"] != "assistant":
            continue
        text = " ".join(b["text"] for b in m["content"] if b["type"] == "text").strip()
        if not text:
            text = " ".join(b.get("thinking", "") for b in m["content"]
                            if b["type"] == "thinking").strip()
        out.append(" ".join(text.split()))
    return out


def _images(transcript, out):
    """tool_use_id -> the image files that call returned."""
    seen = {}
    for m in transcript:
        for b in m["content"]:
            if b["type"] == "tool_result":
                seen[b["tool_use_id"]] = [out / c["file"] for c in b["content"]
                                          if c["type"] == "image"]
    return seen


def _fit_text(cr, text, width):
    if cr.text_extents(text).x_advance <= width:
        return text
    while text and cr.text_extents(text + "…").x_advance > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(cr, text, width, lines):
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if cr.text_extents(trial).x_advance <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
            if len(out) == lines:
                break
    if len(out) < lines and line:
        out.append(line)
    if out and " ".join(out) != text:
        out[-1] = _fit_text(cr, out[-1] + " " * 200, width)  # force an ellipsis
    return out


class _Canvas:
    def __init__(self, width, height):
        self.w, self.h = width, height

    def compose(self, image, title, result=None, ok=True, words=""):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(surf)
        cr.set_source_rgb(*_BG)
        cr.paint()
        # the picture, scaled to fit between the bars
        aw, ah = self.w - 24, self.h - _TOP - _BOTTOM - 12
        s = min(aw / image.get_width(), ah / image.get_height())
        cr.save()
        cr.translate((self.w - image.get_width() * s) / 2,
                     _TOP + 6 + (ah - image.get_height() * s) / 2)
        cr.scale(s, s)
        cr.set_source_surface(image, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()
        # top: the call and its result
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(19)
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(14, 26)
        cr.show_text(_fit_text(cr, title, self.w - 28))
        if result:
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(16)
            cr.set_source_rgb(*((0.6, 0.9, 0.6) if ok else (1, 0.45, 0.4)))
            cr.move_to(14, 51)
            cr.show_text(_fit_text(cr, result, self.w - 28))
        # bottom: the agent's words
        if words:
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_ITALIC,
                                cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(15)
            cr.set_source_rgb(0.85, 0.85, 0.85)
            for k, line in enumerate(_wrap(cr, words, self.w - 28, 2)):
                cr.move_to(14, self.h - _BOTTOM + 22 + k * 20)
                cr.show_text(line)
        surf.flush()
        return surf


def _call_title(i, n, e):
    args = json.dumps(e["args"], separators=(", ", ": "))
    return f"{i + 1}/{n}  {e['tool']}" + ("" if args == "{}" else f"  {args}")


def segments(out, width=1280, height=720, fps=30, hold=0.8, look=0.6, max_run=12.0,
             size=1100):
    """The video as (frame surface, number of frames) pairs."""
    out = Path(out)
    log = [json.loads(s) for s in (out / "tools.jsonl").read_text().splitlines()]
    versions = [json.loads(s)["xml"] for s in (out / "versions.jsonl").read_text().splitlines()]
    transcript = [json.loads(s) for s in (out / "transcript.jsonl").read_text().splitlines()]
    result = json.loads((out / "result.json").read_text())
    words, images = _words(transcript), _images(transcript, out)
    canvas = _Canvas(width, height)
    built = {}

    def design(v):
        if v not in built:
            built[v] = _png(render.render(versions[v], "build", size=size).png)
        return built[v]

    def play(xml, timeout, title, words_):
        r = runs.simulate(xml, timeout=timeout)
        n = r.ticks + 1
        step = max(1, -(-n // int(max_run * fps)))  # ceil: at most max_run seconds
        ticks = list(range(0, n, step))
        if ticks[-1] != r.ticks:
            ticks.append(r.ticks)
        speed = "" if step == 1 else f"  ({step}x speed)"
        for t in ticks:
            yield canvas.compose(_png(r.frame(t, size=size).png), title,
                                 f"tick {t}{speed}", True, words_), 1
        verdict = r.summary().splitlines()[0]
        yield (canvas.compose(_png(r.frame(r.ticks, size=size).png), title, verdict,
                              r.solve_tick is not None, words_), int(hold * 2 * fps))

    yield canvas.compose(design(0), "The level", f"model: {result['model']}"), int(1.5 * fps)
    for i, e in enumerate(log):
        title = _call_title(i, len(log), e)
        said = words[e["turn"] - 1] if "turn" in e and e["turn"] <= len(words) else ""
        first = e["text"].splitlines()[0] if e["text"] else ""
        seen = images.get(e.get("tool_use_id"), [])
        if (e["tool"] == "run" and e["ok"]) or (
                e["tool"] == "submit" and (e["ok"] or e["text"].startswith("Not submitted"))):
            timeout = int(e["args"].get("timeout", 3000)) if e["tool"] == "run" else 6000
            yield from play(versions[e["version_before"]], timeout, title, said)
        elif e["ok"] and seen:
            for f in seen:
                yield canvas.compose(_png(f.read_bytes()), title, first, True, said), \
                    int(look * fps)
        else:
            changed = e["tool"] in _CHANGED and e["ok"]
            yield canvas.compose(design(e["version_after"]), title, first, e["ok"], said), \
                int((hold if changed or not e["ok"] else look) * fps)
    if not result["submitted_by_agent"]:
        final = log[-1]["version_after"] if log else 0
        yield from play(versions[final], 6000, "Final evaluation (the agent didn't submit)", "")


def write(out, path=None, fps=30, **kw):
    """Encode the episode's video (H.264 MP4) with ffmpeg; returns the path."""
    out = Path(out)
    path = Path(path) if path else out / "episode.mp4"
    width, height = kw.get("width", 1280), kw.get("height", 720)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
           "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for surf, n in segments(out, fps=fps, **kw):
            data = bytes(surf.get_data())
            for _ in range(n):
                proc.stdin.write(data)
    finally:
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError(f"ffmpeg failed ({proc.returncode})")
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("episode", help="a harness output directory")
    p.add_argument("-o", "--output", help="default: EPISODE/episode.mp4")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--hold", type=float, default=0.8, help="seconds per design change")
    p.add_argument("--look", type=float, default=0.6, help="seconds per other call")
    p.add_argument("--max-run", type=float, default=12.0,
                   help="longest a run plays, in seconds (longer runs are sped up)")
    a = p.parse_args(argv)
    print(write(a.episode, a.output, fps=a.fps, hold=a.hold, look=a.look, max_run=a.max_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

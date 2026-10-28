"""Compare series: levels solved against effort (tool calls, run calls).

    python3 -m fcenv.compare runs/cmp-haiku-5-5 runs/cmp-sonnet-5-5 runs/cmp-opus-5-5 \\
        --out runs/compare.png

Each series is a run_series output folder (one level per numbered
subfolder, played in order). Per level: tool calls (result.json, writing
notes excluded), run calls (tools.jsonl), tokens, nudges and whether it was
solved. The chart (matplotlib) plots the levels solved against cumulative
tool calls and cumulative runs, one step line per series; a series that
stopped at an unsolved level gets a tail out to the effort it spent there,
ended with a cross. Next to it go the numbers (OUT with .json) and a
Markdown table of them (OUT with .md).
"""

import argparse
import json
from pathlib import Path

NAMES = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5",
         "claude-opus-5-5": "Opus 5.5", "claude-fable-5-1": "Fable 5.1"}
# categorical slots in fixed order: blue, orange, aqua, yellow, magenta
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
INK, MUTED, GRID = "#0b0b0b", "#77766f", "#ebeae6"


def load(folder):
    folder = Path(folder)
    levels = []
    for d in sorted(p for p in folder.iterdir() if p.is_dir() and p.name[:2].isdigit()):
        r = json.loads((d / "result.json").read_text())
        log = [json.loads(x) for x in (d / "tools.jsonl").read_text().splitlines() if x]
        u = r["usage"]
        levels.append({
            "level": d.name, "solved": r["solved"], "stop": r["stop"],
            "tool_calls": r["tool_calls"], "runs": sum(e["tool"] == "run" for e in log),
            "nudges": r["nudges"], "output_tokens": u["output_tokens"],
            "input_tokens": u["input_tokens"] + u["cache_creation_input_tokens"]
                            + u["cache_read_input_tokens"],
            "seconds": r["seconds"]})
    model = json.loads((folder / levels[0]["level"] / "result.json").read_text())["model"]
    return {"name": folder.name, "model": model, "levels": levels}


def name(s):
    return NAMES.get(s["model"], s["model"])


def chart(data, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), facecolor="white")
    for ax, key, title in zip(axes, ("tool_calls", "runs"),
                              ("Against tool calls", "Against build runs (run calls)")):
        ends = []
        for i, s in enumerate(data):
            c = COLORS[i % len(COLORS)]
            xs, ys, x, solved = [0], [0], 0, 0
            for lv in s["levels"]:
                x += lv[key]
                xs.append(x)
                ys.append(solved)
                if lv["solved"]:
                    solved += 1
                    xs.append(x)
                    ys.append(solved)
                    ax.plot(x, solved, "o", ms=5.5, color=c, mec="white", mew=1.2, zorder=3)
                else:
                    ax.plot(x, solved, "x", ms=9, mew=2.5, color=c, zorder=4)
            ax.plot(xs, ys, color=c, lw=2, solid_joinstyle="round", label=name(s), zorder=2)
            ends.append((solved, x, name(s).split()[0], c))
        # labels in the right margin at each line's final level, spread apart
        taken = []
        for solved, x, label, c in sorted(ends, key=lambda e: (-e[0], e[1])):
            y = solved
            while any(abs(y - t) < 0.45 for t in taken):
                y -= 0.45
            taken.append(y)
            ax.annotate(f"{label} {solved}", xy=(1.01, y / 10.4), xycoords="axes fraction",
                        va="center", fontsize=9.5, fontweight="bold", color=c)
        ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=10)
        ax.set_xlabel(f"cumulative {'tool calls' if key == 'tool_calls' else 'runs'}")
        ax.set_ylim(0, 10.4)
        ax.set_xlim(0, None)
        ax.set_yticks(range(0, 11, 2))
        ax.set_ylabel("levels solved")
        ax.grid(axis="y", color=GRID, lw=1)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0].legend(loc="upper center", bbox_to_anchor=(1.2, -0.16), ncol=len(data),
                   frameon=False, fontsize=10)
    fig.subplots_adjust(left=0.06, right=0.9, top=0.88, bottom=0.24, wspace=0.38)
    fig.savefig(path, dpi=150, facecolor="white")


def table(data):
    """A Markdown table: calls / runs per level and series, and totals."""
    levels = sorted({lv["level"] for s in data for lv in s["levels"]})
    rows = ["| Level | " + " | ".join(name(s) for s in data) + " |",
            "|---|" + "---:|" * len(data)]
    for level in levels:
        cells = []
        for s in data:
            lv = next((x for x in s["levels"] if x["level"] == level), None)
            cells.append("–" if lv is None else
                         f"{lv['tool_calls']} / {lv['runs']}" + ("" if lv["solved"] else " ✗"))
        rows.append(f"| {level} | " + " | ".join(cells) + " |")

    def total(f):
        return " | ".join(f(s) for s in data)
    t = lambda s, k: sum(lv[k] for lv in s["levels"])
    kt = lambda n: f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K"
    rows += [f"| **Total** | {total(lambda s: f'{t(s, 'tool_calls')} / {t(s, 'runs')}')} |",
             f"| **Levels solved** | {total(lambda s: str(sum(lv['solved'] for lv in s['levels'])))} |",
             f"| **Output tokens** | {total(lambda s: kt(t(s, 'output_tokens')))} |",
             f"| **Input tokens (incl. cache)** | {total(lambda s: kt(t(s, 'input_tokens')))} |"]
    return "\n".join(rows) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("series", nargs="+", help="run_series output folders")
    p.add_argument("--out", required=True, help="the chart to write (.png or .svg)")
    a = p.parse_args(argv)
    data = [load(s) for s in a.series]
    out = Path(a.out)
    out.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n")
    out.with_suffix(".md").write_text(table(data))
    chart(data, out)
    for s in data:
        print(f"{s['model']}: {sum(lv['solved'] for lv in s['levels'])} solved, "
              f"{sum(lv['tool_calls'] for lv in s['levels'])} calls, "
              f"{sum(lv['runs'] for lv in s['levels'])} runs")


if __name__ == "__main__":
    main()

"""Simulated runs: a design played once, kept as a static object that can be
queried and drawn at any tick (the physics is fast next to agent thought).

    run = simulate(xml, timeout=3000)
    print(run.summary())
    run.frame(120).png        # the level at tick 120
    run.trails(start=100).png # goal-piece paths, ticks 100 to the end
    [p.png for p in run.filmstrip(start=60, end=300, stride=30)]  # pages

Images show the whole level by default, or view="action" to frame where
the pieces are in that window (plus the goal area).
"""

import ctypes as C
import io
import math
from dataclasses import dataclass, field

import cairo
import numpy as np

from . import render as R
from .design import fccore

TICKS_PER_SECOND = 30
_TRAIL_COLOURS = [(0.85, 0.1, 0.55), (0.1, 0.35, 0.95), (0.95, 0.5, 0.0),
                  (0.1, 0.65, 0.3), (0.55, 0.2, 0.85), (0.0, 0.6, 0.7)]


@dataclass
class Run:
    xml: str
    ticks: int  # ticks simulated; states cover ticks 0..ticks
    states: np.ndarray  # (ticks + 1, blocks, 6): x, y, angle, vx, vy, omega
    solve_tick: int  # None if not solved
    end: str  # "solved", "timeout" or "goal piece left the world"
    left_level: int = None  # ID of the goal piece that left, if any
    breaks: list = field(default_factory=list)  # (tick, block ID, block ID, x, y)
    _scene: object = None
    _frame: tuple = None

    # ------------------------------------------------------------ queries

    def state(self, tick, block_id):
        """(x, y, angle, vx, vy, omega) of a player block at `tick`."""
        i = list(self._scene.design_ids).index(block_id)
        return tuple(self.states[tick, i])

    def summary(self):
        s = self._scene
        lines = []
        secs = lambda t: f"{t / TICKS_PER_SECOND:.1f} s"  # noqa: E731
        if self.end == "solved":
            lines.append(f"Solved at tick {self.solve_tick} ({secs(self.solve_tick)}).")
        elif self.end == "timeout":
            lines.append(f"Not solved: ran the full {self.ticks} ticks ({secs(self.ticks)}).")
        else:
            lines.append(f"Stopped at tick {self.ticks} ({secs(self.ticks)}): goal piece "
                         f"#{self.left_level} left the world.")
        gx, gy, gw, gh = s.goal
        lines.append("Goal pieces:")
        for i in self._goals:
            xs, ys = self.states[:, i, 0], self.states[:, i, 1]
            dx = np.maximum(np.abs(xs - gx) - gw / 2, 0)
            dy = np.maximum(np.abs(ys - gy) - gh / 2, 0)
            dist = np.hypot(dx, dy)
            k = int(np.argmin(dist))
            kind = "goal circle" if s.blocks[i].shape == fccore.SHAPE_CIRCLE else "goal rect"
            line = (f"  #{s.design_ids[i]} {kind}: {R._fmt_pt((round(xs[0], 1), round(ys[0], 1)))}"
                    f" -> {R._fmt_pt((round(xs[-1], 1), round(ys[-1], 1)))}")
            if self.end != "solved":
                where = ("centre inside the goal area" if dist[k] == 0
                         else f"centre {dist[k]:.1f} from the goal area")
                line += f"; closest at tick {k}, {where}"
            lines.append(line)
        if self.breaks:
            lines.append("Joint breaks:")
            for t, a, b, x, y in self.breaks:
                lines.append(f"  tick {t}: #{a} and #{b} came apart at "
                             f"{R._fmt_pt((round(x, 1), round(y, 1)))}")
        return "\n".join(lines)

    # ------------------------------------------------------------ images

    def _states_at(self, tick):
        return [fccore.BodyState(*self.states[tick, i]) for i in range(self.states.shape[1])]

    def _scene_at(self, tick):
        return self._scene.at(self._states_at(tick))

    def _window(self, start, end):
        start = min(max(int(start), 0), self.ticks)
        end = self.ticks if end is None else min(max(int(end), start), self.ticks)
        return start, end

    def _view(self, view, start, end):
        """The region to show: "level" (the whole level), "action" (where the
        pieces are between ticks start and end, plus the goal area, with a
        margin), or an explicit (x0, y0, x1, y1)."""
        if view == "level":
            return self._frame
        if view != "action":
            return tuple(view)
        pts = self.states[start:end + 1, :self._scene.n_design, :2].reshape(-1, 2)
        gx, gy, gw, gh = self._scene.goal
        xs = np.concatenate([pts[:, 0], [gx - gw / 2, gx + gw / 2]])
        ys = np.concatenate([pts[:, 1], [gy - gh / 2, gy + gh / 2]])
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        pad = max(0.08 * max(x1 - x0, y1 - y0), 40)  # 40 covers most piece sizes
        wx0, wy0, wx1, wy1 = R._WORLD
        return (max(x0 - pad, wx0), max(y0 - pad, wy0), min(x1 + pad, wx1), min(y1 + pad, wy1))

    def frame(self, tick, size=1024, view="level", block_labels=False):
        """The level at `tick`."""
        tick = self._window(tick, tick)[0]
        return R._render_scene(self._scene_at(tick), self._view(view, tick, tick), size,
                               labels="none", block_labels=block_labels,
                               caption=f"tick {tick}")

    def trails(self, start=0, end=None, which="goal", size=1024, view="level"):
        """Paths of the goal pieces (which="goal") or all pieces ("all")
        between ticks start and end (default: the whole run), drawn on the
        frame at `end`. A dot marks where each path starts."""
        start, end = self._window(start, end)
        blocks = self._goals if which == "goal" else range(self._scene.n_design)

        def overlay(cr, scale, pieces):
            for k, i in enumerate(blocks):
                colour = _TRAIL_COLOURS[k % len(_TRAIL_COLOURS)]
                xs, ys = self.states[start:end + 1, i, 0], self.states[start:end + 1, i, 1]
                cr.set_source_rgba(*colour, 0.9)
                cr.set_line_width(2.5 / scale)
                cr.new_path()
                cr.move_to(xs[0], ys[0])
                for x, y in zip(xs[1:], ys[1:]):
                    cr.line_to(x, y)
                cr.stroke()
                cr.new_path()
                cr.arc(xs[0], ys[0], 5 / scale, 0, 2 * math.pi)
                cr.fill()

        return R._render_scene(self._scene_at(end), self._view(view, start, end), size,
                               labels="none", overlay=overlay,
                               caption=f"trails, ticks {start}-{end}")

    def filmstrip(self, start=0, end=None, stride=None, ticks=None, per_page=9, size=1024,
                  view="level"):
        """Frames at `ticks`, or every `stride` ticks from start to end
        (always including end), laid out `per_page` to an image in a square
        grid. With view="action", all pages share one frame: where the
        pieces are across the whole window. Returns a list of pages."""
        start, end = self._window(start, end)
        if ticks is None:
            stride = stride or max(1, math.ceil((end - start) / per_page))
            ticks = list(range(start, end + 1, stride))
            if ticks[-1] != end:
                ticks.append(end)
        ticks = sorted(set(min(max(int(t), 0), self.ticks) for t in ticks))
        region = self._view(view, ticks[0], ticks[-1])
        cols = math.ceil(math.sqrt(per_page))
        cell = size // cols
        pages = []
        for first in range(0, len(ticks), per_page):
            chunk = ticks[first:first + per_page]
            rows = math.ceil(len(chunk) / cols)
            # small cells: smaller joint rings, or they swamp small wheels
            imgs = [R._render_scene(self._scene_at(t), region, cell, grid="none",
                                    labels="none", caption=f"tick {t}",
                                    joint_px=1.5 if cell < 500 else 3.0).png
                    for t in chunk]
            surfs = [cairo.ImageSurface.create_from_png(io.BytesIO(p)) for p in imgs]
            cw = max(s.get_width() for s in surfs)
            ch = max(s.get_height() for s in surfs)
            page = cairo.ImageSurface(cairo.FORMAT_RGB24, cw * min(cols, len(chunk)), ch * rows)
            cr = cairo.Context(page)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            for k, s in enumerate(surfs):
                cr.set_source_surface(s, (k % cols) * cw, (k // cols) * ch)
                cr.paint()
            buf = io.BytesIO()
            page.write_to_png(buf)
            pages.append(R.Render(buf.getvalue(), region, []))
        return pages


def simulate(xml, timeout=3000, until_solved=True):
    """Play the design for up to `timeout` ticks (30 per second). Stops
    early when solved (if until_solved) or when a goal piece leaves the
    physics world (Box2D freezes bodies outside it, so it never returns)."""
    if isinstance(xml, bytes):
        xml = xml.decode()
    scene = R._Scene(xml)
    scene.design_ids = [b["id"] for b in scene.design_blocks]
    frame = R._frame(scene, "level", 0.03)
    x0, y0, x1, y1 = R._WORLD
    goals = [i for i in range(scene.n_design) if scene.blocks[i].is_goal]

    with fccore.Sim(xml.encode()) as sim:
        n = sim.n_blocks
        states = np.empty((timeout + 1, n, 6))
        states[0] = [(s.x, s.y, s.angle, s.vx, s.vy, s.omega) for s in sim.state()]
        idx = (C.c_int32 * n)(*range(n))
        joint_pairs = [(j.block1, j.block2, j.x, j.y) for j in sim.joints()]
        breaks, end, left = [], "timeout", None
        t = 0
        while t < timeout:
            row = states[t + 1].ctypes.data_as(C.POINTER(fccore.BodyState))
            fccore._lib.fc_sim_record(sim._h, 1, 1, idx, n, row)
            t += 1
            if fccore._lib.fc_sim_get_joints(sim._h, None, 0) != len(joint_pairs):
                now = [(j.block1, j.block2, j.x, j.y) for j in sim.joints()]
                alive = {(a, b) for a, b, _, _ in now}
                for a, b, x, y in joint_pairs:
                    if (a, b) not in alive:
                        breaks.append((t, scene.design_ids[a], scene.design_ids[b], x, y))
                joint_pairs = now
            if until_solved and sim.solve_tick is not None:
                end = "solved"
                break
            for i in goals:
                x, y = states[t, i, 0], states[t, i, 1]
                if not (x0 <= x <= x1 and y0 <= y <= y1):
                    end, left = "goal piece left the world", scene.design_ids[i]
                    break
            if left is not None:
                break
        run = Run(xml, t, states[:t + 1].copy(), sim.solve_tick, end, left, breaks,
                  scene, frame)
    run._goals = goals
    return run

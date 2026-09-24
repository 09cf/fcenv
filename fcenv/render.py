"""Render a design to PNG, in fcsim's colours and shapes, for agents to look
at. Two framings: the whole level, or the build area with a margin of its
surroundings. Grid and joint-label styles are options, for experimenting."""

import math
from dataclasses import dataclass, field

import cairo

from . import edit
from .design import fccore

# fcsim's default palette (graph.c piece_color_table, palette 0): per type,
# (outer, inner) as ARGB
_PALETTE = {
    fccore.STATIC_RECT: (0xff007f09, 0xff01be02),
    fccore.STATIC_CIRCLE: (0xff007f09, 0xff01be02),
    fccore.DYNAMIC_RECT: (0xffc5550e, 0xfff8dc2f),
    fccore.DYNAMIC_CIRCLE: (0xffbd591b, 0xfff98931),
    fccore.GOAL_RECT: (0xffb86461, 0xfffe6766),
    fccore.GOAL_CIRCLE: (0xffb86461, 0xfffe6766),
    fccore.CW_GOAL_CIRCLE: (0xffb86461, 0xfffe6766),
    fccore.CCW_GOAL_CIRCLE: (0xffb86461, 0xfffe6766),
    fccore.WHEEL: (0xff0b6afc, 0xff8cfce4),
    fccore.CW_WHEEL: (0xfffd8003, 0xffffed00),
    fccore.CCW_WHEEL: (0xffce49a3, 0xffffcfce),
    fccore.WATER_ROD: (0xfffeffff, 0xff0001fe),
    fccore.SOLID_ROD: (0xffb55a04, 0xff6a3502),
}
_JOINT = 0xff808080
_BUILD_AREA = (0xff7878ee, 0xffbedcf8)
_GOAL_AREA = (0xffbc6667, 0xfff29291)
_SKY = 0xff87bdf1

# the physics world's bounds (gen.c): nothing exists outside them
_WORLD = (-2000.0, -1450.0, 2000.0, 1450.0)


def _rgb(argb):
    return ((argb >> 16) & 255) / 255, ((argb >> 8) & 255) / 255, (argb & 255) / 255


def _nice_step(span, target_lines):
    raw = span / max(target_lines, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


class _Scene:
    """Everything to draw, in world coordinates (tick 0)."""

    def __init__(self, xml):
        raw = xml.encode() if isinstance(xml, str) else xml
        with fccore.Sim(raw) as sim:
            self.blocks = sim.blocks()
            self.state = sim.state()
            self.n_design = sim.n_design_blocks
            b, g = sim.build_area(), sim.goal_area()
            self.build = (b.x, b.y, b.w, b.h)
            self.goal = (g.x, g.y, g.w, g.h)
        d = edit.describe(xml)
        self.flags = {i: b["flags"] for i, b in enumerate(d["blocks"])}
        self.joints = d["joints"]  # x, y, name, members

    def extent(self, i):
        """Axis-aligned bounds of block i."""
        info, st = self.blocks[i], self.state[i]
        if info.shape == fccore.SHAPE_CIRCLE:
            r = info.radius
            return st.x - r, st.y - r, st.x + r, st.y + r
        c, s = abs(math.cos(st.angle)), abs(math.sin(st.angle))
        hw, hh = info.w / 2 * c + info.h / 2 * s, info.w / 2 * s + info.h / 2 * c
        return st.x - hw, st.y - hh, st.x + hw, st.y + hh


def _frame(scene, view, margin):
    if view == "level":
        boxes = [scene.extent(i) for i in range(len(scene.blocks))]
        for (x, y, w, h) in (scene.build, scene.goal):
            boxes.append((x - w / 2, y - h / 2, x + w / 2, y + h / 2))
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        pad = margin * max(x1 - x0, y1 - y0)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        # huge floors etc.: never show beyond the world bounds
        return (max(x0, _WORLD[0]), max(y0, _WORLD[1]),
                min(x1, _WORLD[2]), min(y1, _WORLD[3]))
    if view == "build":
        x, y, w, h = scene.build
        pad = margin * max(w, h)
        return x - w / 2 - pad, y - h / 2 - pad, x + w / 2 + pad, y + h / 2 + pad
    x0, y0, x1, y1 = view  # an explicit (x0, y0, x1, y1) region
    return x0, y0, x1, y1


@dataclass(frozen=True)
class Hidden:
    """A group of joints whose labels were left off the render."""

    names: list  # joint names, as the API accepts them
    center: tuple  # (x, y)
    zoom: tuple  # (x0, y0, x1, y1) to render them apart, or None if they
    #              all sit at one point (zooming can't separate them)


@dataclass(frozen=True)
class Render:
    png: bytes
    view: tuple  # the (x0, y0, x1, y1) shown
    hidden: list = field(default_factory=list)  # of Hidden

    def notes(self):
        """The hidden labels as text, to return alongside the image."""
        out = []
        for h in self.hidden:
            where = f"near {_fmt_pt(h.center)}"
            names = ", ".join(h.names)
            if h.zoom:
                out.append(f"{len(h.names)} joints {where} not labelled: {names}. "
                           f"To see them: view={_fmt_box(h.zoom)}.")
            else:
                out.append(f"{len(h.names)} joints all at {_fmt_pt(h.center)}, "
                           f"not labelled: {names}.")
        return "\n".join(out)


def _fmt_pt(p):
    return f"({p[0]:g}, {p[1]:g})"


def _fmt_box(b):
    return "(" + ", ".join(f"{v:g}" for v in b) + ")"


def _zoom_region(pts, size):
    """A region (x0, y0, x1, y1) that spreads `pts` (world coordinates)
    enough for their labels, or None if they coincide."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span < 0.5:
        return None
    gaps = [math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]]
    gaps = [g for g in gaps if g >= 0.5] or [span]
    # at the default size, the closest distinct pair should be ~2 cluster
    # radii apart; but always show the whole group
    fit = size * min(gaps) / (2 * _CLUSTER_PX)
    half = max(span / 2 * 1.15 + 2, min(fit, span * 3) / 2, 5)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return (math.floor(cx - half), math.floor(cy - half),
            math.ceil(cx + half), math.ceil(cy + half))


def render(xml, view="build", size=1024, margin=None, grid="lines",
           labels="clusters", block_labels=False, zoom_hints="none"):
    """Render the design at tick 0. Returns a Render: .png (bytes), .view
    (the region shown) and .hidden (joints whose labels were left off, with
    where to zoom to see them; .notes() gives them as text).

    view: "build" (the build area framed by `margin` of its surroundings,
          default 0.25), "level" (everything, default margin 0.03), or an
          (x0, y0, x1, y1) region.
    grid: "none", "lines" (grid lines, values on rulers along the edges),
          "rulers" (ticks on the edges only).
    labels: which joints get a label (their name as the API accepts it).
          Wheels and goal circles only show their centre, plus any outer
          joint something is attached to.
          "none"; "joints": every label, even overlapping;
          "declutter": skip labels that would overlap, and mark each area
          where some were skipped with how many;
          "clusters": where labels would collide, drop all of that group's
          labels and mark the group instead.
    block_labels: label pieces with their IDs and level blocks as L<i>.
    zoom_hints: "none" (default) puts just the count on each marker's badge;
          "badge" also writes the zoom region there. Either way the regions
          are in .hidden, and in .notes() as text.
    """
    if margin is None:
        margin = 0.03 if view == "level" else 0.25
    scene = _Scene(xml)
    x0, y0, x1, y1 = _frame(scene, view, margin)
    ruler = 0 if grid == "none" else 34
    scale = (size - ruler) / max(x1 - x0, y1 - y0)
    W = int(round((x1 - x0) * scale)) + ruler
    H = int(round((y1 - y0) * scale)) + ruler
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()

    def px(x, y):
        return ruler + (x - x0) * scale, ruler + (y - y0) * scale

    # world -> pixels: +y is down in both
    cr.save()
    cr.rectangle(ruler, ruler, W - ruler, H - ruler)
    cr.clip()
    cr.set_source_rgb(*_rgb(_SKY))
    cr.paint()
    cr.translate(ruler - x0 * scale, ruler - y0 * scale)
    cr.scale(scale, scale)
    min_w = 1.5 / scale  # keep thin things visible when zoomed out

    def rect(cx, cy, w, h, angle, colour):
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(angle)
        cr.rectangle(-max(w, min_w) / 2, -max(h, min_w) / 2, max(w, min_w), max(h, min_w))
        cr.set_source_rgb(*_rgb(colour))
        cr.fill()
        cr.restore()

    def circle(cx, cy, r, colour):
        cr.arc(cx, cy, max(r, min_w), 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(colour))
        cr.fill()

    for (x, y, w, h), (outer, inner) in ((scene.build, _BUILD_AREA), (scene.goal, _GOAL_AREA)):
        rect(x, y, w, h, 0, outer)
        rect(x, y, abs(w - 8), abs(h - 8), 0, inner)

    def draw_block(i, part):
        """Draw block i's outline ("outer"), fill ("inner") or both."""
        info, st = scene.blocks[i], scene.state[i]
        outer, inner = _PALETTE[info.type_id]
        if scene.flags.get(i, 0) and i < scene.n_design:
            outer, inner = 0xffff0000, 0xffff8080  # illegal: tinted red
        if info.shape == fccore.SHAPE_CIRCLE:
            if part != "inner":
                circle(st.x, st.y, info.radius, outer)
            if part != "outer" and info.radius > 4:
                circle(st.x, st.y, info.radius - 4, inner)
        elif info.type_id in (fccore.WATER_ROD, fccore.SOLID_ROD):
            # fcsim draws water rods at double thickness
            h = info.h * 2 if info.type_id == fccore.WATER_ROD else info.h
            if part != "inner":
                rect(st.x, st.y, info.w, h, st.angle, outer)
            if part != "outer":
                rect(st.x, st.y, abs(info.w - 4), abs(h - 4), st.angle, inner)
        else:
            if part != "inner":
                rect(st.x, st.y, info.w, info.h, st.angle, outer)
            if part != "outer":
                rect(st.x, st.y, abs(info.w - 8), abs(info.h - 8), st.angle, inner)

    # Terrain: every outline, then every fill, so overlapping terrain reads
    # as one solid mass (as in fcsim, which draws outlines and fills in
    # separate layers). Pieces are drawn one at a time, so crossing rods
    # stay distinct.
    level = range(scene.n_design, len(scene.blocks))
    for i in level:
        draw_block(i, "outer")
    for i in level:
        draw_block(i, "inner")
    for i in range(scene.n_design):
        draw_block(i, "both")

    # joints: fcsim's grey rings (radius 2-4), at least a few pixels wide
    r_out = max(4.0, 3.0 / scale)
    cr.set_source_rgb(*_rgb(_JOINT))
    cr.set_line_width(r_out * 0.5)
    for j in scene.joints:
        cr.new_path()
        cr.arc(j["x"], j["y"], r_out * 0.75, 0, 2 * math.pi)
        cr.stroke()

    if grid == "lines":
        step = _nice_step(max(x1 - x0, y1 - y0), 10)
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(1 / scale)
        gx = math.ceil(x0 / step) * step
        while gx <= x1:
            cr.move_to(gx, y0)
            cr.line_to(gx, y1)
            gx += step
        gy = math.ceil(y0 / step) * step
        while gy <= y1:
            cr.move_to(x0, gy)
            cr.line_to(x1, gy)
            gy += step
        cr.stroke()
    cr.restore()

    # labels, in pixel space
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(11)
    hidden = _draw_labels(cr, scene, xml, px, labels, block_labels, zoom_hints, size,
                          (x0, y0, x1, y1))

    if grid != "none":
        step = _nice_step(max(x1 - x0, y1 - y0), 10)
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.rectangle(0, 0, W, ruler)
        cr.rectangle(0, 0, ruler, H)
        cr.fill()
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.set_font_size(10)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_line_width(1)
        gx = math.ceil(x0 / step) * step
        while gx <= x1:
            p, _ = px(gx, y0)
            cr.move_to(p, ruler - 6)
            cr.line_to(p, ruler)
            cr.stroke()
            t = f"{gx:g}"
            e = cr.text_extents(t)
            cr.move_to(p - e.width / 2, ruler - 10)
            cr.show_text(t)
            gx += step
        gy = math.ceil(y0 / step) * step
        while gy <= y1:
            _, p = px(x0, gy)
            cr.move_to(ruler - 6, p)
            cr.line_to(ruler, p)
            cr.stroke()
            t = f"{gy:g}"
            e = cr.text_extents(t)
            cr.save()
            cr.move_to(ruler - 10, p + e.width / 2)
            cr.rotate(-math.pi / 2)
            cr.show_text(t)
            cr.restore()
            gy += step
        cr.move_to(3, 12)
        cr.show_text("+y down")

    import io

    buf = io.BytesIO()
    surf.write_to_png(buf)
    return Render(buf.getvalue(), (x0, y0, x1, y1), hidden)


_SPOTS = ((6, -6, "ne"), (6, 6, "se"), (-6, -6, "nw"), (-6, 6, "sw"),
          (8, 0, "e"), (-8, 0, "w"), (0, -8, "n"), (0, 8, "s"))
_CLUSTER_PX = 30  # joints closer than this (in pixels) can collide


def _overlaps(box, taken):
    return any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
               for b in taken)


def _place(cr, text, x, y, taken, force):
    """A free box for `text` next to (x, y), on the canvas, or (if `force`)
    the default spot pulled onto the canvas; else None."""
    ext = cr.text_extents(text)
    w, h = ext.width + 4, ext.height + 4
    cw, ch = cr.get_target().get_width(), cr.get_target().get_height()
    for dx, dy, where in _SPOTS:
        bx = x + dx - (w if "w" in where else 0) - (w / 2 if where in "ns" else 0)
        by = y + dy - (h if "n" in where else 0) - (h / 2 if where in "ew" else 0)
        box = (bx, by, bx + w, by + h)
        if box[0] >= 0 and box[1] >= 0 and box[2] <= cw and box[3] <= ch \
                and not _overlaps(box, taken):
            return box
    if not force:
        return None
    bx = min(max(x + 6, 0), cw - w)
    by = min(max(y - 6 - h, 0), ch - h)
    return (bx, by, bx + w, by + h)


def _draw_text(cr, text, box, colour=(0, 0, 0), bg=(1, 1, 1, 0.8)):
    ext = cr.text_extents(text)
    cr.set_source_rgba(*bg)
    cr.rectangle(box[0], box[1], box[2] - box[0], box[3] - box[1])
    cr.fill()
    cr.set_source_rgb(*colour)
    cr.move_to(box[0] + 2 - ext.x_bearing, box[1] + 2 - ext.y_bearing)
    cr.show_text(text)


def _groups(points, idx):
    """Connected groups of the points at indices `idx`, linking points
    within _CLUSTER_PX of each other."""
    idx = list(idx)
    parent = {i: i for i in idx}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for k, a in enumerate(idx):
        for b in idx[k + 1:]:
            if math.dist(points[a], points[b]) < _CLUSTER_PX:
                parent[find(a)] = find(b)
    out = {}
    for i in idx:
        out.setdefault(find(i), []).append(i)
    return list(out.values())


def _draw_marker(cr, pts, text, taken):
    """A dashed ring around `pts` with a badge saying what's hidden there."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    r = max(math.dist((cx, cy), p) for p in pts) + 9
    cr.set_source_rgb(0.85, 0.1, 0.55)
    cr.set_line_width(2)
    cr.set_dash([4, 3])
    cr.new_path()  # arc would otherwise join the previous current point
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()
    cr.set_dash([])
    box = _place(cr, text, cx + r * 0.7, cy - r * 0.7, taken, force=True)
    taken.append(box)
    _draw_text(cr, text, box, colour=(1, 1, 1), bg=(0.85, 0.1, 0.55, 0.95))


_SAME_POINT = 0.5  # world units: joints this close share one label


def _draw_labels(cr, scene, xml, px, labels, block_labels, zoom_hints, size, bounds):
    ids = [b for b in edit.describe(xml)["blocks"]]
    wheel_ids = {b["id"] for b in ids if "Wheel" in b["type"]}
    taken = []
    if block_labels:
        for i in range(len(scene.blocks)):
            st = scene.state[i]
            text = f"#{ids[i]['id']}" if i < scene.n_design else f"L{i - scene.n_design}"
            box = _place(cr, text, *px(st.x, st.y), taken, force=True)
            taken.append(box)
            _draw_text(cr, text, box, colour=(0.35, 0, 0.35))
    if labels == "none":
        return []

    # a wheel's outer joints only get a label when something is attached;
    # only joints in view get one
    x0, y0, x1, y1 = bounds
    shown = [j for j in scene.joints
             if not (len(j["members"]) == 1 and j["members"][0].block in wheel_ids
                     and j["members"][0].slot > 0)
             and x0 <= j["x"] <= x1 and y0 <= j["y"] <= y1]
    # distinct joints at (practically) one point share a label: no zoom
    # separates them
    items = []  # (x, y, [names])
    for j in shown:
        name = f"{j['name'].block}.{j['name'].slot}"
        for it in items:
            if math.dist((it[0], it[1]), (j["x"], j["y"])) < _SAME_POINT:
                it[2].append(name)
                break
        else:
            items.append((j["x"], j["y"], [name]))
    hidden = []
    stacked = {}  # item index -> its Hidden entry (a combined label "+N")
    for k, (x, y, names) in enumerate(items):
        if len(names) > 3:
            stacked[k] = Hidden(list(names), (round(x, 1), round(y, 1)), None)
            hidden.append(stacked[k])

    def item_text(names):
        head = "/".join(names[:3])
        return head + (f" +{len(names) - 3}" if len(names) > 3 else "")

    joints = [{"x": x, "y": y} for x, y, _ in items]
    texts = [item_text(names) for _, _, names in items]
    points = [px(x, y) for x, y, _ in items]

    def mark(group, what):
        # the group's own entry covers any stacked joints inside it
        for i in group:
            if i in stacked:
                hidden.remove(stacked.pop(i))
        world = [(joints[i]["x"], joints[i]["y"]) for i in group]
        names = [n for i in group for n in items[i][2]]
        center = (round(sum(p[0] for p in world) / len(world), 1),
                  round(sum(p[1] for p in world) / len(world), 1))
        h = Hidden(names, center, _zoom_region(world, size))
        hidden.append(h)
        group_count = len(names)
        text = f"{group_count} {what}"
        if zoom_hints == "badge":
            text += f" · zoom {_fmt_box(h.zoom)}" if h.zoom else " · all at one point"
        _draw_marker(cr, [points[i] for i in group], text, taken)

    if labels == "joints":
        for text, p in zip(texts, points):
            box = _place(cr, text, *p, taken, force=True)
            taken.append(box)
            _draw_text(cr, text, box)
        return hidden

    def greedy(indices, base):
        placed, failed, tk = {}, [], list(base)
        for i in indices:
            box = _place(cr, texts[i], *points[i], tk, force=False)
            if box is None:
                failed.append(i)
            else:
                tk.append(box)
                placed[i] = box
        return placed, failed

    everyone = range(len(joints))
    if labels == "declutter":
        placed, failed = greedy(everyone, taken)
        for i, box in placed.items():
            _draw_text(cr, texts[i], box)
        taken.extend(placed.values())
        # mark each area where labels were skipped
        for group in _groups(points, failed):
            mark(group, "hidden")
        return hidden

    if labels == "clusters":
        # drop whole groups of colliding labels until the rest fit
        groups = _groups(points, everyone)
        group_of = {i: g for g in groups for i in g}
        dropped = set()
        while True:  # each pass drops at least one more group, or succeeds
            keep = [i for i in everyone if i not in dropped]
            placed, failed = greedy(keep, taken)
            if not failed:
                break
            for i in failed:
                dropped.update(group_of[i])
        for i, box in placed.items():
            _draw_text(cr, texts[i], box)
        taken.extend(placed.values())
        for g in groups:
            if g[0] in dropped:
                mark(g, "joints")
        return hidden
    raise ValueError(f"unknown labels style {labels!r}")

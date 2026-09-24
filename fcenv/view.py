"""Text view of a design for agents: pieces grouped by connected component,
every joint named the way the API accepts it, and exact level geometry."""

import math

from . import edit
from .design import parse


def _n(v):
    """Compact number: up to 3 decimals, no trailing zeros."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pt(x, y):
    return f"({_n(x)}, {_n(y)})"


def _deg(rad):
    return _n(math.degrees(rad))


def _ref(r):
    return f"{r.block}.{r.slot}"


_KIND = {
    "HollowRod": "water rod", "SolidRod": "wood rod",
    "NoSpinWheel": "wheel", "ClockwiseWheel": "cw wheel",
    "CounterClockwiseWheel": "ccw wheel",
    "JointedDynamicRectangle": "goal rect",
}


def _box_corners(x, y, w, h, angle):
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        px, py = sx * w / 2, sy * h / 2
        out.append((x + c * px - s * py, y + s * px + c * py))
    return out


def summary(xml):
    """A text summary of the design. Pieces are grouped by connected
    component (blocks joined through shared joints). Each joint slot is
    listed by name (block.slot): a slot that shares a joint with a
    lower-named slot shows "= name"; otherwise its position, and which
    other slots share it."""
    d = edit.describe(xml)
    design = parse(xml)
    joints_of = {}
    for j in d["joints"]:
        for m in j["members"]:
            joints_of[m] = j
    blocks = {b["id"]: b for b in d["blocks"]}

    def zone(z):
        v = {k: float(s) for k, s in z.values.items()}
        return (f"x {_n(v['x'] - v['width'] / 2)}..{_n(v['x'] + v['width'] / 2)}, "
                f"y {_n(v['y'] - v['height'] / 2)}..{_n(v['y'] + v['height'] / 2)}")

    out = [
        "Coordinates: +x right, +y down; angles in degrees, clockwise.",
        f"Build area: {zone(design.start)}",
        f"Goal area: {zone(design.end)} (all goal pieces must end up inside)",
        f"Pieces: {len(blocks)} of {edit.Rules().max_design_blocks}",
        "",
    ]
    for k, comp in enumerate(d["components"], 1):
        out.append(f"Component {k} ({len(comp)} part{'s' if len(comp) > 1 else ''}):")
        for bid in comp:
            out.extend(_block_lines(blocks[bid], joints_of))
        out.append("")

    out.append("Level (fixed scenery unless dynamic; not connectable):")
    for i, b in enumerate(design.level_blocks):
        x, y, a = b.num("x"), b.num("y"), b.num("rotation")
        dyn = "dynamic" if b.tag.startswith("Dynamic") else "static"
        if b.tag.endswith("Circle"):
            # level circles store their radius as the XML width
            out.append(f"  L{i} {dyn} circle, centre {_pt(x, y)}, radius {_n(b.num('width'))}")
            continue
        w, h = b.num("width"), b.num("height")
        line = f"  L{i} {dyn} rect, centre {_pt(x, y)}, {_n(w)} x {_n(h)}"
        if abs(a) < 1e-12:
            line += (f"; top edge y {_n(y - h / 2)}, x {_n(x - w / 2)}..{_n(x + w / 2)}")
        else:
            corners = ", ".join(_pt(*c) for c in _box_corners(x, y, w, h, a))
            line += f", angle {_deg(a)}; corners {corners}"
        out.append(line)
    return "\n".join(out)


def _block_lines(b, joints_of):
    kind = _KIND.get(b["type"], b["type"])
    if b["goal"] and "Wheel" in b["type"]:
        kind = "goal circle"
    refs = b["joints"]
    if b["type"] in ("HollowRod", "SolidRod"):
        (ax, ay), (bx, by) = ((joints_of[r]["x"], joints_of[r]["y"]) for r in refs)
        desc = f"{_pt(ax, ay)} to {_pt(bx, by)}, length {_n(math.hypot(bx - ax, by - ay))}"
    elif "Wheel" in b["type"]:
        desc = f"centre {_pt(b['x'], b['y'])}, diameter {_n(b['width'])}"
    else:
        desc = (f"centre {_pt(b['x'], b['y'])}, {_n(b['width'])} x {_n(b['height'])}, "
                f"angle {_deg(b['angle'])}")
    notes = []
    if b["flags"] & 1:
        notes.append("overlapping")
    if b["flags"] & 2:
        notes.append("outside build area")
    note = f" [{', '.join(notes)}]" if notes else ""
    lines = [f"  #{b['id']} {kind}, {desc}{note}"]
    parts = []
    for r in refs:
        j = joints_of[r]
        if j["name"] != r:
            parts.append(f"{_ref(r)} = {_ref(j['name'])}")
        else:
            others = [m for m in j["members"] if m != r]
            shared = f" +{','.join(_ref(m) for m in others)}" if others else ""
            parts.append(f"{_ref(r)} {_pt(j['x'], j['y'])}{shared}")
    lines.append("      joints: " + " | ".join(parts))
    return lines

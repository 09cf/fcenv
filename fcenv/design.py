"""Design XML model and joint graph.

The XML is the only state. `Design` keeps every block's values as the exact
strings from the XML, so untouched blocks round-trip unchanged. The joint
graph is never stored: it is read back from the XML by importing it with
fccore, so it is always what the simulation will see.
"""

import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "python"))
import fccore  # noqa: E402

ROD_TAGS = ("HollowRod", "SolidRod")
WHEEL_TAGS = ("NoSpinWheel", "ClockwiseWheel", "CounterClockwiseWheel")
FIELDS = ("rotation", "x", "y", "width", "height")


def fmt(v):
    """Write a number the way it will be read back: shortest round-trip."""
    v = float(v)
    return "0" if v == 0 else repr(v)


@dataclass(frozen=True)
class BlockXml:
    """One block's XML, with values kept as the original strings."""

    tag: str
    values: dict  # FIELDS -> str
    goal: bool

    @property
    def is_rod(self):
        return self.tag in ROD_TAGS

    @property
    def is_wheel(self):
        return self.tag in WHEEL_TAGS

    def num(self, name):
        return float(self.values[name])


@dataclass(frozen=True)
class Zone:
    values: dict  # x, y, width, height -> str


@dataclass(frozen=True)
class Design:
    level_blocks: tuple  # of BlockXml
    player_blocks: tuple  # of BlockXml, in XML order (= block index)
    ids: tuple  # stable block ID of each player block (the XML id attribute)
    start: Zone
    end: Zone
    level_id: str = ""

    def index_of(self, block_id):
        return self.ids.index(block_id) if block_id in self.ids else None


def _stable_ids(elements):
    """Block IDs from the id attributes, or 0..n-1 if they aren't distinct
    non-negative integers (the importer only needs <jointedTo> to match
    them, so any distinct values work)."""
    try:
        ids = tuple(int((e.get("id") or "").strip()) for e in elements)
    except ValueError:
        ids = None
    if ids is None or len(set(ids)) != len(ids) or any(i < 0 for i in ids):
        return tuple(range(len(elements)))
    return ids


def _text(el, path):
    found = el.find(path)
    return (found.text or "").strip() if found is not None else ""


def _block(el):
    values = {
        "rotation": _text(el, "rotation") or "0",
        "x": _text(el, "position/x"),
        "y": _text(el, "position/y"),
        "width": _text(el, "width"),
        "height": _text(el, "height"),
    }
    return BlockXml(el.tag, values, _text(el, "goalBlock") == "true")


def _zone(el):
    return Zone({
        "x": _text(el, "position/x"),
        "y": _text(el, "position/y"),
        "width": _text(el, "width"),
        "height": _text(el, "height"),
    })


def parse(xml):
    """Parse retrieveLevel/saveDesign XML into a Design."""
    if isinstance(xml, str):
        xml = xml.encode()
    root = ET.fromstring(xml)
    level = root.find("level")
    if level is None:
        raise ValueError("no <level> element")
    lb = level.find("levelBlocks")
    pb = level.find("playerBlocks")
    pb = list(pb) if pb is not None else []
    return Design(
        level_blocks=tuple(_block(e) for e in (lb if lb is not None else [])),
        player_blocks=tuple(_block(e) for e in pb),
        ids=_stable_ids(pb),
        start=_zone(level.find("start")),
        end=_zone(level.find("end")),
        level_id=_text(root, "levelId"),
    )


def _block_xml(b, block_id, jointed_to):
    v = b.values
    id_attr = f' id="{block_id}"' if block_id is not None else ""
    joints = "".join(f"<jointedTo>{j}</jointedTo>" for j in jointed_to)
    return (
        f"<{b.tag}{id_attr}><rotation>{v['rotation']}</rotation>"
        f"<position><x>{v['x']}</x><y>{v['y']}</y></position>"
        f"<width>{v['width']}</width><height>{v['height']}</height>"
        f"<goalBlock>{'true' if b.goal else 'false'}</goalBlock>"
        f"<joints>{joints}</joints></{b.tag}>"
    )


def _zone_xml(tag, z):
    v = z.values
    return (
        f"<{tag}><position><x>{v['x']}</x><y>{v['y']}</y></position>"
        f"<width>{v['width']}</width><height>{v['height']}</height></{tag}>"
    )


def serialize(design, jointed_to):
    """XML for `design`. jointed_to[i] lists the block indices player block i
    is jointed to; they are written as the blocks' stable IDs."""
    level = "".join(_block_xml(b, None, ()) for b in design.level_blocks)
    ids = design.ids
    player = "".join(
        _block_xml(b, ids[i], [ids[j] for j in jointed_to[i]])
        for i, b in enumerate(design.player_blocks)
    )
    return (
        '<?xml version="1.0"?><retrieveLevel>'
        f"<levelId>{design.level_id}</levelId><level>"
        f"<levelBlocks>{level}</levelBlocks><playerBlocks>{player}</playerBlocks>"
        f"{_zone_xml('start', design.start)}{_zone_xml('end', design.end)}"
        "</level></retrieveLevel>"
    )


# ------------------------------------------------------------------ graph


@dataclass(frozen=True)
class JointRef:
    """A joint slot of a player block: (block, slot). In the public API
    `block` is the block's stable ID; internally it is the block index.
    Slots follow
    fcsim's canonical order: rod 0/1 (ends); wheel 0 centre, 1-4
    circumference, 5-8 inner (radius > 20 only); goal rect 0 centre, 1-4
    corners. A ref names the whole joint stack the slot belongs to."""

    block: int
    slot: int


@dataclass
class Stack:
    """One design joint: a joint stack. `gen` is the generating slot (a goal
    rect corner, a wheel's circumference/inner joint) or None; `att` are the
    attached slots (rod ends, wheel centres) in chain order."""

    x: float
    y: float
    gen: JointRef = None
    att: list = field(default_factory=list)

    @property
    def members(self):
        return ([self.gen] if self.gen else []) + list(self.att)

    def key(self):
        return (self.gen, tuple(self.att))


@dataclass
class Graph:
    stacks: list  # of Stack, in the design's joint list order
    slots: list  # per player block: its number of joint slots
    types: list  # per player block: fccore type id
    flags: list  # per player block: fccore ILLEGAL_* flags

    def stack_of(self, ref):
        for s in self.stacks:
            if ref in s.members:
                return s
        return None

    def canonical(self):
        return sorted((s.key() for s in self.stacks), key=repr)


def read_graph(xml):
    """Import `xml` with fccore and return its joint graph."""
    if isinstance(xml, str):
        xml = xml.encode()
    with fccore.Sim(xml) as sim:
        n = sim.n_design_blocks
        blocks = sim.blocks()
        owner = {}  # joint id -> {block: slot}
        slots = []
        for b in range(n):
            ids = sim.block_joint_ids(b)
            slots.append(len(ids))
            for slot, jid in enumerate(ids):
                owner.setdefault(jid, {})[b] = slot
        stacks = []
        for jid, j in enumerate(sim.design_joints()):
            own = owner.get(jid, {})
            gen = JointRef(j.gen, own[j.gen]) if j.gen >= 0 else None
            att = [JointRef(b, own[b]) for b in j.att]
            stacks.append(Stack(j.x, j.y, gen, att))
        flags = sim.check_legal().flags
        return Graph(stacks, slots, [blocks[i].type_id for i in range(n)], list(flags))


def copy_graph(g):
    return replace(
        g,
        stacks=[Stack(s.x, s.y, s.gen, list(s.att)) for s in g.stacks],
        slots=list(g.slots),
        types=list(g.types),
        flags=list(g.flags),
    )

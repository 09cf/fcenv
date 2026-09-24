"""Design edits: pure functions from (xml, operation) to Ok(xml, info) or Err.

Each edit:
1. reads the joint graph by importing the XML with fccore;
2. validates its arguments, applies the operation to that graph and checks
   the editor's invariants for the blocks it touches;
3. writes new XML, keeping untouched blocks' values verbatim and choosing
   <jointedTo> lists that make the importer rebuild the intended graph;
4. re-imports the result to confirm the joint graph is exactly the intended
   one, and runs fccore's legality check (overlap, build area), rejecting
   edits that add violations.

No physics is stepped. Connections are always explicit: an endpoint is a
JointRef (connect to that joint), or a point (a new free joint there), given
directly as (x, y) or relative to a joint as Offset/Polar.

Blocks are named by stable IDs (the XML id attribute): a new block gets the
next unused ID, and IDs never change afterwards, even when block order does.
Internally, blocks are handled by index (XML order), which is what fccore
and the physics see; IDs are translated at the API boundary.
"""

import itertools
import math
from dataclasses import dataclass, field, replace
from enum import Enum

from .design import (
    BlockXml,
    JointRef,
    Stack,
    copy_graph,
    fccore,
    fmt,
    parse,
    read_graph,
    serialize,
)

ROD_TYPES = {fccore.WATER_ROD, fccore.SOLID_ROD}
WHEEL_TYPES = {
    fccore.WHEEL, fccore.CW_WHEEL, fccore.CCW_WHEEL,
    fccore.GOAL_CIRCLE, fccore.CW_GOAL_CIRCLE, fccore.CCW_GOAL_CIRCLE,
}
GOAL_TYPES = {fccore.GOAL_RECT, fccore.GOAL_CIRCLE, fccore.CW_GOAL_CIRCLE,
              fccore.CCW_GOAL_CIRCLE}


class RodKind(Enum):
    WATER = "water"
    WOOD = "wood"


class Spin(Enum):
    NONE = "none"
    CW = "cw"
    CCW = "ccw"


_ROD = {RodKind.WATER: ("HollowRod", "4", fccore.WATER_ROD),
        RodKind.WOOD: ("SolidRod", "8", fccore.SOLID_ROD)}
_WHEEL = {Spin.NONE: ("NoSpinWheel", fccore.WHEEL),
          Spin.CW: ("ClockwiseWheel", fccore.CW_WHEEL),
          Spin.CCW: ("CounterClockwiseWheel", fccore.CCW_WHEEL)}


class ErrorCode(Enum):
    BAD_REFERENCE = "bad_reference"  # no such block or joint slot
    CANNOT_DELETE_GOAL = "cannot_delete_goal"  # deleting a goal piece
    WRONG_BLOCK_TYPE = "wrong_block_type"  # e.g. rewiring a wheel
    SAME_JOINT = "same_joint"  # a block would attach twice to one joint
    SHARED_TWICE = "shared_twice"  # two blocks would share two joints
    ROD_TOO_SHORT = "rod_too_short"
    PIECE_LIMIT = "piece_limit"
    OVERLAP = "overlap"  # also: two wheels on one joint (see _invariants)
    OUTSIDE_BUILD_AREA = "outside_build_area"
    UNREPRESENTABLE = "unrepresentable"  # XML can't express this unambiguously


@dataclass(frozen=True)
class EditError:
    code: ErrorCode
    message: str
    blocks: tuple = ()  # block IDs involved


@dataclass(frozen=True)
class Ok:
    xml: str
    info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Err:
    error: EditError


@dataclass(frozen=True)
class Rules:
    """Editor limits. None disables a limit."""

    # all design blocks, goal pieces included; fcsim's editor refuses new
    # pieces at 300 (MAX_PIECES_HARD_CAP)
    max_design_blocks: int = 300
    min_rod_length: float = 1.0  # the game clamps rod length to >= 1


@dataclass(frozen=True)
class NewRef:
    """In build: joint `slot` of the `index`-th piece of the same batch,
    which must come earlier in the batch."""

    index: int
    slot: int


@dataclass(frozen=True)
class Offset:
    """The point (dx, dy) away from `base` (a JointRef, a NewRef in a batch,
    or an (x, y) point). A free point: it doesn't connect to `base`."""

    base: object
    dx: float
    dy: float


@dataclass(frozen=True)
class Polar:
    """The point `length` away from `base` at `angle` degrees: 0 is +x, and
    since +y is down, 90 is straight down (angles turn clockwise on screen).
    A free point: it doesn't connect to `base`."""

    base: object
    length: float
    angle: float


class _Fail(Exception):
    """Internal: abort an edit with an EditError (messages already use IDs)."""

    def __init__(self, code, message, blocks=()):
        super().__init__(message)
        self.error = EditError(code, message, tuple(blocks))


# ------------------------------------------------------------------ state


@dataclass
class _State:
    design: object
    graph: object
    orig_jointed: list  # per block index: original <jointedTo> as indices

    def bid(self, index):
        return self.design.ids[index]

    def index(self, block_id):
        i = self.design.index_of(block_id)
        if i is None:
            raise _Fail(ErrorCode.BAD_REFERENCE, f"no block {block_id}")
        return i


def _load(xml):
    import xml.etree.ElementTree as ET

    design = parse(xml)
    graph = read_graph(xml)
    if len(design.player_blocks) != len(graph.slots):
        raise ValueError("design contains player blocks fccore doesn't import")
    raw = xml.encode() if isinstance(xml, str) else xml
    pb = ET.fromstring(raw).find("level/playerBlocks")
    elements = list(pb) if pb is not None else []
    by_id = {}
    for i, e in enumerate(elements):
        by_id.setdefault((e.get("id") or "").strip(), i)
    orig = [[by_id[j.text.strip()] for j in e.findall("joints/jointedTo")
             if j.text and j.text.strip() in by_id] for e in elements]
    return _State(design, graph, orig)


def _run(fn):
    """Turn internal failures into Err results."""
    try:
        return fn()
    except _Fail as f:
        return Err(f.error)


def _ref(st, ref):
    """Public JointRef (block ID) -> internal JointRef (block index)."""
    if not isinstance(ref, JointRef):
        raise _Fail(ErrorCode.BAD_REFERENCE, f"{ref!r} is not a joint")
    i = st.index(ref.block)
    if not 0 <= ref.slot < st.graph.slots[i]:
        raise _Fail(ErrorCode.BAD_REFERENCE,
                    f"block {ref.block} has no joint slot {ref.slot}")
    return JointRef(i, ref.slot)


def _public(st, ref):
    return JointRef(st.bid(ref.block), ref.slot)


def _endpoint(st, graph, ep, batch=()):
    """Resolve a public endpoint to an internal JointRef or an (x, y) point.
    `batch` lists the internal indices of earlier pieces of the same batch."""
    if isinstance(ep, NewRef):
        if not (0 <= ep.index < len(batch) and 0 <= ep.slot < graph.slots[batch[ep.index]]):
            raise _Fail(ErrorCode.BAD_REFERENCE,
                        f"@{ep.index}:{ep.slot} must name a joint of an earlier piece "
                        "of the same batch")
        return JointRef(batch[ep.index], ep.slot)
    if isinstance(ep, JointRef):
        return _ref(st, ep)
    if isinstance(ep, (Offset, Polar)):
        x, y = _point_of(graph, _endpoint(st, graph, ep.base, batch))
        if isinstance(ep, Offset):
            return (x + float(ep.dx), y + float(ep.dy))
        a = math.radians(float(ep.angle))
        return (x + float(ep.length) * math.cos(a), y + float(ep.length) * math.sin(a))
    try:
        x, y = ep
        return (float(x), float(y))
    except (TypeError, ValueError):
        raise _Fail(ErrorCode.BAD_REFERENCE, f"{ep!r} is not a joint or a point")


def _point_of(graph, ep):
    if isinstance(ep, JointRef):
        s = graph.stack_of(ep)
        return s.x, s.y
    return ep


# ------------------------------------------------------------------ commit


def _invariants(design, graph, changed):
    """Editor invariants, checked for joints involving changed blocks."""
    ids = design.ids
    pairs = {}
    for s in graph.stacks:
        blocks = [m.block for m in s.members]
        if not changed.intersection(blocks):
            continue
        if len(set(blocks)) != len(blocks):
            dup = next(b for b in blocks if blocks.count(b) > 1)
            raise _Fail(ErrorCode.SAME_JOINT,
                        f"block {ids[dup]} would attach twice to one joint", [ids[dup]])
        # Two wheels on one joint are reported as an overlap, which is what
        # they are, but the legality check can't see it: blocks sharing a
        # joint never collide, so fccore would happily simulate them. The
        # game's editor never reaches this state (it refuses to snap a wheel
        # onto a joint with a wheel, leaving it unattached and overlapping),
        # so check it here, and give the agent the same error it would get
        # for any other overlap.
        wheels = [ids[b] for b in blocks if graph.types[b] in WHEEL_TYPES]
        if len(wheels) > 1:
            raise _Fail(ErrorCode.OVERLAP,
                        f"a joint can hold at most one wheel: wheels {wheels} would "
                        "overlap", wheels)
        for pair in itertools.combinations(sorted(blocks), 2):
            if changed.intersection(pair):
                pairs[pair] = pairs.get(pair, 0) + 1
    for (a, b), n in pairs.items():
        if n > 1:
            raise _Fail(ErrorCode.SHARED_TWICE,
                        f"blocks {ids[a]} and {ids[b]} would share {n} joints "
                        "(a block can't connect to another block twice, e.g. a rod "
                        "across two joints of one wheel or goal rect)", [ids[a], ids[b]])


def _candidates(graph, n):
    """Per block, per attached slot: blocks earlier in that slot's chain, the
    one directly beneath first. The importer needs one of them in
    <jointedTo> to find the joint."""
    cands = [dict() for _ in range(n)]
    for s in graph.stacks:
        chain = s.members
        for k, m in enumerate(chain):
            if k:
                cands[m.block][m.slot] = [c.block for c in reversed(chain[:k])]
    return cands


def _predecessors(graph):
    """(block, slot) -> block directly beneath in its chain, or None."""
    out = {}
    for s in graph.stacks:
        chain = s.members
        for k, m in enumerate(chain):
            out[(m.block, m.slot)] = chain[k - 1].block if k else None
    return out


def _choose_jointed_to(design, graph, first_choice, max_imports=24):
    """Find <jointedTo> lists that import back into `graph`. Returns
    (xml, imported graph) or None."""
    n = len(design.player_blocks)
    cands = _candidates(graph, n)
    options = []
    for b in range(n):
        slots = sorted(cands[b])
        combos = [sorted(set(c)) for c in itertools.product(*(cands[b][s] for s in slots))]
        first = first_choice[b]
        if first is not None and set(first) in [set(c) for c in combos]:
            combos.remove(sorted(set(first)))
            combos.insert(0, sorted(set(first)))
        options.append(combos or [[]])
    choice = [0] * n
    want = _predecessors(graph)
    for _ in range(max_imports):
        xml = serialize(design, [options[b][choice[b]] for b in range(n)])
        got = read_graph(xml)
        if got.canonical() == graph.canonical():
            return xml, got
        # the importer works block by block: fix the first block that differs
        have = _predecessors(got)
        bad = min((k[0] for k in set(want) | set(have) if want.get(k) != have.get(k)),
                  default=None)
        if bad is None or choice[bad] + 1 >= len(options[bad]):
            return None
        choice[bad] += 1
        for later in range(bad + 1, n):
            choice[later] = 0
    return None


def _commit(st, design, graph, changed, old_index, first_choice, info):
    """Serialize, verify and legality-check the intended design/graph.
    old_index[i] is the pre-edit index of new block i (None if new)."""
    _invariants(design, graph, changed)
    found = _choose_jointed_to(design, graph, first_choice)
    if found is None:
        raise _Fail(ErrorCode.UNREPRESENTABLE,
                    "the XML format can't express this joint graph unambiguously "
                    "(two joints of connected blocks at the same point?)",
                    sorted(design.ids[i] for i in changed))
    xml, got = found
    for i, flags in enumerate(got.flags):
        if got.types[i] in GOAL_TYPES:
            continue
        before = 0 if old_index[i] is None or i in changed else st.graph.flags[old_index[i]]
        new = flags & ~before
        bid = design.ids[i]
        if new & fccore.ILLEGAL_OVERLAP:
            raise _Fail(ErrorCode.OVERLAP, f"block {bid} would overlap another block", [bid])
        if new & fccore.ILLEGAL_OUTSIDE_BUILD_AREA:
            raise _Fail(ErrorCode.OUTSIDE_BUILD_AREA,
                        f"block {bid} would be outside the build area", [bid])
    return Ok(xml, info)


def _next_id(design):
    return max(design.ids, default=-1) + 1


# ------------------------------------------------------------------ edits


def add_rod(xml, kind, a, b, rules=Rules(), count=1):
    """Add a rod from `a` to `b`. Each endpoint is a JointRef (connect to
    that joint), or a free point: (x, y), Offset or Polar. With count > 1,
    add that many identical rods, e.g. as weights: rods sharing a JointRef
    end all attach to that joint, and each gets its own free joint at a
    point. info: blocks, joints (per rod); with count 1 also block, joints."""
    result = add_rods(xml, [(kind, a, b)] * count, rules)
    if isinstance(result, Ok) and count == 1:
        info = dict(result.info, block=result.info["blocks"][0],
                    joints=result.info["joints"][0])
        return Ok(result.xml, info)
    return result


@dataclass(frozen=True)
class Rod:
    """A rod piece for build(): kind "wood" or "water", ends a and b."""

    kind: str
    a: object
    b: object


@dataclass(frozen=True)
class Wheel:
    """A (40mm) wheel piece for build(): spin "none", "cw" or "ccw"."""

    spin: str
    center: object


# a new wheel's rim joints (slots 1-4), relative to its centre
_RIM = ((20, 0), (0, 20), (-20, 0), (0, -20))


def build(xml, pieces, rules=Rules()):
    """Add several pieces (Rod and Wheel) at once, atomically: all of them or
    none. Endpoints and centres are a JointRef, a point, an Offset/Polar, or
    NewRef(i, slot): joint `slot` of the i-th piece earlier in the batch (a
    rod's 0-1, a wheel's 0-4). info: blocks, joints (per piece)."""
    return _run(lambda: _build(_load(xml), list(pieces), rules))


def add_rods(xml, rods, rules=Rules()):
    """build() with rods only; `rods` is a list of (kind, a, b)."""
    return build(xml, [Rod(*r) for r in rods], rules)


def add_wheel(xml, spin, center, rules=Rules()):
    """Add a wheel centred on `center`. info: block, joints."""
    r = build(xml, [Wheel(spin, center)], rules)
    if isinstance(r, Ok):
        return Ok(r.xml, {"block": r.info["blocks"][0], "joints": r.info["joints"][0]})
    return r


def _build(st, pieces, rules, keep_ids=None):
    n0 = len(st.design.player_blocks)
    if rules.max_design_blocks is not None and n0 + len(pieces) > rules.max_design_blocks:
        raise _Fail(ErrorCode.PIECE_LIMIT,
                    f"design has {n0} blocks; adding {len(pieces)} would exceed "
                    f"{rules.max_design_blocks}")
    graph = copy_graph(st.graph)
    blocks = list(st.design.player_blocks)
    ids = list(st.design.ids)
    batch = []
    for i, piece in enumerate(pieces):
        where = f"piece {i} of the batch: " if len(pieces) > 1 else ""
        n = n0 + i
        try:
            ends = [_endpoint(st, graph, ep, batch)
                    for ep in ((piece.a, piece.b) if isinstance(piece, Rod) else (piece.center,))]
        except _Fail as f:
            raise _Fail(f.error.code, where + f.error.message, f.error.blocks)
        if isinstance(piece, Rod):
            a, b = ends
            if (isinstance(a, JointRef) and isinstance(b, JointRef)
                    and graph.stack_of(a) is graph.stack_of(b)):
                raise _Fail(ErrorCode.SAME_JOINT, f"{where}both ends are the same joint")
            (ax, ay), (bx, by) = _point_of(graph, a), _point_of(graph, b)
            dx, dy = bx - ax, by - ay
            length = math.sqrt(dx * dx + dy * dy)
            if length < rules.min_rod_length:
                raise _Fail(ErrorCode.ROD_TOO_SHORT,
                            f"{where}rod length {length:g} < {rules.min_rod_length:g}")
            tag, height, type_id = _ROD[RodKind(piece.kind)]
            blocks.append(BlockXml(tag, {"rotation": fmt(math.atan2(dy, dx)),
                                         "x": fmt(ax + dx / 2), "y": fmt(ay + dy / 2),
                                         "width": fmt(length), "height": height}, False))
            n_slots = 2
        else:
            (c,) = ends
            tag, type_id = _WHEEL[Spin(piece.spin)]
            x, y = _point_of(graph, c)
            blocks.append(BlockXml(tag, {"rotation": "0", "x": fmt(x), "y": fmt(y),
                                         "width": "40", "height": "40"}, False))
            # rim joints, generated by the wheel
            for slot, (ox, oy) in enumerate(_RIM, 1):
                graph.stacks.append(Stack(x + ox, y + oy, JointRef(n, slot), []))
            n_slots = 5
        for slot, ep in enumerate(ends):
            ref = JointRef(n, slot)
            if isinstance(ep, JointRef):
                graph.stack_of(ep).att.append(ref)
            else:
                graph.stacks.append(Stack(ep[0], ep[1], None, [ref]))
        ids.append(keep_ids[i] if keep_ids else max(ids, default=-1) + 1)
        graph.slots.append(n_slots)
        graph.types.append(type_id)
        graph.flags.append(0)
        batch.append(n)
    design = replace(st.design, player_blocks=tuple(blocks), ids=tuple(ids))
    new_ids = [ids[n] for n in batch]
    info = {"blocks": new_ids,
            "joints": [[JointRef(b, s) for s in range(graph.slots[n])]
                       for b, n in zip(new_ids, batch)]}
    return _commit(st, design, graph, set(batch),
                   list(range(n0)) + [None] * len(pieces),
                   [list(j) for j in st.orig_jointed] + [None] * len(pieces), info)


def _deleted(st, block):
    """_State without the block at index `block`, plus old indices."""
    remap = {i: (i if i < block else i - 1) for i in range(len(st.graph.slots)) if i != block}

    def mv(r):
        return JointRef(remap[r.block], r.slot)

    stacks = []
    for s in st.graph.stacks:
        att = [mv(m) for m in s.att if m.block != block]
        gen = mv(s.gen) if s.gen and s.gen.block != block else None
        if gen or att:
            stacks.append(Stack(s.x, s.y, gen, att))
    graph = copy_graph(st.graph)
    graph.stacks = stacks
    for lst in (graph.slots, graph.types, graph.flags):
        del lst[block]
    d = st.design
    design = replace(d, player_blocks=d.player_blocks[:block] + d.player_blocks[block + 1:],
                     ids=d.ids[:block] + d.ids[block + 1:])
    first = [[remap[j] for j in jt if j in remap]
             for i, jt in enumerate(st.orig_jointed) if i != block]
    old_index = [i for i in range(len(d.player_blocks)) if i != block]
    return _State(design, graph, first), old_index


def delete(xml, block, rules=Rules()):
    """Delete the block with ID `block`. info: {}."""
    def go():
        st = _load(xml)
        i = st.index(block)
        if st.graph.types[i] in GOAL_TYPES:
            raise _Fail(ErrorCode.CANNOT_DELETE_GOAL,
                        f"block {block} is a goal piece; goal pieces can't be deleted",
                        [block])
        st2, old_index = _deleted(st, i)
        return _commit(st, st2.design, st2.graph, set(), old_index, st2.orig_jointed, {})
    return _run(go)


def rewire(xml, block, slot, target, rules=Rules()):
    """Reconnect end `slot` (0 or 1) of rod `block` to `target` (a JointRef),
    or detach it (target None, leaving a free joint where it was). The rod
    stretches to the new joint. It keeps its ID but moves to the end of the
    block order, as if deleted and re-added (block order affects the
    physics). info: block, joints."""
    def go():
        st = _load(xml)
        i = st.index(block)
        if st.graph.types[i] not in ROD_TYPES:
            raise _Fail(ErrorCode.WRONG_BLOCK_TYPE,
                        f"block {block} is not a rod; rewire accepts rods", [block])
        if slot not in (0, 1):
            raise _Fail(ErrorCode.BAD_REFERENCE, f"rods have slots 0 and 1, not {slot}")
        t = _ref(st, target) if target is not None else None
        info = {"block": block, "joints": [JointRef(block, 0), JointRef(block, 1)]}
        if t is not None:
            if st.graph.stack_of(t) is st.graph.stack_of(JointRef(i, slot)):
                # already connected there: nothing to do
                return Ok(xml if isinstance(xml, str) else xml.decode(), info)
            if st.graph.stack_of(t) is st.graph.stack_of(JointRef(i, 1 - slot)):
                raise _Fail(ErrorCode.SAME_JOINT,
                            f"{target} is the rod's other end", [block])
        ends = []  # public endpoints for the re-added rod
        for s in (0, 1):
            stack = st.graph.stack_of(JointRef(i, s))
            others = [m for m in stack.members if m.block != i]
            if s == slot and t is not None:
                ends.append(target)
            elif s != slot and others:
                ends.append(_public(st, others[0]))  # stay on the same joint
            else:
                ends.append((stack.x, stack.y))
        kind = RodKind.WATER if st.graph.types[i] == fccore.WATER_ROD else RodKind.WOOD
        st2, old_index = _deleted(st, i)
        # legality compares against the pre-edit flags
        st2.graph = replace(st2.graph, flags=[st.graph.flags[k] for k in old_index])
        result = _build(st2, [Rod(kind, ends[0], ends[1])], rules, keep_ids=[block])
        return Ok(result.xml, info)
    return _run(go)


def move_joint(xml, joint, to, rules=Rules()):
    """Move the joint stack `joint` (a JointRef) to `to`: an (x, y) point, or
    an Offset/Polar (relative to where things are before the move).

    As in fcsim's editor: any non-rod block with a joint in the moved set
    (wheel, goal rect, goal circle) moves rigidly, taking all its joints
    along, until nothing else joins. Rods stretch; a rod with both ends in
    the moved set translates. Moving never connects or disconnects anything,
    and block order is unchanged. Goal pieces can be moved.
    info: moved (blocks translated), stretched (rods stretched), delta."""
    def go():
        st = _load(xml)
        graph = st.graph
        start = graph.stack_of(_ref(st, joint))
        tx, ty = _point_of(graph, _endpoint(st, graph, to))
        dx, dy = tx - start.x, ty - start.y

        # closure: moved joint stacks and rigid (non-rod) blocks
        moved, rigid, queue = [start], set(), [start]
        while queue:
            s = queue.pop()
            for m in s.members:
                if graph.types[m.block] in ROD_TYPES or m.block in rigid:
                    continue
                rigid.add(m.block)
                for sl in range(graph.slots[m.block]):
                    other = graph.stack_of(JointRef(m.block, sl))
                    if all(other is not x for x in moved):
                        moved.append(other)
                        queue.append(other)
        moved_ids = {id(s) for s in moved}

        blocks = list(st.design.player_blocks)
        stretched = []
        for i, b in enumerate(blocks):
            if i in rigid:
                v = dict(b.values, x=fmt(b.num("x") + dx), y=fmt(b.num("y") + dy))
                blocks[i] = replace(b, values=v)
            elif graph.types[i] in ROD_TYPES:
                ends = [graph.stack_of(JointRef(i, s)) for s in (0, 1)]
                if not any(id(e) in moved_ids for e in ends):
                    continue
                (ax, ay), (bx, by) = ((e.x + dx, e.y + dy) if id(e) in moved_ids
                                      else (e.x, e.y) for e in ends)
                ex, ey = bx - ax, by - ay
                length = math.sqrt(ex * ex + ey * ey)
                if length < rules.min_rod_length:
                    raise _Fail(ErrorCode.ROD_TOO_SHORT,
                                f"rod {st.bid(i)} would be {length:g} long "
                                f"(< {rules.min_rod_length:g})", [st.bid(i)])
                v = dict(b.values, rotation=fmt(math.atan2(ey, ex)), x=fmt(ax + ex / 2),
                         y=fmt(ay + ey / 2), width=fmt(length))
                blocks[i] = replace(b, values=v)
                stretched.append(i)

        design = replace(st.design, player_blocks=tuple(blocks))
        n = len(blocks)
        info = {"moved": sorted(st.bid(i) for i in rigid),
                "stretched": sorted(st.bid(i) for i in stretched), "delta": (dx, dy)}
        # same joint graph, same block order: only positions change
        return _commit(st, design, copy_graph(graph), rigid | set(stretched),
                       list(range(n)), [list(j) for j in st.orig_jointed], info)
    return _run(go)


def move_component(xml, block, dx, dy, rules=Rules()):
    """Translate the whole connected component containing block `block`
    (every block joined to it through shared joints, goal pieces included)
    by (dx, dy). Nothing stretches, connects or disconnects.
    info: moved (block IDs), delta."""
    def go():
        st = _load(xml)
        i0 = st.index(block)
        comp = next(g for g in _components(st.graph) if i0 in g)
        blocks = list(st.design.player_blocks)
        for i in comp:
            b = blocks[i]
            v = dict(b.values, x=fmt(b.num("x") + float(dx)), y=fmt(b.num("y") + float(dy)))
            blocks[i] = replace(b, values=v)
        design = replace(st.design, player_blocks=tuple(blocks))
        n = len(blocks)
        info = {"moved": sorted(st.bid(i) for i in comp), "delta": (float(dx), float(dy))}
        return _commit(st, design, copy_graph(st.graph), set(comp), list(range(n)),
                       [list(j) for j in st.orig_jointed], info)
    return _run(go)


# ------------------------------------------------------------------ queries


def _components(graph):
    """Connected components of player blocks (by index), via shared joints,
    in order of their first block."""
    parent = list(range(len(graph.slots)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for s in graph.stacks:
        blocks = [m.block for m in s.members]
        for other in blocks[1:]:
            parent[find(other)] = find(blocks[0])
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def describe(xml):
    """The design, for inspection. Blocks and joints are named by ID.

    blocks: id, type, goal, x, y, angle, width, height, joints (one JointRef per
            slot), flags (fccore ILLEGAL_* bits)
    joints: x, y, name (canonical JointRef: lowest block ID, then slot),
            members (all JointRefs naming it: generator first, then chain)
    components: lists of block IDs connected through shared joints
    """
    st = _load(xml)
    ids = st.design.ids
    blocks = []
    for i, b in enumerate(st.design.player_blocks):
        blocks.append({
            "id": ids[i], "type": b.tag, "goal": b.goal,
            "x": b.num("x"), "y": b.num("y"), "angle": b.num("rotation"),
            "width": b.num("width"), "height": b.num("height"),
            "joints": [JointRef(ids[i], s) for s in range(st.graph.slots[i])],
            "flags": st.graph.flags[i],
        })
    joints = []
    for s in st.graph.stacks:
        members = [_public(st, m) for m in s.members]
        name = min(members, key=lambda r: (r.block, r.slot))
        joints.append({"x": s.x, "y": s.y, "name": name, "members": members})
    components = [[ids[i] for i in g] for g in _components(st.graph)]
    return {"blocks": blocks, "joints": joints, "components": components}

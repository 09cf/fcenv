"""Design edits: pure functions from (xml, operation) to Ok(xml, info) or Err.

Each edit:
1. reads the joint graph by importing the XML with fccore;
2. applies the operation to that graph and checks the editor's invariants
   for the blocks it touches;
3. writes new XML, keeping untouched blocks' values verbatim and choosing
   <jointedTo> lists that make the importer rebuild the intended graph;
4. re-imports the result to confirm the joint graph is exactly the intended
   one, and runs fccore's legality check (overlap, build area), rejecting
   edits that add violations.

No physics is stepped. Connections are always explicit: an endpoint is either
a JointRef (connect to that joint stack) or a Point (a free joint there).
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
    IMMUTABLE_BLOCK = "immutable_block"  # goal or level block
    WRONG_BLOCK_TYPE = "wrong_block_type"  # e.g. rewiring a wheel
    SAME_JOINT = "same_joint"  # a block would attach twice to one joint
    SHARED_TWICE = "shared_twice"  # two blocks would share two joints
    TWO_WHEELS = "two_wheels"  # a joint would hold two wheels
    ROD_TOO_SHORT = "rod_too_short"
    PIECE_LIMIT = "piece_limit"
    OVERLAP = "overlap"
    OUTSIDE_BUILD_AREA = "outside_build_area"
    UNREPRESENTABLE = "unrepresentable"  # XML can't express this unambiguously


@dataclass(frozen=True)
class EditError:
    code: ErrorCode
    message: str
    blocks: tuple = ()


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


def _err(code, message, blocks=()):
    return Err(EditError(code, message, tuple(blocks)))


# ------------------------------------------------------------------ state


@dataclass
class _State:
    design: object
    graph: object
    orig_jointed: list  # per block: original <jointedTo> as block indices


def _load(xml):
    design = parse(xml)
    graph = read_graph(xml)
    if len(design.player_blocks) != len(graph.slots):
        raise ValueError("design contains player blocks fccore doesn't import")
    ids = {}
    for i, el_id in enumerate(_ids(xml)):
        ids.setdefault(el_id, i)
    orig = [[ids[j] for j in jt if j in ids] for jt in _jointed_ids(xml)]
    return _State(design, graph, orig)


def _player_elements(xml):
    import xml.etree.ElementTree as ET

    if isinstance(xml, str):
        xml = xml.encode()
    pb = ET.fromstring(xml).find("level/playerBlocks")
    return list(pb) if pb is not None else []


def _ids(xml):
    return [(e.get("id") or "").strip() for e in _player_elements(xml)]


def _jointed_ids(xml):
    return [[(j.text or "").strip() for j in e.findall("joints/jointedTo")]
            for e in _player_elements(xml)]


def _check_ref(st, ref):
    if not isinstance(ref, JointRef):
        return None
    if not (0 <= ref.block < len(st.graph.slots)) or not (
            0 <= ref.slot < st.graph.slots[ref.block]):
        return _err(ErrorCode.BAD_REFERENCE, f"no joint {ref}")
    return None


def _position(st, ep):
    if isinstance(ep, JointRef):
        s = st.graph.stack_of(ep)
        return s.x, s.y
    return float(ep[0]), float(ep[1])


# ------------------------------------------------------------------ commit


def _invariants(graph, changed):
    """Editor invariants, checked for joints involving changed blocks."""
    pairs = {}
    for s in graph.stacks:
        blocks = [m.block for m in s.members]
        if not changed.intersection(blocks):
            continue
        if len(set(blocks)) != len(blocks):
            dup = [b for b in blocks if blocks.count(b) > 1]
            return _err(ErrorCode.SAME_JOINT,
                        f"block {dup[0]} would attach twice to one joint", dup[:1])
        wheels = [b for b in blocks if graph.types[b] in WHEEL_TYPES]
        if len(wheels) > 1:
            return _err(ErrorCode.TWO_WHEELS,
                        f"a joint would hold two wheels ({wheels})", wheels)
        for pair in itertools.combinations(sorted(blocks), 2):
            if changed.intersection(pair):
                pairs[pair] = pairs.get(pair, 0) + 1
    for pair, n in pairs.items():
        if n > 1:
            return _err(ErrorCode.SHARED_TWICE,
                        f"blocks {pair[0]} and {pair[1]} would share {n} joints "
                        "(a block can't connect to another block twice, e.g. a rod "
                        "across two joints of one wheel or goal rect)", pair)
    return None


def _candidates(graph, n):
    """Per block, per attached slot: blocks earlier in that slot's chain, the
    one directly beneath first. The importer needs one of them in
    <jointedTo> to find the joint."""
    cands = [dict() for _ in range(n)]
    for s in graph.stacks:
        chain = s.members
        for k, m in enumerate(chain):
            if k == 0:
                continue
            earlier = [c.block for c in reversed(chain[:k])]
            cands[m.block][m.slot] = earlier
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
        jt = [options[b][choice[b]] for b in range(n)]
        xml = serialize(design, jt)
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
    bad = _invariants(graph, changed)
    if bad:
        return bad
    found = _choose_jointed_to(design, graph, first_choice)
    if found is None:
        return _err(ErrorCode.UNREPRESENTABLE,
                    "the XML format can't express this joint graph unambiguously "
                    "(two joints of connected blocks at the same point?)",
                    sorted(changed))
    xml, got = found
    for i, flags in enumerate(got.flags):
        if got.types[i] in GOAL_TYPES:
            continue
        before = 0 if old_index[i] is None or i in changed else st.graph.flags[old_index[i]]
        new = flags & ~before
        if new & fccore.ILLEGAL_OVERLAP:
            return _err(ErrorCode.OVERLAP, f"block {i} would overlap another block", [i])
        if new & fccore.ILLEGAL_OUTSIDE_BUILD_AREA:
            return _err(ErrorCode.OUTSIDE_BUILD_AREA,
                        f"block {i} would be outside the build area", [i])
    return Ok(xml, info)


def _identity_first_choice(st):
    return [list(j) for j in st.orig_jointed]


# ------------------------------------------------------------------ edits


def add_rod(xml, kind, a, b, rules=Rules()):
    """Add a rod from `a` to `b`. Each endpoint is a JointRef (connect to
    that joint) or an (x, y) point (a new free joint). info: block, joints."""
    st = _load(xml)
    return _add_rod(st, RodKind(kind), a, b, rules, _identity_first_choice(st),
                    list(range(len(st.graph.slots))), {})


def _add_rod(st, kind, a, b, rules, first_choice, old_index, info):
    for ep in (a, b):
        bad = _check_ref(st, ep)
        if bad:
            return bad
    if (isinstance(a, JointRef) and isinstance(b, JointRef)
            and st.graph.stack_of(a) is st.graph.stack_of(b)):
        return _err(ErrorCode.SAME_JOINT, f"{a} and {b} are the same joint")
    n = len(st.design.player_blocks)
    if rules.max_design_blocks is not None and n >= rules.max_design_blocks:
        return _err(ErrorCode.PIECE_LIMIT, f"design already has {n} blocks")
    (ax, ay), (bx, by) = _position(st, a), _position(st, b)
    dx, dy = bx - ax, by - ay
    length = math.sqrt(dx * dx + dy * dy)
    if length < rules.min_rod_length:
        return _err(ErrorCode.ROD_TOO_SHORT,
                    f"rod length {length:g} < {rules.min_rod_length:g}")
    tag, height, type_id = _ROD[kind]
    block = BlockXml(tag, {"rotation": fmt(math.atan2(dy, dx)),
                           "x": fmt(ax + dx / 2), "y": fmt(ay + dy / 2),
                           "width": fmt(length), "height": height}, False)
    design = replace(st.design, player_blocks=st.design.player_blocks + (block,))
    graph = copy_graph(st.graph)
    for slot, ep in enumerate((a, b)):
        ref = JointRef(n, slot)
        if isinstance(ep, JointRef):
            graph.stack_of(ep).att.append(ref)
        else:
            graph.stacks.append(Stack(float(ep[0]), float(ep[1]), None, [ref]))
    graph.slots.append(2)
    graph.types.append(type_id)
    graph.flags.append(0)
    info = dict(info, block=n, joints=[JointRef(n, 0), JointRef(n, 1)])
    return _commit(st, design, graph, {n}, old_index + [None], first_choice + [None], info)


def add_wheel(xml, spin, center, rules=Rules()):
    """Add a (40mm) wheel centred on `center`: a JointRef or an (x, y) point.
    info: block, joints."""
    st = _load(xml)
    bad = _check_ref(st, center)
    if bad:
        return bad
    n = len(st.design.player_blocks)
    if rules.max_design_blocks is not None and n >= rules.max_design_blocks:
        return _err(ErrorCode.PIECE_LIMIT, f"design already has {n} blocks")
    tag, type_id = _WHEEL[Spin(spin)]
    x, y = _position(st, center)
    block = BlockXml(tag, {"rotation": "0", "x": fmt(x), "y": fmt(y),
                           "width": "40", "height": "40"}, False)
    design = replace(st.design, player_blocks=st.design.player_blocks + (block,))
    graph = copy_graph(st.graph)
    ref = JointRef(n, 0)
    if isinstance(center, JointRef):
        graph.stack_of(center).att.append(ref)
    else:
        graph.stacks.append(Stack(x, y, None, [ref]))
    # circumference joints, generated by the wheel (positions don't matter
    # for the graph comparison)
    for slot in range(1, 5):
        graph.stacks.append(Stack(x, y, JointRef(n, slot), []))
    graph.slots.append(5)
    graph.types.append(type_id)
    graph.flags.append(0)
    info = {"block": n, "joints": [JointRef(n, s) for s in range(5)]}
    old_index = list(range(n)) + [None]
    return _commit(st, design, graph, {n}, old_index,
                   _identity_first_choice(st) + [None], info)


def _deleted(st, block):
    """Design, graph, index map and first-choice lists without `block`."""
    remap = {i: (i if i < block else i - 1) for i in range(len(st.graph.slots)) if i != block}
    mv = lambda r: JointRef(remap[r.block], r.slot)  # noqa: E731
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
    blocks = st.design.player_blocks
    design = replace(st.design, player_blocks=blocks[:block] + blocks[block + 1:])
    first = [[remap[j] for j in jt if j in remap]
             for i, jt in enumerate(st.orig_jointed) if i != block]
    old_index = [i for i in range(len(blocks)) if i != block]
    return design, graph, remap, first, old_index


def _check_editable(st, block):
    if not (0 <= block < len(st.graph.slots)):
        return _err(ErrorCode.BAD_REFERENCE, f"no block {block}")
    if st.graph.types[block] in GOAL_TYPES:
        return _err(ErrorCode.IMMUTABLE_BLOCK, f"block {block} is a goal piece", [block])
    return None


def delete(xml, block, rules=Rules()):
    """Delete player block `block`. Later blocks shift down by one.
    info: index_map {old index: new index}."""
    st = _load(xml)
    bad = _check_editable(st, block)
    if bad:
        return bad
    design, graph, remap, first, old_index = _deleted(st, block)
    return _commit(st, design, graph, set(), old_index, first, {"index_map": remap})


def rewire(xml, block, slot, target, rules=Rules()):
    """Reconnect end `slot` (0 or 1) of rod `block` to `target` (a JointRef),
    or detach it (target None, leaving a free joint where it was). The rod
    stretches to the new joint and moves to the end of the block order, as
    if deleted and re-added. info: block, joints, index_map."""
    st = _load(xml)
    bad = _check_editable(st, block) or _check_ref(st, target)
    if bad:
        return bad
    if st.graph.types[block] not in (fccore.WATER_ROD, fccore.SOLID_ROD):
        return _err(ErrorCode.WRONG_BLOCK_TYPE, f"block {block} is not a rod", [block])
    if slot not in (0, 1):
        return _err(ErrorCode.BAD_REFERENCE, f"rods have slots 0 and 1, not {slot}")
    if target is not None:
        tstack = st.graph.stack_of(target)
        if tstack is st.graph.stack_of(JointRef(block, slot)):
            # already connected there: nothing to do
            n = len(st.graph.slots)
            return Ok(xml if isinstance(xml, str) else xml.decode(),
                      {"block": block, "joints": [JointRef(block, 0), JointRef(block, 1)],
                       "index_map": {i: i for i in range(n)}})
        if tstack is st.graph.stack_of(JointRef(block, 1 - slot)):
            return _err(ErrorCode.SAME_JOINT,
                        f"{target} is the rod's other end", [block])
    kind = RodKind.WATER if st.graph.types[block] == fccore.WATER_ROD else RodKind.WOOD
    ends = []
    for s in (0, 1):
        stack = st.graph.stack_of(JointRef(block, s))
        others = [m for m in stack.members if m.block != block]
        if s == slot and target is not None:
            ends.append(target)
        elif s != slot and others:
            ends.append(others[0])  # stay connected to the same joint
        else:
            ends.append((stack.x, stack.y))
    design, graph, remap, first, old_index = _deleted(st, block)
    mapped = [JointRef(remap[e.block], e.slot) if isinstance(e, JointRef) else e
              for e in ends]
    # continue from the post-deletion state; legality still compares against
    # the pre-edit flags
    st2 = _State(design, replace(graph, flags=[st.graph.flags[i] for i in old_index]),
                 first)
    return _add_rod(st2, kind, mapped[0], mapped[1], rules, first,
                    list(range(len(old_index))), {"index_map": remap})


# ------------------------------------------------------------------ queries


def describe(xml):
    """Blocks and joints of a design, for inspection.

    blocks: index, type, goal, x, y, angle, joints (JointRefs, one per slot)
    joints: x, y, members (JointRefs; generator first, then chain order)
    """
    st = _load(xml)
    blocks = []
    for i, b in enumerate(st.design.player_blocks):
        blocks.append({
            "index": i, "type": b.tag, "goal": b.goal,
            "x": b.num("x"), "y": b.num("y"), "angle": b.num("rotation"),
            "width": b.num("width"),
            "joints": [JointRef(i, s) for s in range(st.graph.slots[i])],
        })
    joints = [{"x": s.x, "y": s.y, "members": s.members} for s in st.graph.stacks]
    return {"blocks": blocks, "joints": joints, "flags": list(st.graph.flags)}

"""The agent-facing tools: JSON schemas, argument parsing, and dispatch into a
Session. Results are tool-result content blocks (text, and PNG images as
base64), as Claude's API takes them.

Argument conventions:
- a joint is a string "block:slot", e.g. "3:1" (as labelled in images);
- a point is [x, y];
- a relative point is {"from": "3:1", "offset": [dx, dy]} or
  {"from": "3:1", "length": 60, "angle": 45} (degrees; 0 = +x, 90 = down);
- in build, "@i:s" is joint s of the batch's i-th piece.
"""

import base64
import re

from .design import JointRef
from .edit import NewRef, Offset, Polar, Rod, Wheel

_JOINT = {"type": "string", "description": 'A joint, "block:slot", e.g. "3:1".'}
_POINT_OR_JOINT = {
    "anyOf": [{"type": "string"},
              {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
              {"type": "object"}],
    "description": ('A joint name string ("3:1") to connect to, or a free point: [x, y], '
                    '{"from": joint, "offset": [dx, dy]} or '
                    '{"from": joint, "length": L, "angle": degrees}.'),
}
_REGION = {
    "description": ('"build" (the build area and its surroundings), "level" (all of '
                    'it), or [x0, y0, x1, y1].'),
}
_RUN_REGION = {
    "description": ('"level" (all of it), "action" (where the pieces go in that time, '
                    'plus the goal area), or [x0, y0, x1, y1].'),
}


def _tool(name, description, props=None, required=()):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {},
                             "required": list(required)}}


TOOLS = [
    _tool("add_rod", "Add a rod (or `count` identical rods) from a to b.",
          {"kind": {"enum": ["wood", "water"]}, "a": _POINT_OR_JOINT, "b": _POINT_OR_JOINT,
           "count": {"type": "integer", "minimum": 1}}, ["kind", "a", "b"]),
    _tool("build", "Add several rods and wheels in one call (all or none). Joints can "
          'also be "@i:s": joint s of the i-th piece in this call (rod ends 0-1; wheel '
          "centre 0, rim 1-4).",
          {"pieces": {"type": "array", "items": {"anyOf": [
              {"type": "object", "properties": {"rod": {"enum": ["wood", "water"]},
                                                "a": _POINT_OR_JOINT, "b": _POINT_OR_JOINT},
               "required": ["rod", "a", "b"]},
              {"type": "object", "properties": {"wheel": {"enum": ["none", "cw", "ccw"]},
                                                "center": _POINT_OR_JOINT},
               "required": ["wheel", "center"]}]}}}, ["pieces"]),
    _tool("add_wheel", "Add a wheel centred on a joint or a point.",
          {"spin": {"enum": ["none", "cw", "ccw"]}, "center": _POINT_OR_JOINT},
          ["spin", "center"]),
    _tool("delete", "Delete a piece.", {"block": {"type": "integer"}}, ["block"]),
    _tool("rewire", "Connect end 0 or 1 of a rod to another joint (the rod stretches), "
          "or detach it (to: null).",
          {"rod": {"type": "integer"}, "end": {"enum": [0, 1]},
           "to": {"description": "A joint, or null.", "type": ["string", "null"]}},
          ["rod", "end", "to"]),
    _tool("move_joint", "Move a joint. Wheels and goal pieces on it move with it; rods "
          "stretch.", {"joint": _JOINT, "to": _POINT_OR_JOINT}, ["joint", "to"]),
    _tool("move_component", "Move a piece and everything connected to it.",
          {"block": {"type": "integer"}, "dx": {"type": "number"}, "dy": {"type": "number"}},
          ["block", "dx", "dy"]),
    _tool("undo", "Undo the last change."),
    _tool("redo", "Redo the last undone change."),
    _tool("save", "Save the current design under a name.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("load", "Load a saved design (undoable).", {"name": {"type": "string"}}, ["name"]),
    _tool("list_saves", "List saved designs."),
    _tool("describe", "The design as text: pieces grouped by connected component, "
          "their joints, and the level."),
    _tool("view", "An image of the design, with joints labelled.", {"region": _REGION}),
    _tool("run", "Run the design, up to `timeout` ticks (30 per second); stops early "
          "if solved.", {"timeout": {"type": "integer", "minimum": 1}}),
    _tool("run_frame", "An image of the latest run at a tick; motion adds arrows for "
          "the next 6 ticks' movement.",
          {"tick": {"type": "integer"}, "region": _RUN_REGION, "motion": {"type": "boolean"}},
          ["tick"]),
    _tool("run_trails", "The latest run's goal-piece paths between two ticks.",
          {"start": {"type": "integer"}, "end": {"type": "integer"}, "region": _RUN_REGION}),
    _tool("run_filmstrip", "Frames of the latest run every `stride` ticks, 9 per image.",
          {"start": {"type": "integer"}, "end": {"type": "integer"},
           "stride": {"type": "integer", "minimum": 1}, "region": _RUN_REGION,
           "motion": {"type": "boolean"}}),
    _tool("help", "Reference on a topic, or the list of topics.",
          {"topic": {"type": "string"}}),
    _tool("submit", "Submit the current design as final. Ends the session."),
]

from .docs import HELP  # noqa: E402  (the on-demand reference)


def _not_a_number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        raise ValueError(f'joint names are strings like "3:1", not numbers ({v})')


def _joint(s):
    _not_a_number(s)
    m = re.fullmatch(r"\s*(@?)(\d+):(\d+)\s*", str(s))
    if not m:
        raise ValueError(f"{s!r} is not a joint like \"3:1\"")
    cls = NewRef if m.group(1) else JointRef
    return cls(int(m.group(2)), int(m.group(3)))


def _endpoint(v):
    _not_a_number(v)
    if isinstance(v, str):
        return _joint(v)
    if isinstance(v, dict):
        base = _endpoint(v["from"])
        if "offset" in v:
            dx, dy = v["offset"]
            return Offset(base, float(dx), float(dy))
        return Polar(base, float(v["length"]), float(v["angle"]))
    x, y = v
    return (float(x), float(y))


def _piece(p):
    if "rod" in p:
        return Rod(p["rod"], _endpoint(p["a"]), _endpoint(p["b"]))
    if "wheel" in p:
        return Wheel(p["wheel"], _endpoint(p["center"]))
    raise ValueError(f'{p!r} is neither a rod ({{"rod": ...}}) nor a wheel ({{"wheel": ...}})')


def _region(v, default):
    if v is None:
        return default
    return v if isinstance(v, str) else tuple(float(c) for c in v)


def dispatch(session, name, args, **meta):
    """Run tool `name` with JSON `args` on `session`: (ok, content blocks).
    `meta` goes into the call's log entry."""
    args = dict(args or {})
    s = session
    fns = {
        "add_rod": lambda: s.build([Rod(args["kind"], _endpoint(args["a"]),
                                        _endpoint(args["b"]))] * int(args.get("count", 1))),
        "build": lambda: s.build([_piece(p) for p in args["pieces"]]),
        "add_wheel": lambda: s.build([Wheel(args["spin"], _endpoint(args["center"]))]),
        "delete": lambda: s.delete(int(args["block"])),
        "rewire": lambda: s.rewire(int(args["rod"]), int(args["end"]),
                                   None if args.get("to") is None else _joint(args["to"])),
        "move_joint": lambda: s.move_joint(_joint(args["joint"]), _endpoint(args["to"])),
        "move_component": lambda: s.move_component(int(args["block"]), float(args["dx"]),
                                                   float(args["dy"])),
        "undo": s.undo,
        "redo": s.redo,
        "save": lambda: s.save(str(args["name"])),
        "load": lambda: s.load(str(args["name"])),
        "list_saves": s.list_checkpoints,
        "describe": s.describe,
        "view": lambda: s.view(_region(args.get("region"), "build")),
        "run": lambda: s.run(int(args.get("timeout", 3000))),
        "run_frame": lambda: s.run_frame(int(args["tick"]), _region(args.get("region"), "level"),
                                         bool(args.get("motion", False))),
        "run_trails": lambda: s.run_trails(int(args.get("start", 0)), args.get("end"),
                                           _region(args.get("region"), "level")),
        "run_filmstrip": lambda: s.run_filmstrip(int(args.get("start", 0)), args.get("end"),
                                                 args.get("stride"),
                                                 _region(args.get("region"), "level"),
                                                 bool(args.get("motion", False))),
        "help": lambda: _help(args.get("topic")),
        "submit": s.submit,
    }
    from .session import Result
    # every call is logged and counted, unknown tools included
    res = s.call(name, args, fns.get(name, lambda: Result(False, f"Unknown tool {name!r}.")),
                 **meta)
    blocks = [{"type": "text", "text": res.text}]
    for png in res.images:
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(png).decode()}})
    return res.ok, blocks


def _help(topic):
    from .session import Result
    if topic in HELP:
        return Result(True, HELP[topic])
    return Result(topic is None, "Topics: " + ", ".join(HELP))

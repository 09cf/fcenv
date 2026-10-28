"""The task prompt and the on-demand reference (the help tool)."""

from . import edit, view
from .design import parse


def task_prompt(session, level=None, levels=None):
    """The prompt for an agent starting a session on its level: level
    `level` of `levels` when playing a series."""
    d = parse(session.versions[0])
    goals = [b for b in edit.describe(session.versions[0])["blocks"] if b["goal"]]

    def area(z):
        v = {k: float(s) for k, s in z.values.items()}
        return (f"x {view._n(v['x'] - v['width'] / 2)} to {view._n(v['x'] + v['width'] / 2)}, "
                f"y {view._n(v['y'] - v['height'] / 2)} to {view._n(v['y'] + v['height'] / 2)}")

    limit = (f" You have {session.max_tool_calls} tool calls."
             if session.max_tool_calls is not None else "")
    budget = ""
    if session.budget is not None and session.budget.shown:
        budget = """\
This level has a budget for your work, spent by thinking, writing and
reading. Each tool result ends with how much of it is used. If
it runs out, the level ends and your design is evaluated as it is.

"""
    nb = session.notebook
    notes = ""
    if nb is not None:
        which = f" (this is level {level} of {levels})" if level and levels else ""
        free = (" It doesn't count towards your tool calls."
                if session.max_tool_calls is not None else "")
        notes = f"""
You're playing a series of levels{which}. Each level starts a fresh
conversation, and only your notebook carries over: it's below, and
write_notes replaces it (at most {nb.cap} characters). write_notes works at
any time, even after submit.{free} You go on to the next level only by
beating this one. Once you have, you can update your notes.

Your notes so far:
<notes>
{nb.text.strip() or "(empty)"}
</notes>
"""
    return f"""\
This is an experimental evaluation. You are playing Fantastic Contraption, a
physics puzzle game, through tools.

Your goal is to beat the level: build a machine that, once running, gets
every goal piece (the red pieces) completely inside the goal area. You build
with rods and wheels inside the build area; when the design runs, gravity
and the wheels' motors take over and you can't intervene.

This level:
- build area: {area(d.start)}
- goal area: {area(d.end)}
- goal pieces: {len(goals)} ({", ".join(f"#{b['id']}" for b in goals)})

Coordinates: +x is right and +y is DOWN; angles are in degrees, clockwise.
Joints are named "block:slot", e.g. "3:1", as labelled in images.

Start with describe and view to see the level. Build with the build tools,
check your machine with run and the run images, and adjust. Save designs you
may want to return to. When your design beats the level, call submit: it's
run once more, for up to {session.submit_timeout // 30} seconds, and if all
goal pieces are inside the goal area at the same moment during that run, the
level is beaten and the session ends. submit only accepts a design that
beats the level: if it doesn't, submit says so and you carry on.{limit}

{budget}The help tool has details on: {", ".join(HELP)}.
{notes}"""


HELP = {
    "pieces": """\
Pieces you can build:
- Wood rod: solid, 8 thick. Collides with everything except water rods.
- Water rod: 4 thick. Collides only with the level's terrain and blocks;
  passes through all other pieces, including other water rods and goal
  pieces. Useful as a frame that doesn't get in the way.
- Wheel: 40 across. "cw" wheels turn clockwise and "ccw" counter-clockwise,
  at up to about 0.8 turns per second; "none" wheels spin
  freely. A wheel's motor drives it relative to whatever its centre is
  joined to, so a powered wheel on its own does nothing.

Goal pieces are red rectangles and circles that must end up in
the goal area. You can connect to their joints and move them, but not
delete them. As with all pieces, only ones inside the build area can be
picked up, and they can't be moved out of it.

Level pieces: green is fixed terrain. Orange and yellow pieces are movable
parts of the level: they fall, roll and get pushed.

Gravity pulls down (+y) at 300 units/s². There are 30 ticks per second.""",

    "joints": """\
Joints are named "block:slot":
- rods: slot 0 and 1 are the two ends;
- wheels: 0 is the centre; 1-4 are on the rim (right, below, left, above,
  turning with the wheel); wheels wider than 40 (some goal circles) also
  have 5-8, 20 from the centre;
- goal rectangles: 0 is the centre, 1-4 are the corners.
When several pieces share a joint, any of their names refers to it; images
and describe show it under the lowest block number.

Connecting: give a joint name wherever a point is expected (add_rod,
add_wheel, build, rewire) to attach there. A point ([x, y] or relative) makes a new,
unattached joint, even if it lands exactly on an existing one.

Joined pieces pivot freely around the joint and never collide with each
other. A joint breaks if the pieces pull it about 50 units apart during a
run.""",

    "building": """\
Rules (a build call that breaks one is rejected and changes nothing):
- every piece you build must lie entirely inside the build area, including
  its thickness (a 2-unit tolerance);
- pieces can't overlap each other or the level (water rods excepted, as for
  collisions), unless they share a joint;
- two pieces can share at most one joint (so a rod can't join two joints of
  the same piece);
- rods must be at least 1 long; at most 300 pieces in total, goal pieces
  included;
- not too many pieces piled in one spot (e.g. a few hundred rods on one
  joint): the physics engine can only track so many overlapping pieces. A
  run where pieces pile up past that limit stops there, unsolved, as the
  original game breaks.

Positions: [x, y]; or relative to a joint: {"from": "3:1", "offset": [dx, dy]},
or {"from": "3:1", "length": L, "angle": A} (A in degrees: 0 right, 90 down).
Relative points never attach to their "from" joint.

build adds any number of rods and wheels in one call, all or none, so a
whole machine can go in at once. In it, "@i:s" names joint s of the i-th
piece of the same call (counting from 0; rod ends 0-1, wheel centre 0 and
rim 1-4). A rod with two wheels:
  {"pieces": [{"rod": "wood", "a": [0, 100], "b": [80, 100]},
              {"wheel": "cw", "center": "@0:0"},
              {"wheel": "cw", "center": "@0:1"}]}
add_rod with count builds identical copies (e.g. weights hanging from one
joint).

move_joint moves one joint: wheels and goal pieces on it move with it, rods
stretch. move_component moves a piece and everything connected to it.
rewire reconnects one end of a rod; the rod then counts as newest.
Nothing connects or disconnects when you move things. You can only pick up
a joint or piece inside the build area, and a move can't make anything
overlap or leave the build area.""",

    "winning": """\
The level is beaten at the first moment every goal piece is completely inside
the goal area at the same time. Afterwards it stays beaten, whatever happens.

"Completely inside": a goal circle's edge must be inside the goal area. A
goal rectangle must be 2 units clear of every edge of the goal area (more
when it's rotated, since its corners then stick out further).""",

    "running": """\
run plays your design (default up to 3000 ticks, 100 s) and summarises what
happened: whether it was solved and when, where each goal piece started and
ended, and for unsolved runs how close each got. It stops early once solved,
or if a goal piece leaves the world for good. Only the latest run is kept.

Images of the latest run:
- run_frame(tick): the scene at one tick. motion=true adds arrows showing
  where points on each moving piece go over the next 6 ticks (arrows with a
  double head went further than drawn). Pieces at rest get no arrows.
- run_trails(start, end): the paths the goal pieces took between two ticks.
- run_filmstrip(start, end, stride): frames every `stride` ticks, 9 per image.
region: "level" (everything), "action" (just where the pieces go, plus the
goal area), or [x0, y0, x1, y1].""",

    "images": """\
Colours: sky blue background; the build area is light blue, the goal area
pink. Green is fixed terrain; orange and yellow are movable level pieces;
red is goal pieces. Wood rods are brown, water rods blue with a white edge.
Wheels: blue (unpowered), yellow (cw), pink (ccw). Grey rings are joints.
Pieces drawn in bright red break a build rule.

view labels every joint with its name. Where labels would overlap, a group
is left unlabelled and circled, and the text with the image lists the names
there and a region to view to see them. Joints at the same point share one
label ("3:1/4:1/5:1"). Wheels only label their centre, plus any rim joint
with something attached. Grid values are along the edges.""",

    "saves": """\
undo and redo step through your changes. save stores the current design
under a name; load brings one back (and can itself be undone); list_saves
lists them. load("start") goes back to the level as given, to start over.
submit ends the session, once your design beats the level (it doesn't
accept one that doesn't).""",
}

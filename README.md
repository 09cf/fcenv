# fcenv

An environment for playing Fantastic Contraption through an API.

- `core/`: fccore, headless physics extracted from fcsim, with a C API and
  ctypes bindings. See `core/README.md`.
- `fcenv/`: the Python layer agents use to build designs.
- `tests/`: tests for `fcenv/` (see [Tests](#tests)).

## Editing designs

The XML is the only state. Edits are pure functions in `fcenv.edit`, each
taking XML plus an operation and returning `Ok(xml, info)` or
`Err(EditError)`:

```python
from fcenv import edit, view
from fcenv.design import JointRef
from fcenv.edit import Offset, Polar

r = edit.add_rod(xml, "wood", JointRef(0, 1), Offset(JointRef(0, 1), 60, 0))
r = edit.add_wheel(r.xml, "cw", JointRef(r.info["block"], 1))
r = edit.add_rod(r.xml, "water", JointRef(2, 1), Polar(JointRef(2, 1), 50, 90))
print(view.summary(r.xml))   # pieces grouped by component, joints by name
edit.describe(r.xml)         # the same as data
```

Operations:

- `add_rod(xml, kind, a, b, count=1)`: `count` > 1 adds identical rods, e.g.
  as weights (rods sharing a joint end all attach to it, and free ends get
  a joint each, coincident but unconnected).
- `add_rods(xml, [(kind, a, b), ...])`: a batch, all or nothing. Endpoints
  can also be `NewRef(i, slot)`, a joint of the i-th rod earlier in the
  batch.
- `add_wheel(xml, spin, center)`.
- `delete(xml, block)`.
- `rewire(xml, rod, end, target)`.
- `move_joint(xml, joint, to)`: as in fcsim's editor, non-rod blocks with a
  joint in the moved set (wheels, goal pieces) move rigidly, and rods
  stretch; nothing connects or disconnects.
- `move_component(xml, block, dx, dy)`: translate everything connected to
  `block`, rods included, so nothing stretches.

- **Connections are explicit.** An endpoint is a `JointRef` (connect to that
  joint) or an `(x, y)` point (a new free joint there). Nothing snaps.
- **Relative points.** `Offset(base, dx, dy)` and `Polar(base, length,
  angle)` give a point relative to a joint (or a `NewRef`, or a point).
  Angles are degrees, 0 = +x; +y is down, so 90 points straight down. They
  are free points: they don't connect to their base.
- **Blocks have stable IDs**, stored as the XML id attribute. A new block
  gets the next unused ID, and IDs never change or get reused.
  `JointRef(block ID, slot)` names a joint stack through any of its members;
  `view.summary` shows each joint under its lowest-ID member's name. Slots use
  fcsim's canonical order: rods 0/1; wheels 0 centre, 1-4 circumference, 5-8
  inner (radius > 20); goal rects 0 centre, 1-4 corners.
- **Block order is XML order**, which affects the physics. `rewire` keeps the
  rod's ID but moves it to the end of the order, as re-adding it would.
- **States the original editor prohibits are rejected:**
  - a joint holding two wheels (reported as an overlap: jointed blocks never
    collide, so the overlap check can't see it, but that's what it is);
  - two blocks sharing more than one joint, including a rod across two joints
    of one block (a goal rect, a wheel or a goal circle);
  - a block attached twice to one joint;
  - player blocks overlapping, or leaving the build area. This uses fccore's
    check; edits must not add violations, which keeps the game's leniency for
    designs that start illegal;
  - deleting goal pieces;
  - rods shorter than `Rules.min_rod_length` (default 1), or more design
    blocks than `Rules.max_design_blocks` (default 300, fcsim's editor cap;
    goal pieces count).

Every edit re-imports its result with fccore and checks the joint graph came
back exactly as intended. The importer rebuilds joints from `<jointedTo>`
block lists plus proximity, so this catches anything the XML can't express
(`UNREPRESENTABLE`). Untouched blocks keep their exact value strings; all 837
dataset designs re-serialize with identical physics.

No physics is stepped during edits.

## Viewing designs

- `view.summary(xml)`: text, with pieces grouped by connected component,
  every joint under the name the API accepts, and exact level geometry.
- `render.render(xml, view)`: a PNG in fcsim's colours, with grid lines and
  joint labels. `view` is `"build"` (the build area plus a margin of its
  surroundings), `"level"` (everything), or an `(x0, y0, x1, y1)` region.
  It returns a `Render` with `.png`, `.view`, `.hidden` and `.notes()`:
  - where labels would collide, the whole group is left unlabelled and
    circled on the image;
  - `.notes()` lists each unlabelled group's joint names and a region to
    render it at, which is exactly the `view` argument for a zoomed render;
  - joints at practically the same point share a label (`3.1/4.1/5.1`),
    since no zoom separates them;
  - wheels and goal circles only label their centre, plus any outer joint
    something is attached to.

Rendering needs `pycairo`.

## Runs

`run.simulate(xml, timeout=3000)` plays the design once and returns a
`Run`: every block's position and angle at every tick (float32, 12 bytes
per block per tick), kept as a static object to query and draw at any tick
(the physics is fast next to an agent's thinking).
A run stops early when it's solved, or when a goal piece leaves the physics
world, since Box2D freezes it there and it never returns.

- `run.summary()`: the outcome, each goal piece's start and end, and for
  unsolved runs its closest approach to the goal area. Joint breaks are
  listed only if there were any.
- `run.state(tick, block_id)`: (x, y, angle).
- Images show the whole level (`view="level"`, the default), or
  `view="action"`: where the pieces are in that time window, plus the goal
  area.
  - `run.frame(tick)`;
  - `run.trails(start=0, end=None, which="goal")`: goal-piece (or
    `"all"`) paths between two ticks, drawn on the frame at `end`;
  - `run.filmstrip(start=0, end=None, stride=… or ticks=[…], per_page=9)`:
    a list of pages of captioned frames.
- Every joint of every piece is drawn at every tick, wheels' unused
  circumference joints included.
- `motion=True` (on `frame` and `filmstrip`) adds arrows for how points on
  each moving piece move over the next `horizon` ticks (default 6). This is
  the actual recorded motion, not a projection from velocities.
  - Samples: rod ends and about every 30px along rods; the centre and
    corners of goal rects and dynamic level rects, so rotation shows; the
    centre of wheels, goal circles and dynamic level circles.
  - Motion under 4px isn't drawn, so still parts show nothing.
  - Arrows are capped at 70px; capped ones get a double head.
  - Arrows too similar to one already kept (starting within 18px, pointing
    within 20°, similar length) are dropped in a random order, so every
    arrow drawn is still a real trace.


## Sessions and tools

`session.Session(level_xml)` is one play session. It keeps every design
version (undo/redo move between them), named saves, the latest run, and a
log entry per tool call (`log_jsonl()`), for recreating the whole session.
`submit()` evaluates the design and ends the session. `max_tool_calls` caps
the number of calls.

`tools.TOOLS` are the agent-facing tool definitions (name, description,
JSON input schema). `tools.dispatch(session, name, args)` runs one and
returns `(ok, content)`, with content as Claude tool-result blocks: text,
plus PNG images for `view` and the run images.

- Joints are strings (`"3.1"`, as labelled in images); points are `[x, y]`;
  relative points are `{"from": "3.1", "offset": [dx, dy]}` or
  `{"from": "3.1", "length": L, "angle": deg}`; in `add_rods`, `"@i.s"` is
  end s of the batch's rod i.
- Build tools return one line (what changed, with the new joint names), not
  images; `describe` and `view` show the design.
- Tools: add_rod, add_rods, add_wheel, delete, rewire, move_joint,
  move_component, undo, redo, save, load, list_saves, describe, view, run,
  run_frame, run_trails, run_filmstrip, help, submit.

`docs.task_prompt(session)` is the prompt for an agent starting a session:
the task (an experimental eval; beat the level), the level's build area,
goal area and goal pieces, the conventions, and the help topics.
`docs.HELP` is the reference the `help` tool serves: pieces, joints,
building, winning, running, images, saves.

## Levels and saving

- `levels.fetch(link)` gets a level or design's XML from
  fantasticcontraption.com, given any link with `levelId=`/`designId=` (FC's
  or fcsim's) or an id like `L646726`. It's for setting up an environment;
  nothing else uses the network.
- `levels.to_save_design(xml, name, description)` gives FC's saveDesign
  format, which FC and fcsim export.
- `python3 -m fcenv.levels decomp/scripts/contraption/data levels` writes
  the game's built-in levels (47 official, 3 tutorials) from the decompiled
  SWF to `levels/{official,tutorial}/NN-name.xml` (the `<retrieveLevel>`
  element, as the server sends it), plus `levels/index.json` with each
  level's number, id, name, paid-only flag, goal pieces and pre-built
  pieces. Their terrain matches the server's copies in the cached designs.
  `levels/` is gitignored, like `decomp/`.

## Tests

`python3 -m unittest tests.test_edit tests.test_render tests.test_session`
(after `make -C
core`). Tests that use real designs need `core/tests/fetch_designs.py` run
once; they're skipped otherwise.

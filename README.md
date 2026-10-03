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
- `build(xml, [Rod(kind, a, b), Wheel(spin, center), ...])`: a batch of
  rods and wheels, all or nothing. Endpoints and centres can also be
  `NewRef(i, slot)`, a joint of the i-th piece earlier in the batch (a
  rod's 0-1, a wheel's 0-4, rim joints included). `add_rods(xml, [(kind, a,
  b), ...])` is the rods-only form.
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
  - joints at practically the same point share a label (`3:1/4:1/5:1`),
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
version (undo/redo move between them), named saves (`start`, the level as
given, is always there), the latest run, and a
log entry per tool call (`log_jsonl()`), for recreating the whole session.
`submit()` evaluates the design and ends the session. `max_tool_calls` caps
the number of calls.

`tools.TOOLS` are the agent-facing tool definitions (name, description,
JSON input schema). `tools.dispatch(session, name, args)` runs one and
returns `(ok, content)`, with content as Claude tool-result blocks: text,
plus PNG images for `view` and the run images.

- Joints are strings (`"3:1"`, as labelled in images); points are `[x, y]`;
  relative points are `{"from": "3:1", "offset": [dx, dy]}` or
  `{"from": "3:1", "length": L, "angle": deg}`; in `build`, `"@i:s"` is
  joint s of the batch's piece i.
- `build` takes a list of pieces, `{"rod": "wood", "a": ..., "b": ...}` or
  `{"wheel": "cw", "center": ...}`, so a whole machine can go in with one
  call.
- `delete` takes a list of pieces (all or none), and `load("start")` resets:
  both were one call per piece before, and an agent spent 36 calls
  clearing designs.
- A rejected edit lists every problem it would cause, with where the pieces
  are ("3 overlaps: piece 1 of the batch (cw wheel at (10, 230), radius 20)
  would overlap piece 14 of the batch (wood rod (120, 170)-(-40, 230)); ..."),
  and says that nothing changed. A run of the same design as the last run
  says so (and whether a change was rejected in between).
- Build tools return one line (what changed, with the new joint names), not
  images; `describe` and `view` show the design.
- Tools: add_rod, add_wheel, build, delete, rewire, move_joint,
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

## Agent harness

`fcenv/harness.py` runs Claude on levels through the Messages API (needs
`pip install anthropic`, plus an API key or an `ant auth login` profile):

```
python3 -m fcenv.harness LEVEL.xml [LEVEL.xml ...] --out runs/try1
    [--model claude-opus-5] [--effort high] [--max-tool-calls N]
    [--max-turns 300] [--notes | --notes-from NOTES.md] [--notes-cap 15000]
    [--budget DOLLARS] [--level-budget 3] [--hide-budget]
python3 -m fcenv.harness LEVEL.xml --out x --print-prompt   # no API calls
```

- Levels are played in order, each as a fresh conversation, into
  `OUT/<level name>/`; one level is a series of one.
- The task prompt is the first user message; the tools are `tools.TOOLS`.
  Each turn's tool calls are dispatched in order and returned together.
  Thinking is adaptive (with summaries kept in the transcript).
- The history is append-only, images included, with automatic prompt
  caching. At about 1,400 tokens per image, even a 200-call level fits
  easily in the context.
- Tool calls are counted (`tool_calls` in `result.json`), not limited:
  there's no limit unless `--max-tool-calls` sets one, and then the prompt
  tells the agent. Cost is what `--budget` limits; `--max-turns` is a
  backstop against runaway loops.
- If the agent replies without calling a tool, it's told no one is there
  and to carry on; if its reply was cut off at `max_tokens`, it's told that
  instead (twice in all, then the level ends). Turns longer than 30 s log
  progress.
- A level ends on submit, the tool-call limit, the turn limit, a refusal
  or the budget. If the agent didn't submit, the current design is
  evaluated anyway; `submitted_by_agent` in `result.json` records which.
- **Notes** (off unless `--notes` or `--notes-from`): a notebook
  (`session.Notebook`, capped at `--notes-cap` characters) carries over
  between levels. It's in each level's prompt, and `write_notes` replaces
  it; writing notes is free (no tool call used) and works after submit.
  After each level the agent is told the outcome and gets up to 4 turns to
  update its notes (a reply without a write_notes call gets one reminder
  that the notes are unchanged). `--notes-from` starts from earlier notes, e.g. to try
  one level with what a series learned.
- **Cost**: each level's `result.json` has an estimated cost (from
  `harness.PRICES`), and `--budget` stops the run once the estimate passes
  it: mid-level, and before starting the next level.
- **Level budget** (`--level-budget`, $3 by default): each level's
  allowance, which the agent sees. The prompt explains it (including that
  later turns cost more, as each re-reads the conversation), and every tool
  result ends with `[Budget: 34% of this level's used]`; from 75% it adds
  about how many turns are left at the current rate, and from 90% what
  happens at the end. When it runs out the level ends (stop
  `level_budget`; the design is evaluated, the notes phase still runs and
  the series goes on). `--hide-budget` enforces it without showing it, for
  comparisons. As in other agent evals (METR's scaffolds, Google's Budget
  Tracker, Anthropic's task budgets), the remaining budget is shown and
  updated every step, and set generously.
- Output per level, written as it goes:
  - `result.json`: stop reason, solved and when, counts, token usage, cost;
  - `transcript.md`: the readable version: turn by turn, the agent's
    thinking (folded) and words, its tool calls written compactly and
    their results, with images inline and messages from the harness
    marked. `python3 -m fcenv.readable DIR` (re)makes it for a level or a
    whole series;
  - `transcript.jsonl`: every message, timestamped, with images saved under
    `images/`;
  - `tools.jsonl`: one entry per tool call (tool, arguments, result text,
    time, design version before and after, and the turn and `tool_use_id`
    that link it to the transcript);
  - `versions.jsonl`: the XML of every design version. With `tools.jsonl`
    it's the whole build process step by step (e.g. for rendering it as a
    video), and runs can be recomputed from the version they ran on;
  - the final design as `final.xml`, `final_save.xml`, `final_view.png`
    and `final_trails.png`; `notes.md`, the notebook after the level.
- And for the series: `summary.json` (every level's result, solved count,
  total cost) and `notes.md` (the latest notes).

### Through Claude Code

`fcenv/cc.py` plays the same way with Claude Code (`claude -p`) running
the loop, e.g. on a Claude subscription instead of API credits (needs
`pip install mcp` and Claude Code logged in):

```
python3 -m fcenv.cc LEVEL.xml [LEVEL.xml ...] --out runs/cc1 [--model M]
    [--effort E] [--level-budget 3] [--hide-budget] [--max-minutes 30]
    [--max-usage 0.8] [--max-level-usage 0.3]
    [--notes | --notes-from NOTES.md]
```

- The tools are served over MCP from the driver's process
  (`toolserver.ToolServer`, streamable HTTP on localhost), so a level's
  session survives across its claude runs: the first, then resumed runs
  for nudges and the notes phase.
- Claude Code gets our prompt with an empty system prompt, no built-in
  tools, no settings files and an empty working directory, so it sees only
  our tools. It runs on the plan's default model unless `--model` says
  otherwise; `model` in `result.json` records which.
- The level budget works as with the API, in list-price dollars computed
  from the same token counts (Claude Code reports each message's usage),
  so the agent sees the same thing either way; `list_price_cost` in
  `result.json` is what the level would have cost on the API.
- Hidden backstops: wall clock per level (`--max-minutes`, plus
  `--notes-minutes` for the notes phase), and plan usage, which Claude Code
  reports (how full the 5-hour and 7-day windows are). A level stops once
  it has used `--max-level-usage` of either window itself (stop
  `level_usage`; its notes phase still runs and the series goes on), and
  everything stops once either window passes `--max-usage`. `plan_usage` in `result.json` has
  each window before and after the level. A long level costs far more than
  its time suggests (each turn re-reads the growing context), so the usage
  limits are the ones that matter; wall clock is a backstop.
- Output is the same as the API harness's (tool calls are linked to the
  transcript by tool and arguments), plus `cc/stream.jsonl`, Claude Code's
  events with images left out.

`python3 -m fcenv.video runs/try1/LEVEL` makes `episode.mp4` there from the
record alone (needs ffmpeg): each design change in the build view, rejected
calls in red, the images the agent looked at, and its runs and the
submission re-simulated and played in real time (sped up past `--max-run`
seconds). The call and its result are captioned on top, and the agent's
words for that turn at the bottom.

## Tests

`python3 -m unittest tests.test_edit tests.test_render tests.test_session
tests.test_harness tests.test_video tests.test_cc` (after `make -C core`; the harness tests use a scripted
client, not the API, and `tests/fake_claude.py` stands in for Claude Code). Tests that use real designs need `core/tests/fetch_designs.py` run
once; they're skipped otherwise.

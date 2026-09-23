# fcenv

An environment for playing Fantastic Contraption through an API.

- `core/`: fccore, headless physics extracted from fcsim, with a C API and
  ctypes bindings. See `core/README.md`.
- `fcenv/`: the Python layer agents use to build designs.
- `tests/`: tests for `fcenv/` (`python3 -m unittest discover tests`; needs
  `make -C core` first).

## Editing designs

The XML is the only state. Edits are pure functions in `fcenv.edit`, each
taking XML plus an operation and returning `Ok(xml, info)` or
`Err(EditError)`:

```python
from fcenv import edit
from fcenv.design import JointRef

r = edit.add_rod(xml, "wood", JointRef(0, 1), (80, -20))   # goal rect corner -> free point
r = edit.add_wheel(r.xml, "cw", JointRef(r.info["block"], 1))
edit.describe(r.xml)   # blocks, joints and their members
```

Operations: `add_rod(xml, kind, a, b)`, `add_wheel(xml, spin, center)`,
`delete(xml, block)`, `rewire(xml, rod, end, target)`.

- **Connections are explicit.** An endpoint is a `JointRef` (connect to that
  joint) or an `(x, y)` point (a new free joint there). Nothing snaps.
- **`JointRef(block, slot)`** names a joint stack through any of its
  members. Slots use fcsim's canonical order: rods 0/1; wheels 0 centre, 1-4
  circumference, 5-8 inner (radius > 20); goal rects 0 centre, 1-4 corners.
- **Block indices are XML order.** `delete` shifts later blocks down, and
  `rewire` moves the rod to the end, as re-adding it would (block order
  affects the physics). `info["index_map"]` maps old indices to new.
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

Planned: `move_joint`; bulk placement (e.g. many rods as weights in one call).

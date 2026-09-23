# fccore

Headless Fantastic Contraption physics, extracted from
[fcsim](https://github.com/evenifyouforget/fcsim) at commit `60fe54d`, behind
a small C API (`include/fccore.h`) with ctypes bindings (`python/fccore.py`).
No UI, GL, editor state machine or globals.

By default fccore follows the original game where fcsim differs from it:
the [win check](#win-check) and [wheel joints on XML import](#xml-import).
This raises agreement with real FC from 77.3% (fcsim) to **97.8%** of the
tracked designs (see [Agreement](#agreement-with-the-original-game)).

In fcsim-compatible mode (`FC_OPT_FCSIM`, `fc_run --fcsim`) it matches fcsim
**bit-for-bit**: every body's position, velocity, angle and angular velocity
on every tick, across all 837 designs in ftlib's dataset.

## Build and test

```sh
make                          # build/libfccore.{a,so}, build/fc_run
make reference                # build fcsim's run_single_design_xml from ../fcsim
make fetch                    # download the 837 test designs into tests/data/cache
make test                     # API tests + bit-exact comparison against fcsim
make agreement                # report agreement with the original Flash game
make BUILD=build/fpatan FPATAN=1  # variant using x87 fpatan for atan2 (x86 only)
```

`build/fc_run [max_ticks] [--trace] [--fcsim] < design.xml` prints the solve
tick (or -1) and the final tick. `--trace` writes per-tick state in fcsim's
CLI log format. With `--fcsim` it's a drop-in replacement for fcsim's
`run_single_design_xml`.

```python
import fccore
with fccore.Sim(xml) as sim:
    sim.check_legal()        # Legality(legal=True, flags=[...])
    sim.run(3000)            # solve tick or None
    sim.state(); sim.joints()
```

## What was taken from fcsim

| Vendored | Purpose |
|---|---|
| `src/box2d`, `include/box2d` | fcsim's modified old Box2D |
| `src/fpmath/{sincos,atan2,strtod}.c` | deterministic trig and number parsing (`794-stable`: no `fpatan.s`) |
| `src/graph.{c,h}` | design model, materials, XML → design (joint snapping) |
| `src/gen.{c,h}` | world building, collision filter, motors, `step()` including joint breaking |
| `src/xml.{c,h}`, `src/export.c`, `src/str.*` | XML in/out |

Changes to vendored files:
- `graph.c`/`graph.h` drop `merge_design` and `clean_copy_design` (UI features
  that pulled in `arena.h`).
- `graph.c`/`graph.h`/`gen.c` add the original game's large-wheel inner joints (see
  [XML import](#xml-import)), gated by `design->fc_import_rules`. They also
  raise per-block joint arrays from 5 to `MAX_BLOCK_JOINTS` (9).

With `fc_import_rules` off, behaviour is fcsim's.

`src/fccore.c` is new. It holds:
- the API;
- the tick loop from `tick_func`;
- the original game's win check (below);
- fcsim's win check, copied verbatim from `arena.cpp` (`get_rect_bb` ...
  `goal_blocks_inside_goal_area`), available as `FC_WIN_FCSIM`;
- a legality check adapted from `mark_overlaps`.

## Win check

`FC_WIN_FLASH` (the default) reimplements `Player.hasWon()` from the FFDec
decompile (`../decomp/scripts/contraption/Player.as`). It works on Flash
display-list values, not physics values:

- Block and goal-area centres are `DisplayObject.x/y`: integer twips (1/20 px),
  truncated toward zero. The goal area's width and height stay exact.
- Goal circles use `x ± radius`.
- Goal rects use `DisplayObject.width/height - 4`: the axis-aligned bounds of
  the rotated foreground graphic. The anchor circles at the corners (radius 3,
  2px stroke) give local bounds of `±(w/2 + 4) × ±(h/2 + 4)`. So an unrotated
  goal rect must sit 2mm inside the goal area on every side.
- Rotations with |deg| ≥ 32768 act as -32768°, as in fcsim.

Each piece was checked against the dataset with `tests/wincheck_fit.py`,
which records goal-block trajectories once and scores alternative models
(twips rounding modes, padding, bounds algorithms, rotation handling).
Padding 4 is a sharp optimum: of the 825 tracked designs, 3.9 scores 801 and
4.1 scores 798, against 807.
Truncation beats rounding and no quantisation for circles, rects and the zone.

`FC_WIN_FCSIM` keeps fcsim's check.

## XML import

`convert_xml` follows the `Wheel`
constructor (`../decomp/scripts/contraption/blocks/`):

- **Inner joints.** A wheel with radius > 20 (diameter > 40; reachable
  in-game for goal circles via the level editor) gets 9 joints: centre, 4 on
  the circumference (rotated with the wheel), and 4 at `(x±20, y)`,
  `(x, y±20)` from the centre. The inner four are *not* rotated with the wheel.
  With a small rotation θ the circumference joints shift by about rθ while the
  inner ones stay put, so they look slightly offset when zoomed in. Joints
  are created in that order, which matches the order the game builds physics
  joints in (`Player.initJoints`). Rods snap to inner joints like any other
  joint, and blocks sharing one don't collide.

`FC_OPT_FCSIM_IMPORT` restores fcsim's 5-joint import.

## Things to know

- **Simulate the exact XML string you submit.** Body and joint creation
  order, and ulp-level rod perturbations, both affect the result. The API only
  builds worlds from XML. Never simulate fcsim's `export_design` output
  instead: it truncates numbers, and one round trip through it changes the
  solve tick of 13 out of 200 sampled real designs.
- **Symbol hygiene.** fcsim defines a global `step()`, which clashes with
  glibc's legacy `step()`: loaded as a shared library, the call resolved to
  glibc's and crashed. libfccore is built with `-fvisibility=hidden
  -Wl,-Bsymbolic` and only exports `fc_*`.
- **Floating point.** Built with `-ffp-contract=off -fno-fast-math`. Output is
  identical to fcsim's `-O3 -flto` build.
- **Header dependencies** are tracked (`-MMD`). An early build without them
  silently linked `export.o` against a stale `struct design` layout and
  crashed.
- **fcsim bug found:** `arena_init` leaves its `struct xml_level`
  uninitialised, so designs containing `<levelBlocks/>` crash the native
  CLI (45 of 837). fccore zero-initialises it. The reference test feeds fcsim
  `<levelBlocks></levelBlocks>` instead.
- **Legality** (`fc_sim_check_legal`) flags every design block that overlaps
  another block or leaves the build area (4mm tolerance). The design is legal
  if no non-goal block is flagged. This is stricter than fcsim's editor, which
  only checks the pieces being dragged.

## Agreement with the original game

Against ftlib's `fc_data.tsv` (solve ticks recorded in Flash FC), scored the
same way as ftlib's `test_single_design_fcsim`.

The target isn't the original game in every respect. fccore deliberately
doesn't model the game's reload on Go (see [Known gaps](#known-gaps)), so
the 12 designs that only agree when that reload is emulated are excluded from
tracking and listed in `tests/data/excluded.tsv`. That the reload experiment
didn't fix the remaining mismatches is weak evidence that those are genuine
targets. `fc_agreement.py` and `wincheck_fit.py` skip excluded designs; the
bit-exact fcsim comparison still covers all 837.

| Mode | Tracked designs agreeing |
|---|---|
| `FC_OPT_FCSIM` (fcsim) | 638/825 (77.3%) |
| `FC_OPT_FCSIM_IMPORT` (original-game win check, fcsim import) | 788/825 (95.5%) |
| default (+ original-game wheel import) | **807/825 (97.8%)** |

The 18 remaining mismatches:

| Count | Cause |
|---|---|
| 3 | "huge rotation" designs whose physics blows up (NaN/1e100 positions) |
| 5 | extreme goal-rect probes: 869°–49,000° rotations on rects up to 1000mm. They need an effective angle ~0.25° off, or no clamp; no single rule fits without breaking other levels |
| 2 | L688109/L688111: free-falling goal circle exactly one ulp outside the goal edge, which FC solves at ticks 181/54. Unexplained and time-dependent, so not a static check |
| 6 | D688035, 036, 037, 038, 042, 044 (consecutive IDs; not investigated) |
| 2 | D1483815 (off by 1 tick at tick 44712), D688257 |

## Known gaps

- **Reload on Go (deliberately not modelled, as in fcsim).** Pressing Go in
  the game re-exports the design and re-parses it (`Player.startSimulation`
  → `getLevelXml` → `loadFromXml`). Everything survives that exactly except
  rods: their angle, centre and width are recomputed from their endpoints, so
  endpoints come back perturbed by an ulp or so. That matters only in very
  chaotic designs. It accounts for 12 designs, including
  every water slinky and the "doesn't solve on second play" designs, since
  each play reloads again. They're excluded from tracking. fccore simulates the XML exactly as parsed,
  loaded once. That's deterministic and keeps the XML as the single source of
  truth, at the cost of rare desyncs from FC. A one-ulp change to the initial
  state flips the solve tick of even matching slinky designs, so
  fccore/fcsim physics is otherwise bit-exact with Flash on those runs.
- **Build-area legality.** FC's `checkPlayerBlocksLegality` (`Player.as`)
  deletes rods with an endpoint outside the build zone and colliding player
  blocks on load. This probably never matters for designs built in the editor,
  which enforces its constraints at build time. `fc_sim_check_legal` uses
  fcsim's bounding-box rule.
- **Import tie-breaking.** `BlockCollection.getClosestJointCollection` breaks
  exact distance ties by block order in the design. fcsim's
  `find_closest_joint` breaks them by `<jointedTo>` order. This only matters for
  hand-edited XML with coincident joints.

## Test data

`tests/data/fc_data.tsv` is from
[ftlib](https://github.com/evenifyouforget/ftlib) (`test/fc_data.tsv`, LGPL-2.1).
The designs themselves are fetched from fantasticcontraption.com and cached
locally (gitignored).

## License

fcsim is MIT, except `src/fpmath/atan2.c` (LGPL-2.1-or-later). See
`../fcsim/LICENSE.md` and `../fcsim/ATTRIBUTION.md`.

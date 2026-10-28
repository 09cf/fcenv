General tips (from L1-L10):
- Workflow: describe + view region "level" first. Build, run (timeout ~600-900), then submit as soon as run says "Solved". Submit re-runs once for up to 200 s.
- Win = all goal pieces fully inside goal area at the same moment; just passing through counts (no need to stop there).
- Coordinates: +y DOWN. cw wheels drive a car RIGHT, ccw LEFT. Wheels are 40 across (radius 20). Put centres at ground_y - 21 (ground_y - 20 gets rejected as overlapping).
- In 2D a wheel's spin direction does NOT flip when the car turns upside down: a ccw wheel always drives LEFT when it's the one touching the ground. So put cw wheels on the bottom and ccw wheels on the top, and the car reverses once it flips over.
- A cw wheel pressed against a wall on its right side CLIMBS the wall (friction pushes it up). This is why cars pitch up and flip backward at steep obstacles.
- Wheel joints: c:0 centre, c:1 right rim, c:2 bottom, c:3 left, c:4 top (rim joints turn with the wheel).
- The whole wheel (centre +/- 20) must be inside the build area, so wheel centre x <= build_xmax - 20. Wheels also can't overlap level scenery.
- Gravity 300 units/s^2, 30 ticks/s. Wheels spin about 0.8 turns/s, so a car moves roughly 100 units/s.
- Wheel motor torque is WEAK: a wheel can't swing a long arm (~150) with the goal piece on it upward (I tried this on L8 and it just drooped). Use cars instead.
- Simple car: 2 cw wheels ~70-100 apart (centres at the same y), a wood rod between the centres, and wood rods from both centres to the goal piece (triangle). Works well.
- WEIGHT PLACEMENT: put the goal piece (the heaviest part) at the FRONT of the car when climbing steep obstacles; weight at the back makes the car flip backward.
- Gaps in the ground: make the car longer than the gap, with 3+ wheels spaced wider than the gap, so 2 wheels always touch the ground.
- Check for overhead terrain (e.g. circles hanging down, pillars) and keep the WHOLE frame and goal piece below it. Watch the bottom edge of overhead static blocks.
- The build tool with "@i:s" refs (wheel centre = slot 0) builds the whole car in one call. Goal piece joints are like "0:0" (centre), rect corners 0:1..0:4 (0:1 top-right, 0:2 top-left, 0:3 bottom-right, 0:4 bottom-left).
- You CAN move the goal piece (move_joint "0:0" to [x,y]) within the build area before building. Useful for putting it high/forward.
- move_joint on a free node stretches the attached rods, which makes small adjustments easy (it gets rejected if a rod would then overlap a wheel; delete that rod first).
- Rods that cross each other or pass within 20 of a wheel centre get rejected (overlap). Fix: route through an extra free joint (e.g. [x, y] point above the wheel).
- To rebuild: delete all pieces except #0 (list the block numbers), move 0:0, build again.
- Pre-placed pieces in the level can just be ignored.
- If the build area floats above the ground, the car just drops onto the ground. Work out the goal piece's height relative to the wheels so it ends up at the right height after the drop.
- Physics with loose junk is chaotic: small changes give very different results. If a run nearly works, just try small variations.
- When a run fails, run_filmstrip (region "level", stride 15, end ~300) quickly shows where it got stuck.

Level log:
L1 "rolling away": the car carried the ball right over a hill into the goal. Solved at tick 130.
L2 "reach up": the goal block floated high (y 100), goal area also high (y<144.5), ground at y 246. I built a tall car directly under the block: wheels at (215,225) & (320,225) cw, rods wheelA-0:4, wheelB-0:3, wheelA-0:3, base rod. Solved at tick 76.
L3 "mind the gap": a ~100-wide gap (x 300-402). A long 3-wheel car (cw wheels at x -40, 100, 240, y 162, spacing 140 > gap), rods wheel-wheel and every wheel -> ball. Solved at tick 126.
L4 "junkyard": loose junk before the goal. A short car got stuck. What worked: a long 5-wheel car across the whole build area: cw wheels at x -122,-60,2,64 (y 225) plus a raised FRONT wheel at (114,185), chain rods, every wheel -> ball. Solved at tick 324.
L5 "the wall": a tall stack of loose blocks then a 157-wide gap. What worked: a big 2-row truss car, 8 cw wheels (bottom row y 222 at x -100,-20,60,130; top row y 160 at x -60,20,100; front-high wheel (130,120)), triangulated, goal piece at (90,95) on the top. Solved at tick 312.
L6 "on a roll": floating build area with a big dynamic ball; a 3-wheel cw car left of the ball dropped down and pushed the ball right, carrying the piece high. Solved at tick 222.
L7 "full up": a deep pit, a pillar overhead. A long low truss (cw wheels at x -544,-466,-280,-94, y 123, top nodes, piece at (-280,50)) bridged the pit. A piece placed too high jammed on the pillar. Solved at tick 188.
L8 "higher": the goal was directly above the build area. A tall car in a valley drove up a hill, which lifted the piece into the goal. 3 cw wheels at x -200,-120,-40 (y 205), piece at (-120,50). Solved at tick 137.
L9 "around the bend": the goal was under the platform the build area sits on, through a 190-wide gap. A compact flip-over box car (cw wheels at the bottom, ccw on top, piece in the centre, 100x100) fell in, flipped and drove back left. Solved at tick 773.
L10 "up the hump": a steep semicircle hump (r 102, height 100, x 249-453) on flat ground (y 212), goal beyond it. FAILED: a car with a tall front, a ramp-like diagonal frame (it rested on the hump edge), and an elbow car with the front wheels high (front on top, rear stuck at the wall); all had the piece at the back/middle and flipped or wedged. WORKED: a long low car, cw wheels y 191 at x -97,-30,45,118, plus a stepped front wheel at (118,140), with the goal piece at the FRONT (centre (52,120)) braced to the front wheels (rods 3:0-0:0, 2:0-0:0, 1:0-0:2, 5:0-0:1, 4:0-0:3). Solved at tick 763.

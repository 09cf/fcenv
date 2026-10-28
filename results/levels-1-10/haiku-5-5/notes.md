GENERAL NOTES FOR FANTASTIC CONTRACTION (carry to every level)

A. Process
- Start: describe, view (level), run once as-is. If goal pieces can already drift in, you may win with no building.
- Cheap loop: small change -> run -> run_filmstrip with a region crop around the action -> adjust.
- Always RUN after building before filmstrip: filmstrip shows the last run, not the current design.
- If a run is stuck or repeatedly fails, switch to a structurally different mechanism early.
- submit only accepts a design that wins on its check run.

B. Build rules learned
- Pieces must lie fully inside the build area (wheel disc too).
- Wheel rim joints: "N:1".."N:4" around the rim; with a ccw wheel at (200,70): 1:1=(220,70) right, 1:2=(200,90) bottom, 1:3=(180,70) left, 1:4=(200,50) top.
- A rod pinned to ONE rim joint is a free pendulum about that pin (not rigid with the wheel). To make a rigid arm, attach two rods from two different rim joints to the same tip joint (triangle with the wheel). Then tip path is a true circle.
- Rods meeting at a joint nearly parallel (collinear) are rejected as overlap.
- Free pieces too close to rod ends are rejected as overlap.
- Wood rods rest on terrain top edge; keep centres ~4 above terrain top. Water rods collide with terrain and pieces.

C. Goal pieces
- Goal wins when all goal pieces are fully inside the goal box at one instant.
- Pieces barely bounce. Ramps do not launch free pieces.

D. Physics (measured)
- Motor ~0.8 turns/s. Rim speed = 2*pi*r*0.8.
- Piece carried over a wheel top leaves at ~175 u/s. Too slow.
- Arm strike on a free piece on the ground gives ~300 u/s flat, then it drops off the terrain edge (gap x 300..402 has no ground).
- Frame with wheel + legs + base is unstable under torque: with a rigid tip arm it got knocked and the wheel slid right within ~30 ticks.
- A rigid whirl around the upper-right is blocked by fixed scenery: circle L4 (27,27) r61 and ground L0.

E. Level 2 (solved): ccw wheel near goal side, water legs to wood base, wood arm from rim to goal piece joint 0:0; piece whirls out through goal window at ~3 o'clock. Works only if the whirl circle clears fixed terrain.

F. Level 3 (NOT solved). Goal box x 483-642, y 55-191. Goal piece starts near (171,106).
- Terrain: L0 flat top y=182 for x -111..300. Open gap x 300..402. Right terrain L1 top y=184 from x 402..655.
- Needed: piece leaves near (276,134) moving up-right at ~300 u/s at ~50 deg. Then it lands on L1 or goes through the box directly (computed: works if launched at 50 deg with speed ~300-400 from (276,134)).
- Problem: the arm-hit timing and frame stability. Piece placed on the tip path at ~50 deg from bottom gets hit, but the frame then moves/collapses and the launch goes down-right instead.
- Next ideas: (1) a much more braced frame (more legs, two wheels mirrored to cancel torque, or a low wheel close to the base); (2) rigid tip arm at the right edge with the piece at the 45-55 deg point; (3) make the frame heavier (add wood rods) so the wheel can't walk.

G. Dead ends (level 2): cart on the bar flies off the round end going down; short flinger arm lands short; wedge launch from cart too slow; counter-rod on opposite side made swing worse.

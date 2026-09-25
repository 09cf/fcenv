"""Tests for the fccore C API, through the ctypes bindings."""

import re
import unittest

from common import cases

import fccore


def block(tag, x, y, w, h, rot=0.0, goal=False, bid=None, joints=()):
    id_attr = f' id="{bid}"' if bid is not None else ""
    j = "".join(f"<jointedTo>{j}</jointedTo>" for j in joints)
    return (
        f"<{tag}{id_attr}><rotation>{rot}</rotation>"
        f"<position><x>{x}</x><y>{y}</y></position>"
        f"<width>{w}</width><height>{h}</height>"
        f"<goalBlock>{'true' if goal else 'false'}</goalBlock>"
        f"<joints>{j}</joints></{tag}>"
    )


def design(player, level=(), build=(0, 0, 400, 400), goal=(0, 0, 100, 100)):
    def zone(tag, z):
        x, y, w, h = z
        return (
            f"<{tag}><position><x>{x}</x><y>{y}</y></position>"
            f"<width>{w}</width><height>{h}</height></{tag}>"
        )

    return (
        "<retrieveLevel><levelId>1</levelId><level>"
        f"<levelBlocks>{''.join(level)}</levelBlocks>"
        f"<playerBlocks>{''.join(player)}</playerBlocks>"
        f"{zone('start', build)}{zone('end', goal)}"
        "</level></retrieveLevel>"
    )


FLOOR = block("StaticRectangle", 0, 300, 2000, 40)
GOAL = block("JointedDynamicRectangle", 0, 0, 40, 40, goal=True, bid=0)


def trajectory(sim, ticks):
    out = []
    for _ in range(ticks):
        out.append([(s.x, s.y, s.angle, s.vx, s.vy, s.omega) for s in sim.state()])
        sim.step()
    return out


def sample_designs(n=10):
    cs = cases()
    if not cs:
        raise unittest.SkipTest("no cached designs; run tests/fetch_designs.py")
    return [c[1].read_text() for c in cs[:n]]


class TestBasics(unittest.TestCase):
    def test_bad_xml(self):
        with self.assertRaises(ValueError):
            fccore.Sim("<nope")

    def test_blocks_and_areas(self):
        rod = block("SolidRod", 50, 0, 60, 8, bid=1, joints=[0])
        with fccore.Sim(design([GOAL, rod], [FLOOR])) as sim:
            blocks = sim.blocks()
            self.assertEqual(sim.n_blocks, 3)
            self.assertEqual(sim.n_design_blocks, 2)
            self.assertEqual(blocks[0].type_id, fccore.GOAL_RECT)
            self.assertTrue(blocks[0].is_goal)
            self.assertEqual(blocks[1].type_id, fccore.SOLID_ROD)
            self.assertEqual(blocks[2].type_id, fccore.STATIC_RECT)
            self.assertTrue(blocks[2].is_level)
            self.assertEqual((blocks[2].w, blocks[2].h), (2000, 40))
            b = sim.build_area()
            self.assertEqual((b.x, b.y, b.w, b.h), (0, 0, 400, 400))

    def test_rod_joint_attaches(self):
        # rod's left end lands on the goal rect's top-right corner joint
        rod = block("SolidRod", 50, -20, 60, 8, bid=1, joints=[0])
        with fccore.Sim(design([GOAL, rod])) as sim:
            joints = sim.joints()
            self.assertEqual(len(joints), 1)
            self.assertEqual({joints[0].block1, joints[0].block2}, {0, 1})
            self.assertAlmostEqual(joints[0].x, 20)
            self.assertAlmostEqual(joints[0].y, -20)

    def test_solves_immediately_when_goal_starts_in_goal_area(self):
        with fccore.Sim(design([GOAL])) as sim:
            self.assertTrue(sim.goal_reached())
            self.assertEqual(sim.run(100), 1)
            self.assertEqual(sim.tick, 1)

    def test_falls_into_goal_area(self):
        goal = block("JointedDynamicRectangle", 0, 0, 40, 40, goal=True, bid=0)
        xml = design([goal], [FLOOR], goal=(0, 250, 200, 60))
        with fccore.Sim(xml) as sim:
            self.assertFalse(sim.goal_reached())
            t = sim.run(1000)
            self.assertIsNotNone(t)
            self.assertEqual(sim.solve_tick, t)
            self.assertEqual(sim.tick, t)

    def test_step_keeps_first_solve_tick(self):
        with fccore.Sim(design([GOAL])) as sim:
            sim.step(50)
            self.assertEqual(sim.tick, 50)
            self.assertEqual(sim.solve_tick, 1)


class TestFlashWinCheck(unittest.TestCase):
    """The original game's win check (Player.hasWon on display-list values)."""

    TALL = (0, 0, 100, 4000)  # goal area x in [-50, 50]

    def solve_tick(self, goal, win_check=fccore.WIN_FLASH, zone=TALL):
        with fccore.Sim(design([goal], goal=zone), win_check=win_check) as sim:
            return sim.run(5)

    def rect(self, x, rot=0.0):
        return block("JointedDynamicRectangle", x, 0, 40, 40, rot=rot, goal=True, bid=0)

    def circle(self, x):
        return block("NoSpinWheel", x, 0, 40, 40, goal=True, bid=0)

    def test_goal_rect_needs_2mm_margin(self):
        # rendered bounds are +-(w/2 + 4); hasWon subtracts 4 from the width
        self.assertEqual(self.solve_tick(self.rect(28)), 1)
        self.assertIsNone(self.solve_tick(self.rect(28.05)))
        self.assertIsNone(self.solve_tick(self.rect(-28.05)))
        # fcsim only requires the geometric rectangle to be inside
        self.assertEqual(self.solve_tick(self.rect(30), fccore.WIN_FCSIM), 1)
        self.assertIsNone(self.solve_tick(self.rect(30)))

    def test_rotated_goal_rect_uses_rendered_bounds(self):
        # at 45 degrees the half-extent is (20 + 4) * sqrt(2) - 2 ~= 31.94
        rot = 0.7853981633974483
        self.assertEqual(self.solve_tick(self.rect(18, rot)), 1)
        self.assertIsNone(self.solve_tick(self.rect(18.2, rot)))

    def test_circle_position_truncated_to_twips(self):
        self.assertEqual(self.solve_tick(self.circle(30)), 1)
        self.assertEqual(self.solve_tick(self.circle(30.049)), 1)  # 600.98 twips -> 600
        self.assertIsNone(self.solve_tick(self.circle(30.05)))
        self.assertIsNone(self.solve_tick(self.circle(30.049), fccore.WIN_FCSIM))
        self.assertEqual(self.solve_tick(self.circle(-30.049)), 1)  # toward zero

    def test_goal_area_centre_truncated_to_twips(self):
        # centre -0.049 truncates toward zero to 0, so the right edge is at
        # 50 rather than 49.951, and a circle reaching exactly 50 is inside
        zone = (-0.049, 0, 100, 4000)
        self.assertEqual(self.solve_tick(self.circle(30), zone=zone), 1)
        self.assertIsNone(self.solve_tick(self.circle(30), fccore.WIN_FCSIM, zone=zone))


class TestWheelImport(unittest.TestCase):
    """XML import rules from the original game (Wheel.as, BlockCollection.as)."""

    def joints(self, player, opts=0):
        with fccore.Sim(design(player), opts=opts) as sim:
            return [(j.block1, j.block2, j.x, j.y) for j in sim.joints()]

    def wheel(self, d, rot=0.0, goal=False, x=0, y=0, bid=0, joints=()):
        return block("NoSpinWheel", x, y, d, d, rot=rot, goal=goal, bid=bid, joints=joints)

    def rod(self, x0, y0, x1, y1, bid=1, joints=(0,), tag="HollowRod"):
        import math
        return block(tag, (x0 + x1) / 2, (y0 + y1) / 2,
                     math.hypot(x1 - x0, y1 - y0), 4,
                     rot=math.atan2(y1 - y0, x1 - x0), bid=bid, joints=joints)

    def test_rod_attaches_to_inner_joint_of_large_wheel(self):
        # (20, 0) is 10mm from the circumference joint at (30, 0): only the
        # inner joint is within snapping range
        player = [self.wheel(60), self.rod(20, 0, 80, 0)]
        self.assertEqual(self.joints(player), [(0, 1, 20.0, 0.0)])
        self.assertEqual(self.joints(player, fccore.OPT_FCSIM_IMPORT), [])

    def test_inner_joints_need_radius_over_20(self):
        # rod end at (0, 20): exactly on the inner joint for d=41, but for
        # d=40 the only joint nearby is the circumference joint
        (_, _, x, y), = self.joints([self.wheel(41), self.rod(0, 20, 0, 80)])
        self.assertEqual(y, 20.0)
        (_, _, x, y), = self.joints([self.wheel(40), self.rod(0, 20.4, 0, 80)])
        self.assertAlmostEqual(y, 20.0)
        # nearest wins: 0.2 from the inner joint, 0.3 from the circumference
        (_, _, x, y), = self.joints([self.wheel(41), self.rod(0, 20.2, 0, 80)])
        self.assertEqual(y, 20.0)
        (_, _, x, y), = self.joints([self.wheel(41), self.rod(0, 20.3, 0, 80)])
        self.assertEqual(y, 20.5)

    def test_inner_joints_do_not_rotate(self):
        player = [self.wheel(60, rot=0.7853981633974483), self.rod(20, 0, 80, 0)]
        self.assertEqual(self.joints(player), [(0, 1, 20.0, 0.0)])

    def test_goal_circles_get_inner_joints(self):
        player = [self.wheel(60, goal=True), self.rod(0, -20, 0, -80)]
        self.assertEqual(self.joints(player), [(0, 1, 0.0, -20.0)])

    def test_jointed_rod_does_not_collide_with_wheel(self):
        # the rod runs through the wheel; sharing a joint disables collision
        player = [self.wheel(60), self.rod(20, 0, 80, 0, tag="SolidRod")]
        with fccore.Sim(design(player)) as sim:
            self.assertTrue(sim.check_legal().legal)
        with fccore.Sim(design(player), opts=fccore.OPT_FCSIM_IMPORT) as sim:
            self.assertFalse(sim.check_legal().legal)


class TestLegality(unittest.TestCase):
    def test_legal(self):
        rod = block("SolidRod", 100, 100, 60, 8, bid=1)
        with fccore.Sim(design([GOAL, rod], [FLOOR])) as sim:
            self.assertEqual(sim.check_legal(), fccore.Legality(True, [0, 0]))

    def test_outside_build_area(self):
        rod = block("SolidRod", 300, 100, 60, 8, bid=1)
        with fccore.Sim(design([GOAL, rod])) as sim:
            leg = sim.check_legal()
            self.assertFalse(leg.legal)
            self.assertEqual(leg.flags, [0, fccore.ILLEGAL_OUTSIDE_BUILD_AREA])

    def test_build_area_has_4mm_tolerance(self):
        # build area edge is at x=200, widened by 4 total (2 per side), so a
        # 60-wide rod fits while its centre is at x <= 172
        for x, legal in ((171.9, True), (172, True), (172.1, False)):
            rod = block("SolidRod", x, 100, 60, 8, bid=1)
            with fccore.Sim(design([GOAL, rod])) as sim:
                self.assertEqual(sim.check_legal().legal, legal, x)

    def test_overlap_with_level(self):
        rod = block("SolidRod", 0, 150, 60, 8, bid=1)
        level = block("StaticRectangle", 0, 150, 100, 40)
        with fccore.Sim(design([GOAL, rod], [level])) as sim:
            leg = sim.check_legal()
            self.assertFalse(leg.legal)
            self.assertEqual(leg.flags[1], fccore.ILLEGAL_OVERLAP)

    def test_water_rods_pass_through_each_other(self):
        a = block("HollowRod", 0, 100, 80, 4, bid=1)
        b = block("HollowRod", 0, 100, 80, 4, rot=1.0, bid=2)
        with fccore.Sim(design([GOAL, a, b])) as sim:
            self.assertTrue(sim.check_legal().legal)
        a = block("SolidRod", 0, 100, 80, 8, bid=1)
        b = block("SolidRod", 0, 100, 80, 8, rot=1.0, bid=2)
        with fccore.Sim(design([GOAL, a, b])) as sim:
            leg = sim.check_legal()
            self.assertFalse(leg.legal)
            self.assertEqual(leg.flags[1:], [fccore.ILLEGAL_OVERLAP] * 2)

    def test_overlap_pairs(self):
        # rods 1 and 2 cross; rod 3 sits in the second level block; the goal
        # (0) and the first level block touch nothing
        a = block("SolidRod", 0, 100, 80, 8, bid=1)
        b = block("SolidRod", 0, 100, 80, 8, rot=1.0, bid=2)
        c = block("SolidRod", 100, 150, 40, 8, bid=3)
        level = [FLOOR, block("StaticRectangle", 100, 150, 60, 40)]
        with fccore.Sim(design([GOAL, a, b, c], level)) as sim:
            # design blocks 0-3, then level blocks 4-5
            self.assertEqual(sim.overlaps(), [(1, 2), (3, 5)])
            flags = sim.check_legal().flags
            self.assertEqual([bool(f & fccore.ILLEGAL_OVERLAP) for f in flags],
                             [False, True, True, True])
        with fccore.Sim(design([GOAL, a], [FLOOR])) as sim:
            self.assertEqual(sim.overlaps(), [])

    def test_check_does_not_disturb_simulation(self):
        for xml in sample_designs():
            with fccore.Sim(xml) as a, fccore.Sim(xml) as b:
                a.step(5)
                b.step(5)
                a.check_legal()
                self.assertEqual(trajectory(a, 100), trajectory(b, 100))


class TestDeterminism(unittest.TestCase):
    def test_interleaved_sims_match(self):
        for xml in sample_designs():
            with fccore.Sim(xml) as solo:
                expect = trajectory(solo, 200)
            with fccore.Sim(xml) as a, fccore.Sim(xml) as b:
                ta, tb = [], []
                for _ in range(200):
                    ta.append([(s.x, s.y, s.angle, s.vx, s.vy, s.omega) for s in a.state()])
                    tb.append([(s.x, s.y, s.angle, s.vx, s.vy, s.omega) for s in b.state()])
                    a.step()
                    b.step()
            self.assertEqual(ta, expect)
            self.assertEqual(tb, expect)

    def test_record_matches_stepping(self):
        for xml in sample_designs():
            with fccore.Sim(xml) as a, fccore.Sim(xml) as b:
                # trajectory() samples before each step, record() after it;
                # expect the states after steps 3, 6, ..., 60
                a.step()
                expect = trajectory(a, 60)[2::3]
                frames = b.record(60, stride=3)
                self.assertEqual(b.tick, 60)
                got = [[(s.x, s.y, s.angle, s.vx, s.vy, s.omega) for s in f] for f in frames]
                self.assertEqual(got, expect)
            with fccore.Sim(xml) as c:
                arr = c.record_array(60, stride=3, blocks=[0])
                self.assertEqual(arr.shape, (20, 1, 6))
                self.assertEqual(arr[:, 0, 0].tolist(), [f[0][0] for f in expect])

    def test_reset_matches_fresh(self):
        for xml in sample_designs():
            with fccore.Sim(xml) as sim:
                expect = trajectory(sim, 150)
                sim.reset()
                self.assertEqual(sim.tick, 0)
                self.assertIsNone(sim.solve_tick)
                self.assertEqual(trajectory(sim, 150), expect)


class TestExport(unittest.TestCase):
    def test_export_reparses(self):
        for xml in sample_designs():
            with fccore.Sim(xml) as sim:
                out = sim.export_xml(name="t")
                blocks = [(b.type_id, b.is_goal, b.is_level) for b in sim.blocks()]
            with fccore.Sim(out) as sim2:
                self.assertEqual(
                    [(b.type_id, b.is_goal, b.is_level) for b in sim2.blocks()], blocks
                )

    def test_export_round_trip_drift_is_small(self):
        # export_design is not lossless (rods are re-derived from centre,
        # width and angle via sin/cos/atan2 on import, and dtostr truncates),
        # so a round trip moves coordinates slightly. Small drift can still
        # flip a chaotic design's outcome: always simulate the exact XML that
        # will be submitted, never a design that will be re-serialised.
        nums = lambda s: [float(x) for x in re.findall(r">(-?[0-9.]+)<", s)]
        for xml in sample_designs():
            with fccore.Sim(xml) as sim:
                once = sim.export_xml()
            with fccore.Sim(once) as sim:
                twice = sim.export_xml()
            a, b = nums(once), nums(twice)
            self.assertEqual(len(a), len(b))
            self.assertLess(max(abs(x - y) for x, y in zip(a, b)), 1e-5)


if __name__ == "__main__":
    unittest.main()

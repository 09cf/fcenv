"""Tests for fcenv.edit: explicit-connection design edits over XML."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core" / "tests"))

from fcenv import edit  # noqa: E402
from fcenv.design import JointRef, fccore, parse  # noqa: E402
from fcenv.edit import Err, ErrorCode, NewRef, Offset, Ok, Polar, Rules  # noqa: E402

LEVEL = (
    "<retrieveLevel><levelId>1</levelId><level><levelBlocks>"
    "<StaticRectangle><rotation>0</rotation><position><x>0</x><y>300</y></position>"
    "<width>2000</width><height>40</height><goalBlock>false</goalBlock><joints/>"
    "</StaticRectangle></levelBlocks><playerBlocks>"
    '<JointedDynamicRectangle id="0"><rotation>0</rotation><position><x>0</x><y>0</y>'
    "</position><width>40</width><height>40</height><goalBlock>true</goalBlock>"
    "<joints/></JointedDynamicRectangle></playerBlocks>"
    "<start><position><x>0</x><y>0</y></position><width>400</width><height>400</height>"
    "</start><end><position><x>300</x><y>0</y></position><width>100</width>"
    "<height>100</height></end></level></retrieveLevel>"
)
GOAL = 0  # the goal rect's corners are slots 1-4: (20,-20), (-20,-20), (20,20), (-20,20)


def ok(result):
    assert isinstance(result, Ok), result
    return result


def err(result, code):
    assert isinstance(result, Err), result
    assert result.error.code == code, result
    return result.error


def stacks(xml):
    """{frozenset of members} for joints with more than one member."""
    return {frozenset(j["members"]) for j in edit.describe(xml)["joints"]
            if len(j["members"]) > 1}


class TestAdd(unittest.TestCase):
    def test_rod_free_and_connected(self):
        r = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20)))
        self.assertEqual(r.info["block"], 1)
        self.assertIn(frozenset({JointRef(0, 1), JointRef(1, 0)}), stacks(r.xml))
        rod = edit.describe(r.xml)["blocks"][1]
        self.assertEqual((rod["type"], rod["x"], rod["y"], rod["width"]),
                         ("SolidRod", 50.0, -20.0, 60.0))

    def test_rod_between_two_joints(self):
        x = ok(edit.add_rod(LEVEL, "water", JointRef(GOAL, 1), (80, -20))).xml
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), JointRef(GOAL, 3))).xml
        self.assertEqual(stacks(x), {frozenset({JointRef(0, 1), JointRef(1, 0)}),
                                     frozenset({JointRef(1, 1), JointRef(2, 0)}),
                                     frozenset({JointRef(0, 3), JointRef(2, 1)})})

    def test_stack_of_three(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (80, 60))).xml
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (140, -20))).xml
        self.assertIn(frozenset({JointRef(1, 1), JointRef(2, 0), JointRef(3, 0)}), stacks(x))

    def test_wheel_on_joint(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml
        r = ok(edit.add_wheel(x, "cw", JointRef(1, 1)))
        self.assertIn(frozenset({JointRef(1, 1), JointRef(2, 0)}), stacks(r.xml))
        self.assertEqual(len(r.info["joints"]), 5)
        # and a rod on the wheel's circumference joint (80+20, -20)
        r = ok(edit.add_rod(r.xml, "water", JointRef(2, 1), (100, 60)))
        self.assertIn(frozenset({JointRef(2, 1), JointRef(3, 0)}), stacks(r.xml))

    def test_result_simulates(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml
        x = ok(edit.add_wheel(x, "cw", JointRef(1, 1))).xml
        with fccore.Sim(x) as sim:
            self.assertEqual(len(sim.joints()), 2)
            sim.step(30)


class TestRejections(unittest.TestCase):
    def test_bad_reference(self):
        err(edit.add_rod(LEVEL, "wood", JointRef(0, 9), (80, 0)), ErrorCode.BAD_REFERENCE)
        err(edit.add_rod(LEVEL, "wood", JointRef(5, 0), (80, 0)), ErrorCode.BAD_REFERENCE)
        err(edit.delete(LEVEL, 3), ErrorCode.BAD_REFERENCE)

    def test_goal_pieces_are_immutable(self):
        err(edit.delete(LEVEL, GOAL), ErrorCode.CANNOT_DELETE_GOAL)

    def test_both_ends_on_one_joint(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml
        # (0,1) and (1,0) are the same joint stack
        err(edit.add_rod(x, "wood", JointRef(0, 1), JointRef(1, 0)), ErrorCode.SAME_JOINT)
        err(edit.add_rod(x, "wood", JointRef(1, 0), JointRef(1, 0)), ErrorCode.SAME_JOINT)

    def test_two_blocks_share_one_joint_at_most(self):
        # a second rod between the same two joints
        x = ok(edit.add_rod(LEVEL, "water", JointRef(GOAL, 1), (80, -20))).xml
        err(edit.add_rod(x, "water", JointRef(1, 0), JointRef(1, 1)), ErrorCode.SHARED_TWICE)
        # including a rod across two corners of one block: the game drops
        # the second connection (BlockCollection.createRod, sharesBlockWith)
        err(edit.add_rod(LEVEL, "water", JointRef(GOAL, 1), JointRef(GOAL, 3)),
            ErrorCode.SHARED_TWICE)

    def test_rod_across_one_wheel(self):
        x = ok(edit.add_wheel(LEVEL, "none", (100, 0))).xml  # joints at 100 +- 20
        err(edit.add_rod(x, "water", JointRef(1, 1), JointRef(1, 3)), ErrorCode.SHARED_TWICE)
        err(edit.add_rod(x, "water", JointRef(1, 0), JointRef(1, 1)), ErrorCode.SHARED_TWICE)

    def test_rod_across_one_goal_circle(self):
        # a 60mm goal circle: circumference joints at 30, inner joints at 20
        circle = ('<NoSpinWheel id="1"><rotation>0</rotation><position><x>100</x>'
                  '<y>0</y></position><width>60</width><height>60</height>'
                  '<goalBlock>true</goalBlock><joints/></NoSpinWheel>')
        x = LEVEL.replace("</playerBlocks>", circle + "</playerBlocks>")
        err(edit.add_rod(x, "water", JointRef(1, 1), JointRef(1, 5)), ErrorCode.SHARED_TWICE)
        err(edit.add_rod(x, "water", JointRef(1, 6), JointRef(1, 8)), ErrorCode.SHARED_TWICE)
        ok(edit.add_rod(x, "water", JointRef(1, 5), (150, 60)))  # one inner joint is fine

    def test_one_wheel_per_joint(self):
        x = ok(edit.add_wheel(LEVEL, "none", (100, 0))).xml
        # reported as an overlap, though jointed wheels never collide
        err(edit.add_wheel(x, "cw", JointRef(1, 0)), ErrorCode.OVERLAP)
        err(edit.add_wheel(x, "cw", JointRef(1, 2)), ErrorCode.OVERLAP)  # on its spoke

    def test_rod_too_short(self):
        err(edit.add_rod(LEVEL, "wood", (100, 0), (100.5, 0)), ErrorCode.ROD_TOO_SHORT)
        ok(edit.add_rod(LEVEL, "wood", (100, 0), (100.5, 0), Rules(min_rod_length=0.1)))

    def test_piece_limit(self):
        self.assertEqual(Rules().max_design_blocks, 300)  # fcsim's editor cap
        err(edit.add_wheel(LEVEL, "none", (100, 0), Rules(max_design_blocks=1)),
            ErrorCode.PIECE_LIMIT)
        ok(edit.add_wheel(LEVEL, "none", (100, 0), Rules(max_design_blocks=2)))

    def test_overlap(self):
        e = err(edit.add_rod(LEVEL, "wood", (-50, 0), (50, 0)), ErrorCode.OVERLAP)
        self.assertEqual(e.blocks, (1,))
        # water rods pass through design blocks
        ok(edit.add_rod(LEVEL, "water", (-50, 0), (50, 0)))

    def test_outside_build_area(self):
        err(edit.add_rod(LEVEL, "wood", (0, -100), (500, -100)), ErrorCode.OUTSIDE_BUILD_AREA)

    def test_errors_leave_no_trace(self):
        # pure functions: a failed edit returns no XML at all
        r = edit.add_rod(LEVEL, "wood", (-50, 0), (50, 0))
        self.assertFalse(hasattr(r, "xml"))


class TestDelete(unittest.TestCase):
    def test_ids_are_stable(self):
        x = ok(edit.add_wheel(LEVEL, "none", (100, 0))).xml  # 1
        x = ok(edit.add_wheel(x, "none", (160, 0))).xml  # 2
        x = ok(edit.delete(x, 1)).xml
        blocks = edit.describe(x)["blocks"]
        self.assertEqual([(b["id"], b["x"]) for b in blocks], [(0, 0), (2, 160)])
        # new blocks never reuse an ID
        self.assertEqual(ok(edit.add_wheel(x, "none", (100, 0))).info["block"], 3)
        err(edit.delete(x, 1), ErrorCode.BAD_REFERENCE)

    def test_deleting_the_bottom_of_a_stack_keeps_the_rest_connected(self):
        x = ok(edit.add_rod(LEVEL, "wood", (60, 0), (120, 0))).xml  # 1 creates (120, 0)
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (120, 80))).xml  # 2
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (180, 0))).xml  # 3
        x = ok(edit.delete(x, 1)).xml
        self.assertIn(frozenset({JointRef(2, 0), JointRef(3, 0)}), stacks(x))

    def test_deleting_a_wheel_frees_its_spokes(self):
        x = ok(edit.add_wheel(LEVEL, "none", (100, 0))).xml
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (120, 80))).xml  # on (120, 0)
        x = ok(edit.delete(x, 1)).xml
        d = edit.describe(x)
        self.assertEqual(len(d["blocks"]), 2)
        self.assertEqual(stacks(x), set())


class TestRewire(unittest.TestCase):
    def setUp(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml  # 1
        self.x = ok(edit.add_rod(x, "water", (60, 60), (120, 60))).xml  # 2

    def test_reconnect_moves_rod_to_the_end(self):
        r = ok(edit.rewire(self.x, 1, 1, JointRef(2, 0)))
        self.assertEqual(r.info["block"], 1)  # same ID...
        blocks = edit.describe(r.xml)["blocks"]
        self.assertEqual([b["id"] for b in blocks], [0, 2, 1])  # ...last in order
        self.assertEqual(stacks(r.xml), {frozenset({JointRef(0, 1), JointRef(1, 0)}),
                                         frozenset({JointRef(1, 1), JointRef(2, 0)})})
        self.assertEqual((blocks[2]["x"], blocks[2]["y"]), (40.0, 20.0))  # (20,-20)-(60,60)

    def test_detach_leaves_a_free_joint(self):
        x = ok(edit.add_rod(self.x, "water", JointRef(1, 1), (80, 40))).xml  # 3
        r = ok(edit.rewire(x, 3, 0, None))
        self.assertEqual(stacks(r.xml), {frozenset({JointRef(0, 1), JointRef(1, 0)})})
        rod = edit.describe(r.xml)["blocks"][3]
        self.assertEqual((rod["id"], rod["x"], rod["y"], rod["width"]), (3, 80.0, 10.0, 60.0))

    def test_detaching_can_create_an_overlap(self):
        # a wood rod sharing a joint with the goal rect doesn't collide with
        # it; once detached it does
        err(edit.rewire(self.x, 1, 0, None), ErrorCode.OVERLAP)

    def test_rewire_to_current_joint_is_a_no_op(self):
        r = ok(edit.rewire(self.x, 1, 0, JointRef(GOAL, 1)))
        self.assertEqual(r.xml, self.x)

    def test_only_rods(self):
        x = ok(edit.add_wheel(self.x, "none", (100, -100))).xml
        err(edit.rewire(x, 3, 0, None), ErrorCode.WRONG_BLOCK_TYPE)
        err(edit.rewire(self.x, 0, 0, None), ErrorCode.WRONG_BLOCK_TYPE)  # goal rect

    def test_cannot_rewire_to_own_other_end(self):
        err(edit.rewire(self.x, 2, 0, JointRef(2, 1)), ErrorCode.SAME_JOINT)


class TestBatch(unittest.TestCase):
    def test_weights(self):
        # 5 rods hanging from one joint, free ends all at (20, 100)
        r = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 3), (20, 100), count=5))
        self.assertEqual(r.info["blocks"], [1, 2, 3, 4, 5])
        self.assertEqual(stacks(r.xml), {frozenset(
            [JointRef(0, 3)] + [JointRef(b, 0) for b in range(1, 6)])})
        # (free ends are recomputed from the rod on import, so compare loosely)
        free = [j for j in edit.describe(r.xml)["joints"]
                if abs(j["x"] - 20) < 1e-9 and abs(j["y"] - 100) < 1e-9]
        self.assertEqual(len(free), 5)  # coincident but unconnected

    def test_new_refs_build_a_triangle(self):
        r = ok(edit.add_rods(LEVEL, [
            ("wood", (60, 60), (120, 60)),
            ("wood", NewRef(0, 1), (90, 120)),
            ("wood", NewRef(1, 1), NewRef(0, 0)),
        ]))
        self.assertEqual(r.info["blocks"], [1, 2, 3])
        self.assertEqual(stacks(r.xml), {
            frozenset({JointRef(1, 1), JointRef(2, 0)}),
            frozenset({JointRef(2, 1), JointRef(3, 0)}),
            frozenset({JointRef(1, 0), JointRef(3, 1)}),
        })

    def test_batch_is_atomic(self):
        e = err(edit.add_rods(LEVEL, [("wood", (60, 60), (120, 60)),
                                      ("wood", (-50, 0), (50, 0))]), ErrorCode.OVERLAP)
        self.assertEqual(e.blocks, (2,))

    def test_batch_references(self):
        err(edit.add_rods(LEVEL, [("wood", NewRef(0, 0), (120, 60))]), ErrorCode.BAD_REFERENCE)
        err(edit.add_rods(LEVEL, [("wood", (60, 60), (120, 60)),
                                  ("wood", JointRef(1, 1), (120, 0))]),
            ErrorCode.BAD_REFERENCE)  # new rods are named with NewRef

    def test_batch_piece_limit(self):
        err(edit.add_rod(LEVEL, "water", (60, 60), (120, 60), Rules(max_design_blocks=3),
                         count=3), ErrorCode.PIECE_LIMIT)
        ok(edit.add_rod(LEVEL, "water", (60, 60), (120, 60), Rules(max_design_blocks=3),
                        count=2))


class TestMove(unittest.TestCase):
    def setUp(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml  # 1
        x = ok(edit.add_wheel(x, "cw", JointRef(1, 1))).xml  # 2 at (80, -20)
        self.x = ok(edit.add_rod(x, "water", JointRef(2, 1), (100, 60))).xml  # 3 on (100, -20)

    def blocks(self, xml):
        return [(b["x"], b["y"]) for b in edit.describe(xml)["blocks"]]

    def test_wheel_moves_rigidly_and_rods_stretch(self):
        r = ok(edit.move_joint(self.x, JointRef(1, 1), (80, -60)))
        self.assertEqual(r.info, {"moved": [2], "stretched": [1, 3], "delta": (0.0, -40.0)})
        self.assertEqual(self.blocks(r.xml),
                         [(0, 0), (50, -40), (80, -60), (100, 0)])
        self.assertEqual(stacks(r.xml), stacks(self.x))  # nothing (dis)connects

    def test_moving_a_goal_corner_moves_the_goal_piece(self):
        r = ok(edit.move_joint(self.x, JointRef(GOAL, 1), (40, -20)))
        self.assertEqual(r.info["moved"], [0])
        self.assertEqual(self.blocks(r.xml)[0], (20, 0))

    def test_rod_with_both_ends_moved_translates(self):
        # wheel centred on a goal corner: goal, wheel and the rod between
        # them all move together
        x = ok(edit.add_wheel(LEVEL, "none", JointRef(GOAL, 3))).xml  # 1 at (-20, 20)
        x = ok(edit.add_rod(x, "water", JointRef(GOAL, 1), JointRef(1, 2))).xml  # 2
        r = ok(edit.move_joint(x, JointRef(GOAL, 0), (10, 0)))
        self.assertEqual(r.info["moved"], [0, 1])
        before, after = edit.describe(x)["blocks"][2], edit.describe(r.xml)["blocks"][2]
        self.assertEqual((after["x"] - before["x"], after["width"]), (10, before["width"]))

    def test_move_rejections(self):
        err(edit.move_joint(self.x, JointRef(3, 1), (500, 60)), ErrorCode.OUTSIDE_BUILD_AREA)
        err(edit.move_joint(self.x, JointRef(3, 1), (100, -19.5)), ErrorCode.ROD_TOO_SHORT)
        err(edit.move_joint(self.x, JointRef(9, 0), (0, 0)), ErrorCode.BAD_REFERENCE)
        # the wood rod swung through the goal rect
        err(edit.move_joint(self.x, JointRef(1, 1), (0, 20)), ErrorCode.OVERLAP)


class TestRelative(unittest.TestCase):
    def test_offset_and_polar(self):
        r = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), Offset(JointRef(GOAL, 1), 60, 0)))
        rod = edit.describe(r.xml)["blocks"][1]
        self.assertEqual((rod["x"], rod["y"], rod["width"]), (50.0, -20.0, 60.0))
        # 90 degrees is straight down (+y)
        r = ok(edit.add_rod(r.xml, "water", JointRef(1, 1), Polar(JointRef(1, 1), 50, 90)))
        rod = edit.describe(r.xml)["blocks"][2]
        self.assertAlmostEqual(rod["x"], 80)
        self.assertAlmostEqual(rod["y"], 5)

    def test_relative_points_are_free(self):
        # an offset of zero is a new joint at the same place, not a connection
        x = ok(edit.add_rod(LEVEL, "water", (60, 60), (120, 60))).xml
        x = ok(edit.add_rod(x, "water", Offset(JointRef(1, 1), 0, 0), (120, 120))).xml
        self.assertEqual(stacks(x), set())

    def test_relative_to_batch_and_move(self):
        r = ok(edit.add_rods(LEVEL, [("wood", (60, 60), (120, 60)),
                                     ("wood", NewRef(0, 1), Polar(NewRef(0, 1), 40, -90))]))
        self.assertAlmostEqual(edit.describe(r.xml)["blocks"][2]["y"], 40)
        m = ok(edit.move_joint(r.xml, JointRef(1, 0), Offset(JointRef(1, 0), 0, -10)))
        self.assertEqual(m.info["delta"], (0.0, -10.0))

    def test_wheel_centre_offset(self):
        r = ok(edit.add_wheel(LEVEL, "cw", Offset(JointRef(GOAL, 1), 100, 0)))
        self.assertEqual(edit.describe(r.xml)["blocks"][1]["x"], 120)


class TestDescribe(unittest.TestCase):
    def test_components_and_joint_names(self):
        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml  # 1
        x = ok(edit.add_rod(x, "water", (60, 100), (120, 100))).xml  # 2, alone
        x = ok(edit.add_wheel(x, "cw", JointRef(1, 1))).xml  # 3
        d = edit.describe(x)
        self.assertEqual(d["components"], [[0, 1, 3], [2]])
        shared = next(j for j in d["joints"] if JointRef(3, 0) in j["members"])
        self.assertEqual(shared["name"], JointRef(1, 1))  # lowest ID names it


class TestSummary(unittest.TestCase):
    def test_grouped_by_component_with_api_names(self):
        from fcenv import view

        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml  # 1
        x = ok(edit.add_rod(x, "water", (60, 100), (120, 100))).xml  # 2, alone
        x = ok(edit.add_wheel(x, "cw", JointRef(1, 1))).xml  # 3
        s = view.summary(x)
        c1, c2 = s.index("Component 1 (3 parts)"), s.index("Component 2 (1 part)")
        self.assertTrue(c1 < s.index("#0 goal rect, centre (0, 0), 40 x 40") < c2)
        self.assertTrue(c1 < s.index("#3 cw wheel") < c2 < s.index("#2 water rod"))
        self.assertIn("1.1 (80, -20) +3.0", s)  # shared joint, named by lowest ID
        self.assertIn("3.0 = 1.1", s)
        self.assertIn("L0 static rect, centre (0, 300), 2000 x 40; top edge y 280", s)


class TestRender(unittest.TestCase):
    def test_png_and_views(self):
        from fcenv import render

        x = ok(edit.add_rod(LEVEL, "wood", JointRef(GOAL, 1), (80, -20))).xml
        for view in ("build", "level", (-50, -50, 150, 50)):
            r = render.render(x, view)
            self.assertTrue(r.png.startswith(b"\x89PNG"))
            self.assertEqual(r.hidden, [])
        self.assertEqual(render.render(x, (-50, -50, 150, 50)).view, (-50, -50, 150, 50))

    def test_stacked_joints_are_reported(self):
        from fcenv import render

        x = ok(edit.add_rod(LEVEL, "water", JointRef(GOAL, 3), (20, 100), count=6)).xml
        r = render.render(x, "build")
        # the six free ends sit at one point: no zoom can separate them
        stacked = [h for h in r.hidden if h.zoom is None]
        self.assertEqual(len(stacked), 1)
        self.assertEqual(sorted(stacked[0].names), [f"{b}.1" for b in range(1, 7)])
        self.assertIn("all at (20, 100)", r.notes())


class TestRun(unittest.TestCase):
    def test_solves_like_fccore(self):
        from fcenv import run

        goal = LEVEL.replace("<x>300</x><y>0</y></position><width>100</width>",
                             "<x>0</x><y>260</y></position><width>400</width>")
        r = run.simulate(goal)  # the goal rect falls onto the floor, inside
        with fccore.Sim(goal) as sim:
            self.assertEqual(r.solve_tick, sim.run(3000))
        self.assertEqual((r.end, r.ticks), ("solved", r.solve_tick))
        self.assertEqual(r.states.shape[0], r.ticks + 1)
        self.assertTrue(r.summary().startswith(f"Solved at tick {r.solve_tick}"))

    def test_timeout_and_images(self):
        from fcenv import run

        r = run.simulate(LEVEL, timeout=45)
        self.assertEqual((r.end, r.ticks, r.solve_tick), ("timeout", 45, None))
        self.assertIn("closest at tick", r.summary())
        self.assertNotIn("Joint breaks", r.summary())  # only mentioned if any
        for img in (r.frame(20), r.trails(), r.onion(count=4)):
            self.assertTrue(img.png.startswith(b"\x89PNG"))
        pages = r.filmstrip(stride=5, per_page=4)
        self.assertEqual(len(pages), 3)  # ticks 0, 5, ..., 45: 10 frames

    def test_goal_piece_leaving_the_world_ends_the_run(self):
        from fcenv import run

        # no floor: the goal rect falls out of the world
        x = LEVEL.replace("<y>300</y>", "<y>5000</y>")
        r = run.simulate(x, timeout=3000)
        self.assertEqual((r.end, r.left_level), ("goal piece left the world", 0))
        self.assertLess(r.ticks, 3000)
        self.assertGreater(r.state(r.ticks, 0)[1], 1400)


class TestRealDesigns(unittest.TestCase):
    """Against the dataset designs (skipped if not fetched)."""

    @classmethod
    def setUpClass(cls):
        from common import cases

        cls.cases = cases()
        if not cls.cases:
            raise unittest.SkipTest("no cached designs; run core/tests/fetch_designs.py")

    def test_serialization_is_lossless(self):
        # re-serializing (regenerated <jointedTo>, verbatim values) gives
        # identical physics
        for uid, path, *_ in self.cases[::7]:
            x = path.read_bytes()
            st = edit._load(x)
            got = edit._choose_jointed_to(st.design, st.graph, [list(j) for j in st.orig_jointed])
            self.assertIsNotNone(got, uid)
            with fccore.Sim(x) as a, fccore.Sim(got[0]) as b:
                self.assertEqual(a.record_array(100).tobytes(),
                                 b.record_array(100).tobytes(), uid)

    def test_summary_renders(self):
        from fcenv import view

        for uid, path, *_ in self.cases[::25]:
            self.assertIn("Component 1", view.summary(path.read_text()), uid)

    def test_zoom_hints_resolve_clusters(self):
        from fcenv import render

        for uid, path, *_ in self.cases[::40]:
            x = path.read_text()
            for h in render.render(x, "build").hidden:
                if h.zoom:
                    inner = render.render(x, h.zoom).hidden
                    # what's left after zooming in is joints at one point
                    self.assertTrue(all(g.zoom is None for g in inner), (uid, h.zoom))

    def test_runs_match_fccore(self):
        from fcenv import run

        for uid, path, *_ in self.cases[::60]:
            x = path.read_text()
            r = run.simulate(x, timeout=600)
            with fccore.Sim(x) as sim:
                want = sim.run(600)
            if r.end != "goal piece left the world":
                self.assertEqual(r.solve_tick, want, uid)

    def test_delete_last_player_piece(self):
        n = 0
        for uid, path, *_ in self.cases[::5]:
            x = path.read_text()
            d = edit.describe(x)
            pieces = [b["id"] for b in d["blocks"] if not b["goal"]]
            if not pieces:
                continue
            r = edit.delete(x, pieces[-1])
            self.assertIsInstance(r, Ok, (uid, r))
            self.assertEqual(len(parse(r.xml).player_blocks), len(d["blocks"]) - 1)
            # untouched blocks keep their exact values
            before, after = parse(x), parse(r.xml)
            kept = [b for i, b in zip(before.ids, before.player_blocks) if i != pieces[-1]]
            self.assertEqual([b.values for b in kept], [b.values for b in after.player_blocks],
                             uid)
            self.assertEqual([i for i in before.ids if i != pieces[-1]], list(after.ids), uid)
            n += 1
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()

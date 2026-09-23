"""Tests for fcenv.edit: explicit-connection design edits over XML."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core" / "tests"))

from fcenv import edit  # noqa: E402
from fcenv.design import JointRef, fccore, parse  # noqa: E402
from fcenv.edit import Err, ErrorCode, Ok, Rules  # noqa: E402

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
    def test_delete_shifts_indices(self):
        x = ok(edit.add_wheel(LEVEL, "none", (100, 0))).xml
        x = ok(edit.add_wheel(x, "none", (160, 0))).xml
        r = ok(edit.delete(x, 1))
        self.assertEqual(r.info["index_map"], {0: 0, 2: 1})
        self.assertEqual(edit.describe(r.xml)["blocks"][1]["x"], 160.0)

    def test_deleting_the_bottom_of_a_stack_keeps_the_rest_connected(self):
        x = ok(edit.add_rod(LEVEL, "wood", (60, 0), (120, 0))).xml  # 1 creates (120, 0)
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (120, 80))).xml  # 2
        x = ok(edit.add_rod(x, "water", JointRef(1, 1), (180, 0))).xml  # 3
        x = ok(edit.delete(x, 1)).xml
        self.assertIn(frozenset({JointRef(1, 0), JointRef(2, 0)}), stacks(x))

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
        self.assertEqual(r.info["block"], 2)
        self.assertEqual(r.info["index_map"], {0: 0, 2: 1})
        self.assertEqual(stacks(r.xml), {frozenset({JointRef(0, 1), JointRef(2, 0)}),
                                         frozenset({JointRef(1, 0), JointRef(2, 1)})})
        rod = edit.describe(r.xml)["blocks"][2]
        self.assertEqual((rod["x"], rod["y"]), (40.0, 20.0))  # (20,-20)-(60,60)

    def test_detach_leaves_a_free_joint(self):
        x = ok(edit.add_rod(self.x, "water", JointRef(1, 1), (80, 40))).xml  # 3
        r = ok(edit.rewire(x, 3, 0, None))
        self.assertEqual(stacks(r.xml), {frozenset({JointRef(0, 1), JointRef(1, 0)})})
        rod = edit.describe(r.xml)["blocks"][3]
        self.assertEqual((rod["x"], rod["y"], rod["width"]), (80.0, 10.0, 60.0))

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
            got = edit._choose_jointed_to(st.design, st.graph, edit._identity_first_choice(st))
            self.assertIsNotNone(got, uid)
            with fccore.Sim(x) as a, fccore.Sim(got[0]) as b:
                self.assertEqual(a.record_array(100).tobytes(),
                                 b.record_array(100).tobytes(), uid)

    def test_delete_last_player_piece(self):
        n = 0
        for uid, path, *_ in self.cases[::5]:
            x = path.read_text()
            d = edit.describe(x)
            pieces = [b["index"] for b in d["blocks"] if not b["goal"]]
            if not pieces:
                continue
            r = edit.delete(x, pieces[-1])
            self.assertIsInstance(r, Ok, (uid, r))
            self.assertEqual(len(parse(r.xml).player_blocks), len(d["blocks"]) - 1)
            # untouched blocks keep their exact values
            before = parse(x).player_blocks
            after = parse(r.xml).player_blocks
            kept = [b for i, b in enumerate(before) if i != pieces[-1]]
            self.assertEqual([b.values for b in kept], [b.values for b in after], uid)
            n += 1
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()

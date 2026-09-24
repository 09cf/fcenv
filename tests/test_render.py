"""Pixel-level checks of fcenv.render and runtime frames."""

import io
import sys
import unittest
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fcenv import edit, render, run  # noqa: E402
from fcenv.design import JointRef  # noqa: E402
from tests.test_edit import GOAL, LEVEL  # noqa: E402

VIEW = (-100, -100, 300, 300)  # 1024 px across: 2.56 px per unit
SIZE = 1024


def colour(png, x, y, view=VIEW, size=SIZE):
    """(r, g, b) at world point (x, y) of a render with grid="none"."""
    s = cairo.ImageSurface.create_from_png(io.BytesIO(png))
    scale = size / max(view[2] - view[0], view[3] - view[1])
    px, py = int((x - view[0]) * scale), int((y - view[1]) * scale)
    data, stride = s.get_data(), s.get_stride()
    b, g, r = data[py * stride + 4 * px: py * stride + 4 * px + 3]
    return r, g, b


def rgb(argb):
    return (argb >> 16) & 255, (argb >> 8) & 255, argb & 255


def plain(xml, view=VIEW, **kw):
    return render.render(xml, view, SIZE, grid="none", labels="none", **kw).png


class TestColours(unittest.TestCase):
    def test_areas_pieces_and_sky(self):
        png = plain(LEVEL)
        self.assertEqual(colour(png, 250, 250), rgb(render._SKY))  # nothing there
        self.assertEqual(colour(png, 150, 150), rgb(render._BUILD_AREA[1]))
        self.assertEqual(colour(png, 0, 10), rgb(render._PALETTE[4][1]))  # goal rect fill
        self.assertEqual(colour(png, 0, 290), rgb(render._PALETTE[0][1]))  # floor fill

    def test_rod_and_joint(self):
        x = edit.add_rod(LEVEL, "wood", (60, 100), (180, 100)).xml
        png = plain(x)
        self.assertEqual(colour(png, 120, 100), rgb(render._PALETTE[12][1]))  # rod fill
        self.assertEqual(colour(png, 180, 103), rgb(render._JOINT))  # ring at its end

    def test_illegal_pieces_are_red(self):
        # an overlapping rod, made by hand: the edit API would refuse it
        rod = ('<SolidRod id="1"><rotation>0</rotation><position><x>0</x><y>0</y></position>'
               '<width>100</width><height>8</height><goalBlock>false</goalBlock><joints/>'
               '</SolidRod>')
        x = LEVEL.replace("</playerBlocks>", rod + "</playerBlocks>")
        self.assertEqual(colour(plain(x), 40, 0), (255, 128, 128))

    def test_overlapping_terrain_merges(self):
        # two overlapping static rects: no outline inside the overlap
        block = ('<StaticRectangle><rotation>0</rotation><position><x>{}</x><y>200</y>'
                 '</position><width>100</width><height>40</height><goalBlock>false'
                 '</goalBlock><joints/></StaticRectangle>')
        x = LEVEL.replace("<levelBlocks>", "<levelBlocks>" + block.format(150)
                          + block.format(220))
        png = plain(x)
        fill = rgb(render._PALETTE[0][1])
        self.assertEqual(colour(png, 198.5, 200), fill)  # where the first one's edge was
        self.assertEqual(colour(png, 171.5, 200), fill)  # and the second's
        self.assertEqual(colour(png, 101.5, 200), rgb(render._PALETTE[0][0]))  # outer edge


class TestLabels(unittest.TestCase):
    def test_labels_change_pixels_only_near_joints(self):
        x = edit.add_rod(LEVEL, "wood", (60, 100), (180, 100)).xml
        a = render.render(x, VIEW, SIZE, grid="none", labels="none").png
        b = render.render(x, VIEW, SIZE, grid="none", labels="joints").png
        self.assertNotEqual(colour(a, 187, 95), colour(b, 187, 95))  # label "1.1"
        self.assertEqual(colour(a, 120, 250), colour(b, 120, 250))  # far away


class TestRuntime(unittest.TestCase):
    def test_frames_show_motion(self):
        r = run.simulate(LEVEL, timeout=60)
        frame = r._view("level", 0, 0)
        size = 1024
        y0, y60 = r.state(0, GOAL)[1], r.state(60, GOAL)[1]
        self.assertGreater(y60 - y0, 100)  # the goal rect has fallen
        fill = rgb(render._PALETTE[4][1])
        a = r.frame(0, size=size, grid="none").png
        b = r.frame(60, size=size, grid="none").png
        self.assertEqual(colour(a, 0, y0, frame, size), fill)
        self.assertEqual(colour(b, 0, y60, frame, size), fill)
        self.assertNotEqual(colour(b, 0, y0, frame, size), fill)

    def test_motion_arrows(self):
        r = run.simulate(LEVEL, timeout=60)
        frame = r._view("level", 0, 0)
        still = r.frame(30, size=1024, grid="none").png
        moving = r.frame(30, size=1024, motion=True, grid="none").png
        # an arrow somewhere along the goal rect's path (thinning keeps one
        # of its five similar arrows, so scan its whole width)
        y30, y36 = int(r.state(30, GOAL)[1]), int(r.state(36, GOAL)[1])
        diff = sum(colour(still, x, y, frame) != colour(moving, x, y, frame)
                   for x in range(-25, 26, 2) for y in range(y30 - 25, y36 + 1, 2))
        self.assertGreater(diff, 0)


if __name__ == "__main__":
    unittest.main()

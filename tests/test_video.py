"""Tests for fcenv.video, on episodes recorded with the scripted client."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fcenv import video  # noqa: E402
from tests.test_harness import play  # noqa: E402

EPISODE = [
    [("view", {})],
    [("add_rod", {"kind": "wood", "a": "0.1", "b": {"from": "0.1", "offset": [60, 0]}}),
     ("delete", {"block": 0})],
    [("run", {"timeout": 40}), ("run_frame", {"tick": 10})],
    [("submit", {})],
]


class TestVideo(unittest.TestCase):
    def test_segments(self):
        _, out, _ = play(EPISODE)
        segs = list(video.segments(out, fps=10, hold=1, look=0.5, max_run=2))
        self.assertTrue(all((s.get_width(), s.get_height()) == (1280, 720) for s, _ in segs))
        # title (15), view (5), add_rod (10), rejected delete (10); the run's
        # 41 ticks in at most 20 frames (every 3rd, plus the last), then its
        # end held (20); run_frame (5); submit's run (up to 6000 ticks, sped
        # up to 20 frames or so), then its end held
        counts = [n for _, n in segs]
        self.assertEqual(counts[:4], [15, 5, 10, 10])
        self.assertEqual(counts[4:19], [1] * 15)
        self.assertEqual(counts[19], 20)  # the run's last frame, held
        self.assertEqual(counts[20], 5)  # run_frame: the image the agent saw
        self.assertEqual(sum(counts[21:-1]), 21)  # submit's run, sped up
        self.assertEqual(counts[-1], 20)

    def test_unsubmitted_episodes_end_with_the_evaluation(self):
        _, out, _ = play([[("describe", {})]], max_turns=1)
        segs = list(video.segments(out, fps=10, max_run=1))
        self.assertEqual([n for _, n in segs[:2]], [15, 6])
        self.assertGreater(len(segs), 3)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_write(self):
        _, out, _ = play(EPISODE)
        path = video.write(out, fps=10, hold=0.3, look=0.2, max_run=1)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,nb_frames",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
        w, h, n = probe.split(",")
        self.assertEqual((w, h), ("1280", "720"))
        self.assertGreater(int(n), 20)


if __name__ == "__main__":
    unittest.main()

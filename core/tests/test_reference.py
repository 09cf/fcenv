"""fccore must match fcsim bit-for-bit.

For every cached design, run fcsim's own run_single_design_xml and fccore's
fc_run with per-tick tracing in fcsim-compatible mode (--fcsim), and require
the traces (every design block's
position, velocity, angle and angular velocity at 17 significant digits, every
tick) and the final solve/end ticks to be byte-identical.

Needs: make, tests/build_reference.sh, tests/fetch_designs.py
"""

import os
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor

from common import FC_RUN, REFERENCE, cases, ticks_needed

# the reference logs every tick, so keep the window bounded
TRACE_TICKS = int(os.environ.get("TRACE_TICKS", "1500"))


def run(cmd, xml, ticks):
    r = subprocess.run(
        [str(cmd), str(ticks)] + (["--trace", "--fcsim"] if cmd == FC_RUN else []),
        input=xml,
        capture_output=True,
        timeout=600,
    )
    return r.returncode, r.stdout, r.stderr


def compare(case):
    uid, path, solve, max_ticks, _ = case
    xml = path.read_bytes()
    ticks = min(ticks_needed(solve, max_ticks), TRACE_TICKS)
    # fcsim's arena_init leaves its xml_level uninitialised, so a self-closing
    # <levelBlocks/> gives the reference a garbage list and it crashes; feed it
    # the equivalent open/close form instead
    ref = run(REFERENCE, xml.replace(b"<levelBlocks/>", b"<levelBlocks></levelBlocks>"), ticks)
    ours = run(FC_RUN, xml, ticks)
    if ref[0] != 0:
        return uid, "reference failed (skipped)"
    if ours[0] != 0:
        return uid, f"fc_run failed: {ours[2][:200]!r}"
    if ref[1] != ours[1]:
        return uid, f"result differs: ref={ref[1]!r} ours={ours[1]!r}"
    if ref[2] != ours[2]:
        a, b = ref[2].splitlines(), ours[2].splitlines()
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return uid, f"trace differs at line {i}: ref={a[i:i+1]} ours={b[i:i+1]}"
    return uid, None


class TestMatchesFcsim(unittest.TestCase):
    def test_traces_identical(self):
        for p in (FC_RUN, REFERENCE):
            self.assertTrue(p.exists(), f"missing {p}")
        cs = cases()
        self.assertGreater(len(cs), 0, "no cached designs; run tests/fetch_designs.py")
        with ThreadPoolExecutor(os.cpu_count()) as ex:
            results = list(ex.map(compare, cs))
        skipped = [u for u, e in results if e and "skipped" in e]
        failures = [(u, e) for u, e in results if e and "skipped" not in e]
        print(f"\n{len(cs) - len(failures) - len(skipped)}/{len(cs)} identical, "
              f"{len(skipped)} skipped (reference failed)")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()

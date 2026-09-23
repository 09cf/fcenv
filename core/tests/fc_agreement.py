"""Report how often fccore agrees with the original Flash game.

Ground truth is ftlib's fc_data.tsv: the solve tick each design achieved in
real FC (or a tick count it ran for without solving). Some gaps are known
(e.g. 9-joint wheels), so this is a report, not a pass/fail test. Pass
--binary build/reference/run_single_design_xml to score fcsim itself.

Designs listed in tests/data/excluded.tsv are reported separately.

usage: python3 tests/fc_agreement.py [-v] [--binary PATH]
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import FC_RUN, cases, excluded

# with no tick bound in the dataset, give up after this many ticks
FALLBACK_MAX_TICKS = 400_000


def run_ticks(solve, max_ticks):
    """Same tick budget as ftlib's test_single_design_fcsim."""
    t = []
    if solve:
        t.append(solve + 1000)
    if max_ticks:
        t.append(max_ticks)
    return min(t) if t else FALLBACK_MAX_TICKS


def check(binary, case):
    uid, path, solve, max_ticks, comment = case
    xml = path.read_bytes().replace(b"<levelBlocks/>", b"<levelBlocks></levelBlocks>")
    r = subprocess.run([str(binary), str(run_ticks(solve, max_ticks))], input=xml,
                       capture_output=True, timeout=7200)
    if r.returncode != 0:
        return uid, False, "error", comment
    got, end = (int(x) for x in r.stdout.split()[:2])
    # scoring as in ftlib: expected solve after the ticks we ran means
    # "must not solve"
    if solve is None or solve > end:
        ok = got == -1
        want = f"solve at {solve}" if solve else f"no solve within {end}"
    else:
        ok = got == solve
        want = f"solve at {solve}"
    return uid, ok, f"expected {want}, got {got if got >= 0 else 'no solve'}", comment


def main():
    verbose = "-v" in sys.argv
    binary = FC_RUN
    if "--binary" in sys.argv:
        binary = Path(sys.argv[sys.argv.index("--binary") + 1])
    skip = excluded()
    cs = cases()
    with ThreadPoolExecutor(os.cpu_count()) as ex:
        results = list(ex.map(lambda c: check(binary, c), cs))
    tracked = [r for r in results if r[0] not in skip]
    bad = [r for r in tracked if not r[1]]
    for uid, _, msg, comment in bad if verbose else []:
        print(f"MISMATCH {uid}: {msg}  {comment}")
    n = len(tracked)
    n_ex = len(results) - n
    ex_ok = sum(r[1] for r in results if r[0] in skip)
    print(f"{binary.name}: agree with real FC on {n - len(bad)}/{n} tracked designs "
          f"({100 * (n - len(bad)) / max(n, 1):.1f}%); "
          f"{n_ex} excluded (tests/data/excluded.tsv), of which {ex_ok} agree anyway")


if __name__ == "__main__":
    main()

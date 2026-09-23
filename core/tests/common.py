import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tests"))

from fetch_designs import CACHE, load_rows  # noqa: E402

FC_RUN = ROOT / "build" / "fc_run"
REFERENCE = ROOT / "build" / "reference" / "run_single_design_xml"


def cases():
    """(uid, xml_path, fc_solve_tick, fc_max_ticks, comment) for cached designs."""
    out = []
    for uid, solve, max_ticks, comment in load_rows():
        path = CACHE / f"{uid}.xml"
        if path.exists() and path.stat().st_size > 0:
            out.append((uid, path, solve, max_ticks, comment))
    return out


def excluded():
    """{uid: reason} for designs excluded from agreement tracking
    (tests/data/excluded.tsv)."""
    out = {}
    for line in (ROOT / "tests" / "data" / "excluded.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            uid, reason, *_ = line.split("\t")
            out[uid] = reason
    return out


def ticks_needed(solve, max_ticks):
    """Ticks to run to decide a case: just past the FC solve tick, or max_ticks."""
    return max(solve + 1 if solve else 0, max_ticks or 0)

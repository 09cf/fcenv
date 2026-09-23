"""Fetch and cache the FC designs listed in tests/data/fc_data.tsv.

fc_data.tsv comes from ftlib (github.com/evenifyouforget/ftlib, test/fc_data.tsv)
and records the solve tick each design achieved in the original Flash game.

Designs are cached under tests/data/cache/ (gitignored). Already-cached
entries are skipped, so this is safe to rerun.
"""

import csv
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
CACHE = DATA / "cache"
URL = "http://www.fantasticcontraption.com/retrieveLevel.php"


def extract_id(s):
    m = re.search(r"(?:Id=)?(\d+)", s or "")
    return int(m.group(1)) if m else None


def load_rows():
    with open(DATA / "fc_data.tsv", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            row = row + [""] * 8
            level_id, design_id = extract_id(row[0]), extract_id(row[1])
            uid = f"D{design_id}" if design_id else f"L{level_id}"
            solve = int(row[2]) if row[2] else None
            max_ticks = int(row[3]) if row[3] else None
            yield uid, solve, max_ticks, row[4]


def fetch(uid):
    params = {"id": uid[1:]}
    if uid[0] == "D":
        params["loadDesign"] = "1"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(URL, data=data, timeout=30) as r:
        return r.read()


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = list(load_rows())[:limit]
    for i, (uid, *_rest) in enumerate(rows):
        path = CACHE / f"{uid}.xml"
        if path.exists():
            continue
        try:
            body = fetch(uid)
        except Exception as e:
            print(f"{uid}: fetch failed: {e}", file=sys.stderr)
            continue
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.rename(path)
        print(f"[{i + 1}/{len(rows)}] {uid}", flush=True)
        time.sleep(0.2)


if __name__ == "__main__":
    main()

"""Getting levels in and designs out.

fetch() talks to fantasticcontraption.com, for setting up an environment
(e.g. saving level files ahead of time); nothing else here uses the network.
from_decomp() reads the game's own levels out of the decompiled SWF.

    python3 -m fcenv.levels decomp/scripts/contraption/data levels/
"""

import json
import re
import sys
from pathlib import Path
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

_RETRIEVE = "http://www.fantasticcontraption.com/retrieveLevel.php"


def parse_link(link):
    """("level" | "design", id) from a link containing levelId= or designId=
    (FC's own links, fcsim's), or a bare id with a prefix like "L646726"."""
    m = re.search(r"(level|design)Id=(\d+)", link, re.IGNORECASE)
    if m:
        return m.group(1).lower(), int(m.group(2))
    m = re.fullmatch(r"\s*([LD])(\d+)\s*", link, re.IGNORECASE)
    if m:
        return ("level" if m.group(1).upper() == "L" else "design"), int(m.group(2))
    raise ValueError(f"no levelId or designId in {link!r}")


def fetch(link=None, level_id=None, design_id=None, timeout=30):
    """The XML of a level or design from fantasticcontraption.com, given a
    link (see parse_link) or an id."""
    if link is not None:
        kind, n = parse_link(link)
        level_id, design_id = (n, None) if kind == "level" else (None, n)
    params = {"id": design_id if design_id is not None else level_id}
    if design_id is not None:
        params["loadDesign"] = "1"
    if params["id"] is None:
        raise ValueError("give a link, level_id or design_id")
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(_RETRIEVE, data=data, timeout=timeout) as r:
        return r.read().decode()


def to_save_design(xml, name="", description="", user="", level_id=None):
    """The design in FC's saveDesign format (as FC and fcsim export it). The
    <level> element is copied verbatim; level_id defaults to the XML's."""
    if isinstance(xml, bytes):
        xml = xml.decode()
    level = re.search(r"<level>.*</level>", xml, re.DOTALL)
    if not level:
        raise ValueError("no <level> element")
    if level_id is None:
        m = re.search(r"<levelId>\s*(\d*)\s*</levelId>", xml)
        level_id = m.group(1) if m else ""
    return (
        '<?xml version="1.0"?><saveDesign>'
        f"<name>{escape(name)}</name><description>{escape(description)}</description>"
        f"<userId>{escape(str(user))}</userId><levelId>{level_id}</levelId>"
        f"<isSolution>0</isSolution>{level.group(0)}</saveDesign>"
    )


def from_decomp(data_dir):
    """The official and tutorial levels built into the game, from the
    decompiled contraption/data/{Official,Tutorial}Levels.as: a list of dicts
    with set ("official" or "tutorial"), number, level_id, name, paid_only
    and xml (the <retrieveLevel> element, as the server sends it)."""
    out = []
    for kind, f in (("tutorial", "TutorialLevels.as"), ("official", "OfficialLevels.as")):
        text = (Path(data_dir) / f).read_text()
        for x in re.findall(r"<retrieveLevel>.*?</retrieveLevel>", text, re.DOTALL):
            # the decompiler unescapes names ("Pit & Pendulum")
            x = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+);)", "&amp;", x)
            x = "\n".join(line for line in x.splitlines() if line.strip()) + "\n"
            number = int(re.search(r"<(?:level|tutorial)Number>(\d+)<", x).group(1))
            name = re.search(r"<name>(.*?)</name>", x).group(1)
            out.append({"set": kind, "number": number,
                         "level_id": int(re.search(r"<levelId>(\d+)<", x).group(1)),
                         "name": name.replace("&amp;", "&"),
                         # OfficialLevels.isPaidOnly
                         "paid_only": kind == "official" and number > 21, "xml": x})
    return out


def write_decomp_levels(data_dir, out_dir):
    """Write from_decomp()'s levels as out_dir/{official,tutorial}/NN-name.xml,
    plus out_dir/index.json (everything but the XML, with the file name, the
    number of goal pieces and of pre-built player pieces)."""
    from .design import parse
    out_dir = Path(out_dir)
    index = []
    for lv in from_decomp(data_dir):
        slug = re.sub(r"[^a-z0-9]+", "-", lv["name"].lower()).strip("-")
        path = Path(lv["set"]) / f"{lv['number']:02d}-{slug}.xml"
        (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / path).write_text(lv["xml"])
        d = parse(lv["xml"])
        goals = sum(b.goal for b in d.player_blocks)
        index.append({k: v for k, v in lv.items() if k != "xml"} | {
            "file": str(path), "goal_pieces": goals,
            "prebuilt_pieces": len(d.player_blocks) - goals})
    (out_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    return index


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python3 -m fcenv.levels DECOMP_DATA_DIR OUT_DIR")
    print(f"{len(write_decomp_levels(*sys.argv[1:]))} levels written to {sys.argv[2]}")

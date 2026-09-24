"""Getting levels in and designs out.

fetch() talks to fantasticcontraption.com, for setting up an environment
(e.g. saving level files ahead of time); nothing else here uses the network.
"""

import re
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

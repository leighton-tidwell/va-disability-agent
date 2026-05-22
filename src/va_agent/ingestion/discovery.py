"""Auto-discovery of Diagnostic Code identifiers in a CFR section's XML.

Hand-curated DC lists rot the moment the CFR is amended. The §4.71a table
encodes a DC header as a ``<TR>`` whose first ``<TD>`` text starts with a
4-digit number. We scan once and trust the source.
"""

from __future__ import annotations

import re

from lxml import etree

DC_HEADER_RE = re.compile(r"^\s*(\d{4})\b")


def discover_dc_codes(xml_text: str) -> list[str]:
    """Return Diagnostic Code identifiers in the order they appear in the XML.

    The eCFR §4.71a representation places each DC on its own ``<TR>`` row whose
    first ``<TD>`` text begins with the 4-digit DC number (often followed by a
    title fragment). Subsequent rating-tier rows do *not* start with 4 digits,
    so a simple regex on the leading token of each row's first cell is enough
    to enumerate every DC without missing tiers or double-counting.

    Duplicates are de-duplicated while preserving first-seen order — a defence
    in depth against any future XML structure where a DC's text spills into a
    second header-like row.
    """
    root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    seen: set[str] = set()
    codes: list[str] = []
    for tr in root.findall(".//TR"):
        tds = tr.findall("TD")
        if not tds:
            continue
        left = "".join(tds[0].itertext()).strip()
        if not left:
            continue
        match = DC_HEADER_RE.match(left)
        if not match:
            continue
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes

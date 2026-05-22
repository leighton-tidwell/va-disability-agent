"""Tests for Diagnostic Code auto-discovery against the cached §4.71a XML."""

from __future__ import annotations

from pathlib import Path

import pytest

from va_agent.ingestion.discovery import discover_dc_codes

CACHED_XML = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ecfr_cache"
    / "2025-01-01-t38-p4-s4.71a.xml"
)


@pytest.fixture(scope="module")
def section_xml() -> str:
    if not CACHED_XML.exists():
        pytest.skip(f"cached §4.71a XML missing at {CACHED_XML}")
    return CACHED_XML.read_text(encoding="utf-8")


def test_discovers_dc_5260(section_xml: str) -> None:
    codes = discover_dc_codes(section_xml)
    assert "5260" in codes, "tracer DC must be discoverable"


def test_discovers_many_dcs(section_xml: str) -> None:
    codes = discover_dc_codes(section_xml)
    # §4.71a defines well over 100 DCs in the 5000-series; assert a sane floor.
    assert len(codes) > 30, f"expected >30 DCs, got {len(codes)}"


def test_codes_are_unique(section_xml: str) -> None:
    codes = discover_dc_codes(section_xml)
    assert len(codes) == len(set(codes)), "discovered codes must be unique"


def test_codes_are_four_digits(section_xml: str) -> None:
    codes = discover_dc_codes(section_xml)
    assert all(c.isdigit() and len(c) == 4 for c in codes), codes


def test_empty_xml_yields_empty_list() -> None:
    assert discover_dc_codes("<DIV1></DIV1>") == []


def test_only_real_dc_rows_count() -> None:
    # A row whose first TD starts with a non-4-digit token should be ignored.
    xml = (
        "<DIV1><TABLE>"
        "<TR><TD>5260 Leg, limitation of flexion of:</TD><TD></TD></TR>"
        "<TR><TD>Flexion limited to 45°</TD><TD>10</TD></TR>"
        "<TR><TD>5261 Leg, limitation of extension of:</TD><TD></TD></TR>"
        "<TR><TD>Note: see §4.71a</TD><TD></TD></TR>"
        "</TABLE></DIV1>"
    )
    assert discover_dc_codes(xml) == ["5260", "5261"]

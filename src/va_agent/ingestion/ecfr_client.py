"""Fetch CFR section XML from the eCFR API, with on-disk caching.

The eCFR API ``/api/versioner/v1/full/{date}/title-{N}.xml`` accepts query
parameters to scope down to a specific part / section.

We cache raw responses under ``data/ecfr_cache/`` keyed by (date, section) so
re-runs don't hit the network.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import httpx

ECFR_BASE = "https://www.ecfr.gov/api/versioner/v1/full"

# eCFR's `/full/{date}` endpoint returns content as it existed on the given
# date. Future dates 404. We default to a known-good recent date rather than
# date.today() so callers don't get bitten by it. Slice #13 will resolve this
# properly by querying the versioner's amendment-dates endpoint.
DEFAULT_RETRIEVAL_DATE = date(2025, 1, 1)


def cache_dir(project_root: Path) -> Path:
    d = project_root / "data" / "ecfr_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(retrieval_date: date, title: int, part: str, section: str | None) -> str:
    key = f"{retrieval_date.isoformat()}-t{title}-p{part}-s{section or 'ALL'}"
    return key.replace("/", "_").replace("§", "")


def fetch_section_xml(
    *,
    title: int = 38,
    part: str = "4",
    section: str,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
    timeout_s: float = 30.0,
) -> tuple[str, Path]:
    """Fetch section XML; return (xml_text, cache_path).

    ``section`` is the section number including subdivision letter (e.g. "4.71a").
    """
    retrieval_date = retrieval_date or DEFAULT_RETRIEVAL_DATE
    project_root = project_root or Path(__file__).resolve().parents[3]
    cache = cache_dir(project_root) / f"{_cache_key(retrieval_date, title, part, section)}.xml"
    if cache.exists():
        return cache.read_text(encoding="utf-8"), cache

    url = f"{ECFR_BASE}/{retrieval_date.isoformat()}/title-{title}.xml"
    params: dict[str, str] = {"part": part}
    if section:
        params["section"] = section
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        text = resp.text
    cache.write_text(text, encoding="utf-8")
    return text, cache


def content_hash(text: str) -> str:
    """Stable SHA-256 of the source text — stored on every CFR-derived node."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

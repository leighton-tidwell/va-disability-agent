"""LLM-driven extraction of DiagnosticCodeExtractions from raw CFR text.

The LLM is constrained by Pydantic via LangChain's ``with_structured_output``.
For the v1 tracer we extract one Diagnostic Code at a time — broader bulk
extraction over a full section is added when slice #5 lands.
"""

from __future__ import annotations

from typing import Protocol

from langchain_openai import ChatOpenAI
from lxml import etree

from .schemas import DiagnosticCodeExtraction

EXTRACTION_INSTRUCTIONS = """\
You are extracting a single VA Diagnostic Code from 38 CFR Part 4 text.

Rules:
- Use the exact 4-digit DC number that appears in the source (e.g. "5260").
- "body_system" must be one of: musculoskeletal, mental, hearing, respiratory,
  cardiovascular, digestive, skin, neurological, endocrine, eye, dental,
  genitourinary, hemic-lymphatic, gynecological, general.
- "section" is the CFR section the DC lives in (e.g. "4.71a").
- "rating_levels" must include every percent listed in the source. Use only
  these percents: 0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100.
- For each rating level, copy the criterion text verbatim into criteria[].text.
- Where a criterion contains a numeric threshold (e.g. "Flexion limited to 45°"),
  populate criteria[].measurements with the structured form:
    name="flexion", body_part="knee", operator="<=", value=45, unit="degrees".
  Use operator "<=" for "limited to N" / "not greater than N", ">=" for
  "at least N", "=" for exact matches.
- If a criterion has no numeric threshold (e.g. "Slight subluxation"), leave
  measurements as an empty list — do NOT invent numbers.
- Capture cross-references like "DC 5003" or "§4.59" in cross_references.
- Capture verbatim Note (1), Note (2), etc. in notes.
- raw_text MUST be the verbatim source text you were given.
"""


class StructuredExtractor(Protocol):
    """Anything that can take raw CFR text and return a DiagnosticCodeExtraction.

    The Protocol exists so tests can inject a fake extractor without touching
    the OpenAI API.
    """

    def extract(self, raw_text: str) -> DiagnosticCodeExtraction: ...


class OpenAIExtractor:
    """LangChain + ChatOpenAI with Pydantic-bound structured output."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0) -> None:
        self._llm = ChatOpenAI(model=model, temperature=temperature).with_structured_output(
            DiagnosticCodeExtraction
        )

    def extract(self, raw_text: str) -> DiagnosticCodeExtraction:
        result = self._llm.invoke(
            [
                ("system", EXTRACTION_INSTRUCTIONS),
                ("user", raw_text),
            ]
        )
        return result  # type: ignore[return-value]


def extract_dc_text(xml_text: str, dc_code: str) -> str:
    """Locate a Diagnostic Code's text block within a section's XML.

    eCFR represents §4.71a as a `<TABLE>` of `<TR>` rows. The header row for a
    DC is a TR whose first TD starts with the 4-digit DC code; subsequent
    rows hold the criterion text (left TD) and rating percent (right TD)
    until the next DC header row. Also captures rows whose left TD starts
    with "Note" — those are part of the DC.

    Falls back to scanning `<P>` paragraphs for sections (like §4.1–§4.31)
    that aren't formatted as tables.
    """
    root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)

    block = _extract_from_table_rows(root, dc_code)
    if block is None:
        block = _extract_from_paragraphs(root, dc_code)
    if block is None:
        raise ValueError(f"DC {dc_code} not found in section XML")
    return block


def _row_cells(tr) -> list[str]:
    return ["".join(td.itertext()).strip() for td in tr.findall("TD")]


def _looks_like_dc_header_text(text: str) -> bool:
    head = text.lstrip()[:5]
    return len(head) >= 4 and head[:4].isdigit()


def _extract_from_table_rows(root, dc_code: str) -> str | None:
    rows = root.findall(".//TR")
    collected_lines: list[str] = []
    capturing = False
    for tr in rows:
        cells = _row_cells(tr)
        if not cells:
            continue
        left = cells[0]
        if not left:
            continue
        starts_with_target = left.lstrip().startswith(dc_code)
        if starts_with_target:
            if capturing:
                # Hit the next DC header that also matches our code — stop.
                break
            capturing = True
            collected_lines.append(left)
            continue
        if capturing and _looks_like_dc_header_text(left) and not starts_with_target:
            break
        if capturing:
            right = cells[1] if len(cells) > 1 else ""
            if right:
                collected_lines.append(f"{left} — {right}%")
            else:
                collected_lines.append(left)
    if not collected_lines:
        return None
    return "\n".join(collected_lines)


def _extract_from_paragraphs(root, dc_code: str) -> str | None:
    paragraphs = root.findall(".//P")
    collected: list[str] = []
    capturing = False
    for p in paragraphs:
        text = "".join(p.itertext()).strip()
        if not text:
            continue
        starts = text.lstrip().startswith(dc_code)
        if starts:
            if capturing:
                break
            capturing = True
            collected.append(text)
            continue
        if capturing and _looks_like_dc_header_text(text) and not starts:
            break
        if capturing:
            collected.append(text)
    if not collected:
        return None
    return "\n\n".join(collected)

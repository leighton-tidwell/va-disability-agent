"""LLM-driven extraction of DiagnosticCodeExtractions from raw CFR text.

The LLM is constrained by Pydantic via LangChain's ``with_structured_output``.
For the v1 tracer we extract one Diagnostic Code at a time — broader bulk
extraction over a full section is added when slice #5 lands.
"""

from __future__ import annotations

from typing import Protocol

from langchain_openai import ChatOpenAI
from lxml import etree

from .schemas import DiagnosticCodeExtraction, RuleExtraction

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


RULE_EXTRACTION_INSTRUCTIONS = """\
You are extracting a single general-provisions rule from 38 CFR Part 4,
§4.1 through §4.31. These are prose rules — NOT Diagnostic Codes.

Important rules in this range include:
- §4.14 Pyramiding (no double-rating the same symptom)
- §4.25 Combined Ratings Table (how Rating Percentages combine)
- §4.26 Bilateral Factor (the matching-limbs uplift)
- §4.40 Functional Loss, §4.45 Joints, §4.59 Painful motion

For the source you are given:
- "id" must be a short snake_case identifier (e.g. "pyramiding",
  "bilateral_factor", "combined_ratings_table", "functional_loss").
- "name" is the human-readable rule title from the section heading
  (e.g. "Pyramiding"). Strip the leading "§ 4.NN" if present.
- "text" is the verbatim CFR rule body — keep it intact, do not paraphrase.
- "body_system" defaults to "general" — only change it if the rule is
  scoped to a specific body system in the text itself.
- "section" is the CFR section number (e.g. "4.14"), no leading "§".
- "applies_to" should list scopes the rule explicitly references — body
  system names, DC codes ("5260"), or section refs ("4.71a"). Leave empty
  for global rules.
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


class RuleExtractor(Protocol):
    """Protocol for extractors that turn a section's prose into a RuleExtraction.

    Mirrors ``StructuredExtractor`` but carries the section identifier so the
    LLM can populate it without re-derivation.
    """

    def extract(self, raw_text: str, *, section: str) -> RuleExtraction: ...


class OpenAIRuleExtractor:
    """LangChain + ChatOpenAI with RuleExtraction-bound structured output."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0) -> None:
        self._llm = ChatOpenAI(model=model, temperature=temperature).with_structured_output(
            RuleExtraction
        )

    def extract(self, raw_text: str, *, section: str) -> RuleExtraction:
        result = self._llm.invoke(
            [
                ("system", RULE_EXTRACTION_INSTRUCTIONS),
                ("user", f"Section: {section}\n\n{raw_text}"),
            ]
        )
        return result  # type: ignore[return-value]


def extract_section_text(xml_text: str) -> tuple[str, str]:
    """Pull the heading and body text out of a §4.X section's XML.

    Returns ``(heading, body)`` where heading is the ``<HEAD>`` text (e.g.
    "§ 4.14 Avoidance of pyramiding.") and body is the concatenation of all
    ``<P>`` paragraphs in the section, separated by blank lines.
    """
    root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    head_el = root.find(".//HEAD")
    heading = "".join(head_el.itertext()).strip() if head_el is not None else ""
    body_parts = [
        "".join(p.itertext()).strip()
        for p in root.findall(".//P")
        if "".join(p.itertext()).strip()
    ]
    return heading, "\n\n".join(body_parts)


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

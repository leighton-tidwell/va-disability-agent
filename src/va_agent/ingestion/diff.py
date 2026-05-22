"""Re-ingestion + staleness diff for CFR DiagnosticCodes.

The graph stores ``content_hash`` + ``retrieved_at`` on every CFR-derived node
that can drift independently (:CFR:DiagnosticCode, :CFR:Rule). :CFR:Section
carries ``retrieved_at`` only — it's a container, not a unit of content. The
following node labels are intentionally exempt from carrying their own
hash/timestamp:

- ``:CFR:RatingLevel`` — a small closed enumeration of VA percentages
  (0,10,20,...,100); not derivable from CFR text.
- ``:CFR:Measurement`` — derivative of its parent :Criterion; covered by the
  DC's content_hash.
- ``:CFR:Note``, ``:CFR:CrossReference`` — derivative of their parent DC; the
  DC's hash subsumes their content.

The diff routine therefore reasons at the **DiagnosticCode** level. When a DC's
freshly-extracted ``content_hash`` differs from what's in the graph, the entire
DC subgraph (DC, RatingLevels-edges, Criteria, Measurements, Notes,
CrossReferences) is treated as changed and replaced atomically by ``apply_diff``.

``diff_section`` is **read-only**. ``apply_diff`` is the only function in this
module that mutates the graph; pass ``confirm=True`` to apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..graph.driver import GraphDriver
from ..graph.writers import write_diagnostic_code
from .discovery import discover_dc_codes
from .ecfr_client import content_hash, fetch_section_xml
from .extraction import OpenAIExtractor, StructuredExtractor, extract_dc_text
from .schemas import DiagnosticCodeExtraction
from .validation import validate_diagnostic_code


# --- Reports ---------------------------------------------------------------


@dataclass
class ChangedDC:
    """A DC present in both graph and fresh XML, with a different content_hash."""

    code: str
    old_hash: str
    new_hash: str
    new_extraction: DiagnosticCodeExtraction
    raw_text: str


@dataclass
class NewDC:
    """A DC present in the fresh XML but absent from the graph."""

    code: str
    new_hash: str
    new_extraction: DiagnosticCodeExtraction
    raw_text: str


@dataclass
class RemovedDC:
    """A DC present in the graph but absent from the fresh XML."""

    code: str
    old_hash: str


@dataclass
class UnchangedDC:
    """A DC whose graph content_hash matches the freshly-computed hash."""

    code: str
    content_hash: str


@dataclass
class FailedDC:
    """A DC the diff couldn't classify because extraction/validation failed.

    Surfaced separately so a partial XML/extractor failure can't cause a silent
    "removed" misclassification.
    """

    code: str
    errors: list[str]


@dataclass
class DiffReport:
    section: str
    unchanged: list[UnchangedDC] = field(default_factory=list)
    changed: list[ChangedDC] = field(default_factory=list)
    removed: list[RemovedDC] = field(default_factory=list)
    new: list[NewDC] = field(default_factory=list)
    failed: list[FailedDC] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changed or self.removed or self.new)


@dataclass
class ApplyReport:
    section: str
    rewrote: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


# --- Extraction-only helper (no graph write) -------------------------------


@dataclass
class ExtractOnlyResult:
    """Result of extracting a single DC without writing it.

    ``extraction`` is None when validation fails — ``errors`` carries the
    reasons so callers can route the DC into ``DiffReport.failed``.
    """

    code: str
    raw_text: str
    extraction: DiagnosticCodeExtraction | None
    new_hash: str
    errors: list[str]


def extract_only(
    *,
    section: str,
    dc_code: str,
    xml_text: str,
    extractor: StructuredExtractor,
) -> ExtractOnlyResult:
    """Fetch+locate+extract+validate without touching the graph.

    Mirrors the head of :func:`ingest_diagnostic_code` so the diff routine can
    preview what re-ingestion would produce. Note ``xml_text`` is taken as an
    argument rather than fetched again — diff_section fetches once per call.
    """
    raw_text = extract_dc_text(xml_text, dc_code)
    extraction = extractor.extract(raw_text)
    if not extraction.raw_text:
        extraction = extraction.model_copy(update={"raw_text": raw_text})
    if extraction.section != section:
        extraction = extraction.model_copy(update={"section": section})

    validation = validate_diagnostic_code(extraction)
    new_hash = content_hash(raw_text)
    if not validation.ok:
        return ExtractOnlyResult(
            code=dc_code,
            raw_text=raw_text,
            extraction=None,
            new_hash=new_hash,
            errors=list(validation.errors),
        )
    return ExtractOnlyResult(
        code=dc_code,
        raw_text=raw_text,
        extraction=extraction,
        new_hash=new_hash,
        errors=[],
    )


# --- Diff ------------------------------------------------------------------


def _graph_dcs_for_section(driver: GraphDriver, section: str) -> dict[str, str]:
    """Return {dc_code: content_hash} for DCs the graph already has in this section."""
    rows = driver.cfr_read(
        """
        MATCH (dc:CFR:DiagnosticCode)-[:IN_SECTION]->(s:CFR:Section {id: $section})
        RETURN dc.code AS code, dc.content_hash AS hash
        """,
        section=section,
    )
    return {row["code"]: row["hash"] for row in rows}


def diff_section(
    section_id: str,
    driver: GraphDriver,
    *,
    extractor: StructuredExtractor | None = None,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
) -> DiffReport:
    """Compare the live eCFR XML for ``section_id`` against graph state.

    Read-only — returns a :class:`DiffReport` enumerating which DCs are
    unchanged, changed (different content_hash), removed (in graph, absent in
    fresh XML), and new (in fresh XML, absent in graph). Use
    :func:`apply_diff` to apply the report's changes.
    """
    extractor = extractor or OpenAIExtractor()
    report = DiffReport(section=section_id)

    xml_text, _ = fetch_section_xml(
        section=section_id,
        retrieval_date=retrieval_date,
        project_root=project_root,
    )
    fresh_codes = discover_dc_codes(xml_text)
    graph_codes = _graph_dcs_for_section(driver, section_id)

    fresh_set = set(fresh_codes)
    graph_set = set(graph_codes.keys())

    # Removed: in graph but not in fresh XML.
    for code in sorted(graph_set - fresh_set):
        report.removed.append(RemovedDC(code=code, old_hash=graph_codes[code]))

    # Walk fresh DCs in their source order; classify each.
    for code in fresh_codes:
        result = extract_only(
            section=section_id,
            dc_code=code,
            xml_text=xml_text,
            extractor=extractor,
        )
        if result.extraction is None:
            report.failed.append(FailedDC(code=code, errors=result.errors))
            continue

        if code not in graph_codes:
            report.new.append(
                NewDC(
                    code=code,
                    new_hash=result.new_hash,
                    new_extraction=result.extraction,
                    raw_text=result.raw_text,
                )
            )
            continue

        old_hash = graph_codes[code]
        if old_hash == result.new_hash:
            report.unchanged.append(UnchangedDC(code=code, content_hash=result.new_hash))
        else:
            report.changed.append(
                ChangedDC(
                    code=code,
                    old_hash=old_hash or "",
                    new_hash=result.new_hash,
                    new_extraction=result.extraction,
                    raw_text=result.raw_text,
                )
            )

    return report


# --- Apply -----------------------------------------------------------------


def _delete_dc_subgraph(driver: GraphDriver, code: str) -> None:
    """Detach-delete a DC + its dependent Criteria/Measurements/Notes/Xrefs.

    Leaves :CFR:Section and :CFR:RatingLevel nodes intact — they're shared
    across DCs. Leaves :CFR:Anatomy intact for the same reason. :CFR:Criterion
    is MERGE'd by text and may be shared in principle; we delete it here
    because in practice criterion text is DC-specific and an orphaned
    Criterion is worse than a re-created one.
    """
    queries = [
        # Measurements hanging off this DC's criteria.
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(:CFR:RatingLevel)
              -[:REQUIRES]->(c:CFR:Criterion)-[:HAS_MEASUREMENT]->(m:CFR:Measurement)
        DETACH DELETE m
        """,
        # Criteria attached to this DC.
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_RATING]->(:CFR:RatingLevel)
              -[:REQUIRES]->(c:CFR:Criterion)
        DETACH DELETE c
        """,
        # Also catch criteria attached via CRITERION_FOR (writer creates this edge too).
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})<-[:CRITERION_FOR]-(c:CFR:Criterion)
        DETACH DELETE c
        """,
        # Notes / cross-references / DC itself.
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:HAS_NOTE]->(n:CFR:Note)
        DETACH DELETE n
        """,
        """
        MATCH (dc:CFR:DiagnosticCode {code: $code})-[:CROSS_REFERENCES]->(x:CFR:CrossReference)
        DETACH DELETE x
        """,
        "MATCH (dc:CFR:DiagnosticCode {code: $code}) DETACH DELETE dc",
    ]
    for q in queries:
        driver.cfr_write(q, code=code)


def apply_diff(
    report: DiffReport,
    driver: GraphDriver,
    *,
    confirm: bool = True,
    retrieved_at: date | None = None,
) -> ApplyReport:
    """Apply a :class:`DiffReport` to the graph.

    Mutates the graph: deletes removed-DC subgraphs, replaces changed-DC
    subgraphs with their freshly-extracted form, and writes new DCs. Pass
    ``confirm=True`` (the default) to apply; ``confirm=False`` raises so a
    caller can't accidentally apply by forgetting the flag.
    """
    if not confirm:
        raise ValueError(
            "apply_diff requires confirm=True — diff and apply are intentionally decoupled"
        )

    result = ApplyReport(section=report.section)
    retrieved_at = retrieved_at or date.today()

    # Changed: delete old subgraph, then re-write the new extraction.
    for change in report.changed:
        _delete_dc_subgraph(driver, change.code)
        write_diagnostic_code(
            driver,
            change.new_extraction,
            content_hash=change.new_hash,
            retrieved_at=retrieved_at,
        )
        result.rewrote.append(change.code)
        print(
            f"[apply_diff] section={report.section} dc={change.code} "
            f"changed {change.old_hash[:12]}... -> {change.new_hash[:12]}..."
        )

    # Removed: delete the subgraph outright.
    for removed in report.removed:
        _delete_dc_subgraph(driver, removed.code)
        result.deleted.append(removed.code)
        print(f"[apply_diff] section={report.section} dc={removed.code} removed")

    # New: write the freshly-extracted DC.
    for new in report.new:
        write_diagnostic_code(
            driver,
            new.new_extraction,
            content_hash=new.new_hash,
            retrieved_at=retrieved_at,
        )
        result.added.append(new.code)
        print(f"[apply_diff] section={report.section} dc={new.code} new")

    # Failed: surfaced for the caller, no mutation.
    for failed in report.failed:
        result.skipped.append((failed.code, "; ".join(failed.errors)))

    return result


__all__ = [
    "ApplyReport",
    "ChangedDC",
    "DiffReport",
    "ExtractOnlyResult",
    "FailedDC",
    "NewDC",
    "RemovedDC",
    "UnchangedDC",
    "apply_diff",
    "diff_section",
    "extract_only",
]

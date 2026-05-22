"""Orchestrator: fetch CFR XML → locate DC text → LLM extract → validate →
write to graph (or route to the review queue on failure)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..graph.driver import GraphDriver
from ..graph.writers import (
    apply_anatomy_metadata,
    resolve_cross_references,
    write_diagnostic_code,
    write_rule,
)
from .discovery import discover_dc_codes
from .ecfr_client import content_hash, fetch_section_xml
from .extraction import (
    OpenAIExtractor,
    OpenAIRuleExtractor,
    RuleExtractor,
    StructuredExtractor,
    extract_dc_text,
    extract_section_text,
)
from .schemas import DiagnosticCodeExtraction, RuleExtraction
from .validation import ValidationResult, validate_diagnostic_code, validate_rule


@dataclass
class IngestionReport:
    """What happened during one ingestion call.

    Tracks both Diagnostic-Code ingestion (§4.71a-style sections) and Rule
    ingestion (§4.1–§4.31 general provisions) — the two paths share the same
    report shape so a bulk run can merge them seamlessly.
    """

    section: str
    dc_codes_attempted: list[str] = field(default_factory=list)
    dc_codes_written: list[str] = field(default_factory=list)
    dc_codes_failed: list[tuple[str, list[str]]] = field(default_factory=list)
    rules_attempted: list[str] = field(default_factory=list)
    rules_written: list[str] = field(default_factory=list)
    rules_failed: list[tuple[str, list[str]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "IngestionReport") -> None:
        self.dc_codes_attempted.extend(other.dc_codes_attempted)
        self.dc_codes_written.extend(other.dc_codes_written)
        self.dc_codes_failed.extend(other.dc_codes_failed)
        self.rules_attempted.extend(other.rules_attempted)
        self.rules_written.extend(other.rules_written)
        self.rules_failed.extend(other.rules_failed)
        self.warnings.extend(other.warnings)


def review_queue_path(project_root: Path | None = None) -> Path:
    project_root = project_root or Path(__file__).resolve().parents[3]
    p = project_root / "data" / "review_queue.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _record_failure(path: Path, section: str, dc_code: str, raw_text: str, errors: list[str]) -> None:
    entry = {
        "section": section,
        "dc_code": dc_code,
        "raw_text": raw_text,
        "errors": errors,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _record_rule_failure(
    path: Path, section: str, rule_id: str, raw_text: str, errors: list[str]
) -> None:
    entry = {
        "section": section,
        "rule_id": rule_id,
        "kind": "rule",
        "raw_text": raw_text,
        "errors": errors,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def ingest_diagnostic_code(
    *,
    section: str,
    dc_code: str,
    driver: GraphDriver,
    extractor: StructuredExtractor | None = None,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
) -> IngestionReport:
    """v1 tracer entry point: fetch, locate, extract, validate, write one DC."""
    report = IngestionReport(section=section)
    report.dc_codes_attempted.append(dc_code)
    extractor = extractor or OpenAIExtractor()

    xml_text, _cache_path = fetch_section_xml(
        section=section,
        retrieval_date=retrieval_date,
        project_root=project_root,
    )
    raw_text = extract_dc_text(xml_text, dc_code)
    extraction = extractor.extract(raw_text)
    # The LLM sometimes omits raw_text or section; restore from inputs.
    if not extraction.raw_text:
        extraction = extraction.model_copy(update={"raw_text": raw_text})
    if extraction.section != section:
        extraction = extraction.model_copy(update={"section": section})

    validation = validate_diagnostic_code(extraction)
    report.warnings.extend(validation.warnings)
    if not validation.ok:
        _record_failure(
            review_queue_path(project_root),
            section,
            dc_code,
            raw_text,
            validation.errors,
        )
        report.dc_codes_failed.append((dc_code, list(validation.errors)))
        return report

    write_diagnostic_code(
        driver,
        extraction,
        content_hash=content_hash(raw_text),
        retrieved_at=retrieval_date,
    )
    report.dc_codes_written.append(dc_code)
    return report


def ingest_section(
    *,
    section: str,
    dc_codes: list[str],
    driver: GraphDriver,
    extractor: StructuredExtractor | None = None,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
) -> IngestionReport:
    """Ingest multiple DCs from one section.

    For the tracer (slice #3) callers pass ``["5260"]``. Slice #5 broadens to
    the full §4.71a code list.
    """
    report = IngestionReport(section=section)
    extractor = extractor or OpenAIExtractor()
    for code in dc_codes:
        sub = ingest_diagnostic_code(
            section=section,
            dc_code=code,
            driver=driver,
            extractor=extractor,
            retrieval_date=retrieval_date,
            project_root=project_root,
        )
        report.merge(sub)
    return report


def ingest_section_full(
    *,
    section: str,
    driver: GraphDriver,
    extractor: StructuredExtractor | None = None,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
    enrich_anatomy: bool = True,
    resolve_xrefs: bool = True,
) -> IngestionReport:
    """Ingest every Diagnostic Code discovered in a §X.YZ section.

    Steps:
    1. Fetch the section XML (cached).
    2. Auto-discover every DC via :func:`discover_dc_codes`.
    3. For each DC, run the normal validate-then-write pipeline. Validation
       failures land in the review queue, never the graph.
    4. Optionally run the anatomy enrichment + cross-reference resolution
       post-passes so Anatomy nodes and CrossReference targets are wired up.

    Returns an aggregated IngestionReport via the existing ``merge`` pattern.
    """
    report = IngestionReport(section=section)
    extractor = extractor or OpenAIExtractor()

    xml_text, _ = fetch_section_xml(
        section=section,
        retrieval_date=retrieval_date,
        project_root=project_root,
    )
    dc_codes = discover_dc_codes(xml_text)
    if not dc_codes:
        report.warnings.append(f"no diagnostic codes discovered in section {section!r}")
        return report

    for code in dc_codes:
        sub = ingest_diagnostic_code(
            section=section,
            dc_code=code,
            driver=driver,
            extractor=extractor,
            retrieval_date=retrieval_date,
            project_root=project_root,
        )
        report.merge(sub)

    if enrich_anatomy:
        anatomy_report = apply_anatomy_metadata(driver)
        report.warnings.append(
            f"anatomy enrichment: enriched={anatomy_report['enriched']}, "
            f"paired={anatomy_report['paired']}"
        )
    if resolve_xrefs:
        xref_report = resolve_cross_references(driver)
        report.warnings.append(
            f"cross-reference resolution: resolved={xref_report['resolved']}/"
            f"{xref_report['total']}"
        )

    return report


def ingest_rule_section(
    *,
    section: str,
    driver: GraphDriver,
    extractor: RuleExtractor | None = None,
    retrieval_date: date | None = None,
    project_root: Path | None = None,
) -> IngestionReport:
    """Ingest a §4.1–§4.31 general-provisions section as a :CFR:Rule node.

    Differs from :func:`ingest_diagnostic_code` in that the source has no
    Diagnostic Code structure — it's prose. The extractor produces a
    ``RuleExtraction``; the validator gates it; the writer materialises a
    single ``:CFR:Rule`` node attached to its ``:CFR:Section``.
    """
    report = IngestionReport(section=section)
    extractor = extractor or OpenAIRuleExtractor()

    xml_text, _ = fetch_section_xml(
        section=section,
        retrieval_date=retrieval_date,
        project_root=project_root,
    )
    heading, body = extract_section_text(xml_text)
    if not body:
        report.warnings.append(f"section {section!r} has no extractable body text")
        return report

    raw_text = f"{heading}\n\n{body}" if heading else body
    extraction = extractor.extract(raw_text, section=section)

    # Restore deterministic fields the LLM might drift on.
    if extraction.section != section:
        extraction = extraction.model_copy(update={"section": section})
    if not extraction.text.strip():
        extraction = extraction.model_copy(update={"text": body})

    report.rules_attempted.append(extraction.id)

    validation = validate_rule(extraction)
    report.warnings.extend(validation.warnings)
    if not validation.ok:
        _record_rule_failure(
            review_queue_path(project_root),
            section,
            extraction.id,
            raw_text,
            validation.errors,
        )
        report.rules_failed.append((extraction.id, list(validation.errors)))
        return report

    write_rule(
        driver,
        extraction,
        content_hash=content_hash(raw_text),
        retrieved_at=retrieval_date,
    )
    report.rules_written.append(extraction.id)
    return report


# Re-export for convenience.
__all__ = [
    "IngestionReport",
    "RuleExtraction",
    "ValidationResult",
    "ingest_diagnostic_code",
    "ingest_rule_section",
    "ingest_section",
    "ingest_section_full",
    "review_queue_path",
    "DiagnosticCodeExtraction",
]

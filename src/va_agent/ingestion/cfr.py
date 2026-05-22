"""Orchestrator: fetch CFR XML → locate DC text → LLM extract → validate →
write to graph (or route to the review queue on failure)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..graph.driver import GraphDriver
from ..graph.writers import write_diagnostic_code
from .ecfr_client import content_hash, fetch_section_xml
from .extraction import OpenAIExtractor, StructuredExtractor, extract_dc_text
from .schemas import DiagnosticCodeExtraction
from .validation import ValidationResult, validate_diagnostic_code


@dataclass
class IngestionReport:
    """What happened during one ingestion call."""

    section: str
    dc_codes_attempted: list[str] = field(default_factory=list)
    dc_codes_written: list[str] = field(default_factory=list)
    dc_codes_failed: list[tuple[str, list[str]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "IngestionReport") -> None:
        self.dc_codes_attempted.extend(other.dc_codes_attempted)
        self.dc_codes_written.extend(other.dc_codes_written)
        self.dc_codes_failed.extend(other.dc_codes_failed)
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


# Re-export for convenience.
__all__ = [
    "IngestionReport",
    "ValidationResult",
    "ingest_diagnostic_code",
    "ingest_section",
    "review_queue_path",
    "DiagnosticCodeExtraction",
]

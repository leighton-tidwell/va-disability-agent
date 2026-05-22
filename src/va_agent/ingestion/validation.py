"""Deterministic validation of CFR extractions.

Runs after the LLM extractor. Anything that fails here gets routed to the
review queue instead of the graph — the writer never sees malformed input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import (
    VALID_BODY_SYSTEMS,
    VALID_RATING_PERCENTS,
    DiagnosticCodeExtraction,
)

DC_CODE_RE = re.compile(r"^\d{4}$")
CROSS_REF_RE = re.compile(
    r"""^(
        DC\s\d{4}                |  # DC 5003
        §\s?\d+\.\d+[a-z]?       |  # §4.71a / § 4.59
        38\sC\.?F\.?R\.?\s§\s?\d+\.\d+[a-z]?  # 38 CFR §4.71a
    )$""",
    re.VERBOSE,
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_diagnostic_code(extraction: DiagnosticCodeExtraction) -> ValidationResult:
    """Check structural invariants on a DiagnosticCodeExtraction.

    Validator philosophy: be strict about things the LLM commonly gets wrong
    (codes, percentages, units) and lenient about things only a human can
    judge (whether the body_system is the *right* body_system for this DC).
    """
    result = ValidationResult(ok=True)

    if not DC_CODE_RE.match(extraction.code):
        result.add_error(f"code {extraction.code!r} is not 4 digits")

    if extraction.body_system not in VALID_BODY_SYSTEMS:
        result.add_error(
            f"body_system {extraction.body_system!r} not in {sorted(VALID_BODY_SYSTEMS)}"
        )

    if not extraction.title.strip():
        result.add_error("title is empty")

    if not extraction.section.strip():
        result.add_error("section is empty")

    if not extraction.raw_text.strip():
        result.add_error("raw_text is empty")

    seen_percents: set[int] = set()
    for level in extraction.rating_levels:
        if level.percent not in VALID_RATING_PERCENTS:
            result.add_error(
                f"rating_level.percent {level.percent} not in VA rating set {sorted(VALID_RATING_PERCENTS)}"
            )
        if level.percent in seen_percents:
            result.add_error(f"rating_level.percent {level.percent} appears more than once")
        seen_percents.add(level.percent)

        for criterion in level.criteria:
            if not criterion.text.strip():
                result.add_error(f"criterion under {level.percent}% has empty text")
            for m in criterion.measurements:
                if not m.unit.strip():
                    result.add_error(
                        f"measurement {m.name!r} under {level.percent}% has empty unit"
                    )

    for ref in extraction.cross_references:
        if not CROSS_REF_RE.match(ref.strip()):
            result.add_warning(
                f"cross_reference {ref!r} doesn't match known patterns (DC NNNN / §X.YZ)"
            )

    return result

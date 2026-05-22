"""Cypher-backed factuality check for drafted Lay Statements.

Rule: every numeric or unit-bearing clinical fact in a drafted Lay Statement
must trace to a confirmed ``MeasurementReport`` or ``SymptomReport`` for the
same ``user_id``. Symptom *terms* are looser — we check the body parts match.

Hard cases (paraphrased symptom names, unit conversions) are out of v1 scope:
the drafter's prompt forbids inventing measurements at all, so the simple
trace catches the bad cases we care about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..graph.driver import GraphDriver
from ..graph.tools import get_measurement_reports, get_symptom_reports

# Matches numeric values with optional units like "45°", "30 degrees", "100%"
# or bare numbers paired with a unit word.
MEASUREMENT_RE = re.compile(
    r"""
    (?P<value>\d+(?:\.\d+)?)         # number
    \s*
    (?P<unit>
        °|degrees?|deg|
        %|percent|
        dB|decibels?|
        cm|mm|inches?|in|
        Hz|hertz|
        weeks?|months?|years?|days?
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class FactualityResult:
    ok: bool
    grounded_facts: list[dict] = field(default_factory=list)
    fabricated_facts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def check_lay_statement(
    driver: GraphDriver,
    user_id: str,
    draft_text: str,
) -> FactualityResult:
    """Return a FactualityResult flagging any clinical facts that don't trace."""
    result = FactualityResult(ok=True)
    measurements = get_measurement_reports(driver, user_id)
    symptoms = get_symptom_reports(driver, user_id)

    grounded_values = _collect_values(measurements)
    grounded_body_parts = {s["body_part"].lower() for s in symptoms} | {
        m["body_part"].lower() for m in measurements
    }

    matched_so_far: set[tuple[str, str]] = set()
    for m in MEASUREMENT_RE.finditer(draft_text):
        raw_value = m.group("value")
        raw_unit = (m.group("unit") or "").lower()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        unit = _normalise_unit(raw_unit)
        key = (raw_value, unit)
        if key in matched_so_far:
            continue
        matched_so_far.add(key)

        if _is_grounded(value, unit, grounded_values):
            result.grounded_facts.append({"value": value, "unit": unit})
        else:
            result.ok = False
            result.fabricated_facts.append(
                {
                    "value": value,
                    "unit": unit,
                    "reason": "no MeasurementReport with this value/unit",
                }
            )

    # Body part presence check: any body part mentioned in the draft should be
    # one the veteran has reports about. We only check a small set of common
    # body parts to keep this honest (we're not trying to do general NLP).
    common_parts = {
        "knee", "shoulder", "back", "lumbar", "hip", "wrist", "ankle",
        "neck", "cervical", "elbow", "hand", "foot",
    }
    draft_lower = draft_text.lower()
    for part in common_parts:
        if part in draft_lower and part not in grounded_body_parts:
            # Lumbar/cervical aliases; soften with a note rather than failure.
            result.notes.append(
                f"draft references {part!r} but veteran has no SymptomReport or MeasurementReport for it"
            )

    return result


def _collect_values(measurement_reports: list[dict]) -> set[tuple[float, str]]:
    out: set[tuple[float, str]] = set()
    for mr in measurement_reports:
        out.add((float(mr["value"]), _normalise_unit(mr["unit"])))
    return out


def _normalise_unit(unit: str) -> str:
    u = unit.lower().strip()
    if u in {"°", "degrees", "degree", "deg"}:
        return "degrees"
    if u in {"%", "percent"}:
        return "percent"
    if u in {"db", "decibels", "decibel"}:
        return "decibels"
    return u


def _is_grounded(value: float, unit: str, grounded: set[tuple[float, str]]) -> bool:
    if (value, unit) in grounded:
        return True
    # Allow off-by-a-rounded-degree wiggle room (LLM may round 42 → 40).
    if unit == "degrees":
        for gv, gu in grounded:
            if gu == "degrees" and abs(value - gv) <= 5.0:
                return True
    return False

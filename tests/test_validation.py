"""Validator unit tests — deterministic, no Neo4j, no LLM."""

from __future__ import annotations

import pytest

from va_agent.ingestion.schemas import (
    CriterionExtraction,
    DiagnosticCodeExtraction,
    MeasurementExtraction,
    RatingLevelExtraction,
)
from va_agent.ingestion.validation import validate_diagnostic_code


def make_dc(**overrides) -> DiagnosticCodeExtraction:
    base = dict(
        code="5260",
        title="Leg, limitation of flexion of",
        body_system="musculoskeletal",
        section="4.71a",
        rating_levels=[
            RatingLevelExtraction(
                percent=10,
                criteria=[
                    CriterionExtraction(
                        text="Flexion limited to 45°",
                        measurements=[
                            MeasurementExtraction(
                                name="flexion",
                                body_part="knee",
                                operator="<=",
                                value=45,
                                unit="degrees",
                            )
                        ],
                    )
                ],
            )
        ],
        cross_references=["DC 5003"],
        notes=[],
        raw_text="5260 Leg, limitation of flexion of: Flexion limited to 45° — 10%.",
    )
    base.update(overrides)
    return DiagnosticCodeExtraction(**base)


def test_valid_extraction_passes():
    result = validate_diagnostic_code(make_dc())
    assert result.ok, result.errors


def test_invalid_code_format_fails():
    result = validate_diagnostic_code(make_dc(code="ABCD"))
    assert not result.ok
    assert any("not 4 digits" in e for e in result.errors)


def test_invalid_body_system_fails():
    result = validate_diagnostic_code(make_dc(body_system="bones"))
    assert not result.ok
    assert any("body_system" in e for e in result.errors)


def test_invalid_rating_percent_fails():
    bad_level = RatingLevelExtraction(
        percent=15,  # not in the canonical VA set
        criteria=[CriterionExtraction(text="anything")],
    )
    result = validate_diagnostic_code(make_dc(rating_levels=[bad_level]))
    assert not result.ok
    assert any("not in VA rating set" in e for e in result.errors)


def test_duplicate_rating_percent_fails():
    level = RatingLevelExtraction(percent=10, criteria=[CriterionExtraction(text="a")])
    level2 = RatingLevelExtraction(percent=10, criteria=[CriterionExtraction(text="b")])
    result = validate_diagnostic_code(make_dc(rating_levels=[level, level2]))
    assert not result.ok
    assert any("appears more than once" in e for e in result.errors)


def test_empty_criterion_text_fails():
    level = RatingLevelExtraction(percent=10, criteria=[CriterionExtraction(text="   ")])
    result = validate_diagnostic_code(make_dc(rating_levels=[level]))
    assert not result.ok
    assert any("empty text" in e for e in result.errors)


def test_measurement_missing_unit_fails():
    bad_m = MeasurementExtraction(
        name="flexion", body_part="knee", operator="<=", value=45, unit=""
    )
    level = RatingLevelExtraction(
        percent=10,
        criteria=[CriterionExtraction(text="Flexion limited to 45°", measurements=[bad_m])],
    )
    result = validate_diagnostic_code(make_dc(rating_levels=[level]))
    assert not result.ok
    assert any("empty unit" in e for e in result.errors)


def test_cross_reference_format_warns_not_errors():
    result = validate_diagnostic_code(make_dc(cross_references=["just some text"]))
    assert result.ok  # warning, not error
    assert any("cross_reference" in w for w in result.warnings)


@pytest.mark.parametrize(
    "missing_field,value",
    [("title", ""), ("section", ""), ("raw_text", "")],
)
def test_required_strings_must_be_nonempty(missing_field, value):
    result = validate_diagnostic_code(make_dc(**{missing_field: value}))
    assert not result.ok

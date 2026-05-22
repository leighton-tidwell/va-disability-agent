"""Validator unit tests for RuleExtraction (§4.1–§4.31 general provisions)."""

from __future__ import annotations

import pytest

from va_agent.ingestion.schemas import RuleExtraction
from va_agent.ingestion.validation import validate_rule


def make_rule(**overrides) -> RuleExtraction:
    base = dict(
        id="pyramiding",
        name="Pyramiding",
        text=(
            "The evaluation of the same disability under various diagnoses is "
            "to be avoided. Both the use of manifestations not resulting from "
            "service-connected disease or injury in establishing the service-"
            "connected evaluation, and the evaluation of the same manifestation "
            "under different diagnoses are to be avoided."
        ),
        body_system="general",
        section="4.14",
        applies_to=[],
    )
    base.update(overrides)
    return RuleExtraction(**base)


def test_valid_rule_passes():
    result = validate_rule(make_rule())
    assert result.ok, result.errors


def test_invalid_id_fails():
    result = validate_rule(make_rule(id="Pyramiding"))
    assert not result.ok
    assert any("snake_case" in e for e in result.errors)


def test_empty_text_fails():
    result = validate_rule(make_rule(text="   "))
    assert not result.ok


def test_short_text_warns():
    result = validate_rule(make_rule(text="Too short."))
    assert result.ok
    assert any("suspiciously short" in w for w in result.warnings)


def test_bad_body_system_fails():
    result = validate_rule(make_rule(body_system="bones"))
    assert not result.ok


@pytest.mark.parametrize("bad_section", ["four-point-fourteen", "", "4", "§4.14"])
def test_bad_section_fails(bad_section: str):
    result = validate_rule(make_rule(section=bad_section))
    assert not result.ok


def test_valid_section_with_subsection():
    result = validate_rule(make_rule(section="4.71a"))
    assert result.ok, result.errors

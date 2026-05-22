"""Pydantic schemas for CFR extraction.

These mirror the v1 graph schema for the law side. An LLM produces instances of
``DiagnosticCodeExtraction`` from raw CFR section text; the validator then
checks structural invariants before nodes are written to Neo4j.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Closed set of VA rating percentages used across §4.71a and most other body
# systems. Hearing (§4.85) uses table lookups instead and is out of v1 scope.
VALID_RATING_PERCENTS: frozenset[int] = frozenset({0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100})

VALID_BODY_SYSTEMS: frozenset[str] = frozenset(
    {
        "musculoskeletal",
        "mental",
        "hearing",
        "respiratory",
        "cardiovascular",
        "digestive",
        "skin",
        "neurological",
        "endocrine",
        "eye",
        "dental",
        "genitourinary",
        "hemic-lymphatic",
        "gynecological",
        "general",  # for §4.1–§4.31 general provisions
    }
)

Operator = Literal["<=", "<", "=", ">", ">=", "≤", "≥"]


class MeasurementExtraction(BaseModel):
    """A structured measurement extracted from a criterion's text.

    Example: "Flexion limited to 45°" → name="flexion", operator="<=",
    value=45, unit="degrees".
    """

    name: str = Field(..., description="The measurement (e.g. 'flexion', 'extension', 'ankylosis').")
    body_part: str = Field(..., description="Anatomy this measurement applies to (e.g. 'knee').")
    operator: Operator = Field(..., description="Comparison operator.")
    value: float = Field(..., description="Numeric threshold.")
    unit: str = Field(..., description="Unit of the value (e.g. 'degrees', 'decibels').")


class CriterionExtraction(BaseModel):
    """A single rating criterion: the text + any extractable structured measurements."""

    text: str = Field(..., description="The verbatim criterion text from the CFR.")
    measurements: list[MeasurementExtraction] = Field(default_factory=list)


class RatingLevelExtraction(BaseModel):
    """A rating tier: percent + the criteria that earn that percent."""

    percent: int = Field(..., description="Rating percentage assigned at this tier.")
    criteria: list[CriterionExtraction] = Field(..., min_length=1)


class DiagnosticCodeExtraction(BaseModel):
    """A complete Diagnostic Code with its rating levels and metadata."""

    code: str = Field(..., description="The 4-digit DC identifier (e.g. '5260').")
    title: str = Field(..., description="The DC's title (e.g. 'Leg, limitation of flexion of').")
    body_system: str = Field(..., description="Body system this DC belongs to.")
    section: str = Field(..., description="The CFR section (e.g. '4.71a').")
    rating_levels: list[RatingLevelExtraction] = Field(..., min_length=1)
    cross_references: list[str] = Field(default_factory=list, description="e.g. 'DC 5003', '§4.59'.")
    notes: list[str] = Field(default_factory=list, description="Verbatim Note (1), Note (2), etc.")
    raw_text: str = Field(..., description="Original CFR text the extraction was derived from.")


class RuleExtraction(BaseModel):
    """A general CFR Rule from §4.1–§4.31 (general provisions of the rating schedule).

    These sections do not have Diagnostic Codes — they encode prose rules
    (Pyramiding §4.14, Combined Ratings Table §4.25, Bilateral Factor §4.26,
    Functional Loss §4.40, etc.) that condition how Rating Percentages are
    derived and combined. They become :CFR:Rule nodes in the graph.
    """

    id: str = Field(
        ...,
        description=(
            "Stable rule identifier — the section number without leading '4.' is acceptable. "
            "Examples: 'pyramiding', 'bilateral_factor', 'functional_loss'. "
            "Use snake_case."
        ),
    )
    name: str = Field(..., description="Human-readable rule title (e.g. 'Pyramiding').")
    text: str = Field(..., description="Verbatim rule body text from the CFR.")
    body_system: str = Field(
        default="general",
        description="Body system this rule applies to; defaults to 'general'.",
    )
    section: str = Field(..., description="CFR section the rule lives in (e.g. '4.14').")
    applies_to: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of scopes the rule applies to — body system names "
            "('musculoskeletal'), DC code strings ('5260'), or section refs "
            "('4.71a'). Empty list means the rule is global."
        ),
    )

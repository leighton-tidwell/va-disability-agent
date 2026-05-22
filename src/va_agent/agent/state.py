"""LangGraph state shape for the chat orchestrator.

The state carries everything a node needs to make decisions, plus the running
transcript so a session can be replayed or resumed.

User-side facts (Veteran, SymptomReport, MeasurementReport, …) are persisted to
the graph as soon as they're confirmed; the state only holds them as a working
copy.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict


def _append(left: list, right: list) -> list:
    """LangGraph reducer that appends right onto left (for list-typed state keys)."""
    return list(left) + list(right)


Phase = Literal[
    "intake",
    "job_profile",
    "symptom_exploration",
    "measurement_check",
    "match_candidates",
    "claim_review",
    "evidence_review",
    "lay_statement_draft",
    "exam_prep_generate",
    "review_edit",
    "complete",
]


class Message(TypedDict):
    role: Literal["agent", "veteran", "system"]
    text: str


class FunctionalLoss(TypedDict):
    activity: str
    answer: str  # "yes" | "no" | "unknown"


class SymptomDraft(TypedDict, total=False):
    body_part: str
    text: str
    typical_severity: str | None
    flareup_severity: str | None
    flareup_frequency: str | None
    flareup_duration: str | None
    functional_loss: list[str]


class AgentState(TypedDict, total=False):
    """LangGraph state. ``user_id`` is set on first message; everything else accretes."""

    user_id: str
    phase: Phase

    # Intake
    branch: str | None
    service_period_start: str | None
    service_period_end: str | None
    deployments: list[str]
    discharge_characterization: str | None
    discharge_warning_issued: bool

    # JobProfile
    job_code: str | None
    job_code_branch: str | None
    job_code_in_spine: bool | None
    prioritised_anatomies: list[str]
    risk_rationale: dict[str, str]

    # SymptomExploration
    anatomy_queue: list[str]
    current_anatomy: str | None
    symptoms_recorded: list[SymptomDraft]

    # MatchCandidates → ClaimReview → EvidenceReview
    candidate_dcs: list[dict]
    claim_id: str | None
    weaknesses: list[dict]
    bilateral_prompts: list[dict]
    missing_evidence: dict

    # LayStatementDraft → ExamPrepGenerate → Review/Edit
    lay_statements: dict  # dc_code -> {"text": str, "factuality_ok": bool, "node_id": str}
    exam_preps: dict      # dc_code -> {"node_id": str, ...}
    drafting_queue: list[str]  # remaining DC codes

    # Conversation
    transcript: Annotated[list[Message], _append]

    # Concepts that have been surfaced this session (so we don't repeat)
    surfaced_concepts: list[str]

    # Free-form inputs the orchestrator should process next (test harness only)
    pending_inputs: list[str]

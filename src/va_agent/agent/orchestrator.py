"""LangGraph wiring for the chat orchestrator.

Builds a compiled graph from Intake → JobProfile → SymptomExploration (looping)
→ END. Each node consumes from ``state['pending_inputs']`` so tests + the
notebook can drive sessions with scripted veteran responses.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph

from ..graph.driver import GraphDriver
from .concepts import Concept, load_concepts
from .nodes import (
    claim_review_node,
    evidence_review_node,
    exam_prep_generate_node,
    intake_node,
    job_profile_node,
    lay_statement_draft_node,
    match_candidates_node,
    measurement_check_node,
    review_edit_node,
    symptom_exploration_node,
)
from .state import AgentState


def _route_after_intake(state: AgentState) -> str:
    """Stay in intake until phase advances; then go to job_profile."""
    if state.get("phase") == "job_profile":
        return "job_profile"
    if not state.get("pending_inputs"):
        return END
    return "intake"


def _route_after_job_profile(state: AgentState) -> str:
    if state.get("phase") == "symptom_exploration":
        return "symptom_exploration"
    if not state.get("pending_inputs"):
        return END
    return "job_profile"


def _route_after_symptom_exploration(state: AgentState) -> str:
    if state.get("phase") == "measurement_check":
        return "measurement_check"
    if state.get("phase") == "complete":
        return END
    if not state.get("pending_inputs"):
        return END
    return "symptom_exploration"


def _route_after_measurement_check(state: AgentState) -> str:
    if state.get("phase") == "match_candidates":
        return "match_candidates"
    if state.get("phase") == "complete":
        return END
    if not state.get("pending_inputs"):
        return END
    return "measurement_check"


def _route_after_match_candidates(state: AgentState) -> str:
    if state.get("phase") == "claim_review":
        return "claim_review"
    return END


def _route_after_claim_review(state: AgentState) -> str:
    if state.get("phase") == "evidence_review":
        return "evidence_review"
    return END


def _route_after_evidence_review(state: AgentState) -> str:
    if state.get("phase") == "lay_statement_draft":
        return "lay_statement_draft"
    return END


def _route_after_lay_statement_draft(state: AgentState) -> str:
    if state.get("phase") == "exam_prep_generate":
        return "exam_prep_generate"
    if state.get("phase") == "lay_statement_draft":
        return "lay_statement_draft"
    return END


def _route_after_exam_prep_generate(state: AgentState) -> str:
    if state.get("phase") == "review_edit":
        return "review_edit"
    if state.get("phase") == "exam_prep_generate":
        return "exam_prep_generate"
    return END


def _route_after_review_edit(state: AgentState) -> str:
    return END


def build_orchestrator(
    driver: GraphDriver,
    concepts: list[Concept] | None = None,
):
    """Build and compile the LangGraph chat orchestrator."""
    concepts = concepts if concepts is not None else load_concepts()

    graph = StateGraph(AgentState)
    graph.add_node("intake", partial(intake_node, driver=driver, concepts=concepts))
    graph.add_node(
        "job_profile", partial(job_profile_node, driver=driver, concepts=concepts)
    )
    graph.add_node(
        "symptom_exploration",
        partial(symptom_exploration_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "measurement_check",
        partial(measurement_check_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "match_candidates",
        partial(match_candidates_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "claim_review",
        partial(claim_review_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "evidence_review",
        partial(evidence_review_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "lay_statement_draft",
        partial(lay_statement_draft_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "exam_prep_generate",
        partial(exam_prep_generate_node, driver=driver, concepts=concepts),
    )
    graph.add_node(
        "review_edit",
        partial(review_edit_node, driver=driver, concepts=concepts),
    )

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", _route_after_intake)
    graph.add_conditional_edges("job_profile", _route_after_job_profile)
    graph.add_conditional_edges("symptom_exploration", _route_after_symptom_exploration)
    graph.add_conditional_edges("measurement_check", _route_after_measurement_check)
    graph.add_conditional_edges("match_candidates", _route_after_match_candidates)
    graph.add_conditional_edges("claim_review", _route_after_claim_review)
    graph.add_conditional_edges("evidence_review", _route_after_evidence_review)
    graph.add_conditional_edges("lay_statement_draft", _route_after_lay_statement_draft)
    graph.add_conditional_edges("exam_prep_generate", _route_after_exam_prep_generate)
    graph.add_conditional_edges("review_edit", _route_after_review_edit)

    return graph.compile()


def run_scripted_session(
    driver: GraphDriver,
    user_id: str,
    scripted_inputs: list[str],
    *,
    concepts: list[Concept] | None = None,
) -> AgentState:
    """Convenience entry point for tests + the notebook.

    Takes a list of scripted veteran responses, runs the orchestrator end-to-end,
    and returns the final AgentState (which includes the full transcript).
    """
    app = build_orchestrator(driver, concepts=concepts)
    initial: AgentState = {
        "user_id": user_id,
        "phase": "intake",
        "pending_inputs": list(scripted_inputs),
        "transcript": [],
        "surfaced_concepts": [],
    }
    # LangGraph recursion limit: enough to process many scripted inputs even
    # when each anatomy round consumes ~12 inputs (desc + baseline + flare-up
    # severity/frequency/duration + 5–7 probes).
    final = app.invoke(initial, config={"recursion_limit": 200})
    return final  # type: ignore[return-value]

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
    intake_node,
    job_profile_node,
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
    if state.get("phase") == "complete":
        return END
    if not state.get("pending_inputs"):
        return END
    return "symptom_exploration"


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

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", _route_after_intake)
    graph.add_conditional_edges("job_profile", _route_after_job_profile)
    graph.add_conditional_edges(
        "symptom_exploration", _route_after_symptom_exploration
    )

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

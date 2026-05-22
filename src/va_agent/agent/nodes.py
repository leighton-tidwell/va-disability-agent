"""LangGraph nodes for the first three chat phases.

Each node takes ``AgentState`` and returns a partial dict to merge into state.

Design choice: nodes consume from ``state['pending_inputs']`` (a scripted list
of veteran responses). This makes the v1 flow testable and notebook-demoable
without needing real interactive IO. A future iteration replaces the queue
with a LangGraph ``interrupt()`` to pause for live input.
"""

from __future__ import annotations

from typing import Iterable

from ..graph.driver import GraphDriver
from ..graph.tools import (
    record_jobcode,
    record_measurement,
    record_symptom,
    record_veteran,
)
from .concepts import Concept, find_triggered_concept
from .probes import probes_for
from .state import AgentState, Message, SymptomDraft

# --- helpers ------------------------------------------------------------------


def _say(text: str) -> Message:
    return {"role": "agent", "text": text}


def _heard(text: str) -> Message:
    return {"role": "veteran", "text": text}


def _system(text: str) -> Message:
    return {"role": "system", "text": text}


def _pop_input(state: AgentState) -> tuple[str | None, list[str]]:
    queue = list(state.get("pending_inputs") or [])
    if not queue:
        return None, []
    return queue[0], queue[1:]


def _normalise_branch(text: str) -> str | None:
    t = text.lower()
    if "army" in t:
        return "Army"
    if "marine" in t:
        return "Marine Corps"
    if "navy" in t:
        return "Navy"
    if "air force" in t or "usaf" in t:
        return "Air Force"
    if "coast guard" in t or "uscg" in t:
        return "Coast Guard"
    if "space force" in t:
        return "Space Force"
    return None


_DISCHARGE_KEYWORDS = {
    "honorable": "Honorable",
    "general": "General Under Honorable Conditions",
    "other than honorable": "Other Than Honorable",
    "oth": "Other Than Honorable",
    "bad conduct": "Bad Conduct",
    "dishonorable": "Dishonorable",
    "uncharacterized": "Uncharacterized",
}


def _normalise_discharge(text: str) -> str | None:
    t = text.lower()
    # Longer phrases first
    for needle in sorted(_DISCHARGE_KEYWORDS, key=len, reverse=True):
        if needle in t:
            return _DISCHARGE_KEYWORDS[needle]
    return None


def _is_non_honorable(disc: str | None) -> bool:
    return disc is not None and disc not in {"Honorable", "General Under Honorable Conditions"}


def _to_dict(obj):
    """Convert a dataclass/instance to a dict for state serialisation."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return obj


def _surface_concept_if_triggered(
    state: AgentState, updates: dict, concepts: Iterable[Concept], text: str
) -> list[Message]:
    """Returns the agent messages to append and updates ``updates['surfaced_concepts']``.

    Reads already-surfaced from ``updates`` first (intra-node accumulation), then
    from ``state``. Writes back to ``updates`` so the caller's return picks up
    the update.
    """
    if not text:
        return []
    surfaced = list(updates.get("surfaced_concepts") or state.get("surfaced_concepts") or [])
    c = find_triggered_concept(concepts, text, surfaced)
    if c is None:
        return []
    surfaced.append(c.id)
    updates["surfaced_concepts"] = surfaced
    return [
        _say(
            f"📘 Heads up — about *{c.name}*:\n{c.plain_language}\n(See {c.citation}.)"
        )
    ]


# --- node 1: Intake -----------------------------------------------------------


INTAKE_QUESTIONS = [
    "Hi — I'm here to help you put together a VA disability claim. To start, what branch did you serve in, and roughly when?",
    "Got it. Were you deployed? If so, where? (You can list multiple, or say 'none'.)",
    "Last bit of intake: what was your discharge characterization? (Honorable / General / Other Than Honorable / etc.)",
]


def intake_node(state: AgentState, *, driver: GraphDriver, concepts: list[Concept]) -> dict:
    """Collect branch / service period / deployments / discharge characterization.

    Three-pass loop: asks one question, processes the answer, returns the
    intermediate state. Re-entered (by the graph engine) until all three
    intake fields are collected.
    """
    transcript = []
    updates: dict = {}

    # Decide which intake question is next.
    if state.get("branch") is None:
        question = INTAKE_QUESTIONS[0]
    elif "deployments" not in state:
        question = INTAKE_QUESTIONS[1]
    elif state.get("discharge_characterization") is None:
        question = INTAKE_QUESTIONS[2]
    else:
        # All collected — advance phase, persist to graph.
        record_veteran(
            driver,
            state["user_id"],
            branch=state["branch"],
            deployments=state.get("deployments") or None,
            discharge_characterization=state.get("discharge_characterization"),
        )
        updates["phase"] = "job_profile"
        return updates

    transcript.append(_say(question))

    veteran_text, remaining = _pop_input(state)
    if veteran_text is None:
        # No input available — return the question and wait.
        updates["transcript"] = transcript
        return updates

    transcript.append(_heard(veteran_text))
    transcript.extend(_surface_concept_if_triggered(state, updates, concepts, veteran_text))

    if state.get("branch") is None:
        updates["branch"] = _normalise_branch(veteran_text)
        if updates["branch"] is None:
            transcript.append(
                _system("(intake: couldn't parse a branch from that — using None for v1)")
            )
    elif "deployments" not in state:
        deployments = _parse_deployments(veteran_text)
        updates["deployments"] = deployments
    elif state.get("discharge_characterization") is None:
        disc = _normalise_discharge(veteran_text)
        updates["discharge_characterization"] = disc
        if _is_non_honorable(disc) and not state.get("discharge_warning_issued"):
            transcript.append(
                _say(
                    "I want to flag that an OTH/BCD/Dishonorable discharge can affect VA "
                    "benefit eligibility — there's a Character of Discharge determination "
                    "and many veterans successfully upgrade through the Discharge Review "
                    "Board. We'll continue drafting your claim regardless, so you'll have "
                    "it ready."
                )
            )
            updates["discharge_warning_issued"] = True

    updates["pending_inputs"] = remaining
    updates["transcript"] = transcript
    return updates


def _parse_deployments(text: str) -> list[str]:
    t = text.strip()
    if t.lower() in {"none", "no", "n/a", "nope"}:
        return []
    # Split on comma / semicolon / "and"
    import re

    parts = re.split(r",|;| and ", t, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


# --- node 2: JobProfile -------------------------------------------------------


JOB_PROFILE_QUESTION = (
    "What was your job code (MOS / AFSC / Navy Rating)? If you have a DD-214 handy, read "
    "the primary one off of it. Format like '15T', '0311', 'HM', '2A5X1' is fine."
)
JOB_PROFILE_FAILURE = (
    "I don't see {code} in the job-code spine I have. Could you double-check the code, "
    "or describe your job duties in a sentence so I can ask about likely conditions?"
)


def job_profile_node(state: AgentState, *, driver: GraphDriver, concepts: list[Concept]) -> dict:
    transcript: list[Message] = []
    updates: dict = {}

    if state.get("job_code") is None:
        transcript.append(_say(JOB_PROFILE_QUESTION))
        veteran_text, remaining = _pop_input(state)
        if veteran_text is None:
            updates["transcript"] = transcript
            return updates
        transcript.append(_heard(veteran_text))
        transcript.extend(_surface_concept_if_triggered(state, updates, concepts, veteran_text))

        code, branch = _parse_job_code(veteran_text, state.get("branch"))
        updates["job_code"] = code
        updates["job_code_branch"] = branch
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript

        if code and branch and record_jobcode(driver, state["user_id"], code=code, branch=branch):
            updates["job_code_in_spine"] = True
            anatomies, rationale = _prioritise_anatomies(driver, state["user_id"])
            updates["prioritised_anatomies"] = anatomies
            updates["risk_rationale"] = rationale
            updates["anatomy_queue"] = list(anatomies)
            if anatomies:
                rationale_blurb = "\n".join(
                    f"  • {a} — {rationale.get(a, 'commonly affected for this role')}"
                    for a in anatomies[:5]
                )
                transcript.append(
                    _say(
                        f"Thanks — {code} ({branch}). Based on your role, the conditions I'd "
                        f"want to ask about first are:\n{rationale_blurb}\n"
                        "We'll go through them one at a time. You can always say 'skip' to move on."
                    )
                )
            updates["phase"] = "symptom_exploration"
        else:
            updates["job_code_in_spine"] = False
            transcript.append(_say(JOB_PROFILE_FAILURE.format(code=code or "(blank)")))
            transcript.append(
                _say(
                    "For v1 I'll still ask you about a default set of common areas — knee, "
                    "back, shoulder, hearing, mental health — and you can answer for whichever "
                    "apply."
                )
            )
            updates["prioritised_anatomies"] = ["knee", "back", "shoulder", "hearing", "mental"]
            updates["anatomy_queue"] = list(updates["prioritised_anatomies"])
            updates["risk_rationale"] = {}
            updates["phase"] = "symptom_exploration"
        return updates

    # Already have a job_code — shouldn't usually be re-entered.
    updates["phase"] = "symptom_exploration"
    return updates


def _parse_job_code(text: str, default_branch: str | None) -> tuple[str | None, str | None]:
    import re

    # Take the first token that looks like a job code: digits + optional letter,
    # or letter + digits + letter (AFSC).
    m = re.search(r"\b([0-9]{2,4}[A-Z]?|[1-9][A-Z][0-9][A-Z][0-9])\b", text.upper())
    if not m:
        # Try bare Navy/CG ratings like HM, BM, MM
        m = re.search(r"\b([A-Z]{2,3})\b", text.upper())
        if not m:
            return None, default_branch
    code = m.group(1)
    return code, default_branch


def _prioritise_anatomies(driver: GraphDriver, user_id: str) -> tuple[list[str], dict[str, str]]:
    rows = driver.user_read(
        user_id,
        """
        MATCH (v:User:Veteran {user_id: $user_id})-[:HOLDS_JOBCODE]->(jc:CFR:JobCode)
        OPTIONAL MATCH (jc)-[r:RISK_FOR]->(target)
        OPTIONAL MATCH (jc)-[n:NOISE_EXPOSURE]->(hearing:CFR:Anatomy)
        WITH jc,
             collect(DISTINCT {kind: 'risk', name: coalesce(target.name, ''), source: r.source}) AS risks,
             collect(DISTINCT {kind: 'noise', name: hearing.name, probability: n.probability}) AS noise
        // Deterministic order: noise first, then RISK_FOR sorted by anatomy name.
        RETURN
          [x IN risks WHERE x.name <> '' | x] AS risks,
          [x IN noise WHERE x.name IS NOT NULL | x] AS noise
        """,
    )
    if not rows:
        return [], {}

    row = rows[0]
    rationale: dict[str, str] = {}
    ordered: list[str] = []

    noise_entries = [n for n in row["noise"] if n.get("name")]
    if noise_entries:
        n = noise_entries[0]
        rationale["hearing"] = f"noise exposure: {n['probability']} (Duty MOS Noise Exposure Listing)"
        ordered.append("hearing")

    # Sort risk entries alphabetically by anatomy name for deterministic order.
    risk_entries = sorted(
        [r for r in row["risks"] if r.get("name") and r["name"] not in ordered],
        key=lambda r: r["name"],
    )
    for r in risk_entries:
        name = r["name"]
        rationale[name] = "risk overlay" + (f" ({r['source']})" if r.get("source") else "")
        ordered.append(name)

    return ordered, rationale


# --- node 3: SymptomExploration ----------------------------------------------


def symptom_exploration_node(
    state: AgentState, *, driver: GraphDriver, concepts: list[Concept]
) -> dict:
    """For each Anatomy in the queue, ask whether the veteran has symptoms; if
    so, collect Baseline + Flare-up severity/frequency/duration + run Functional
    Loss Probes.

    This node is re-entered repeatedly until the queue is empty.
    """
    transcript: list[Message] = []
    updates: dict = {}
    queue = list(state.get("anatomy_queue") or [])

    if state.get("current_anatomy") is None:
        if not queue:
            updates["phase"] = "measurement_check"
            transcript.append(
                _say(
                    "That covers the priority anatomies. Next I'll ask about any specific "
                    "measurements (like degrees of motion or hearing thresholds) you have "
                    "from doctor visits, then match your reports against the CFR."
                )
            )
            updates["transcript"] = transcript
            return updates

        anatomy = queue.pop(0)
        updates["current_anatomy"] = anatomy
        updates["anatomy_queue"] = queue
        transcript.append(
            _say(
                f"Let's talk about your {anatomy}. Have you had any pain, stiffness, "
                "weakness, or other issues with it since service? (yes / no / skip)"
            )
        )
        veteran_text, remaining = _pop_input(state)
        if veteran_text is None:
            updates["transcript"] = transcript
            return updates
        transcript.append(_heard(veteran_text))
        updates["pending_inputs"] = remaining

        if _is_negative(veteran_text):
            transcript.append(_say(f"Got it — moving past {anatomy}."))
            updates["current_anatomy"] = None
            if not queue:
                updates["phase"] = "complete"
                transcript.append(
                    _say(
                        "That covers the priority areas. In future slices we'll match your "
                        "reports against the CFR and draft Lay Statements. For now your "
                        "answers are saved to the graph."
                    )
                )
            updates["transcript"] = transcript
            return updates

    # We have a current anatomy with a "yes" response; gather details using
    # whatever inputs remain (post-yes/no) as our working queue.
    anatomy = state.get("current_anatomy") or updates["current_anatomy"]
    working_queue = list(updates.get("pending_inputs", state.get("pending_inputs") or []))
    return _collect_anatomy_details(
        state, anatomy, driver, concepts, transcript, updates, working_queue
    )


def _collect_anatomy_details(
    state: AgentState,
    anatomy: str,
    driver: GraphDriver,
    concepts: list[Concept],
    transcript: list[Message],
    updates: dict,
    remaining: list[str],
) -> dict:
    # Q: describe the symptom
    transcript.append(_say(f"In your own words, what's going on with your {anatomy}?"))
    description, remaining = _pop_from(remaining)
    if description is None:
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(description))
    transcript.extend(_surface_concept_if_triggered(state, updates, concepts, description))

    # Q: baseline severity
    transcript.append(
        _say(
            "On a typical day — not a flare — how would you rate that: "
            "mild, moderate, severe, or very severe?"
        )
    )
    baseline, remaining = _pop_from(remaining)
    if baseline is None:
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(baseline))

    # Q: flare-up severity
    transcript.append(
        _say(
            "And on your worst days — when it flares up — how does it look then? "
            "(mild / moderate / severe / very severe)"
        )
    )
    flareup_severity, remaining = _pop_from(remaining)
    if flareup_severity is None:
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(flareup_severity))
    transcript.extend(_surface_concept_if_triggered(state, updates, concepts, "flare-up"))

    # Q: flare-up frequency
    transcript.append(_say("How often does it flare up? (e.g. daily, weekly, monthly)"))
    flareup_freq, remaining = _pop_from(remaining)
    if flareup_freq is None:
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(flareup_freq))

    # Q: flare-up duration
    transcript.append(_say("And how long does a flare-up last when it happens?"))
    flareup_dur, remaining = _pop_from(remaining)
    if flareup_dur is None:
        updates["pending_inputs"] = remaining
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(flareup_dur))

    # Functional Loss Probes
    losses: list[str] = []
    for probe in probes_for(anatomy):
        transcript.append(_say(probe.question + " (yes / no / skip)"))
        ans, remaining = _pop_from(remaining)
        if ans is None:
            updates["pending_inputs"] = remaining
            updates["transcript"] = transcript
            return updates
        transcript.append(_heard(ans))
        # "no" here means the veteran CAN'T do the activity → Functional Loss.
        # ("yes" means they can still do it → not a loss.)
        if _is_negative(ans):
            # Convert the probe to a functional-loss bullet.
            losses.append(_probe_to_loss(probe.question))

    # Persist to graph.
    sr_id = record_symptom(
        driver,
        state["user_id"],
        text=description,
        body_part=anatomy,
        typical_severity=baseline.strip().lower(),
        flareup_severity=flareup_severity.strip().lower(),
        flareup_frequency=flareup_freq.strip(),
        flareup_duration=flareup_dur.strip(),
        functional_loss=losses,
    )

    transcript.append(
        _say(
            f"Recorded — your {anatomy} report is saved with baseline={baseline}, "
            f"flare-up={flareup_severity} ({flareup_freq}, lasting {flareup_dur}), and "
            f"{len(losses)} functional loss item(s)."
        )
    )

    symptoms_recorded = list(state.get("symptoms_recorded") or [])
    symptoms_recorded.append(
        SymptomDraft(
            body_part=anatomy,
            text=description,
            typical_severity=baseline.strip().lower(),
            flareup_severity=flareup_severity.strip().lower(),
            flareup_frequency=flareup_freq.strip(),
            flareup_duration=flareup_dur.strip(),
            functional_loss=losses,
        )
    )
    updates["symptoms_recorded"] = symptoms_recorded
    updates["current_anatomy"] = None
    updates["pending_inputs"] = remaining
    updates["transcript"] = transcript
    # If we've exhausted the queue after this anatomy, mark complete now so
    # the route doesn't return END before phase gets set.
    queue_after = list(state.get("anatomy_queue") or [])
    if not queue_after:
        updates["phase"] = "complete"
        transcript.append(
            _say(
                "That covers the priority areas. In future slices we'll match your "
                "reports against the CFR and draft Lay Statements. For now your "
                "answers are saved to the graph."
            )
        )
    return updates


def _pop_from(queue: list[str]) -> tuple[str | None, list[str]]:
    if not queue:
        return None, queue
    return queue[0], queue[1:]


def _is_negative(text: str) -> bool:
    t = text.lower().strip()
    if not t:
        return True
    return any(t == n or t.startswith(n + " ") for n in ("no", "nope", "skip", "none", "no thanks"))


def _probe_to_loss(question: str) -> str:
    """Convert a probe question to a Functional Loss bullet.

    "Can you kneel for more than 5 minutes?" → "cannot kneel for more than 5 minutes"
    """
    q = question.strip().rstrip("?")
    if q.lower().startswith("can you "):
        return "cannot " + q[len("Can you ") :].strip()
    if q.lower().startswith("has the "):
        return q.lower().replace("has the", "the").strip()
    if q.lower().startswith("do you "):
        return "yes — " + q.strip()
    return q.lower()


# --- node 4: MeasurementCheck -------------------------------------------------


def measurement_check_node(
    state: AgentState, *, driver: GraphDriver, concepts: list[Concept]
) -> dict:
    """For each recorded Symptom, ask whether the veteran has a concrete
    measurement (degrees of motion, decibels, etc.). Unknowns become
    "to be measured at C&P" notes — not blockers.
    """
    updates: dict = {}
    transcript: list[Message] = []
    symptoms = list(state.get("symptoms_recorded") or [])

    # Find the next symptom that doesn't yet have a measurement and hasn't been
    # asked about. Track via a small marker in state.
    asked = set(state.get("measurement_asked") or [])
    pending_symptom = None
    for s in symptoms:
        key = s.get("body_part", "")
        if key and key not in asked:
            pending_symptom = s
            break

    if pending_symptom is None:
        # All symptoms covered → advance to MatchCandidates.
        updates["phase"] = "match_candidates"
        return updates

    body_part = pending_symptom["body_part"]
    transcript.append(
        _say(
            f"Do you have any specific measurements for your {body_part}? "
            f"For example, a range-of-motion number from a doctor's visit, or a "
            f"hearing test result. If you don't know, just say 'I don't know' — "
            f"the C&P examiner will measure it. (Format: 'flexion 40 degrees' or "
            f"'hearing loss 25 dB at 2000Hz'.)"
        )
    )
    veteran_text, remaining = _pop_input(state)
    if veteran_text is None:
        updates["transcript"] = transcript
        return updates
    transcript.append(_heard(veteran_text))
    updates["pending_inputs"] = remaining

    parsed = _parse_measurement(veteran_text, body_part)
    if parsed is not None:
        record_measurement(
            driver,
            state["user_id"],
            name=parsed["name"],
            body_part=body_part,
            value=parsed["value"],
            unit=parsed["unit"],
            source="user-stated",
        )
        transcript.append(
            _say(
                f"Recorded {parsed['name']} = {parsed['value']} {parsed['unit']} "
                f"for {body_part}."
            )
        )
    else:
        transcript.append(
            _say(
                f"Got it — no measurement on hand for {body_part}. I'll mark this "
                f"as something the C&P examiner will check."
            )
        )

    asked.add(body_part)
    updates["measurement_asked"] = list(asked)  # type: ignore[typeddict-item]
    updates["transcript"] = transcript
    return updates


def _parse_measurement(text: str, body_part: str) -> dict | None:
    """Very small parser — pulls a value+unit out of a free-text response.

    Recognises the patterns we care about for v1: 'flexion 40 degrees',
    '40°', '25 dB'. For v2 we layer in an LLM extractor.
    """
    import re

    t = text.lower().strip()
    if any(t.startswith(p) for p in ("i don't know", "no", "none", "not sure", "skip")):
        return None

    m = re.search(r"(\w+)?\s*([0-9]+(?:\.[0-9]+)?)\s*(°|degrees?|deg|db|decibels?|%|hz|hertz)", t)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    if not name or name in {"a", "an", "the", "of"}:
        # Default the measurement name from the body part: knee → flexion.
        defaults = {"knee": "flexion", "back": "flexion", "shoulder": "abduction"}
        name = defaults.get(body_part, "measurement")
    value = float(m.group(2))
    raw_unit = m.group(3)
    if raw_unit in {"°", "degrees", "degree", "deg"}:
        unit = "degrees"
    elif raw_unit in {"db", "decibels", "decibel"}:
        unit = "decibels"
    elif raw_unit == "%":
        unit = "percent"
    elif raw_unit in {"hz", "hertz"}:
        unit = "hertz"
    else:
        unit = raw_unit
    return {"name": name, "value": value, "unit": unit}


# --- node 5: MatchCandidates --------------------------------------------------


def match_candidates_node(
    state: AgentState, *, driver: GraphDriver, concepts: list[Concept]
) -> dict:
    """Run the Match Retriever; persist a draft Claim with the candidate DCs."""
    from ..retrieval.matcher import find_candidate_dcs
    from ..review.claim_reviewer import create_claim

    updates: dict = {}
    transcript: list[Message] = []

    candidates = find_candidate_dcs(driver, state["user_id"])
    cand_list = [
        {
            "code": c.code,
            "title": c.title,
            "body_system": c.body_system,
            "best_percent": c.best_percent,
            "hit_count": len(c.criterion_hits),
            "supporting_measurements": c.matching_measurements,
        }
        for c in candidates
    ]
    updates["candidate_dcs"] = cand_list

    if not cand_list:
        transcript.append(
            _say(
                "I didn't find any Diagnostic Codes that match your reports yet. "
                "This usually means we need more detail or different measurements. "
                "Skipping ahead — you can always add more reports and re-run."
            )
        )
        updates["phase"] = "complete"
        updates["transcript"] = transcript
        return updates

    transcript.append(
        _say("Based on your reports, here are the Diagnostic Codes I'd recommend filing under:")
    )
    for c in cand_list[:10]:
        transcript.append(
            _say(
                f"  • DC {c['code']} — {c['title']}: best-supported {c['best_percent']}% "
                f"(matched {c['hit_count']} criteria)"
            )
        )

    claim_id = create_claim(driver, state["user_id"], [c["code"] for c in cand_list])
    updates["claim_id"] = claim_id
    transcript.append(
        _say(
            f"I've created a draft Claim ({claim_id[:8]}…) with {len(cand_list)} "
            f"Claimed Conditions. Next I'll run a Claim Review for Pyramiding and "
            f"the Bilateral Factor."
        )
    )
    updates["phase"] = "claim_review"
    updates["transcript"] = transcript
    return updates


# --- node 6: ClaimReview ------------------------------------------------------


def claim_review_node(
    state: AgentState, *, driver: GraphDriver, concepts: list[Concept]
) -> dict:
    """Run Pyramiding + Bilateral Factor checks; surface results."""
    from ..review.claim_reviewer import review_claim

    updates: dict = {}
    transcript: list[Message] = []
    claim_id = state.get("claim_id")
    if not claim_id:
        # Nothing to review.
        updates["phase"] = "evidence_review"
        return updates

    report = review_claim(driver, state["user_id"], claim_id)

    updates["weaknesses"] = [_to_dict(c) for c in report.pyramiding]
    updates["bilateral_prompts"] = [_to_dict(p) for p in report.bilateral_prompts]

    if report.pyramiding:
        transcript.append(_say("⚠️  Pyramiding check found conflicts:"))
        for conflict in report.pyramiding:
            transcript.append(_say(f"  • {conflict.explanation}"))
        transcript.extend(_surface_concept_if_triggered(state, updates, concepts, "pyramiding"))
    else:
        transcript.append(_say("✓ Pyramiding check: no conflicts."))

    if report.bilateral_prompts:
        transcript.append(_say("Bilateral Factor prompts:"))
        for p in report.bilateral_prompts:
            transcript.append(
                _say(
                    f"  • You claimed {p.anatomy} but not {p.paired_anatomy}. Symptoms on "
                    f"both sides trigger the Bilateral Factor (§4.26) — even mild symptoms "
                    f"on the other side are worth claiming."
                )
            )
        transcript.extend(_surface_concept_if_triggered(state, updates, concepts, "bilateral"))
    else:
        transcript.append(_say("✓ Bilateral Factor check: no single-sided gaps."))

    updates["phase"] = "evidence_review"
    updates["transcript"] = transcript
    return updates


# --- node 7: EvidenceReview --------------------------------------------------


def evidence_review_node(
    state: AgentState, *, driver: GraphDriver, concepts: list[Concept]
) -> dict:
    """Per Claimed Condition, list the Evidence types the veteran should
    gather (STR / Buddy Statement / Private Medical Record)."""
    from ..review.claim_reviewer import _enumerate_missing_evidence

    updates: dict = {}
    transcript: list[Message] = []
    claim_id = state.get("claim_id")
    if not claim_id:
        updates["phase"] = "complete"
        return updates

    missing = _enumerate_missing_evidence(driver, state["user_id"], claim_id)
    updates["missing_evidence"] = missing

    transcript.append(_say("Evidence to gather per Claimed Condition:"))
    for code, types in missing.items():
        if not types:
            transcript.append(_say(f"  • DC {code}: ✓ no Evidence gaps"))
        else:
            transcript.append(_say(f"  • DC {code}: " + ", ".join(types)))

    transcript.append(
        _say(
            "Lay Statements for each Claimed Condition will be drafted next. The "
            "Evidence above is what you (or your VSO) need to gather before C&P."
        )
    )
    updates["phase"] = "complete"
    updates["transcript"] = transcript
    return updates

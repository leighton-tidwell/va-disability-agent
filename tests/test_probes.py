"""Functional Loss Probe tests (pure-Python)."""

from __future__ import annotations

from va_agent.agent.probes import ANATOMY_PROBES, probes_for


def test_probes_exist_for_v1_anatomies():
    for anatomy in ["knee", "back", "shoulder", "neck", "ankle", "wrist", "hip", "hearing", "mental"]:
        probes = probes_for(anatomy)
        assert probes, f"no probes for {anatomy}"
        assert all(p.question.strip().endswith("?") for p in probes)
        assert all(p.anatomy == anatomy for p in probes)


def test_probes_fall_back_for_unknown_anatomy():
    probes = probes_for("eyelid")  # not in v1 table
    assert len(probes) >= 2
    assert probes[0].anatomy == "eyelid"


def test_probe_questions_are_unique_per_anatomy():
    for anatomy, questions in ANATOMY_PROBES.items():
        assert len(set(questions)) == len(questions), f"duplicate probe under {anatomy}"

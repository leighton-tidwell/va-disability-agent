"""Functional Loss Probes — specific activity questions per anatomy.

Veterans answering "what can't you do?" routinely undercount the activities
they've adapted around. Asking concrete, anatomy-specific activity questions
("can you kneel for 5 minutes?") elicits Functional Loss that open-ended
questions miss.

For v1 we keep this as a static table per anatomy. v2 can layer Job Code
context (a 15T crew chief gets aviation-specific probes for hearing on top of
the generic ones).
"""

from __future__ import annotations

from dataclasses import dataclass

# Probes per anatomy. Phrasing is intentionally everyday — the veteran answers
# yes/no, and any "yes" is captured as a Functional Loss item on their
# SymptomReport.
ANATOMY_PROBES: dict[str, tuple[str, ...]] = {
    "knee": (
        "Can you kneel for more than 5 minutes without significant pain?",
        "Can you climb a flight of stairs without holding the railing?",
        "Can you squat down to pick something up off the floor?",
        "Can you run, jog, or walk briskly for more than 10 minutes?",
        "Has your knee given out or buckled in the last year?",
    ),
    "back": (
        "Can you sit through a 90-minute movie without shifting or standing up?",
        "Can you lift a full grocery bag off the floor without bending your knees?",
        "Can you sleep through the night without waking from back pain?",
        "Can you stand in one place for 30 minutes without leaning or shifting?",
        "Have you avoided driving more than an hour because of back pain?",
    ),
    "shoulder": (
        "Can you reach overhead to a top shelf without pain?",
        "Can you lift a gallon of milk above your head?",
        "Can you sleep on the affected side without waking?",
        "Can you reach behind your back to tuck in a shirt or fasten a bra?",
        "Has the shoulder slipped, popped out, or felt unstable?",
    ),
    "neck": (
        "Can you turn your head fully to check a blind spot when driving?",
        "Can you look up at the ceiling without significant pain?",
        "Do you get headaches that start from your neck more than once a week?",
        "Can you sleep through the night without waking from neck pain?",
    ),
    "ankle": (
        "Can you walk on uneven ground (gravel, grass) without pain?",
        "Can you stand on tiptoe for more than a few seconds?",
        "Has the ankle rolled or given way in the last year?",
        "Can you go down stairs normally (one foot per step, not pausing on each)?",
    ),
    "wrist": (
        "Can you open a jar lid without using a tool?",
        "Can you carry a full grocery bag by the handles for a block?",
        "Can you turn a key in a lock or doorknob without discomfort?",
        "Can you type or write for 30 minutes without pain?",
    ),
    "hip": (
        "Can you put on socks and shoes without sitting down?",
        "Can you get in and out of a low car seat without help?",
        "Can you walk for 30 minutes without stopping to rest?",
        "Can you sleep on the affected side without waking?",
    ),
    "elbow": (
        "Can you fully straighten your arm to reach a high shelf?",
        "Can you carry a full grocery bag for a block without arm pain?",
        "Can you do pushups, even a few?",
    ),
    "hand": (
        "Can you button a shirt without losing your grip?",
        "Can you grip a screwdriver and turn it tight?",
        "Can you write a paragraph by hand without stopping?",
    ),
    "foot": (
        "Can you walk barefoot on a hard floor without pain?",
        "Can you stand at the kitchen counter for 30 minutes without shifting weight?",
        "Do you wear special inserts, custom shoes, or avoid certain footwear?",
    ),
    "hearing": (
        "Can you follow a conversation in a noisy restaurant without lip-reading?",
        "Do people repeat themselves to you on the phone or in person?",
        "Do you have ringing or buzzing in your ears that's there even when it's quiet?",
        "Has your spouse, partner, or family complained that the TV is too loud?",
    ),
    "mental": (
        "How many nights a week do you sleep through without waking from a nightmare or anxiety?",
        "Have you avoided crowds, certain places, or social events because of how they make you feel?",
        "Can you concentrate on a 30-minute task without intrusive thoughts?",
        "Have you missed work or canceled plans because of how you were feeling?",
    ),
}


@dataclass(frozen=True)
class Probe:
    anatomy: str
    question: str
    index: int


def probes_for(anatomy: str) -> list[Probe]:
    """Return the list of Functional Loss Probes for an Anatomy.

    Unknown anatomies fall back to a small generic set so the chat doesn't
    dead-end on rare body parts.
    """
    questions = ANATOMY_PROBES.get(anatomy.lower())
    if questions is None:
        questions = (
            "Has this affected your ability to do your job, hobbies, or daily activities?",
            "Are there specific things you used to do that you now avoid because of it?",
            "Does it interfere with sleep?",
        )
    return [Probe(anatomy=anatomy, question=q, index=i) for i, q in enumerate(questions)]

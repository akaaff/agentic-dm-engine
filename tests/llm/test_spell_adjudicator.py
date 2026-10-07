"""Live check (needs Ollama): the adjudicator follows the per-spell guidelines on clear-cut casts.

A case passes if the model gets it right in at least 2 of 3 samples - the model is not
deterministic, and the point is that the guideline text steers it, not that it never wavers.
Only unambiguous casts are here; where a ruling is a judgment call (partial or not) the
checks only require "not a plain success"."""

from __future__ import annotations

import pytest

from src.cli.play import build_demo_encounter, build_demo_party
from src.engine.encounter import build_encounter_state
from src.engine.events import Event
from src.engine.state import GameState
from src.graph.nodes.spell_adjudicator import adjudicate_utility_spell

pytestmark = pytest.mark.llm

CASES = [
    ("mage-hand", "Mage Hand", "I use mage hand to lift the key off the table", {"success"}),
    ("mage-hand", "Mage Hand", "I use mage hand to carry the heavy 80 lb chest", {"failure"}),
    ("mage-hand", "Mage Hand", "I use mage hand to stab the goblin", {"failure", "partial"}),
    (
        "message",
        "Message",
        "I send a message to the king in the capital, five miles away",
        {"failure", "partial"},
    ),
    ("disguise-self", "Disguise Self", "I make myself look like a city guard", {"success"}),
    (
        "disguise-self",
        "Disguise Self",
        "I give myself a third arm and wings",
        {"failure", "partial"},
    ),
    ("create-or-destroy-water", "Create or Destroy Water", "I fill my waterskin", {"success"}),
    (
        "create-or-destroy-water",
        "Create or Destroy Water",
        "I create a barrel of wine",
        {"failure"},
    ),
    ("thaumaturgy", "Thaumaturgy", "I blast open the barred iron door", {"failure", "partial"}),
    ("unseen-servant", "Unseen Servant", "I order the servant to attack the goblin", {"failure"}),
]


def _state() -> GameState:
    import random

    return build_encounter_state(build_demo_encounter(), build_demo_party(), random.Random(1))


@pytest.mark.parametrize(("index", "name", "attempt", "acceptable"), CASES)
def test_the_adjudicator_follows_the_guideline(
    index: str, name: str, attempt: str, acceptable: set[str]
) -> None:
    state = _state()
    right = 0
    for _ in range(3):
        event = Event(
            round=1,
            turn_index=0,
            actor="elrond",
            type="spell_cast",
            payload={"spell": name, "spell_index": index, "utility": True, "attempt": attempt},
        )
        if adjudicate_utility_spell(state, event).outcome in acceptable:
            right += 1
    assert right >= 2, f"{name}: {attempt!r} was ruled correctly only {right}/3 times"

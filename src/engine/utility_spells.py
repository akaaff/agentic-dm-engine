"""Spells with no combat mechanic, whose outcome a model rules on (issue #55).

Most of the SRD's level 0-2 utility spells (Mage Hand, Detect Magic, Knock, Zone of Truth...)
have no roll to calculate: success depends on facts about the world - is there a lock, a trap, a
magical aura? - that this engine, which models a battle map and not a scene's contents, doesn't
have. So the engine only does the bookkeeping (slot, concentration, a `spell_cast` event) and
`graph/nodes/spell_adjudicator.py` asks the model to rule on what the player attempted, given
the spell's real SRD text and a short guideline, and records the verdict as a `spell_ruling`
event the narrator must follow.

The guidelines live in `data/spells/utility_guidelines.yaml`, one entry per spell index, so they
can be edited without touching code. They are looked up per cast: `guideline_for(index)` returns
just that spell's entry, and only it goes into the model's prompt. Each says what the spell can do
and mostly what makes an attempt fail or only partly work - the spell text already says what it
does, so the value added is the adjudication line (where "simple" ends and "too much")."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

GUIDELINES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "spells" / ("utility_guidelines.yaml")
)


@cache
def _load_guidelines() -> dict[str, str]:
    loaded = yaml.safe_load(GUIDELINES_PATH.read_text(encoding="utf-8")) or {}
    return {str(index): str(text).strip() for index, text in loaded.items()}


def is_utility_spell(index: str) -> bool:
    return index in _load_guidelines()


def guideline_for(index: str) -> str | None:
    """The guideline for one spell (the only one sent to the model for that cast), or None."""
    return _load_guidelines().get(index)


def utility_spell_indices() -> frozenset[str]:
    return frozenset(_load_guidelines())

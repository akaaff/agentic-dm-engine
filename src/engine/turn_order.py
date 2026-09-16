"""Initiative rolling and turn advancement.

Deliberately decoupled from the full Character/GameState model (added Day 3)
- this only needs each combatant's id and DEX modifier, so it can be built
and tested standalone.
"""

from __future__ import annotations

import random
from typing import NamedTuple

from src.engine.dice import roll_d20


class InitiativeRoll(NamedTuple):
    """One combatant's full initiative-roll detail (issue #14) - previously
    roll_initiative discarded everything but the final id ordering, so
    nothing about *why* the turn order came out the way it did (each
    combatant's natural d20, DEX modifier, and resulting total) was ever
    recorded anywhere. encounter.build_encounter_state turns each of these
    into an "initiative_rolled" Event, the same way other rolls already
    become Events."""

    character_id: str
    natural: int
    modifier: int
    total: int


def roll_initiative(dex_modifiers: dict[str, int], rng: random.Random) -> list[InitiativeRoll]:
    """Returns one InitiativeRoll per combatant, ordered highest-total-first.

    Ties are broken by higher DEX modifier, then by combatant id for a fully
    deterministic result under a fixed seed (the SRD's own tiebreak - "the
    DM decides" - isn't reproducible, so this substitutes a stable rule).
    """
    rolls = []
    for cid, mod in dex_modifiers.items():
        natural = roll_d20(modifier=mod, rng=rng).kept[0]
        rolls.append(InitiativeRoll(cid, natural, mod, natural + mod))
    return sorted(rolls, key=lambda r: (-r.total, -r.modifier, r.character_id))


def next_turn(turn_order: list[str], current_turn: int, round_: int) -> tuple[int, int]:
    """Returns (next_turn_index, next_round)."""
    next_index = (current_turn + 1) % len(turn_order)
    next_round = round_ + 1 if next_index == 0 else round_
    return next_index, next_round

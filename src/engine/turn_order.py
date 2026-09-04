"""Initiative rolling and turn advancement.

Deliberately decoupled from the full Character/GameState model (added Day 3)
- this only needs each combatant's id and DEX modifier, so it can be built
and tested standalone.
"""

from __future__ import annotations

import random

from src.engine.dice import roll_d20


def roll_initiative(dex_modifiers: dict[str, int], rng: random.Random) -> list[str]:
    """Returns combatant ids ordered highest-initiative-first.

    Ties are broken by higher DEX modifier, then by combatant id for a fully
    deterministic result under a fixed seed (the SRD's own tiebreak - "the
    DM decides" - isn't reproducible, so this substitutes a stable rule).
    """
    scored = {
        cid: (roll_d20(modifier=mod, rng=rng).total, mod) for cid, mod in dex_modifiers.items()
    }
    ordered = sorted(scored.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    return [cid for cid, _ in ordered]


def next_turn(turn_order: list[str], current_turn: int, round_: int) -> tuple[int, int]:
    """Returns (next_turn_index, next_round)."""
    next_index = (current_turn + 1) % len(turn_order)
    next_round = round_ + 1 if next_index == 0 else round_
    return next_index, next_round

"""Minimal deterministic monster-turn AI: no LLM call, no persona - a
monster just attacks the nearest living party member (ties broken by
character id for determinism/testability).

This exists only to unblock full zero-human-input autoplay (Day 15's verify
gate): the campaign's monsters still need to act every round, but building
real monster intelligence is explicitly out of scope for this project (see
CLAUDE.md's Day 12 note on the "who controls monster turns" design gap).
This heuristic doesn't touch that gap - it just replaces "no action at all"
with a reasonable deterministic one, and does nothing to prevent the
friendly-fire scenario noted there (that only arises from free-text intent
parsing, which this heuristic never uses).
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import chebyshev_distance
from src.engine.state import Character, GameState


def choose_monster_action(game_state: GameState, actor: Character) -> ParsedAction:
    living_targets = [
        c for c in game_state.characters.values() if c.is_pc and not c.is_dead and c.id != actor.id
    ]
    if not living_targets:
        return ParsedAction(
            actor=actor.id, verb="end_turn", raw_text=f"{actor.name} has no target left."
        )

    target = min(
        living_targets, key=lambda c: (chebyshev_distance(actor.position, c.position), c.id)
    )
    return ParsedAction(
        actor=actor.id,
        verb="attack",
        target=target.id,
        raw_text=f"{actor.name} attacks {target.name}.",
    )

"""Grid position and distance.

Uses the simplified "every square costs 5 ft, including diagonals" movement
rule (Chebyshev distance) rather than the PHB's default alternating 5-10-5-10
diagonal cost. Deliberate simplification: the alternating rule needs a
running parity counter across an entire movement path (state that doesn't
fit a single distance function), and the flat-cost variant is common enough
in tabletop/VTT play to be a reasonable default for this engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

FEET_PER_SQUARE = 5


@dataclass(frozen=True)
class Position:
    x: int
    y: int


def chebyshev_distance(a: Position, b: Position) -> int:
    """Distance in grid squares."""
    return max(abs(a.x - b.x), abs(a.y - b.y))


def distance_feet(a: Position, b: Position) -> int:
    return chebyshev_distance(a, b) * FEET_PER_SQUARE


_ORDINAL_WORDS = ["closest", "2nd closest", "3rd closest"]
"""Beyond 3rd, falls back to "Nth closest" (see rank_label) - a hand-picked
list rather than a general ordinal-suffix function since English's 1st/2nd/
3rd/4th... irregularity only matters for the first three anyway, and this
project's encounters rarely have more than a handful of visible characters."""


def rank_label(rank: int) -> str:
    if rank <= len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[rank - 1]
    return f"{rank}th closest"


def direction_label(actor_pos: Position, other_pos: Position) -> str:
    """8-way compass direction of `other_pos` relative to `actor_pos`, on
    this project's own (x right/east, y down/south) grid convention (see
    BattleMap's own "y=0 is the top row" comment) - found live: the model
    had no reliable way to resolve "the enemy to my left" from raw (x, y)
    pairs alone, so this computes the answer directly instead of asking it
    to do grid arithmetic in its head. Originally intent_parser.py's own
    private helper - promoted here (issue found while widening player_
    agent.py's prompt the same way) so both LLM-facing prompts that need to
    describe relative positions share one implementation instead of two
    copies that could drift."""
    dx = other_pos.x - actor_pos.x
    dy = other_pos.y - actor_pos.y
    ns = "north" if dy < 0 else "south" if dy > 0 else ""
    ew = "west" if dx < 0 else "east" if dx > 0 else ""
    direction = ns + ew
    return f"{direction} of you" if direction else "at your position"


TerrainType = Literal["floor", "wall", "difficult", "hazard"]


class BattleMap(BaseModel):
    """Lives here (not encounter.py, which is where it's authored/consumed)
    so that state.py can hold one on GameState without an import cycle -
    encounter.py already depends on state.py for Character/GameState."""

    width: int
    height: int
    terrain: list[list[TerrainType]]
    spawn_points: dict[str, Position]

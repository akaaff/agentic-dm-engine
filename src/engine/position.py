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

"""Movement cost and wall-blocking against a terrain grid.

Operates on a plain list[list[TerrainType]] (row-major, terrain[y][x]) rather
than the full BattleMap model (position.py) - these functions only ever need
the raw grid, not spawn points or dimensions."""

from __future__ import annotations

from src.engine.position import FEET_PER_SQUARE, Position, TerrainType, chebyshev_distance


def _in_bounds(pos: Position, terrain: list[list[TerrainType]]) -> bool:
    return 0 <= pos.y < len(terrain) and 0 <= pos.x < len(terrain[pos.y])


def move_cost_feet(path: list[Position], terrain: list[list[TerrainType]]) -> int | None:
    """`path` is the sequence of squares moved through, starting with the
    current position (not itself costed). Each step must be adjacent
    (including diagonals) to the previous one. Returns None if the path is
    invalid (a non-adjacent jump) or blocked by a wall or out-of-bounds
    square; walls block entry but a hazard square is enterable (its effect
    is handled elsewhere, this is movement cost only). A `difficult` square
    costs double (10 ft instead of 5 ft) to enter, matching the SRD rule."""
    total = 0
    for i in range(1, len(path)):
        prev, curr = path[i - 1], path[i]
        if chebyshev_distance(prev, curr) != 1:
            return None
        if not _in_bounds(curr, terrain) or terrain[curr.y][curr.x] == "wall":
            return None
        cost = FEET_PER_SQUARE * (2 if terrain[curr.y][curr.x] == "difficult" else 1)
        total += cost
    return total


def can_afford_move(speed: int, path: list[Position], terrain: list[list[TerrainType]]) -> bool:
    cost = move_cost_feet(path, terrain)
    return cost is not None and cost <= speed

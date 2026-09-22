"""Deterministic battle-map assembly from a small closed set of layout/
difficulty choices - the first piece of the "story-adaptive encounters"
initiative (the user's own framing: "build a grid matching the situation
in the story").

`campaign_generator.py`'s LLM call only ever picks a `BattleMapLayout`/
`BattleMapSize`/terrain-amount enum, conditioned on the combat scene's own
narrative text - it never touches a raw per-cell grid. This module turns
those closed-set choices into a real `BattleMap` deterministically. Same
"give the model a closed vocabulary, let Python own the geometry" pattern
already proven repeatedly in this project (cast_spell targeting,
closest-enemy resolution, move-target path computation) - a 7B model
reasoning about exact grid coordinates has been a reliable source of bugs
every other time this project tried it, and a malformed hand-off here
(an unreachable spawn point, terrain out of bounds) would be worse than
those - it would corrupt an encounter before a session even starts.

Every layout is connected *by construction*, not validated after the fact
with pathfinding: `campaign_generator._generated_battle_map`'s own
docstring already took this stance for its one fixed template ("no walls -
minimizes the risk of a procedurally-placed spawn point ending up
unreachable"); this module extends the same risk-averse approach to more
shapes instead of introducing a BFS reachability check as a safety net.
"""

from __future__ import annotations

import random
from typing import Literal

from src.engine.position import BattleMap, Position, TerrainType

BattleMapLayout = Literal["open_room", "narrow_corridor", "two_rooms", "cluttered"]
BattleMapSize = Literal["small", "medium", "large"]
TerrainAmount = Literal["none", "light", "heavy"]

_SIZE_DIMENSIONS: dict[BattleMapSize, tuple[int, int]] = {
    "small": (6, 4),
    "medium": (8, 5),  # matches the old fixed template's own dimensions
    "large": (10, 6),
}

PARTY_SPAWN_NAMES = ["party_1", "party_2", "party_3", "party_4"]
MONSTER_SPAWN_NAMES = ["monster_1", "monster_2", "monster_3", "monster_4"]

_AMOUNT_DENSITY: dict[TerrainAmount, float] = {"none": 0.0, "light": 0.08, "heavy": 0.2}
"""Fraction of a layout's remaining open floor cells (after spawn points are
reserved) converted to difficult/hazard terrain - "heavy" is deliberately
well short of 1.0 so a map is never so cluttered/hazardous that a path
across it stops existing, even though connectivity is already guaranteed
by the base layout shape regardless of how these cells land."""


def _base_terrain(layout: BattleMapLayout, width: int, height: int) -> list[list[TerrainType]]:
    """The layout's wall shape only - difficult/hazard cells are scattered
    on top of this afterward, never in place of it. Every shape here leaves
    a full unbroken path from the left edge to the right edge: narrow_
    corridor's walls are confined to row 0 and row height-1, every row in
    between is entirely open; two_rooms partitions the map with a wall
    column but leaves exactly one row of it as a doorway; open_room and
    cluttered never place a wall at all (cluttered's texture comes purely
    from a heavier difficult-terrain scatter, so it can't ever box in a
    spawn point by accident)."""
    if layout == "narrow_corridor":
        return [
            ["wall"] * width if y in (0, height - 1) else ["floor"] * width for y in range(height)
        ]
    if layout == "two_rooms":
        mid = width // 2
        doorway_row = height // 2
        terrain: list[list[TerrainType]] = []
        for y in range(height):
            row: list[TerrainType] = [
                "wall" if (x == mid and y != doorway_row) else "floor" for x in range(width)
            ]
            terrain.append(row)
        return terrain
    return [["floor"] * width for _ in range(height)]


def _spawn_points(layout: BattleMapLayout, width: int, height: int) -> dict[str, Position]:
    """Party spawns on the left edge, monster spawns on the right edge,
    spread across whichever rows this layout guarantees are walkable
    (narrow_corridor excludes its own walled top/bottom rows) - cycling
    through those rows if there are more spawn slots than usable rows on a
    `small` map."""
    usable_rows = list(range(1, height - 1)) if layout == "narrow_corridor" else list(range(height))

    def rows_for(count: int) -> list[int]:
        return [usable_rows[i % len(usable_rows)] for i in range(count)]

    spawn_points: dict[str, Position] = {}
    for name, y in zip(PARTY_SPAWN_NAMES, rows_for(len(PARTY_SPAWN_NAMES)), strict=True):
        spawn_points[name] = Position(x=0, y=y)
    for name, y in zip(MONSTER_SPAWN_NAMES, rows_for(len(MONSTER_SPAWN_NAMES)), strict=True):
        spawn_points[name] = Position(x=width - 1, y=y)
    return spawn_points


def build_battle_map(
    layout: BattleMapLayout,
    size: BattleMapSize,
    difficult_terrain: TerrainAmount,
    hazard: TerrainAmount,
    rng: random.Random,
) -> BattleMap:
    """Assembles a real `BattleMap` from closed-set choices - `rng` (this
    project's usual injected-randomness convention) only ever decides which
    already-safe floor cells get textured, never anything structural."""
    width, height = _SIZE_DIMENSIONS[size]
    terrain = _base_terrain(layout, width, height)
    spawn_points = _spawn_points(layout, width, height)
    reserved = set(spawn_points.values())

    floor_cells = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if terrain[y][x] == "floor" and Position(x=x, y=y) not in reserved
    ]
    rng.shuffle(floor_cells)

    def scatter(
        amount: TerrainAmount, terrain_type: TerrainType, cells: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        if amount == "none" or not cells:
            return cells
        count = max(1, int(len(cells) * _AMOUNT_DENSITY[amount]))
        for x, y in cells[:count]:
            terrain[y][x] = terrain_type
        return cells[count:]

    remaining_cells = scatter(hazard, "hazard", floor_cells)
    scatter(difficult_terrain, "difficult", remaining_cells)

    return BattleMap(width=width, height=height, terrain=terrain, spawn_points=spawn_points)

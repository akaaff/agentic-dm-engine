"""Deterministic template-assembly tests - a real seeded random.Random is
used (not this project's usual _FixedRandom stub) since the only randomness
here is rng.shuffle(floor_cells), and every assertion cares about
structural properties (dimensions, terrain-type counts, connectivity,
spawn placement) rather than which specific cells got textured."""

from __future__ import annotations

import random

from src.engine.battle_map_templates import (
    MONSTER_SPAWN_NAMES,
    PARTY_SPAWN_NAMES,
    build_battle_map,
)
from src.engine.position import Position


def test_open_room_has_no_walls_at_all() -> None:
    battle_map = build_battle_map("open_room", "medium", "none", "none", random.Random(1))
    assert all(cell == "floor" for row in battle_map.terrain for cell in row)


def test_cluttered_has_no_walls_either_only_difficult_terrain() -> None:
    battle_map = build_battle_map("cluttered", "medium", "heavy", "none", random.Random(1))
    assert all(cell != "wall" for row in battle_map.terrain for cell in row)
    assert any(cell == "difficult" for row in battle_map.terrain for cell in row)


def test_narrow_corridor_walls_only_top_and_bottom_rows() -> None:
    battle_map = build_battle_map("narrow_corridor", "medium", "none", "none", random.Random(1))
    assert all(cell == "wall" for cell in battle_map.terrain[0])
    assert all(cell == "wall" for cell in battle_map.terrain[-1])
    for row in battle_map.terrain[1:-1]:
        assert all(cell == "floor" for cell in row)


def test_two_rooms_has_exactly_one_doorway_row_in_the_wall_column() -> None:
    battle_map = build_battle_map("two_rooms", "medium", "none", "none", random.Random(1))
    mid = battle_map.width // 2
    wall_column_cells = [row[mid] for row in battle_map.terrain]
    assert wall_column_cells.count("wall") == battle_map.height - 1
    assert wall_column_cells.count("floor") == 1


def test_size_dimensions_match_documented_values() -> None:
    small = build_battle_map("open_room", "small", "none", "none", random.Random(1))
    medium = build_battle_map("open_room", "medium", "none", "none", random.Random(1))
    large = build_battle_map("open_room", "large", "none", "none", random.Random(1))
    assert (small.width, small.height) == (6, 4)
    assert (medium.width, medium.height) == (8, 5)
    assert (large.width, large.height) == (10, 6)


def test_heavy_scatters_more_terrain_than_light() -> None:
    light = build_battle_map("open_room", "large", "light", "none", random.Random(1))
    heavy = build_battle_map("open_room", "large", "heavy", "none", random.Random(1))
    light_count = sum(row.count("difficult") for row in light.terrain)
    heavy_count = sum(row.count("difficult") for row in heavy.terrain)
    assert heavy_count > light_count > 0


def test_none_amount_places_no_terrain() -> None:
    battle_map = build_battle_map("open_room", "large", "none", "none", random.Random(1))
    assert all(cell == "floor" for row in battle_map.terrain for cell in row)


def test_spawn_points_are_never_overwritten_by_scattered_terrain() -> None:
    # "heavy" hazard + "heavy" difficult terrain together, on the smallest
    # map, is the tightest squeeze against spawn points surviving the
    # scatter - repeated across several seeds for confidence, not just one.
    for seed in range(10):
        battle_map = build_battle_map("cluttered", "small", "heavy", "heavy", random.Random(seed))
        for pos in battle_map.spawn_points.values():
            assert battle_map.terrain[pos.y][pos.x] == "floor"


def test_spawn_point_names_match_the_established_convention() -> None:
    battle_map = build_battle_map("open_room", "medium", "none", "none", random.Random(1))
    assert set(battle_map.spawn_points) == {*PARTY_SPAWN_NAMES, *MONSTER_SPAWN_NAMES}
    # Party on the left edge, monsters on the right edge.
    for name in PARTY_SPAWN_NAMES:
        assert battle_map.spawn_points[name].x == 0
    for name in MONSTER_SPAWN_NAMES:
        assert battle_map.spawn_points[name].x == battle_map.width - 1


def test_small_map_cycles_spawn_rows_without_crashing() -> None:
    # narrow_corridor + small only has 2 usable interior rows for 4 spawn
    # slots per side - must cycle, not index out of range.
    battle_map = build_battle_map("narrow_corridor", "small", "none", "none", random.Random(1))
    assert len(battle_map.spawn_points) == len(PARTY_SPAWN_NAMES) + len(MONSTER_SPAWN_NAMES)
    for pos in battle_map.spawn_points.values():
        assert 1 <= pos.y <= battle_map.height - 2


def test_every_layout_leaves_a_left_to_right_path_open() -> None:
    # Connectivity guaranteed by construction, checked here as a real BFS
    # rather than just trusted - the one property that actually matters for
    # "can the party reach the monsters."
    for layout in ("open_room", "narrow_corridor", "two_rooms", "cluttered"):
        battle_map = build_battle_map(layout, "medium", "heavy", "heavy", random.Random(1))
        start = battle_map.spawn_points["party_1"]
        goal = battle_map.spawn_points["monster_1"]
        walkable = {
            (x, y)
            for y, row in enumerate(battle_map.terrain)
            for x, cell in enumerate(row)
            if cell != "wall"
        }
        frontier = [(start.x, start.y)]
        seen = {(start.x, start.y)}
        while frontier:
            x, y = frontier.pop()
            if (x, y) == (goal.x, goal.y):
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, y + dy)
                if nxt in walkable and nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert (goal.x, goal.y) in seen, f"{layout}: no path from party_1 to monster_1"


def test_build_battle_map_returns_a_position_keyed_by_the_documented_names() -> None:
    battle_map = build_battle_map("open_room", "medium", "none", "none", random.Random(1))
    assert battle_map.spawn_points["party_1"] == Position(x=0, y=0)

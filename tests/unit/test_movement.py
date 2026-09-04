from src.engine.movement import TerrainType, can_afford_move, move_cost_feet
from src.engine.position import Position

# 5x5 grid, terrain[y][x]:
#   y=0: floor floor    floor floor floor
#   y=1: floor difficult difficult floor floor
#   y=2: floor floor    wall  floor floor
#   y=3: floor floor    floor floor floor
#   y=4: floor floor    floor floor floor
_TERRAIN: list[list[TerrainType]] = [
    ["floor", "floor", "floor", "floor", "floor"],
    ["floor", "difficult", "difficult", "floor", "floor"],
    ["floor", "floor", "wall", "floor", "floor"],
    ["floor", "floor", "floor", "floor", "floor"],
    ["floor", "floor", "floor", "floor", "floor"],
]


def test_straight_line_move_costs_five_feet_per_square() -> None:
    path = [Position(0, 0), Position(1, 0), Position(2, 0)]
    assert move_cost_feet(path, _TERRAIN) == 10


def test_difficult_terrain_doubles_cost() -> None:
    # (1,0) -> (1,1) [difficult] -> (2,1) [difficult]: 10 + 10 = 20 ft
    path = [Position(1, 0), Position(1, 1), Position(2, 1)]
    assert move_cost_feet(path, _TERRAIN) == 20


def test_speed_budget_enforced_against_difficult_terrain_cost() -> None:
    path = [Position(1, 0), Position(1, 1), Position(2, 1)]  # costs 20 ft
    assert can_afford_move(speed=30, path=path, terrain=_TERRAIN) is True
    assert can_afford_move(speed=15, path=path, terrain=_TERRAIN) is False
    assert can_afford_move(speed=20, path=path, terrain=_TERRAIN) is True  # exact budget


def test_wall_blocks_the_path() -> None:
    path = [Position(1, 2), Position(2, 2)]  # (2,2) is a wall
    assert move_cost_feet(path, _TERRAIN) is None
    assert can_afford_move(speed=100, path=path, terrain=_TERRAIN) is False


def test_non_adjacent_jump_is_invalid() -> None:
    path = [Position(0, 0), Position(2, 0)]  # distance 2, not adjacent
    assert move_cost_feet(path, _TERRAIN) is None


def test_out_of_bounds_step_is_blocked() -> None:
    path = [Position(0, 0), Position(-1, 0)]
    assert move_cost_feet(path, _TERRAIN) is None

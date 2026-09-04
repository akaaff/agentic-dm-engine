from src.engine.position import Position, chebyshev_distance, distance_feet


def test_orthogonal_distance() -> None:
    assert chebyshev_distance(Position(0, 0), Position(3, 0)) == 3


def test_diagonal_distance_uses_chebyshev_not_manhattan() -> None:
    # (0,0) -> (3,3): Manhattan would be 6 squares, Chebyshev (this engine's
    # simplified flat-diagonal-cost rule) is 3.
    assert chebyshev_distance(Position(0, 0), Position(3, 3)) == 3


def test_distance_feet_applies_five_feet_per_square() -> None:
    assert distance_feet(Position(0, 0), Position(4, 0)) == 20

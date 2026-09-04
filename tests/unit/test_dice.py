import random

from src.engine.dice import roll, roll_d20


def test_roll_is_reproducible_under_a_fixed_seed() -> None:
    a = roll(2, 6, modifier=3, rng=random.Random(42))
    b = roll(2, 6, modifier=3, rng=random.Random(42))
    assert a == b


def test_roll_stays_within_bounds() -> None:
    rng = random.Random(1)
    for _ in range(200):
        result = roll(3, 8, modifier=0, rng=rng)
        assert len(result.dice) == 3
        assert all(1 <= d <= 8 for d in result.dice)
        assert result.total == sum(result.dice)


def test_roll_d20_no_advantage_uses_single_die() -> None:
    result = roll_d20(modifier=5, rng=random.Random(7))
    assert len(result.dice) == 1
    assert result.kept == result.dice
    assert result.total == result.dice[0] + 5


class _FixedRandom:
    """Minimal random.Random stand-in returning a preset queue of values."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def test_roll_d20_advantage_keeps_the_higher_die() -> None:
    rng = _FixedRandom([5, 17])
    result = roll_d20(modifier=2, rng=rng, advantage=True)  # type: ignore[arg-type]
    assert result.dice == [5, 17]
    assert result.kept == [17]
    assert result.total == 19


def test_roll_d20_disadvantage_keeps_the_lower_die() -> None:
    rng = _FixedRandom([5, 17])
    result = roll_d20(modifier=2, rng=rng, disadvantage=True)  # type: ignore[arg-type]
    assert result.dice == [5, 17]
    assert result.kept == [5]
    assert result.total == 7


def test_roll_d20_advantage_and_disadvantage_cancel_out() -> None:
    rng = _FixedRandom([5])
    result = roll_d20(modifier=0, rng=rng, advantage=True, disadvantage=True)  # type: ignore[arg-type]
    assert len(result.dice) == 1

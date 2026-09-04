from src.engine.turn_order import next_turn, roll_initiative


class _FixedRandom:
    """Minimal random.Random stand-in returning a preset queue of d20 rolls,
    one per roll_initiative call, in dict-iteration order."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def test_roll_initiative_orders_by_total_then_dex_mod_then_id() -> None:
    # Raw d20s (before modifier), in dict-insertion order:
    # pc_fighter=10(+1)=11, pc_rogue=15(+3)=18, pc_wizard=16(+2)=18,
    # goblin_1=12(+2)=14, goblin_2=12(+2)=14, goblin_3=5(+2)=7
    dex_modifiers = {
        "pc_fighter": 1,
        "pc_rogue": 3,
        "pc_wizard": 2,
        "goblin_1": 2,
        "goblin_2": 2,
        "goblin_3": 2,
    }
    rng = _FixedRandom([10, 15, 16, 12, 12, 5])

    order = roll_initiative(dex_modifiers, rng)  # type: ignore[arg-type]

    # 18 (pc_rogue, higher dex_mod wins the 18/18 tie) > 18 (pc_wizard)
    # > 14/14 tie broken by id (goblin_1 < goblin_2) > 11 (pc_fighter) > 7 (goblin_3)
    assert order == [
        "pc_rogue",
        "pc_wizard",
        "goblin_1",
        "goblin_2",
        "pc_fighter",
        "goblin_3",
    ]


def test_next_turn_advances_within_round() -> None:
    order = ["a", "b", "c"]
    idx, rnd = next_turn(order, current_turn=0, round_=1)
    assert (idx, rnd) == (1, 1)


def test_next_turn_wraps_to_new_round() -> None:
    order = ["a", "b", "c"]
    idx, rnd = next_turn(order, current_turn=2, round_=1)
    assert (idx, rnd) == (0, 2)

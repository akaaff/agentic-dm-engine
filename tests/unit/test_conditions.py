from src.engine.conditions import apply_condition, has_condition, remove_condition, tick_conditions
from src.engine.position import Position
from src.engine.state import Character, Condition


def _make_character() -> Character:
    return Character(
        id="thorin",
        name="Thorin",
        is_pc=True,
        hp=10,
        max_hp=10,
        ac=15,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Dwarf",
        class_="Fighter",
        background="Acolyte",
    )


def test_apply_condition_adds_it() -> None:
    character = _make_character()
    apply_condition(character, Condition(name="prone", duration_rounds=1))
    assert has_condition(character, "prone")


def test_apply_condition_does_not_stack_duplicates() -> None:
    character = _make_character()
    apply_condition(character, Condition(name="poisoned", duration_rounds=3))
    apply_condition(character, Condition(name="poisoned", duration_rounds=1))  # refresh
    assert len(character.conditions) == 1
    assert character.conditions[0].duration_rounds == 1


def test_remove_condition() -> None:
    character = _make_character()
    apply_condition(character, Condition(name="prone"))
    removed = remove_condition(character, "prone")
    assert removed is True
    assert not has_condition(character, "prone")


def test_remove_condition_returns_false_if_absent() -> None:
    character = _make_character()
    assert remove_condition(character, "prone") is False


def test_tick_conditions_decrements_and_expires() -> None:
    character = _make_character()
    apply_condition(character, Condition(name="prone", duration_rounds=2))
    apply_condition(character, Condition(name="frightened", duration_rounds=1))
    apply_condition(character, Condition(name="charmed", duration_rounds=None))

    expired_round_1 = tick_conditions(character)
    assert {c.name for c in expired_round_1} == {"frightened"}
    remaining_names = {c.name for c in character.conditions}
    assert remaining_names == {"prone", "charmed"}
    prone = next(c for c in character.conditions if c.name == "prone")
    assert prone.duration_rounds == 1

    expired_round_2 = tick_conditions(character)
    assert {c.name for c in expired_round_2} == {"prone"}
    assert {c.name for c in character.conditions} == {"charmed"}

"""Issue #119: healing a downed (0 HP) ally used to raise their HP but leave them
unconscious and still rolling death saves - only a natural-20 death save ever
cleared the condition."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.position import BattleMap, Position
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _cleric() -> Character:
    return create_character(
        character_id="ilsa",
        name="Ilsa",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-insight", "skill-medicine"],
        chosen_prepared_spells=["bless", "cure-wounds", "guiding-bolt", "healing-word"],
        chosen_equipment=["mace"],
        position=Position(x=0, y=0),
    )


def _fighter() -> Character:
    return create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=1, y=0),
    )


def _state() -> tuple[GameState, Character]:
    cleric, fighter = _cleric(), _fighter()
    state = GameState(
        encounter_id="t",
        characters={cleric.id: cleric, fighter.id: fighter},
        turn_order=[cleric.id, fighter.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=5, height=5, terrain=[["floor"] * 5 for _ in range(5)], spawn_points={}
        ),
    )
    return state, fighter


def _heal(state: GameState, spell: str = "cure-wounds") -> None:
    resolve_action(
        state,
        ParsedAction(
            actor="ilsa", verb="cast_spell", target="brannock", item_or_spell=spell, raw_text="x"
        ),
        _FixedRandom([5]),  # type: ignore[arg-type]
    )


def test_healing_a_downed_ally_wakes_them_and_resets_death_saves() -> None:
    state, fighter = _state()
    fighter.hp = 0
    apply_condition(fighter, Condition(name="unconscious", source="0 HP"))
    fighter.death_save_failures = 2
    fighter.death_save_successes = 1

    _heal(state)

    assert fighter.hp > 0
    assert not has_condition(fighter, "unconscious")
    assert (fighter.death_save_successes, fighter.death_save_failures) == (0, 0)
    assert not fighter.is_stable
    event = next(e for e in state.events if e.type == "condition_removed")
    assert event.actor == "brannock"
    assert event.payload["reason"] == "healed"


def test_a_bonus_action_heal_wakes_them_too() -> None:
    # Healing Word, the classic pick-up from range.
    state, fighter = _state()
    fighter.hp = 0
    apply_condition(fighter, Condition(name="unconscious", source="0 HP"))

    _heal(state, "healing-word")

    assert fighter.hp > 0 and not has_condition(fighter, "unconscious")


def test_a_stable_downed_ally_is_revived_by_healing_as_well() -> None:
    state, fighter = _state()
    fighter.hp = 0
    fighter.is_stable = True
    apply_condition(fighter, Condition(name="unconscious", source="0 HP"))

    _heal(state)

    assert not has_condition(fighter, "unconscious")
    assert fighter.is_stable is False


def test_healing_does_not_undo_sleep() -> None:
    state, fighter = _state()
    fighter.hp = 3
    apply_condition(fighter, Condition(name="unconscious", source="sleep", duration_rounds=10))

    _heal(state)

    assert has_condition(fighter, "unconscious")  # asleep, not dying
    assert not any(e.type == "condition_removed" for e in state.events)


def test_healing_a_conscious_ally_emits_no_wake_event() -> None:
    state, fighter = _state()
    fighter.hp = 3
    _heal(state)
    assert fighter.hp > 3
    assert not any(e.type == "condition_removed" for e in state.events)

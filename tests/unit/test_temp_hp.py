"""Issues #94 / #91: temporary hit points. They soak damage before real HP, never
stack (a grant only replaces them if larger), and last until a long rest. False
Life grants 1d4 + 4; the Fiend warlock's Dark One's Blessing grants CHA mod +
level when it reduces a hostile to 0 HP."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.resting import apply_long_rest
from src.engine.rules import spell_mechanic
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, grant_temp_hp, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    wizard = create_character(
        character_id="elara",
        name="Elara",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "shield", "sleep"],
        chosen_equipment=["dagger"],
        position=Position(x=0, y=0),
    )
    wizard.prepared_spells.append("false-life")
    wizard.spell_slots[1] = 2
    wizard.ac = 10  # a hit is a hit
    return wizard


def _warlock() -> Character:
    return create_character(
        character_id="vex",
        name="Vex",
        race_index="human",
        class_index="warlock",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        chosen_skills=["skill-arcana", "skill-intimidation"],
        chosen_spells=["charm-person", "burning-hands"],
        position=Position(x=0, y=0),
    )


def _state(hero: Character, goblin_hp: int = 100) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=1, y=0))
    goblin.hp = goblin.max_hp = goblin_hp
    return GameState(
        encounter_id="temp_hp_test",
        characters={hero.id: hero, goblin.id: goblin},
        turn_order=[goblin.id, hero.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _goblin_hits() -> ParsedAction:
    return ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="elara",
        item_or_spell="Scimitar",
        raw_text="the goblin slashes",
    )


def test_temporary_hit_points_do_not_stack() -> None:
    wizard = _wizard()
    state = _state(wizard)
    assert grant_temp_hp(state, wizard, wizard, 5, "test") is True
    assert grant_temp_hp(state, wizard, wizard, 3, "test") is False  # smaller: ignored
    assert wizard.temp_hp == 5
    assert grant_temp_hp(state, wizard, wizard, 8, "test") is True  # larger: replaces, not 13
    assert wizard.temp_hp == 8


def test_damage_is_soaked_by_temp_hp_before_real_hp() -> None:
    wizard = _wizard()
    state = _state(wizard)
    hp_before = wizard.hp
    wizard.temp_hp = 2
    # d20 15 hits AC 10; goblin scimitar 1d6+2: die 4 -> 6 damage, 2 soaked.
    resolve_action(state, _goblin_hits(), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert wizard.temp_hp == 0
    assert wizard.hp == hp_before - 4
    soaked = next(e for e in state.events if e.type == "temp_hp")
    assert soaked.payload["change"] == -2


def test_a_hit_smaller_than_the_temp_hp_leaves_real_hp_alone() -> None:
    wizard = _wizard()
    state = _state(wizard)
    hp_before = wizard.hp
    wizard.temp_hp = 20
    resolve_action(state, _goblin_hits(), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert wizard.hp == hp_before
    assert wizard.temp_hp == 14


def _cast_false_life(target: str | None = None) -> ParsedAction:
    return ParsedAction(
        actor="elara",
        verb="cast_spell",
        target=target,
        item_or_spell="false life",
        raw_text="I cast false life",
    )


def test_false_life_is_not_a_heal_spell_any_more() -> None:
    assert spell_mechanic(load_srd().spells["false-life"]) is None


def test_false_life_grants_1d4_plus_4_temporary_hit_points() -> None:
    wizard = _wizard()
    state = _state(wizard)
    state.turn_order = ["elara", "goblin_1"]
    resolve_action(state, _cast_false_life(), _FixedRandom([3]))  # type: ignore[arg-type]
    assert wizard.temp_hp == 7
    assert wizard.spell_slots[1] == 1
    grant = next(e for e in state.events if e.type == "temp_hp")
    assert grant.payload["source"] == "False Life"


def test_false_life_cannot_target_someone_else() -> None:
    wizard = _wizard()
    state = _state(wizard)
    state.turn_order = ["elara", "goblin_1"]
    with pytest.raises(TurnEngineError, match="only be cast on yourself"):
        resolve_action(state, _cast_false_life("goblin_1"), _FixedRandom([3]))  # type: ignore[arg-type]
    assert wizard.spell_slots[1] == 2


def test_a_warlock_gains_temp_hp_for_a_kill() -> None:
    warlock = _warlock()
    state = _state(warlock, goblin_hp=1)
    state.turn_order = ["vex", "goblin_1"]
    cast = ParsedAction(
        actor="vex",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="eldritch blast",
        raw_text="x",
    )
    resolve_action(state, cast, _FixedRandom([18, 18, 6]))  # type: ignore[arg-type]
    assert state.characters["goblin_1"].is_dead
    assert warlock.temp_hp == 4  # CHA 15 + 1 = 16 (+3) + level 1
    blessing = next(e for e in state.events if e.type == "temp_hp")
    assert blessing.payload["source"] == "Dark One's Blessing"


def test_dark_ones_blessing_needs_a_kill_and_a_warlock() -> None:
    warlock = _warlock()
    state = _state(warlock, goblin_hp=100)
    state.turn_order = ["vex", "goblin_1"]
    cast = ParsedAction(
        actor="vex",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="eldritch blast",
        raw_text="x",
    )
    resolve_action(state, cast, _FixedRandom([18, 18, 6]))  # type: ignore[arg-type]
    assert warlock.temp_hp == 0  # the goblin survived


def test_a_long_rest_clears_temporary_hit_points() -> None:
    wizard = _wizard()
    wizard.temp_hp = 9
    apply_long_rest([wizard])
    assert wizard.temp_hp == 0

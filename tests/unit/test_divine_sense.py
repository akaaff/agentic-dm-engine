"""Issue #87: Paladin's Divine Sense - an action, 1 + CHA modifier uses per long rest,
that reveals every celestial, fiend and undead within 60 ft. (Also covers the long-rest
fix found alongside it: a rest rebuilt class_resources from a flat table and wiped the
Charisma-keyed ones - Bardic Inspiration included.)"""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.resting import apply_long_rest
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values: list[int] = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _paladin() -> Character:
    # CHA 14 + human 1 = 15 (+2): 1 + 2 = 3 uses.
    return create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="paladin",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 10, "CON": 13, "INT": 8, "WIS": 12, "CHA": 14},
        chosen_skills=["skill-athletics", "skill-religion"],
        position=Position(x=0, y=0),
    )


def _state(paladin: Character, monsters: list[tuple[str, str, int]]) -> GameState:
    srd = load_srd()
    chars = {paladin.id: paladin}
    for index, char_id, x in monsters:
        chars[char_id] = monster_to_character(srd.monsters[index], char_id, Position(x=x, y=0))
    return GameState(
        encounter_id="divine_sense_test",
        characters=chars,
        turn_order=list(chars),
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=30, height=10, terrain=[["floor"] * 30 for _ in range(10)], spawn_points={}
        ),
    )


def _sense() -> ParsedAction:
    return ParsedAction(actor="brannock", verb="divine_sense", raw_text="I use divine sense")


def test_a_paladin_gets_one_plus_cha_uses() -> None:
    assert _paladin().class_resources["divine_sense"] == 3


def test_it_detects_undead_fiends_and_celestials_within_60_feet_only() -> None:
    paladin = _paladin()
    state = _state(
        paladin,
        [
            ("zombie", "zombie_1", 4),  # 20 ft, undead
            ("imp", "imp_1", 10),  # 50 ft, fiend
            ("goblin", "goblin_1", 2),  # humanoid - not detected
            ("skeleton", "skeleton_far", 20),  # 100 ft - out of range
        ],
    )
    resolve_action(state, _sense(), _FixedRandom([]))  # type: ignore[arg-type]

    event = next(e for e in state.events if e.type == "divine_sense")
    assert {d["id"]: d["kind"] for d in event.payload["detected"]} == {
        "zombie_1": "undead",
        "imp_1": "fiend",
    }
    assert event.payload["range"] == 60
    assert paladin.class_resources["divine_sense"] == 2


def test_it_is_an_action_that_ends_the_turn_and_a_dead_creature_is_not_sensed() -> None:
    paladin = _paladin()
    state = _state(paladin, [("zombie", "zombie_1", 4), ("zombie", "zombie_2", 5)])
    state.characters["zombie_2"].is_dead = True
    resolve_action(state, _sense(), _FixedRandom([]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "divine_sense")
    assert [d["id"] for d in event.payload["detected"]] == ["zombie_1"]
    assert state.turn_order[state.current_turn] == "zombie_1"


def test_it_finds_nothing_when_nothing_is_there() -> None:
    paladin = _paladin()
    state = _state(paladin, [("goblin", "goblin_1", 2)])
    resolve_action(state, _sense(), _FixedRandom([]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "divine_sense")
    assert event.payload["detected"] == []


def test_it_stops_when_the_uses_run_out() -> None:
    paladin = _paladin()
    paladin.class_resources["divine_sense"] = 0
    state = _state(paladin, [])
    with pytest.raises(TurnEngineError, match="divine sense uses remaining"):
        resolve_action(state, _sense(), _FixedRandom([]))  # type: ignore[arg-type]


def test_only_a_paladin_has_it() -> None:
    fighter = create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        position=Position(x=0, y=0),
    )
    state = _state(fighter, [])
    with pytest.raises(TurnEngineError, match="doesn't have Divine Sense"):
        resolve_action(state, _sense(), _FixedRandom([]))  # type: ignore[arg-type]


def test_a_long_rest_refills_divine_sense_to_one_plus_cha() -> None:
    paladin = _paladin()
    paladin.class_resources["divine_sense"] = 0
    apply_long_rest([paladin])
    assert paladin.class_resources["divine_sense"] == 3


def test_a_long_rest_no_longer_wipes_bardic_inspiration() -> None:
    bard = create_character(
        character_id="pip",
        name="Pip",
        race_index="human",
        class_index="bard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        chosen_skills=["skill-insight", "skill-performance", "skill-persuasion"]
        + ["lute", "flute", "lyre"],
        chosen_spells=["cure-wounds", "healing-word", "thunderwave", "sleep"],
        position=Position(x=0, y=0),
    )
    assert bard.class_resources["bardic_inspiration"] == 3  # CHA 16 -> +3
    bard.class_resources["bardic_inspiration"] = 0
    apply_long_rest([bard])
    assert bard.class_resources["bardic_inspiration"] == 3

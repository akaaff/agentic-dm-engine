"""Issue #91: the Warlock now chooses its known spells at creation (2 at level 1,
from the class list plus the Fiend patron's expanded list - the SRD's only
patron) and may only cast those, exactly like the Bard and Sorcerer. Before, a
warlock took no choice and could cast anything, Cure Wounds included."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.engine.actions import ParsedAction
from src.engine.character_creation import (
    SPELLS_KNOWN_BY_LEVEL,
    CharacterCreationError,
    create_character,
    level_up,
)
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _warlock(spells: list[str] | None) -> Character:
    return create_character(
        character_id="vex",
        name="Vex",
        race_index="human",
        class_index="warlock",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        chosen_skills=["skill-arcana", "skill-intimidation"],
        chosen_spells=spells,
        position=Position(x=0, y=0),
    )


def test_the_warlock_knows_two_spells_at_level_one() -> None:
    assert SPELLS_KNOWN_BY_LEVEL["warlock"][1] == 2
    assert _warlock(["charm-person", "hellish-rebuke"]).known_spells == [
        "charm-person",
        "hellish-rebuke",
    ]


def test_the_fiend_patrons_spells_are_on_the_list() -> None:
    # Burning Hands and Command aren't tagged Warlock in the vendored data.
    assert _warlock(["burning-hands", "command"]).known_spells == ["burning-hands", "command"]


@pytest.mark.parametrize(
    ("spells", "message"),
    [
        (None, "requires chosen_spells"),
        (["charm-person"], "exactly 2"),
        (["charm-person", "cure-wounds"], "cure-wounds"),
        (["charm-person", "charm-person"], "Duplicate spell choice"),
    ],
)
def test_the_spell_choice_is_validated(spells: list[str] | None, message: str) -> None:
    with pytest.raises(CharacterCreationError, match=message):
        _warlock(spells)


def _state(warlock: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=1, y=0))
    goblin.hp = goblin.max_hp = 100
    return GameState(
        encounter_id="warlock_test",
        characters={warlock.id: warlock, goblin.id: goblin},
        turn_order=[warlock.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _cast(spell: str, target: str) -> ParsedAction:
    return ParsedAction(
        actor="vex", verb="cast_spell", target=target, item_or_spell=spell, raw_text="x"
    )


def test_a_warlock_cannot_cast_a_spell_it_does_not_know() -> None:
    warlock = _warlock(["charm-person", "hellish-rebuke"])
    state = _state(warlock)
    with pytest.raises(TurnEngineError, match="doesn't know Cure Wounds"):
        resolve_action(state, _cast("cure wounds", "vex"), _FixedRandom([]))  # type: ignore[arg-type]
    assert warlock.spell_slots == {1: 1}  # nothing spent


def test_a_known_spell_and_a_cantrip_still_cast() -> None:
    warlock = _warlock(["charm-person", "hellish-rebuke"])
    state = _state(warlock)
    resolve_action(state, _cast("eldritch blast", "goblin_1"), _FixedRandom([15, 5]))  # type: ignore[arg-type]
    assert any(e.type == "spell_cast" for e in state.events)


def test_a_level_up_can_learn_a_spell() -> None:
    warlock = _warlock(["charm-person", "hellish-rebuke"])
    level_up(warlock, load_srd(), spells_learned=["burning-hands"])
    assert warlock.known_spells[-1] == "burning-hands"
    assert len(warlock.known_spells) == 3


def test_the_class_detail_endpoint_offers_the_warlock_picker() -> None:
    detail = TestClient(app).get("/characters/classes/warlock").json()
    assert detail["spells_known"] == 2
    pool = {s["index"] for s in detail["known_spells_pool"]}
    assert {"charm-person", "hellish-rebuke", "burning-hands", "command"} <= pool
    assert "cure-wounds" not in pool

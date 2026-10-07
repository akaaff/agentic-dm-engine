"""Issue #88: the Life Domain, the SRD's only cleric domain (so every SRD cleric
has it). Disciple of Life adds 2 + the spell's level to a cleric's healing
spells; clerics wear heavy armor; Bless and Cure Wounds are always prepared and
don't count against the number of spells prepared."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.engine.actions import ParsedAction
from src.engine.character_creation import (
    ALWAYS_PREPARED_SPELLS,
    CharacterCreationError,
    create_character,
)
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _cleric(prepared: list[str] | None = None, equipment: list[str] | None = None) -> Character:
    # WIS 15 + human 1 = 16 (+3): prepares 3 + 1 = 4 spells beyond the domain's two.
    return create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
        chosen_prepared_spells=prepared
        or ["healing-word", "shield-of-faith", "guiding-bolt", "sanctuary"],
        chosen_equipment=equipment or [],
        position=Position(x=0, y=0),
    )


def test_bless_and_cure_wounds_are_always_prepared_on_top_of_the_picks() -> None:
    mira = _cleric()
    assert ALWAYS_PREPARED_SPELLS["cleric"] == ["bless", "cure-wounds"]
    assert mira.prepared_spells == [
        "bless",
        "cure-wounds",
        "healing-word",
        "shield-of-faith",
        "guiding-bolt",
        "sanctuary",
    ]


def test_naming_a_domain_spell_among_the_picks_is_ignored_not_counted() -> None:
    # Four non-domain picks are still required; Bless among them doesn't count.
    with pytest.raises(CharacterCreationError, match="exactly 4 prepared spell"):
        _cleric(["bless", "healing-word", "shield-of-faith", "guiding-bolt"])
    mira = _cleric(["bless", "healing-word", "shield-of-faith", "guiding-bolt", "sanctuary"])
    assert mira.prepared_spells.count("bless") == 1


def test_a_cleric_may_wear_heavy_armor() -> None:
    mira = _cleric(equipment=["chain-mail"])
    assert mira.equipped_armor == "chain-mail"
    assert mira.ac == 18  # chain mail 16 (ignores DEX) + the starting shield 2


def test_other_classes_still_cannot_wear_heavy_armor_they_lack() -> None:
    with pytest.raises(CharacterCreationError, match="isn't proficient"):
        create_character(
            character_id="elara",
            name="Elara",
            race_index="elf",
            class_index="wizard",
            background_index="acolyte",
            base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
            chosen_skills=["skill-arcana", "skill-history"],
            chosen_prepared_spells=["magic-missile", "shield", "sleep"],
            chosen_equipment=["chain-mail"],
        )


def _state(mira: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=5, y=0))
    mira.hp, mira.max_hp = 1, 30
    return GameState(
        encounter_id="life_domain_test",
        characters={mira.id: mira, goblin.id: goblin},
        turn_order=[mira.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _heal(spell: str) -> ParsedAction:
    return ParsedAction(
        actor="mira", verb="cast_spell", target="mira", item_or_spell=spell, raw_text="heal"
    )


def test_disciple_of_life_adds_two_plus_the_spell_level_to_cure_wounds() -> None:
    mira = _cleric()
    state = _state(mira)
    resolve_action(state, _heal("cure wounds"), _FixedRandom([5]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "hp_change")
    assert event.payload["amount"] == 5 + 3 + 3  # d8 + WIS 3 + Disciple (2 + level 1)
    assert event.payload["disciple_of_life"] == 3
    assert mira.hp == 12


def test_a_non_cleric_healer_gets_no_disciple_of_life() -> None:
    # The same character with another caster's class: Cure Wounds is on the
    # prepared list either way, but Disciple of Life is a cleric feature.
    druid = _cleric()
    druid.class_index = "druid"
    state = _state(druid)
    resolve_action(state, _heal("cure wounds"), _FixedRandom([5]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "hp_change")
    assert "disciple_of_life" not in event.payload
    assert event.payload["amount"] == 5 + 3


def test_the_class_endpoint_lists_the_always_prepared_spells() -> None:
    detail = TestClient(app).get("/characters/classes/cleric").json()
    assert detail["always_prepared_spells"] == ["bless", "cure-wounds"]
    assert TestClient(app).get("/characters/classes/wizard").json()["always_prepared_spells"] == []

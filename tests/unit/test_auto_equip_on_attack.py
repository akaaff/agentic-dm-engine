"""Issue #83: an attack naming a weapon the actor owns but hasn't equipped draws
it first (a free object interaction in 5e), instead of being rejected with
"use 'equip' first". One equip per turn and legal hand combinations still apply;
a weapon the actor doesn't own is rejected exactly as before."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
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


def _fighter(equipped: list[str], carried: list[str]) -> Character:
    fighter = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        position=Position(x=0, y=0),
    )
    fighter.inventory = [*dict.fromkeys([*equipped, *carried, *fighter.inventory])]
    fighter.equipped_weapons = list(equipped)
    fighter.equipped_shield = None
    return fighter


def _state(fighter: Character, distance: int = 2) -> GameState:
    goblin = monster_to_character(
        load_srd().monsters["goblin"], "goblin_1", Position(x=distance, y=0)
    )
    goblin.hp = goblin.max_hp = 100
    return GameState(
        encounter_id="auto_equip_test",
        characters={fighter.id: fighter, goblin.id: goblin},
        turn_order=[fighter.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=12, height=12, terrain=[["floor"] * 12 for _ in range(12)], spawn_points={}
        ),
    )


def _attack(weapon: str) -> ParsedAction:
    return ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell=weapon,
        raw_text=f"I attack with my {weapon}",
    )


def test_a_carried_bow_is_drawn_and_fired_in_one_attack() -> None:
    fighter = _fighter(["shortsword", "dagger"], ["shortbow"])
    state = _state(fighter)  # the goblin is 10ft away
    resolve_action(state, _attack("shortbow"), _FixedRandom([15, 4]))  # type: ignore[arg-type]

    equip = next(e for e in state.events if e.type == "equip")
    assert equip.payload["items"] == ["shortbow"]
    assert fighter.equipped_weapons == ["shortbow"]  # two light weapons + a bow is too many
    assert fighter.equip_used_this_turn is True
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["source"] == "Shortbow"
    assert state.events.index(equip) < state.events.index(attack)


def test_a_second_light_weapon_joins_the_one_in_hand() -> None:
    fighter = _fighter(["dagger"], ["shortsword"])
    state = _state(fighter, distance=1)
    resolve_action(state, _attack("shortsword"), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert fighter.equipped_weapons == ["dagger", "shortsword"]


def test_a_two_handed_weapon_replaces_the_set() -> None:
    fighter = _fighter(["longsword"], ["greataxe"])
    state = _state(fighter, distance=1)
    resolve_action(state, _attack("greataxe"), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert fighter.equipped_weapons == ["greataxe"]


def test_a_weapon_already_in_hand_is_not_equipped_again() -> None:
    fighter = _fighter(["longsword"], ["dagger"])
    state = _state(fighter, distance=1)
    resolve_action(state, _attack("longsword"), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert not any(e.type == "equip" for e in state.events)
    assert fighter.equip_used_this_turn is False


def test_a_weapon_that_is_not_owned_is_still_rejected() -> None:
    fighter = _fighter(["longsword"], [])
    state = _state(fighter, distance=1)
    with pytest.raises(TurnEngineError, match="isn't in thorin's equipped weapon set"):
        resolve_action(state, _attack("greataxe"), _FixedRandom([]))  # type: ignore[arg-type]
    assert not any(e.type == "equip" for e in state.events)


def test_the_free_draw_is_once_per_turn() -> None:
    fighter = _fighter(["longsword"], ["dagger"])
    fighter.equip_used_this_turn = True
    state = _state(fighter, distance=1)
    with pytest.raises(TurnEngineError, match="already equipped something this turn"):
        resolve_action(state, _attack("dagger"), _FixedRandom([]))  # type: ignore[arg-type]
    assert fighter.equipped_weapons == ["longsword"]

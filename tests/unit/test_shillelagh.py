"""Issue #95: Shillelagh. A bonus-action cantrip: the club or quarterstaff the
caster holds uses the spellcasting ability (WIS for a druid) for melee attack
and damage and its die becomes a d8, for a minute; it ends if the weapon is put
down. Dice are fixed so every total is hand-computable: the druid is a human
with WIS 15+1 (+3), STR 8+1 (-1), proficient with the quarterstaff (+2)."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action, shillelagh_weapon


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _druid(weapon: str = "quarterstaff") -> Character:
    druid = create_character(
        character_id="mara",
        name="Mara",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 13, "CON": 14, "INT": 10, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-survival"],
        chosen_prepared_spells=["cure-wounds", "entangle", "goodberry", "healing-word"],
        chosen_equipment=[weapon],
        position=Position(x=0, y=0),
    )
    druid.equipped_weapons = [weapon]
    return druid


def _state(druid: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=1, y=0))
    goblin.hp = goblin.max_hp = 100
    return GameState(
        encounter_id="shillelagh_test",
        characters={druid.id: druid, goblin.id: goblin},
        turn_order=[druid.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _cast(item: str = "shillelagh") -> ParsedAction:
    return ParsedAction(
        actor="mara", verb="cast_spell", item_or_spell=item, raw_text="I cast shillelagh"
    )


def _attack() -> ParsedAction:
    return ParsedAction(
        actor="mara",
        verb="attack",
        target="goblin_1",
        item_or_spell="quarterstaff",
        raw_text="I bash it",
    )


def test_casting_it_is_a_bonus_action_that_empowers_the_held_weapon() -> None:
    druid = _druid()
    state = _state(druid)
    resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]

    assert shillelagh_weapon(druid) == "quarterstaff"
    assert has_condition(druid, "shillelagh")
    assert druid.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "mara"  # the main action is still there
    assert any(e.type == "spell_cast" and e.payload["spell"] == "Shillelagh" for e in state.events)


def test_the_empowered_staff_uses_wisdom_and_a_d8() -> None:
    druid = _druid()
    state = _state(druid)
    resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]
    resolve_action(state, _attack(), _FixedRandom([15, 5]))  # type: ignore[arg-type]

    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["roll_total"] == 20  # 15 + WIS 3 + proficiency 2
    assert ["WIS mod", 3] in [list(b) for b in attack.payload["attack_bonus_breakdown"]]
    damage = next(e for e in state.events if e.type == "damage_dealt")
    assert damage.payload["amount"] == 8  # d8 (5) + WIS 3


def test_without_the_spell_the_staff_is_a_plain_strength_weapon() -> None:
    druid = _druid()
    state = _state(druid)
    resolve_action(state, _attack(), _FixedRandom([15, 5]))  # type: ignore[arg-type]

    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["roll_total"] == 16  # 15 + STR -1 + proficiency 2
    damage = next(e for e in state.events if e.type == "damage_dealt")
    assert damage.payload["amount"] == 4  # d6 (5) + STR -1


def test_putting_the_weapon_down_ends_the_spell() -> None:
    druid = _druid()
    druid.inventory.append("dagger")
    state = _state(druid)
    resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]
    resolve_action(
        state,
        ParsedAction(actor="mara", verb="equip", params={"items": ["dagger"]}, raw_text="x"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert not has_condition(druid, "shillelagh")


def test_it_needs_a_club_or_quarterstaff_in_hand() -> None:
    druid = _druid("dagger")
    state = _state(druid)
    with pytest.raises(TurnEngineError, match="holding a club or a quarterstaff"):
        resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]
    assert not druid.bonus_action_used


def test_casting_it_again_replaces_it() -> None:
    druid = _druid()
    state = _state(druid)
    resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]
    druid.bonus_action_used = False
    resolve_action(state, _cast(), _FixedRandom([]))  # type: ignore[arg-type]
    assert sum(1 for c in druid.conditions if c.name == "shillelagh") == 1

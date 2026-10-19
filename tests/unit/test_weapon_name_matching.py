"""A player naming a weapon loosely - "crossbow" for "Crossbow, hand", "sword" for the
longsword they are holding - gets the equipped weapon they meant (live-reported: only the exact
text "crossbow, hand" worked). Ties are refused rather than guessed, and naming a different real
weapon still is not treated as a vague reference to this one."""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    TurnEngineError,
    _fuzzy_weapon_matches,
    _match_weapon_by_name,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _items(*indices: str) -> list[dict[str, Any]]:
    srd = load_srd()
    return [srd.equipment[i] for i in indices]


def _index(name: str, *held: str) -> str | None:
    match = _match_weapon_by_name(name, _items(*held))
    return match["index"] if match else None


def test_a_partial_name_picks_the_equipped_weapon_it_is_part_of() -> None:
    held = ("crossbow-hand", "dagger")
    assert _index("crossbow", *held) == "crossbow-hand"
    assert _index("hand crossbow", *held) == "crossbow-hand"
    assert _index("crossbow, hand", *held) == "crossbow-hand"  # the form that always worked
    assert _index("my trusty crossbow", *held) == "crossbow-hand"


def test_sword_means_the_one_sword_that_is_held() -> None:
    assert _index("sword", "longsword", "dagger") == "longsword"
    assert _index("swords", "shortsword", "dagger") == "shortsword"
    assert _index("greatsword", "greatsword") == "greatsword"
    assert _index("axe", "battleaxe", "dagger") == "battleaxe"


def test_a_tie_is_not_guessed() -> None:
    held = _items("shortsword", "longsword")
    assert _match_weapon_by_name("sword", held) is None
    assert {m["index"] for m in _fuzzy_weapon_matches("sword", held)} == {"shortsword", "longsword"}
    specific = _match_weapon_by_name("longsword", held)
    assert specific is not None and specific["index"] == "longsword"  # specific is fine


def test_the_same_weapon_twice_is_not_a_tie() -> None:
    assert _index("dagger", "dagger", "dagger") == "dagger"  # a dual-wielder's pair
    assert _index("blade", "dagger", "dagger") is None  # nothing says "blade"


def test_filler_words_never_match_a_weapon() -> None:
    held = ("crossbow-hand", "dagger")
    assert _index("my left hand", *held) is None  # "hand" is part of "Crossbow, hand"
    assert _index("with the second hand", *held) is None
    assert _index("handaxe", *held) is None


def test_naming_a_different_real_weapon_is_not_a_vague_reference_to_this_one() -> None:
    assert _index("crossbow-light", "crossbow-hand", "dagger") is None
    assert _index("light crossbow", "crossbow-hand", "dagger") is None
    assert _index("greatsword", "shortsword") is None


def _state(*held: str) -> GameState:
    fighter = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    fighter.position = Position(x=0, y=0)
    fighter.equipped_weapons = list(held)
    fighter.inventory = [*fighter.inventory, *held]
    goblin = Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=99,  # always a miss, so only the d20 is consumed
        level=1,
        position=Position(x=4, y=0),  # 20ft: out of melee, well inside a crossbow's range
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )
    return GameState(
        encounter_id="weapon_match_test",
        characters={"thorin": fighter, "goblin_1": goblin},
        turn_order=["thorin", "goblin_1"],
        current_turn=0,
        round=1,
    )


def _attack(state: GameState, weapon: str) -> str:
    action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell=weapon, raw_text="x"
    )
    resolve_action(state, action, _FixedRandom([7]))  # type: ignore[arg-type]
    return str(next(e for e in state.events if e.type == "attack_roll").payload["source"])


def test_an_attack_naming_part_of_the_weapon_uses_it() -> None:
    assert _attack(_state("crossbow-hand", "dagger"), "crossbow") == "Crossbow, hand"


def test_an_ambiguous_name_is_refused_with_the_choices() -> None:
    state = _state("shortsword", "longsword")
    with pytest.raises(TurnEngineError, match="could mean Shortsword or Longsword"):
        _attack(state, "sword")
    assert not state.events  # nothing was rolled or changed

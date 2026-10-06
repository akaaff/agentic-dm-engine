"""Issue #92 (class playtest): Great Weapon Fighting and Two-Weapon Fighting were
rejected at creation (3 of the 6 SRD fighting styles, including the most common
level-1 fighter build). Protection still needs a reaction and stays rejected."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import CharacterCreationError, create_character
from src.engine.dice import roll
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import _pc_attack_params, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter(style: str | None, equipment: list[str]) -> Character:
    return create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=equipment,
        fighting_style=style,
        position=Position(x=0, y=0),
    )


def _ogre_state(fighter: Character) -> GameState:
    ogre = monster_to_character(load_srd().monsters["ogre"], "ogre_1", Position(x=1, y=0))
    return GameState(
        encounter_id="t",
        characters={fighter.id: fighter, ogre.id: ogre},
        turn_order=[fighter.id, ogre.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=5, height=5, terrain=[["floor"] * 5 for _ in range(5)], spawn_points={}
        ),
    )


def _attack_damage(fighter: Character, rng: list[int], item: str | None = None) -> int:
    state = _ogre_state(fighter)
    resolve_action(
        state,
        ParsedAction(
            actor="brannock", verb="attack", target="ogre_1", item_or_spell=item, raw_text="x"
        ),
        _FixedRandom(rng),  # type: ignore[arg-type]
    )
    return sum(e.payload["amount"] for e in state.events if e.type == "damage_dealt")


# ----------------------------------------------------------------- dice.roll


def test_roll_rerolls_dice_at_or_below_the_threshold_once() -> None:
    result = roll(2, 6, modifier=3, rng=_FixedRandom([1, 5, 4]), reroll_at_or_below=2)  # type: ignore[arg-type]
    assert result.dice == [4, 5]
    assert result.total == 12


def test_the_reroll_is_kept_even_if_it_is_low_again() -> None:
    result = roll(2, 6, rng=_FixedRandom([1, 5, 2]), reroll_at_or_below=2)  # type: ignore[arg-type]
    assert result.dice == [2, 5]


def test_no_threshold_draws_no_extra_dice() -> None:
    result = roll(2, 6, rng=_FixedRandom([1, 1]))  # type: ignore[arg-type]
    assert result.dice == [1, 1]


# --------------------------------------------------------- Great Weapon Fighting


def test_gwf_rerolls_ones_and_twos_on_a_two_handed_weapons_damage() -> None:
    # Greatsword 2d6 + STR 3. d20 15 hits the ogre (AC 11). Dice [1, 5]: the 1
    # is rerolled into a 4 -> 4 + 5 + 3 = 12.
    fighter = _fighter("great-weapon-fighting", ["greatsword"])
    assert _attack_damage(fighter, [15, 1, 5, 4]) == 12


def test_without_the_style_the_low_die_stands() -> None:
    fighter = _fighter("defense", ["greatsword"])
    assert _attack_damage(fighter, [15, 1, 5]) == 1 + 5 + 3


def test_gwf_applies_to_each_die_of_a_critical_hit() -> None:
    # Natural 20: 4 dice [2, 6, 1, 6]; the 2 and the 1 are rerolled (3, 4).
    fighter = _fighter("great-weapon-fighting", ["greatsword"])
    assert _attack_damage(fighter, [20, 2, 6, 1, 6, 3, 4]) == 3 + 6 + 4 + 6 + 3


def test_gwf_does_not_apply_to_a_one_handed_weapon_with_a_shield() -> None:
    fighter = _fighter("great-weapon-fighting", ["longsword", "shield"])
    assert fighter.equipped_shield == "shield"
    assert _pc_attack_params(fighter, "longsword", load_srd()).damage_reroll_at_or_below == 0


def test_gwf_applies_to_a_versatile_weapon_held_in_both_hands() -> None:
    fighter = _fighter("great-weapon-fighting", ["longsword"])
    assert fighter.equipped_shield is None
    assert _pc_attack_params(fighter, "longsword", load_srd()).damage_reroll_at_or_below == 2


def test_gwf_does_not_apply_to_a_ranged_weapon() -> None:
    fighter = _fighter("great-weapon-fighting", ["longbow"])
    assert _pc_attack_params(fighter, "longbow", load_srd()).damage_reroll_at_or_below == 0


# ------------------------------------------------------- Two-Weapon Fighting


def test_a_real_off_hand_attack_gets_the_modifier_only_with_the_style() -> None:
    def off_hand_damage(style: str) -> int:
        fighter = _fighter(style, ["shortsword", "dagger"])
        state = _ogre_state(fighter)
        resolve_action(
            state,
            ParsedAction(actor="brannock", verb="offhand_attack", target="ogre_1", raw_text="x"),
            _FixedRandom([15, 3]),  # type: ignore[arg-type]
        )
        return sum(e.payload["amount"] for e in state.events if e.type == "damage_dealt")

    with_style = off_hand_damage("two-weapon-fighting")
    without = off_hand_damage("defense")
    assert without == 3  # the die alone: no ability modifier on the off-hand
    assert with_style == 3 + 3  # + the modifier (STR 15 +1 human = 16 -> +3)


# ------------------------------------------------------------------ creation


def test_creation_accepts_the_two_new_styles_and_still_rejects_protection() -> None:
    _fighter("great-weapon-fighting", ["greatsword"])
    _fighter("two-weapon-fighting", ["shortsword", "dagger"])
    with pytest.raises(CharacterCreationError, match="protection"):
        _fighter("protection", ["longsword", "shield"])

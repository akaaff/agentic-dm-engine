"""Issue #92: the Protection fighting style. When a monster attacks a PC who has
a Protection fighter - wielding a shield, reaction unspent - within 5ft, the
attack pauses *before it is rolled* (ProtectionChoicePending); using the
reaction re-enters the attack with disadvantage, declining rolls it normally.

Dice are fixed: the goblin's Scimitar is +4 to hit, 1d6+2 damage; the wizard
being protected is AC 13."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import (
    ProtectionChoicePending,
    ShieldChoicePending,
    eligible_protector,
    resolve_action,
    resolve_pending_protection_choice,
    resolve_pending_shield_choice,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    wizard = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 15, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["shield", "magic-missile", "sleep"],
        position=Position(x=0, y=0),
    )
    wizard.prepared_spells = ["magic-missile"]  # no Shield unless a test adds it
    wizard.ac = 13
    return wizard


def _fighter(style: str | None = "protection", position: Position | None = None) -> Character:
    fighter = create_character(
        character_id="grom",
        name="Grom",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        fighting_style=style,
        position=position or Position(x=1, y=0),
    )
    fighter.equipped_shield = "shield"
    return fighter


def _state(wizard: Character, fighter: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=0, y=1))
    return GameState(
        encounter_id="protection_test",
        characters={goblin.id: goblin, wizard.id: wizard, fighter.id: fighter},
        turn_order=["goblin_1", "elrond", "grom"],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10,
            height=10,
            terrain=[["floor"] * 10 for _ in range(10)],
            spawn_points={},
        ),
    )


def _goblin_attacks_wizard() -> ParsedAction:
    return ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="elrond",
        item_or_spell="Scimitar",
        raw_text="the goblin slashes",
    )


def _pause(state: GameState, rng: _FixedRandom) -> ProtectionChoicePending:
    with pytest.raises(ProtectionChoicePending) as excinfo:
        resolve_action(state, _goblin_attacks_wizard(), rng)  # type: ignore[arg-type]
    return excinfo.value


def test_an_attack_on_an_ally_pauses_before_anything_is_rolled() -> None:
    state = _state(_wizard(), _fighter())
    rng = _FixedRandom([])  # a roll here would raise
    pause = _pause(state, rng)

    assert pause.choice.attacker_id == "goblin_1"
    assert pause.choice.target_id == "elrond"
    assert pause.choice.protector_id == "grom"
    assert state.events == []


def test_using_protection_spends_the_reaction_and_rolls_with_disadvantage() -> None:
    wizard, fighter = _wizard(), _fighter()
    state = _state(wizard, fighter)
    pause = _pause(state, _FixedRandom([]))
    # Disadvantage: [18, 3] keeps the 3 -> 7 vs AC 13, a miss.
    resolve_pending_protection_choice(
        state,
        pause.choice,
        True,
        _FixedRandom([18, 3]),  # type: ignore[arg-type]
        load_srd(),
    )

    assert fighter.reaction_used_this_round is True
    assert any(e.type == "protection" and e.actor == "grom" for e in state.events)
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["natural"] == 3
    assert attack.payload["hit"] is False
    assert wizard.hp == wizard.max_hp
    assert state.turn_order[state.current_turn] == "elrond"  # the goblin's turn ended


def test_declining_rolls_the_attack_normally() -> None:
    wizard, fighter = _wizard(), _fighter()
    state = _state(wizard, fighter)
    pause = _pause(state, _FixedRandom([]))
    resolve_pending_protection_choice(
        state,
        pause.choice,
        False,
        _FixedRandom([15, 4]),  # type: ignore[arg-type]
        load_srd(),
    )

    assert fighter.reaction_used_this_round is False
    assert not any(e.type == "protection" for e in state.events)
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["hit"] is True  # one d20 only: 15 + 4 = 19
    assert wizard.hp == wizard.max_hp - 6


def test_a_shield_pause_can_follow_the_protection_answer() -> None:
    # The wizard also has Shield: disadvantage [16, 12] keeps 12 -> 16 vs AC
    # 13, a hit that AC 18 would turn into a miss - so the re-rolled attack
    # pauses again, for Shield.
    wizard, fighter = _wizard(), _fighter()
    wizard.prepared_spells = ["shield"]
    wizard.spell_slots = {1: 2}
    state = _state(wizard, fighter)
    pause = _pause(state, _FixedRandom([]))
    rng = _FixedRandom([16, 12, 4])
    with pytest.raises(ShieldChoicePending) as excinfo:
        resolve_pending_protection_choice(state, pause.choice, True, rng, load_srd())  # type: ignore[arg-type]
    assert fighter.reaction_used_this_round is True

    resolve_pending_shield_choice(state, excinfo.value.choice, True, rng, load_srd())  # type: ignore[arg-type]
    assert wizard.ac == 18
    assert wizard.hp == wizard.max_hp


@pytest.mark.parametrize(
    "why",
    ["no_style", "no_shield", "reaction_spent", "too_far", "unconscious"],
)
def test_no_protector_without_the_means(why: str) -> None:
    fighter = _fighter("dueling" if why == "no_style" else "protection")
    if why == "no_shield":
        fighter.equipped_shield = None
    if why == "reaction_spent":
        fighter.reaction_used_this_round = True
    if why == "too_far":
        fighter.position = Position(x=3, y=0)
    if why == "unconscious":
        apply_condition(fighter, Condition(name="unconscious", source="0 HP"))
    wizard = _wizard()
    state = _state(wizard, fighter)
    assert eligible_protector(state, state.characters["goblin_1"], wizard) is None


def test_the_protector_does_not_protect_themselves_or_attack_their_own_side() -> None:
    wizard, fighter = _wizard(), _fighter()
    state = _state(wizard, fighter)
    goblin = state.characters["goblin_1"]
    assert eligible_protector(state, goblin, fighter) is None  # the fighter is the target
    assert eligible_protector(state, wizard, fighter) is None  # same side as the "attacker"


def test_protection_is_a_valid_fighting_style_at_creation() -> None:
    assert _fighter().fighting_style == "protection"

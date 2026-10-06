"""Issue #93: the Shield reaction. A monster's single attack that hits a PC who
could cast Shield - and would miss at AC +5 - pauses (ShieldChoicePending)
before anything about it is recorded; resolve_pending_shield_choice then casts
it (slot + reaction spent, +5 AC for the rest of the round, the attack becomes
a miss) or declines (the hit lands as rolled).

Dice are fixed: the goblin's Scimitar is +4 to hit, 1d6+2 damage; the wizard is
unarmored at AC 13, so a d20 of 12 totals 16 - a hit (>= 13) that AC 18 would
turn into a miss."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import (
    ShieldChoicePending,
    TurnEngineError,
    can_cast_shield,
    resolve_action,
    resolve_pending_shield_choice,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard(*, spells: tuple[str, ...] = ("shield",)) -> Character:
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
    wizard.prepared_spells = list(spells)
    wizard.spell_slots = {1: 2}
    wizard.ac = 13  # unarmored: 10 + DEX 3
    return wizard


def _state(wizard: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=0, y=1))
    return GameState(
        encounter_id="shield_test",
        characters={goblin.id: goblin, wizard.id: wizard},
        turn_order=["goblin_1", "elrond"],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10,
            height=10,
            terrain=[["floor"] * 10 for _ in range(10)],
            spawn_points={},
        ),
    )


def _goblin_attack() -> ParsedAction:
    return ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="elrond",
        item_or_spell="Scimitar",
        raw_text="the goblin slashes",
    )


def _pause(state: GameState, natural: int = 12) -> ShieldChoicePending:
    with pytest.raises(ShieldChoicePending) as excinfo:
        resolve_action(state, _goblin_attack(), _FixedRandom([natural, 4]))  # type: ignore[arg-type]
    return excinfo.value


def test_a_hit_that_shield_would_turn_into_a_miss_pauses_before_recording_anything() -> None:
    wizard = _wizard()
    state = _state(wizard)
    pause = _pause(state)

    assert pause.choice.attacker_id == "goblin_1"
    assert pause.choice.target_id == "elrond"
    assert pause.choice.result.attack_roll.total == 16
    assert state.events == []
    assert wizard.hp == wizard.max_hp
    assert state.turn_order[state.current_turn] == "goblin_1"  # attack not finished


def test_casting_shield_spends_the_slot_and_reaction_and_turns_the_hit_into_a_miss() -> None:
    wizard = _wizard()
    state = _state(wizard)
    pause = _pause(state)
    resolve_pending_shield_choice(state, pause.choice, True, _FixedRandom([]), load_srd())  # type: ignore[arg-type]

    assert wizard.spell_slots[1] == 1
    assert wizard.reaction_used_this_round is True
    assert has_condition(wizard, "shielded")
    assert wizard.ac == 18
    cast = next(e for e in state.events if e.type == "spell_cast")
    assert cast.actor == "elrond"
    assert cast.payload["spell"] == "Shield"
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["hit"] is False
    assert attack.payload["target_ac"] == 18
    assert not any(e.type == "damage_dealt" for e in state.events)
    assert wizard.hp == wizard.max_hp
    assert state.turn_order[state.current_turn] == "elrond"  # the goblin's turn ended


def test_declining_shield_lets_the_hit_land_as_rolled() -> None:
    wizard = _wizard()
    state = _state(wizard)
    pause = _pause(state)
    resolve_pending_shield_choice(state, pause.choice, False, _FixedRandom([]), load_srd())  # type: ignore[arg-type]

    assert wizard.spell_slots[1] == 2
    assert not has_condition(wizard, "shielded")
    assert next(e for e in state.events if e.type == "attack_roll").payload["hit"] is True
    assert wizard.hp == wizard.max_hp - 6  # 4 + 2
    assert state.turn_order[state.current_turn] == "elrond"


def test_the_shield_bonus_ends_when_the_round_turns_over() -> None:
    wizard = _wizard()
    state = _state(wizard)
    pause = _pause(state)
    resolve_pending_shield_choice(state, pause.choice, True, _FixedRandom([]), load_srd())  # type: ignore[arg-type]
    assert wizard.ac == 18

    resolve_action(
        state,
        ParsedAction(actor="elrond", verb="end_turn", raw_text="I wait"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert state.round == 2
    assert not has_condition(wizard, "shielded")
    assert wizard.ac == 13


def test_no_offer_when_the_hit_beats_ac_plus_five() -> None:
    # d20 15 + 4 = 19 >= 18: Shield wouldn't save it.
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _goblin_attack(), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "attack_roll").payload["hit"] is True


def test_no_offer_for_a_miss() -> None:
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _goblin_attack(), _FixedRandom([2]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "attack_roll").payload["hit"] is False


def test_a_critical_hit_is_never_offered() -> None:
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _goblin_attack(), _FixedRandom([20, 4, 3]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "attack_roll").payload["critical"] is True


@pytest.mark.parametrize("why", ["not_prepared", "no_slot", "reaction_spent", "unconscious"])
def test_cannot_cast_shield_without_the_means(why: str) -> None:
    wizard = _wizard(spells=("magic-missile",) if why == "not_prepared" else ("shield",))
    if why == "no_slot":
        wizard.spell_slots = {1: 0}
    if why == "reaction_spent":
        wizard.reaction_used_this_round = True
    if why == "unconscious":
        apply_condition(wizard, Condition(name="unconscious", source="0 HP"))
    assert can_cast_shield(wizard) is False


def test_a_known_spell_counts_too() -> None:
    # Sorcerers know spells rather than prepare them.
    wizard = _wizard(spells=())
    wizard.known_spells = ["shield"]
    assert can_cast_shield(wizard) is True


def test_a_monster_cannot_cast_shield() -> None:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=0, y=0))
    assert can_cast_shield(goblin) is False


def test_casting_shield_on_your_own_turn_explains_it_is_a_reaction() -> None:
    wizard = _wizard()
    state = _state(wizard)
    state.turn_order = ["elrond", "goblin_1"]
    cast = ParsedAction(
        actor="elrond", verb="cast_spell", item_or_spell="shield", raw_text="I cast shield"
    )
    with pytest.raises(TurnEngineError, match="reaction"):
        resolve_action(state, cast, _FixedRandom([]))  # type: ignore[arg-type]
    assert wizard.spell_slots[1] == 2

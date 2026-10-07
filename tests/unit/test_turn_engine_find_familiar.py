"""Issue #56, phase D: Find Familiar.

A familiar is stored on its caster (and persisted), conjured into every fight that caster is
in, helps the caster each turn instead of attacking, and is gone for good when it dies - until
the spell is cast again."""

from __future__ import annotations

import random

import pytest

from src.api.routes.characters import _character_to_record, _record_to_character
from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.monster_ai import choose_monster_action
from src.engine.position import Position
from src.engine.rules import familiar_form_from_text, is_party_member
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    TurnEngineError,
    _apply_damage_and_handle_downing,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    return create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "mage-armor", "find-familiar"],
        chosen_equipment=["dagger"],
    )


def _fighter() -> Character:
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )


def _state() -> GameState:
    # Elrond rolls highest, so he acts first and the cast happens on his turn.
    return build_encounter_state(
        build_demo_encounter(),
        [_wizard(), _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )


def _cast(
    state: GameState, text: str = "I cast find familiar as a raven", **params: object
) -> None:
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="Find Familiar",
        params=dict(params),
        raw_text=text,
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_form_is_read_from_the_casters_words() -> None:
    assert familiar_form_from_text("I cast find familiar as a raven") == "raven"
    assert familiar_form_from_text("a toad please") == "frog"
    assert familiar_form_from_text("two owls") == "owl"
    assert familiar_form_from_text("a poisonous snake") == "poisonous-snake"
    assert familiar_form_from_text("a sea horse") == "sea-horse"


def test_no_form_or_several_forms_are_not_guessed() -> None:
    assert familiar_form_from_text("I cast find familiar") is None
    assert familiar_form_from_text("a bat or a cat") is None


def test_a_cast_gives_the_caster_a_familiar_in_the_fight() -> None:
    state = _state()
    _cast(state)
    elrond = state.characters["elrond"]
    familiar = state.characters["elrond_familiar"]
    assert elrond.familiar == "raven"
    assert familiar.monster_index == "raven"
    assert familiar.summoned_by == "elrond" and familiar.is_pc
    assert not is_party_member(familiar)
    assert familiar.name == "Elrond's raven"
    assert state.turn_order.index("elrond_familiar") == state.turn_order.index("elrond") + 1


def test_it_is_a_ritual_so_it_costs_no_slot_and_no_concentration() -> None:
    state = _state()
    elrond = state.characters["elrond"]
    before = dict(elrond.spell_slots)
    _cast(state)
    assert elrond.spell_slots == before
    assert elrond.concentrating_on is None


def test_the_form_can_come_from_the_parsed_params() -> None:
    state = _state()
    _cast(state, "I summon a familiar", form="owl")
    assert state.characters["elrond"].familiar == "owl"


def test_a_cast_that_names_no_form_is_rejected_and_changes_nothing() -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="needs one form"):
        _cast(state, "I cast find familiar")
    assert state.characters["elrond"].familiar is None
    assert "elrond_familiar" not in state.characters
    assert "elrond_familiar" not in state.turn_order


def test_casting_again_changes_the_form_and_keeps_just_one() -> None:
    state = _state()
    _cast(state, "find familiar, a raven")
    state.current_turn = state.turn_order.index("elrond")
    _cast(state, "find familiar, a cat")
    assert state.characters["elrond"].familiar == "cat"
    assert state.characters["elrond_familiar"].monster_index == "cat"
    assert state.turn_order.count("elrond_familiar") == 1
    assert sum(1 for c in state.characters.values() if c.summoned_by == "elrond") == 1


def test_the_familiar_helps_its_caster_instead_of_attacking() -> None:
    state = _state()
    _cast(state)
    familiar = state.characters["elrond_familiar"]
    action = choose_monster_action(state, familiar)
    assert (action.verb, action.target) == ("help", "elrond")
    state.current_turn = state.turn_order.index("elrond_familiar")
    resolve_action(state, action, random.Random(1))
    assert state.characters["elrond"].has_help_advantage is True
    assert any(e.type == "help" and e.actor == "elrond_familiar" for e in state.events)


def test_it_waits_when_the_caster_already_has_a_help_banked() -> None:
    state = _state()
    _cast(state)
    state.characters["elrond"].has_help_advantage = True
    action = choose_monster_action(state, state.characters["elrond_familiar"])
    assert action.verb == "end_turn"


def test_the_helped_caster_attacks_with_advantage() -> None:
    state = _state()
    _cast(state)
    state.current_turn = state.turn_order.index("elrond_familiar")
    resolve_action(
        state, choose_monster_action(state, state.characters["elrond_familiar"]), random.Random(1)
    )
    state.current_turn = state.turn_order.index("elrond")
    goblin = state.characters["goblin_1"]
    state.characters["elrond"].position = Position(x=goblin.position.x - 1, y=goblin.position.y)
    attack = ParsedAction(
        actor="elrond", verb="attack", target="goblin_1", item_or_spell="dagger", raw_text="x"
    )
    resolve_action(state, attack, _FixedRandom([2, 17, 3]))  # type: ignore[arg-type]
    roll = next(e for e in state.events if e.type == "attack_roll" and e.actor == "elrond")
    assert roll.payload["natural"] == 17  # the better of two d20s


def test_when_it_dies_it_is_gone_until_the_spell_is_cast_again() -> None:
    state = _state()
    _cast(state)
    elrond, familiar = state.characters["elrond"], state.characters["elrond_familiar"]
    goblin = state.characters["goblin_1"]
    _apply_damage_and_handle_downing(
        state, goblin, familiar, 20, "slashing", random.Random(1), load_srd()
    )
    assert familiar.is_dead
    assert elrond.familiar is None


def test_each_new_fight_starts_with_the_familiar() -> None:
    wizard = _wizard()
    wizard.familiar = "owl"
    state = build_encounter_state(
        build_demo_encounter(),
        [wizard, _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    familiar = state.characters["elrond_familiar"]
    assert familiar.monster_index == "owl"
    assert familiar.hp == familiar.max_hp
    assert state.turn_order.index(familiar.id) == state.turn_order.index("elrond") + 1


def test_a_party_without_a_familiar_is_unchanged() -> None:
    state = _state()
    assert not any(c.summoned_by for c in state.characters.values())


def test_the_familiar_survives_a_save_and_reload() -> None:
    wizard = _wizard()
    wizard.familiar = "cat"
    assert _record_to_character(_character_to_record(wizard)).familiar == "cat"
    wizard.familiar = None
    assert _record_to_character(_character_to_record(wizard)).familiar is None

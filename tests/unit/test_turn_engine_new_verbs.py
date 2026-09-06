"""Day 13: skill_check, dodge, disengage, help.

Dodge/help are tested by setting the transient status flag directly (the
same pattern test_turn_engine.py already uses for "downed actor" - hp=0 set
directly rather than scripting combat to get there) rather than scripting a
full multi-turn sequence to reach the state naturally; a separate pair of
tests confirms the verbs themselves set/clear those flags correctly.
"""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.state import Character
from src.engine.turn_engine import DEFAULT_SKILL_CHECK_DC, TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _two_person_party() -> list[Character]:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


# turn_order for [18, 10, 8, 3] is always ["thorin", "elrond", "goblin_1", "goblin_2"]
# (established in test_turn_engine.py and cli/play.py's demo fixture).
_INITIATIVE = [18, 10, 8, 3]


def test_skill_check_succeeds_with_proficiency_at_exactly_the_dc() -> None:
    # Thorin: STR16 -> mod3, proficient in Athletics -> modifier 3+2=5.
    # Natural roll 8 -> total 13 == DEFAULT_SKILL_CHECK_DC(13) -> success (ties go to the roller).
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="skill_check", params={"skill": "athletics"}, raw_text="climb"
    )
    resolve_action(state, action, _FixedRandom([8]))  # type: ignore[arg-type]

    event = state.events[-1]
    assert event.type == "skill_check"
    assert event.payload["dc"] == DEFAULT_SKILL_CHECK_DC
    assert event.payload["roll_total"] == 13
    assert event.payload["success"] is True


def test_skill_check_fails_without_proficiency_same_natural_roll() -> None:
    # Same natural roll (8), but Thorin isn't proficient in Stealth: DEX15
    # -> mod2, no proficiency bonus -> total 10 < 13 -> failure. Proves
    # proficiency (not just the roll) determines the outcome.
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="skill_check", params={"skill": "stealth"}, raw_text="sneak"
    )
    resolve_action(state, action, _FixedRandom([8]))  # type: ignore[arg-type]

    event = state.events[-1]
    assert event.payload["roll_total"] == 10
    assert event.payload["success"] is False


def test_skill_check_requires_skill_param() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="skill_check", raw_text="do something skillful")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_skill_check_rejects_unknown_skill() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="skill_check", params={"skill": "juggling"}, raw_text="juggle"
    )
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_dodge_sets_is_dodging_and_clears_at_start_of_own_next_turn() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="dodge", raw_text="I dodge")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert state.characters["thorin"].is_dodging is True
    assert state.events[-1].type == "dodge"

    # Cycle elrond, goblin_1, goblin_2 with no-ops back around to Thorin's turn.
    for actor_id in ("elrond", "goblin_1", "goblin_2"):
        resolve_action(
            state,
            ParsedAction(actor=actor_id, verb="end_turn", raw_text="pass"),
            _FixedRandom([]),  # type: ignore[arg-type]
        )
    assert state.turn_order[state.current_turn] == "thorin"
    assert state.characters["thorin"].is_dodging is True  # still true until Thorin acts again

    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    # The *previous* thorin turn already resolved and reset current_turn past
    # thorin - is_dodging was cleared at the top of that resolve_action call,
    # before dispatch, regardless of the verb used.
    assert state.characters["thorin"].is_dodging is False


def test_dodging_target_gives_the_attacker_disadvantage() -> None:
    state = _build_demo_state(_INITIATIVE)
    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.characters["goblin_1"].is_dodging = True

    # Elrond's dagger: DEX16 -> mod3, attack_bonus 3+2=5. vs goblin_1 AC15.
    # Disadvantage rolls [14, 5], picks the lower (5): total 10 < 15 -> miss,
    # even though the 14 alone would have hit (19 >= 15).
    action = ParsedAction(
        actor="elrond", verb="attack", target="goblin_1", raw_text="I attack the dodging goblin"
    )
    resolve_action(state, action, _FixedRandom([14, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["roll_total"] == 10
    assert attack_event.payload["hit"] is False


def test_help_grants_the_helped_characters_next_attack_advantage_then_clears() -> None:
    state = _build_demo_state(_INITIATIVE)
    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.characters["elrond"].has_help_advantage = True

    # Elrond's dagger attack_bonus 5 vs goblin_1 AC15. Advantage rolls
    # [8, 15], picks the higher (15): total 20 >= 15 -> hit, even though the
    # flat 8 alone would have missed (13 < 15). Third value (3) is the
    # damage die, consumed because this roll hits (unlike the dodge test's
    # miss, which never reaches damage).
    action = ParsedAction(
        actor="elrond", verb="attack", target="goblin_1", raw_text="I attack, aided"
    )
    resolve_action(state, action, _FixedRandom([8, 15, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["roll_total"] == 20
    assert attack_event.payload["hit"] is True
    assert state.characters["elrond"].has_help_advantage is False  # consumed


def test_help_requires_a_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="help", raw_text="I help")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_help_sets_target_flag_via_the_real_verb() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="help", target="elrond", raw_text="I help Elrond")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert state.characters["elrond"].has_help_advantage is True
    assert state.events[-1].type == "help"
    assert state.events[-1].payload["target"] == "elrond"


def test_disengage_is_a_no_op_that_still_consumes_the_turn() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="disengage", raw_text="I disengage")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert state.events[-1].type == "disengage"
    assert state.turn_order[state.current_turn] == "elrond"

"""Phase 9A: SRD condition effects actually wired into attack rolls, ability
checks, and movement - previously schema-only tags (state.ConditionName)
that nothing but "unconscious" (death saves) ever checked. See rules.py's
condition_attack_advantage/condition_attack_disadvantage/
condition_check_disadvantage/effective_speed for the pure-function logic
(exhaustively tested in test_rules.py) - these tests confirm turn_engine
actually calls them at the right points, end to end through resolve_action.
"""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition
from src.engine.encounter import build_encounter_state
from src.engine.state import Character, Condition
from src.engine.turn_engine import TurnEngineError, resolve_action


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
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


# turn_order for [18, 10, 8, 3] is always thorin, elrond, goblin_1, goblin_2
# (established in test_turn_engine.py); thorin(0,1) and goblin_1(2,1) are
# 10ft apart - beyond a longsword's 5ft reach, so these tests reposition
# goblin_1 onto thorin's square directly (same "poke state, not range" fix
# test_turn_engine_day14.py already applies for the same reason).
_INITIATIVE = [18, 10, 8, 3]


def test_attack_gets_advantage_when_target_is_restrained() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    apply_condition(state.characters["goblin_1"], Condition(name="restrained"))
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack the restrained goblin",
    )
    # [15, 3]: the attack-roll pair (advantage keeps 15, a hit); [5]: the
    # resulting damage die, since a hit always rolls damage.
    resolve_action(state, action, _FixedRandom([15, 3, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 15  # advantage keeps the higher roll


def test_attack_gets_disadvantage_when_attacker_is_prone() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    apply_condition(state.characters["thorin"], Condition(name="prone"))
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack from the ground",
    )
    resolve_action(state, action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 3  # disadvantage keeps the lower roll


def test_charmed_actor_cannot_attack_their_charmer() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    apply_condition(state.characters["thorin"], Condition(name="charmed", source="goblin_1"))
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack the goblin that charmed me",
    )
    with pytest.raises(TurnEngineError, match="charmed"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_stunned_actor_attack_is_forced_to_end_turn() -> None:
    """Phase 9B: a stunned actor's declared attack never reaches the attack
    roll at all - it's silently converted to end_turn (not an error, matching
    the "invalid" verb's philosophy: the actor isn't doing anything wrong by
    having a condition applied to them). Turn order is
    thorin(0)/elrond(1)/goblin_1(2)/goblin_2(3) - confirm the turn actually
    advances to elrond, not just that no exception was raised."""
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    apply_condition(state.characters["thorin"], Condition(name="stunned"))
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack the goblin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert not any(e.type == "attack_roll" for e in state.events)
    assert state.turn_order[state.current_turn] == "elrond"


@pytest.mark.parametrize("condition_name", ["paralyzed", "petrified", "incapacitated"])
def test_other_incapacitating_conditions_are_forced_to_end_turn(condition_name: str) -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    apply_condition(state.characters["thorin"], Condition(name=condition_name))  # type: ignore[arg-type]
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack the goblin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert not any(e.type == "attack_roll" for e in state.events)
    assert state.turn_order[state.current_turn] == "elrond"


def test_grappled_actor_cannot_move() -> None:
    state = _build_demo_state(_INITIATIVE)
    apply_condition(state.characters["thorin"], Condition(name="grappled"))
    action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I try to move away",
        params={"path": [{"x": 1, "y": 1}]},
    )
    with pytest.raises(TurnEngineError, match="cannot afford this move"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

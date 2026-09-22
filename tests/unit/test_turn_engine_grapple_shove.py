"""Phase 9E: grapple and shove actions.

Both verbs are resolved as the same contested check (turn_engine.
_grapple_shove_contest): the actor's Athletics check against the higher of
the target's own Athletics or Acrobatics checks, consuming exactly 3 d20s
in that fixed order every time (actor Athletics, target Athletics, target
Acrobatics - see that function's docstring). Fixtures below hand-compute
all three rolls' totals the same way test_turn_engine_new_verbs.py does for
skill_check.

Thorin (fighter, human): STR15+1(human)=16 -> mod3, proficient in Athletics
(chosen_skills) -> modifier 3+2=5 (established in test_turn_engine_new_
verbs.py). Goblin (SRD stat block): STR8 -> mod-1, DEX14 -> mod2, no skill
proficiencies at all (monster_to_character never sets skill_proficiencies,
so it defaults to empty) -> neither Athletics nor Acrobatics gets a
proficiency bonus.
"""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import build_encounter_state
from src.engine.rules import effective_speed
from src.engine.state import Character
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
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
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


def test_grapple_succeeds_when_actor_total_beats_target_total() -> None:
    # Actor (Thorin) roll 10 -> total 10+5=15.
    # Target (goblin_1) Athletics roll 5 -> total 5-1=4.
    # Target Acrobatics roll 10 -> total 10+2=12. max(4, 12)=12.
    # 15 > 12 -> success.
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="grapple", target="goblin_1", raw_text="I grapple the goblin"
    )
    resolve_action(state, action, _FixedRandom([10, 5, 10]))  # type: ignore[arg-type]

    attempt_event = next(e for e in state.events if e.type == "grapple_attempt")
    assert attempt_event.payload["actor_total"] == 15
    assert attempt_event.payload["target_total"] == 12
    assert attempt_event.payload["success"] is True

    goblin = state.characters["goblin_1"]
    assert has_condition(goblin, "grappled")
    condition_event = next(e for e in state.events if e.type == "condition_applied")
    assert condition_event.payload == {"condition": "grappled", "target": "goblin_1"}


def test_grapple_fails_when_target_total_is_higher() -> None:
    # Actor roll 3 -> total 3+5=8.
    # Target Athletics roll 15 -> total 15-1=14.
    # Target Acrobatics roll 1 -> total 1+2=3. max(14, 3)=14.
    # 8 < 14 -> failure, nothing applied.
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="grapple", target="goblin_1", raw_text="I grapple the goblin"
    )
    resolve_action(state, action, _FixedRandom([3, 15, 1]))  # type: ignore[arg-type]

    attempt_event = next(e for e in state.events if e.type == "grapple_attempt")
    assert attempt_event.payload["actor_total"] == 8
    assert attempt_event.payload["target_total"] == 14
    assert attempt_event.payload["success"] is False
    assert not has_condition(state.characters["goblin_1"], "grappled")
    assert not any(e.type == "condition_applied" for e in state.events)


def test_grapple_ties_go_to_the_defender() -> None:
    # Actor roll 10 -> total 15.
    # Target Athletics roll 16 -> total 16-1=15.
    # Target Acrobatics roll 1 -> total 1+2=3. max(15, 3)=15.
    # 15 == 15 -> a tie leaves things as they were (PHB contest rule) -> the
    # grapple fails, proving the resolution is a strict ">" and not ">=".
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="grapple", target="goblin_1", raw_text="I grapple the goblin"
    )
    resolve_action(state, action, _FixedRandom([10, 16, 1]))  # type: ignore[arg-type]

    attempt_event = next(e for e in state.events if e.type == "grapple_attempt")
    assert attempt_event.payload["actor_total"] == attempt_event.payload["target_total"] == 15
    assert attempt_event.payload["success"] is False
    assert not has_condition(state.characters["goblin_1"], "grappled")


def test_successful_grapple_zeroes_effective_speed_and_blocks_a_move() -> None:
    # Bonus check: apply the same successful-grapple fixture as the first
    # test above, then confirm effective_speed (Phase 9A) already reduces
    # the grappled goblin's movement budget to 0 with no further wiring, and
    # that a subsequent move attempt is rejected for exactly that reason.
    state = _build_demo_state(_INITIATIVE)
    resolve_action(
        state,
        ParsedAction(
            actor="thorin", verb="grapple", target="goblin_1", raw_text="I grapple the goblin"
        ),
        _FixedRandom([10, 5, 10]),  # type: ignore[arg-type]
    )
    goblin = state.characters["goblin_1"]
    assert has_condition(goblin, "grappled")
    assert effective_speed(goblin) == 0

    # Advance to goblin_1's turn (thorin -> elrond -> goblin_1).
    resolve_action(
        state,
        ParsedAction(actor="elrond", verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert state.turn_order[state.current_turn] == "goblin_1"

    # goblin_1 is at (2, 1); (3, 1) is an adjacent floor square, normally a
    # trivially affordable 5ft move - but speed 0 can't afford any move cost.
    move_action = ParsedAction(
        actor="goblin_1",
        verb="move",
        params={"path": [{"x": 3, "y": 1}]},
        raw_text="the goblin tries to back away",
    )
    with pytest.raises(TurnEngineError):
        resolve_action(state, move_action, _FixedRandom([]))  # type: ignore[arg-type]


def test_shove_succeeds_applies_prone_not_grappled() -> None:
    # Same fixture/arithmetic as the successful grapple test - shove uses
    # the identical contested check, only the applied condition differs.
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="shove", target="goblin_1", raw_text="I shove the goblin"
    )
    resolve_action(state, action, _FixedRandom([10, 5, 10]))  # type: ignore[arg-type]

    attempt_event = next(e for e in state.events if e.type == "shove_attempt")
    assert attempt_event.payload["actor_total"] == 15
    assert attempt_event.payload["target_total"] == 12
    assert attempt_event.payload["success"] is True

    goblin = state.characters["goblin_1"]
    assert has_condition(goblin, "prone")
    assert not has_condition(goblin, "grappled")
    condition_event = next(e for e in state.events if e.type == "condition_applied")
    assert condition_event.payload == {"condition": "prone", "target": "goblin_1"}


def test_shove_fails_applies_nothing() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="shove", target="goblin_1", raw_text="I shove the goblin"
    )
    resolve_action(state, action, _FixedRandom([3, 15, 1]))  # type: ignore[arg-type]

    attempt_event = next(e for e in state.events if e.type == "shove_attempt")
    assert attempt_event.payload["success"] is False
    assert not has_condition(state.characters["goblin_1"], "prone")
    assert not any(e.type == "condition_applied" for e in state.events)


def test_grapple_rejects_same_side_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="grapple", target="elrond", raw_text="I grapple Elrond"
    )
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_shove_rejects_same_side_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="shove", target="elrond", raw_text="I shove Elrond")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_grapple_requires_a_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="grapple", raw_text="I try to grapple")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_shove_requires_a_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="shove", raw_text="I try to shove")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_grapple_rejects_a_condition_immune_target() -> None:
    # Issue #18: a shadow (incorporeal undead) is SRD-immune to both
    # grappled and prone - swap goblin_1's own monster_index directly (same
    # "poke the fixture" shortcut other tests in this project already use)
    # rather than authoring a whole new encounter just for this monster.
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].monster_index = "shadow"
    action = ParsedAction(
        actor="thorin", verb="grapple", target="goblin_1", raw_text="I grapple the shadow"
    )
    with pytest.raises(TurnEngineError, match="immune to the grappled condition"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_shove_rejects_a_condition_immune_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].monster_index = "shadow"
    action = ParsedAction(
        actor="thorin", verb="shove", target="goblin_1", raw_text="I shove the shadow"
    )
    with pytest.raises(TurnEngineError, match="immune to the prone condition"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

"""Phase 9C: a hit against an already-unconscious target auto-crits and
inflicts 2 automatic death-save failures (SRD's helpless-creature rule,
previously an entirely unimplemented gap - see CLAUDE.md's Day 22 entry),
plus the new "stabilize" verb (a DC 10 Medicine check that sets a dying
ally's is_stable directly, no roll required from the target)."""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
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
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


_INITIATIVE = [18, 10, 8, 3]  # turn_order: thorin, elrond, goblin_1, goblin_2


def _end_turn(state, actor_id: str) -> None:  # type: ignore[no-untyped-def]
    resolve_action(
        state,
        ParsedAction(actor=actor_id, verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )


def _down_thorin(state) -> None:  # type: ignore[no-untyped-def]
    """Puts thorin at 0 HP and unconscious directly (same "poke state"
    pattern Phase 9A's condition tests already use), without burning a real
    hit to do it."""
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious", source="0 HP"))
    thorin.death_save_successes = 0
    thorin.death_save_failures = 0
    thorin.is_stable = False


# ------------------------------------------------- unconscious-hit auto-crit


def test_hit_against_unconscious_target_is_always_critical_and_doubles_damage() -> None:
    # A "poke state" fixture (same pattern Phase 9A's condition tests use):
    # apply the unconscious condition directly WITHOUT first dropping HP to
    # 0, purely so the resulting damage_dealt event's `amount` reflects the
    # full doubled-dice roll rather than being clamped by an already-0-HP
    # target (the other tests below use a real 0-HP downed character - the
    # actual game scenario - but that clamps `amount` to 0, which can't
    # distinguish doubled dice from single dice).
    #
    # goblin_1's Scimitar (+4) vs thorin (AC12, max_hp 12). An unconscious
    # target already grants condition_attack_advantage (Phase 9A), so the
    # attack roll is rolled twice per SRD advantage rules (roll_d20's `a, b`
    # pair) - use a=11, b=2 so the kept/natural roll is 11 (max of the two):
    # total 11+4=15 >= AC12, a hit that would NOT normally crit (only a
    # natural 20 would) - confirms it crits anyway via force_critical.
    # Damage die is 1d6+2; doubled to 2d6+2, naturals [3, 4] -> total
    # 3+4+2=9.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="unconscious", source="test"))
    state.characters["goblin_1"].position = thorin.position

    action = ParsedAction(actor="goblin_1", verb="attack", target="thorin", raw_text="attack")
    resolve_action(state, action, _FixedRandom([11, 2, 3, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["hit"] is True
    assert attack_event.payload["critical"] is True  # natural 11 would never crit unforced

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 9  # 2d6(3+4)+2, not 1d6+2 - dice were doubled

    assert thorin.hp == 3  # 12 - 9
    assert thorin.death_save_failures == 2  # 2 automatic failures from the hit
    assert thorin.is_dead is False


def test_natural_1_still_misses_an_unconscious_target() -> None:
    # force_critical must not turn a natural-1 auto-miss into a hit. With
    # advantage (from the unconscious condition) rolling two d20s, both must
    # come up 1 for the kept/natural result to be 1.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    _down_thorin(state)
    state.characters["goblin_1"].position = state.characters["thorin"].position

    action = ParsedAction(actor="goblin_1", verb="attack", target="thorin", raw_text="attack")
    resolve_action(state, action, _FixedRandom([1, 1]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["hit"] is False
    assert not any(e.type == "damage_dealt" for e in state.events)
    assert state.characters["thorin"].death_save_failures == 0  # no hit, no auto-failures


def test_three_total_failures_via_repeated_unconscious_hits_kills() -> None:
    # thorin already has 1 failure (e.g. from an earlier self-rolled death
    # save) before a hit lands - the hit's 2 automatic failures push the
    # total to 3, which must kill him exactly like 3 self-rolled failures
    # would (same _check_death_save_failure_threshold helper both paths
    # share).
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    _down_thorin(state)
    thorin = state.characters["thorin"]
    thorin.death_save_failures = 1
    state.characters["goblin_1"].position = thorin.position

    action = ParsedAction(actor="goblin_1", verb="attack", target="thorin", raw_text="attack")
    resolve_action(state, action, _FixedRandom([11, 2, 3, 4]))  # type: ignore[arg-type]

    assert thorin.death_save_failures == 3
    assert thorin.is_dead is True
    assert has_condition(thorin, "unconscious") is False
    assert any(
        e.type == "death" and e.payload["cause"] == "failed death saves" for e in state.events
    )


def test_repeated_hits_on_downed_target_do_not_reset_death_save_progress() -> None:
    # Regression for a latent bug this feature would otherwise have hit
    # immediately: _apply_damage_and_handle_downing used to unconditionally
    # reset death_save_successes/failures/is_stable to a fresh start on
    # every hit against a target already at 0 HP, which would have wiped
    # the very failures this feature just applied. One hit already lands 2
    # failures (previous test); a second, separate hit should stack to 4
    # total (still just as dead, but proves nothing got reset back to 0 in
    # between - if it had, this second hit alone would show 2, not 4).
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    _down_thorin(state)
    thorin = state.characters["thorin"]
    state.characters["goblin_1"].position = thorin.position

    action = ParsedAction(actor="goblin_1", verb="attack", target="thorin", raw_text="attack")
    resolve_action(state, action, _FixedRandom([11, 2, 3, 4]))  # type: ignore[arg-type]
    assert thorin.death_save_failures == 2  # 2 failures alone doesn't kill
    assert thorin.is_dead is False

    # A second, separate hit should stack to 4 total (still just as dead,
    # but proves nothing got reset back to 0 in between - if it had, this
    # second hit alone would show 2, not 4). Reset current_turn back to
    # goblin_1 - the first attack already advanced it to goblin_2.
    state.current_turn = state.turn_order.index("goblin_1")
    resolve_action(state, action, _FixedRandom([11, 2, 3, 4]))  # type: ignore[arg-type]
    assert thorin.death_save_failures == 4  # 2 + 2, not reset to 2
    assert thorin.is_dead is True


# --------------------------------------------------------------- stabilize


def test_stabilize_success_sets_target_stable() -> None:
    # thorin (actor, stabilizing elrond) rolls the Medicine check - the
    # ACTOR's stats, not the dying target's. Medicine is WIS-governed;
    # thorin's WIS10 -> mod 0, not proficient in Medicine -> modifier 0. DC
    # 10, natural 12 -> total 12 -> success.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    elrond = state.characters["elrond"]
    elrond.hp = 0
    apply_condition(elrond, Condition(name="unconscious", source="0 HP"))
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(
        actor="thorin", verb="stabilize", target="elrond", raw_text="I try to stabilize Elrond"
    )
    resolve_action(state, action, _FixedRandom([12]))  # type: ignore[arg-type]

    assert elrond.is_stable is True
    assert elrond.hp == 0  # stabilize doesn't heal, only stops the dying
    skill_event = next(e for e in state.events if e.type == "skill_check")
    assert skill_event.payload["success"] is True
    assert skill_event.payload["skill"] == "medicine"
    assert any(
        e.type == "condition_applied" and e.payload["condition"] == "stable" for e in state.events
    )


def test_stabilize_failure_does_not_set_target_stable() -> None:
    # Same setup (thorin's Medicine modifier is 0), natural 5 -> total 5 <
    # DC10 -> failure.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    elrond = state.characters["elrond"]
    elrond.hp = 0
    apply_condition(elrond, Condition(name="unconscious", source="0 HP"))
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(
        actor="thorin", verb="stabilize", target="elrond", raw_text="I try to stabilize Elrond"
    )
    resolve_action(state, action, _FixedRandom([5]))  # type: ignore[arg-type]

    assert elrond.is_stable is False
    skill_event = next(e for e in state.events if e.type == "skill_check")
    assert skill_event.payload["success"] is False
    assert not any(e.type == "condition_applied" for e in state.events)


def test_stabilize_rejects_a_conscious_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="stabilize", target="elrond", raw_text="I try to stabilize Elrond"
    )
    with pytest.raises(TurnEngineError, match="not a valid stabilize target"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_stabilize_rejects_an_already_stable_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    elrond = state.characters["elrond"]
    elrond.hp = 0
    apply_condition(elrond, Condition(name="unconscious", source="0 HP"))
    elrond.is_stable = True
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(
        actor="thorin", verb="stabilize", target="elrond", raw_text="I try to stabilize Elrond"
    )
    with pytest.raises(TurnEngineError, match="not a valid stabilize target"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_stabilize_rejects_a_dead_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    elrond = state.characters["elrond"]
    elrond.hp = 0
    elrond.is_dead = True
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(
        actor="thorin", verb="stabilize", target="elrond", raw_text="I try to stabilize Elrond"
    )
    with pytest.raises(TurnEngineError, match="not a valid stabilize target"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_stabilize_requires_a_target() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="stabilize", raw_text="I try to stabilize someone")
    with pytest.raises(TurnEngineError, match="requires a target"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

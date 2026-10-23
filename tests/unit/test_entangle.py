"""Entangle end to end: a creature that fails the Strength save is restrained for real
(speed 0, so it can't move), the cast records the break-free DC, the restrained creature can use
its action for a Strength check against that DC (a player by saying so, a monster on its own when
nothing is in reach), and a restrained creature with something to hit still attacks.

Live question: "how does Entangle work?" - the answer included "a restrained goblin still reports
full speed", which was a bug: restrained was documented as speed 0 but only grappled zeroed it."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.monster_ai import choose_monster_action
from src.engine.position import Position
from src.engine.rules import effective_speed
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action
from src.graph.nodes.actor_options import actor_options_summary


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _druid() -> Character:
    druid = create_character(
        character_id="sylva",
        name="Sylva",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 13, "CON": 14, "INT": 10, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-perception"],
        chosen_prepared_spells=["entangle", "goodberry", "longstrider", "faerie-fire"],
        chosen_equipment=["quarterstaff"],
    )
    druid.position = Position(x=0, y=0)
    return druid


def _goblin(goblin_id: str, x: int) -> Character:
    return Character(
        id=goblin_id,
        name=goblin_id.replace("_", " ").title(),
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=10,
        level=1,
        position=Position(x=x, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _state(goblin_x: int = 3) -> GameState:
    from src.cli.play import build_demo_encounter  # for a real battle map
    from src.engine.encounter import build_encounter_state

    state = build_encounter_state(
        build_demo_encounter(),
        [_druid()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    druid = state.characters["sylva"]
    druid.position = Position(x=0, y=0)
    for monster_id in [c for c in state.characters if c.startswith("goblin")]:
        del state.characters[monster_id]
    goblin = _goblin("goblin_1", goblin_x)
    state.characters["goblin_1"] = goblin
    state.turn_order = ["sylva", "goblin_1"]
    state.current_turn = 0
    return state


def _restrain(goblin: Character, dc: int | None = 13) -> None:
    apply_condition(
        goblin,
        Condition(
            name="restrained",
            duration_rounds=10,
            source="sylva",
            spell="Entangle",
            break_free_dc=dc,
        ),
    )


def test_a_restrained_creature_has_no_speed_and_cannot_move() -> None:
    state = _state()
    goblin = state.characters["goblin_1"]
    assert effective_speed(goblin) == 30
    _restrain(goblin)
    assert effective_speed(goblin) == 0
    state.current_turn = 1
    move = ParsedAction(
        actor="goblin_1",
        verb="move",
        params={"path": [{"x": goblin.position.x, "y": goblin.position.y + 1}]},
        raw_text="x",
    )
    with pytest.raises(TurnEngineError, match="restrained and can't move"):
        resolve_action(state, move, _FixedRandom([]))  # type: ignore[arg-type]


def test_casting_entangle_restrains_a_target_that_fails_and_records_the_dc() -> None:
    state = _state()
    cast = ParsedAction(
        actor="sylva",
        verb="cast_spell",
        item_or_spell="Entangle",
        target="goblin_1",
        targets=["goblin_1"],
        raw_text="x",
    )
    resolve_action(state, cast, _FixedRandom([2]))  # type: ignore[arg-type]  # STR save: 2 - 1 = 1
    goblin = state.characters["goblin_1"]
    restraint = next(c for c in goblin.conditions if c.name == "restrained")
    assert restraint.spell == "Entangle" and restraint.source == "sylva"
    assert restraint.break_free_dc == 13  # 8 + proficiency 2 + WIS 3 (human +1: 16)
    assert state.characters["sylva"].concentrating_on == "Entangle"


def test_a_target_that_makes_the_save_is_not_restrained() -> None:
    state = _state()
    cast = ParsedAction(
        actor="sylva",
        verb="cast_spell",
        item_or_spell="Entangle",
        target="goblin_1",
        targets=["goblin_1"],
        raw_text="x",
    )
    resolve_action(state, cast, _FixedRandom([19]))  # type: ignore[arg-type]
    assert not has_condition(state.characters["goblin_1"], "restrained")


def test_a_restrained_creature_can_break_free_with_a_strength_check() -> None:
    state = _state()
    goblin = state.characters["goblin_1"]
    _restrain(goblin)
    state.current_turn = 1
    free = ParsedAction(actor="goblin_1", verb="escape_grapple", raw_text="x")
    resolve_action(state, free, _FixedRandom([18]))  # type: ignore[arg-type]  # 18 - 1 = 17 >= 13
    event = next(e for e in state.events if e.type == "restraint_escape")
    assert event.payload["success"] is True and event.payload["dc"] == 13
    assert event.payload["roll_total"] == 17 and event.payload["spell"] == "Entangle"
    assert not has_condition(goblin, "restrained")


def test_a_failed_attempt_leaves_it_restrained_and_still_costs_the_action() -> None:
    state = _state()
    goblin = state.characters["goblin_1"]
    _restrain(goblin)
    state.current_turn = 1
    free = ParsedAction(actor="goblin_1", verb="escape_grapple", raw_text="x")
    resolve_action(state, free, _FixedRandom([3]))  # type: ignore[arg-type]  # 3 - 1 = 2 < 13
    event = next(e for e in state.events if e.type == "restraint_escape")
    assert event.payload["success"] is False
    assert has_condition(goblin, "restrained")
    assert state.turn_order[state.current_turn] == "sylva"  # the action was spent: turn passed


def test_a_player_with_nothing_holding_them_cannot_break_free() -> None:
    state = _state()
    free = ParsedAction(actor="sylva", verb="escape_grapple", raw_text="x")
    with pytest.raises(TurnEngineError, match="isn't grappled or restrained"):
        resolve_action(state, free, _FixedRandom([10]))  # type: ignore[arg-type]


def test_a_restrained_monster_with_nothing_in_reach_tries_to_break_free() -> None:
    state = _state(goblin_x=6)  # 30 ft away: nothing within a scimitar's reach
    goblin = state.characters["goblin_1"]
    _restrain(goblin)
    action = choose_monster_action(state, goblin)
    assert action.verb == "escape_grapple"


def test_a_restrained_monster_with_a_target_in_reach_attacks_it() -> None:
    state = _state(goblin_x=1)  # next to the druid
    goblin = state.characters["goblin_1"]
    _restrain(goblin)
    action = choose_monster_action(state, goblin)
    assert action.verb == "attack" and action.target == "sylva"


def test_a_restrained_companion_is_told_it_can_break_free() -> None:
    state = _state()
    druid = state.characters["sylva"]
    apply_condition(
        druid,
        Condition(
            name="restrained",
            duration_rounds=10,
            source="goblin_1",
            spell="Entangle",
            break_free_dc=14,
        ),
    )
    summary = actor_options_summary(druid, load_srd())
    assert "RESTRAINED" in summary and "DC 14" in summary and "struggle free" in summary

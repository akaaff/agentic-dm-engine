"""Found live: moving up to a monster used to end the whole turn, leaving no
chance to then attack (the same gap made monster AI take two real turns to
close distance and attack - see monster_ai.choose_monster_action, unchanged,
which already benefits for free once move stops ending the turn). Real SRD
gives every turn a movement budget separate from the action; "dash" is the
one exception - it genuinely *is* the action, so it still ends the turn.
"""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _open_map(width: int, height: int) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def _thorin_and_goblin_state(thorin_position: Position, goblin_position: Position) -> GameState:
    # Thorin: STR16->mod3, longsword proficient -> attack_bonus 5, speed 30ft
    # (human, no exhaustion/grappling - full effective_speed).
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=thorin_position,
    )
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", goblin_position)
    # Boosted well past the SRD's real 7 HP - several fixtures here deal
    # more than that in one hit, and a dead goblin ends the encounter
    # (victory), which would stop the turn from ever advancing to it -
    # same reasoning as test_turn_engine_class_features.py's own _goblin.
    goblin.hp = goblin.max_hp = 100
    return GameState(
        encounter_id="move_and_act_test",
        characters={thorin.id: thorin, goblin.id: goblin},
        turn_order=[thorin.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )


def test_move_does_not_end_the_turn_and_a_follow_up_attack_succeeds() -> None:
    # Thorin starts 10ft (2 squares) from the goblin - never adjacent, so
    # approaching it provokes no opportunity attack (OAs only fire on
    # leaving a threatened square, not entering one). AC 15; natural 15 ->
    # total 20 -> hits; damage die 4 + STR mod 3 = 7.
    state = _thorin_and_goblin_state(Position(x=0, y=0), Position(x=2, y=0))
    move_action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I close in",
        params={"path": [{"x": 1, "y": 0}]},
    )
    resolve_action(state, move_action, _FixedRandom([]))  # type: ignore[arg-type]

    # Still Thorin's turn - the move alone didn't end it.
    assert state.turn_order[state.current_turn] == "thorin"
    assert state.characters["thorin"].position == Position(x=1, y=0)
    assert state.characters["thorin"].movement_used_feet == 5

    attack_action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I swing my longsword",
    )
    resolve_action(state, attack_action, _FixedRandom([15, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["hit"] is True
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 7
    # attack still ends the turn (unchanged) - now the goblin's.
    assert state.turn_order[state.current_turn] == "goblin_1"


def test_two_partial_moves_in_one_turn_share_a_single_speed_budget() -> None:
    # Thorin's speed is 30ft. Two legal 15ft moves in the same turn sum to
    # exactly the budget; a third move of any distance is then unaffordable.
    state = _thorin_and_goblin_state(Position(x=0, y=0), Position(x=9, y=9))
    first_move = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I advance",
        params={"path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}]},
    )
    resolve_action(state, first_move, _FixedRandom([]))  # type: ignore[arg-type]
    assert state.characters["thorin"].movement_used_feet == 15
    assert state.turn_order[state.current_turn] == "thorin"

    second_move = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I keep going",
        params={"path": [{"x": 4, "y": 0}, {"x": 5, "y": 0}, {"x": 6, "y": 0}]},
    )
    resolve_action(state, second_move, _FixedRandom([]))  # type: ignore[arg-type]
    assert state.characters["thorin"].movement_used_feet == 30
    assert state.turn_order[state.current_turn] == "thorin"

    third_move = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I try to keep going",
        params={"path": [{"x": 7, "y": 0}]},
    )
    with pytest.raises(TurnEngineError, match="cannot afford this move"):
        resolve_action(state, third_move, _FixedRandom([]))  # type: ignore[arg-type]
    # Rejected - budget untouched.
    assert state.characters["thorin"].movement_used_feet == 30
    assert state.characters["thorin"].position == Position(x=6, y=0)


def test_movement_used_feet_resets_on_the_actors_own_next_turn() -> None:
    state = _thorin_and_goblin_state(Position(x=0, y=0), Position(x=9, y=9))
    move_action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I advance",
        params={"path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}, {"x": 4, "y": 0}]},
    )
    resolve_action(state, move_action, _FixedRandom([]))  # type: ignore[arg-type]
    assert state.characters["thorin"].movement_used_feet == 20

    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="I hold there"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert state.turn_order[state.current_turn] == "goblin_1"

    # goblin_1 is far away (9,9) and has no path in an empty 10x10 map given
    # its own speed - it'll move toward Thorin, unaffected by this test's
    # own assertions (nothing here depends on where it ends up).
    goblin_move = ParsedAction(
        actor="goblin_1",
        verb="move",
        raw_text="it approaches",
        params={"path": [{"x": 8, "y": 8}]},
    )
    resolve_action(state, goblin_move, _FixedRandom([]))  # type: ignore[arg-type]
    resolve_action(
        state,
        ParsedAction(actor="goblin_1", verb="end_turn", raw_text="it holds"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )

    assert state.round == 2
    assert state.turn_order[state.current_turn] == "thorin"
    assert state.characters["thorin"].movement_used_feet == 0


def test_dash_still_ends_the_turn() -> None:
    state = _thorin_and_goblin_state(Position(x=0, y=0), Position(x=9, y=9))
    dash_action = ParsedAction(
        actor="thorin",
        verb="dash",
        raw_text="I dash",
        params={"path": [{"x": 1, "y": 0}]},
    )
    resolve_action(state, dash_action, _FixedRandom([]))  # type: ignore[arg-type]
    # Unlike plain move, dash still spends the action - the turn moves on.
    assert state.turn_order[state.current_turn] == "goblin_1"

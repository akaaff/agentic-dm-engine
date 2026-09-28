"""Live-found: a companion's own free-text turn repeated the identical
out-of-range attack ("I swing my battleaxe at kobold_1", 25ft away, max 5ft)
three times in a row, never once trying to move first - player_agent_node
has no notion of weapon/spell range, so nothing ever told it to close the
distance instead of retrying the same declaration verbatim. rules_engine_
node now catches turn_engine's own AttackOutOfRangeError and, for a
companion specifically, redirects the rejected attack into an actual move
toward the same target (see src.engine.monster_ai.build_move_toward_target)
instead of just failing it - the companion's next free-text attempt then
has a real shot at actually being in range.
"""

from __future__ import annotations

import random

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position, distance_feet
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError
from src.graph.nodes.rules_engine_node import make_rules_engine_node
from src.graph.state_schema import GraphState
from src.observability import log_event


def _open_map(width: int, height: int) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def _grom(position: Position, is_companion: bool) -> Character:
    return create_character(
        character_id="companion_grom" if is_companion else "grom",
        name="Grom Ironfist",
        race_index="dwarf",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 10, "CHA": 12},
        chosen_skills=["skill-athletics", "skill-intimidation"],
        chosen_equipment=["chain-mail", "shield", "battleaxe"],
        is_companion=is_companion,
        position=position,
    )


def _state(grom_position: Position, kobold_position: Position, is_companion: bool) -> GameState:
    grom = _grom(grom_position, is_companion)
    srd = load_srd()
    kobold = monster_to_character(srd.monsters["kobold"], "kobold_1", kobold_position)
    return GameState(
        encounter_id="out_of_range_redirect_test",
        characters={grom.id: grom, kobold.id: kobold},
        turn_order=[grom.id, kobold.id],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )


def _attack_action(actor_id: str, raw_text: str) -> ParsedAction:
    return ParsedAction(
        actor=actor_id,
        verb="attack",
        target="kobold_1",
        item_or_spell="battleaxe",
        raw_text=raw_text,
    )


def _graph_state(game_state: GameState, action: ParsedAction, raw_text: str) -> GraphState:
    return {
        "game_state": game_state,
        "raw_text": raw_text,
        "parsed_action": action,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


def test_companions_out_of_range_attack_redirects_to_a_move_instead_of_failing() -> None:
    # 25ft away (5 squares), well beyond Battleaxe's 5ft melee range.
    game_state = _state(Position(x=0, y=0), Position(x=5, y=0), is_companion=True)
    action = _attack_action("companion_grom", "I swing my battleaxe at kobold_1.")
    state = _graph_state(game_state, action, "I swing my battleaxe at kobold_1.")
    node = make_rules_engine_node(rng=random.Random(1))

    result = node(state)

    grom = result["game_state"].characters["companion_grom"]
    events = result["game_state"].events
    # Moved closer, not stuck at the original square, and no attack_roll
    # was ever recorded - the attack itself never resolved this call.
    assert grom.position != Position(x=0, y=0)
    assert grom.movement_used_feet > 0
    assert [e.type for e in events] == ["move"]
    # A successful redirect isn't a real hard case - nothing to review.
    assert not log_event.EVENTS_LOG_PATH.exists()


def test_a_second_attempt_after_the_redirect_is_now_in_range_and_actually_attacks() -> None:
    # Grom starts 10ft away (2 squares, speed 25ft after chain mail) - one
    # redirect should close the whole gap in a single move.
    game_state = _state(Position(x=0, y=0), Position(x=2, y=0), is_companion=True)
    action = _attack_action("companion_grom", "I swing my battleaxe at kobold_1.")
    state = _graph_state(game_state, action, "I swing my battleaxe at kobold_1.")
    node = make_rules_engine_node(rng=random.Random(1))

    first = node(state)
    grom = first["game_state"].characters["companion_grom"]
    kobold = first["game_state"].characters["kobold_1"]
    assert distance_feet(grom.position, kobold.position) <= 5

    # The companion's own follow-up attempt (same free text, same turn -
    # movement_used_feet already reflects the redirect above) now resolves
    # as a real attack, not another redirect.
    second_action = _attack_action("companion_grom", "I swing my battleaxe at kobold_1.")
    second_state = _graph_state(
        first["game_state"], second_action, "I swing my battleaxe at kobold_1."
    )
    second = node(second_state)
    assert "attack_roll" in [e.type for e in second["game_state"].events]


def test_a_human_pcs_out_of_range_attack_still_fails_honestly_and_gets_logged() -> None:
    # Companions get redirected; a human's own explicit "attack" should
    # stay an honest rejection, not silently become a move they never
    # asked for.
    game_state = _state(Position(x=0, y=0), Position(x=5, y=0), is_companion=False)
    action = _attack_action("grom", "I swing my battleaxe at kobold_1.")
    state = _graph_state(game_state, action, "I swing my battleaxe at kobold_1.")
    node = make_rules_engine_node(rng=random.Random(1))

    with pytest.raises(TurnEngineError):
        node(state)

    lines = log_event.EVENTS_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "out of range" in lines[0]

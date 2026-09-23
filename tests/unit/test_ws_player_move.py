"""Live-reported bug fix: click-to-move (the `player_move` WS message) used
to send a single-element path straight to the clicked square - valid only
when that square was exactly one hop (chebyshev distance 1) from the
actor's current position, since move_cost_feet requires every consecutive
pair in a path to be adjacent. Any click further away silently failed with
"cannot afford this move", regardless of anything actually blocking the
way. Fixed by computing a real multi-square path via the same approach_path/
occupied_squares_by_side logic free-text "move to X" already uses (see
graph/nodes/intent_parser._resolve_move_target) - see session.py's own
player_move handler for the full reasoning.

Uses debug_action-adjacent TestClient plumbing (a stub narrator, no real
LLM call) matching test_ws_session.py's own established pattern - a real
player_move message is what's actually under test here, not debug_action
itself.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.engine.position import BattleMap, Position
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


def _stub_scene_image(state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _make_character(char_id: str, *, is_pc: bool, position: Position, speed: int = 30) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        is_companion=is_pc and char_id != "mover",
        hp=10,
        max_hp=10,
        ac=15,
        position=position,
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=speed,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _corridor_state() -> GameState:
    # A 1-row corridor (no diagonal detour possible) so a successful click
    # past the ally genuinely has to path *through* its square, the same
    # scenario test_monster_ai.py's own ally-pass-through test already
    # proves at the choose_monster_action layer - this test proves the same
    # underlying fix reaches a real human's click-to-move too.
    mover = _make_character("mover", is_pc=True, position=Position(x=0, y=0))
    ally = _make_character("ally", is_pc=True, position=Position(x=3, y=0))
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=6, y=0))
    return GameState(
        encounter_id="player-move-test",
        characters={c.id: c for c in [mover, ally, goblin]},
        turn_order=["mover", "ally", "goblin_1"],
        current_turn=0,
        round=1,
        status="in_progress",
        battle_map=BattleMap(width=7, height=1, terrain=[["floor"] * 7], spawn_points={}),
    )


def test_click_to_move_computes_a_real_multi_square_path() -> None:
    reset_sessions()
    create_session(
        "player-move-test",
        _corridor_state(),
        graph=build_graph(narrator_fn=_stub_narrator, scene_image_fn=_stub_scene_image),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/session/player-move-test") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # awaiting_input

        ws.send_json({"type": "player_move", "to": {"x": 5, "y": 0}})
        narration = ws.receive_json()
        assert narration["type"] == "narration"
        state_msg = ws.receive_json()
        assert state_msg["type"] == "state_update"

    mover_pos = state_msg["game_state"]["characters"]["mover"]["position"]
    # Reached (or got close to) the clicked square - genuinely walked past
    # the ally at x=3, not stuck rejecting the click as "cannot afford."
    assert mover_pos["x"] > 3
    assert mover_pos != {"x": 3, "y": 0}  # never landed on the ally's own square


def test_click_to_move_still_rejects_landing_on_an_occupied_square() -> None:
    # Clicking exactly on an already-occupied square (ally or hostile) still
    # can't be where the move ends - approach_path naturally stops short of
    # a hostile square (never a valid step) and trims a trailing ally one,
    # so this should land adjacent to it, never on it.
    reset_sessions()
    create_session(
        "player-move-test-2",
        _corridor_state(),
        graph=build_graph(narrator_fn=_stub_narrator, scene_image_fn=_stub_scene_image),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/session/player-move-test-2") as ws:
        ws.receive_json()
        ws.receive_json()

        ws.send_json({"type": "player_move", "to": {"x": 3, "y": 0}})  # the ally's own square
        narration = ws.receive_json()
        assert narration["type"] == "narration"
        state_msg = ws.receive_json()
        assert state_msg["type"] == "state_update"

    mover_pos = state_msg["game_state"]["characters"]["mover"]["position"]
    assert mover_pos != {"x": 3, "y": 0}

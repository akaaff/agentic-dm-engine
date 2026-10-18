"""A real player's turn stays open after their main action while a bonus action is still
usable, and the `end_turn` WS message is the way to hand it back (live-reported: the turn used
to pass with the bonus action unspent).

parse_intent_sequence is stubbed to a canned attack so this exercises the server's hold and
`end_turn` handling, not the model's parse."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import create_session, reset_sessions
from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


def _stub_narrator(_state: GraphState) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def _fighter(hp: int) -> Character:
    return Character(
        id="thorin",
        name="Thorin",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=hp,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        class_resources={"second_wind": 1},
        equipped_weapons=["longsword"],
    )


def _goblin() -> Character:
    # Plenty of HP and next to Thorin, so the attack neither kills it nor needs a move first.
    return Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=15,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _set_up_session(session_id: str, hp: int) -> None:
    thorin, goblin = _fighter(hp), _goblin()
    game_state = GameState(
        encounter_id="end_turn_test",
        characters={thorin.id: thorin, goblin.id: goblin},
        turn_order=[thorin.id, goblin.id],
        current_turn=0,
        round=1,
    )
    create_session(
        session_id,
        game_state,
        graph=build_graph(narrator_fn=_stub_narrator, scene_image_fn=_stub_scene_image),
    )


def _attack(_state: GraphState) -> list[ParsedAction]:
    return [
        ParsedAction(
            actor="thorin",
            verb="attack",
            target="goblin_1",
            item_or_spell="longsword",
            raw_text="I swing",
        )
    ]


def _drain_until(ws: Any, wanted: str) -> list[dict[str, Any]]:
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == wanted:
            return messages


def _last(messages: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return [m for m in messages if m["type"] == kind][-1]


def _current(message: dict[str, Any]) -> str:
    state = message["game_state"]
    return str(state["turn_order"][state["current_turn"]])


def test_a_hurt_fighters_turn_stays_open_after_their_attack_and_end_turn_hands_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", _attack)
    _set_up_session("test-end-turn-held", hp=5)
    with TestClient(app).websocket_connect("/ws/session/test-end-turn-held") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # initial awaiting_input

        ws.send_json({"type": "player_action", "text": "irrelevant - parsing is stubbed"})
        held = _drain_until(ws, "awaiting_input")
        state_update = _last(held, "state_update")
        thorin = state_update["game_state"]["characters"]["thorin"]
        assert _current(state_update) == "thorin"  # still Thorin's turn: Second Wind is unspent
        assert thorin["turn_held_for_bonus"] is True and thorin["action_used_this_turn"] is True
        assert _last(held, "awaiting_input")["actor"] == "thorin"

        ws.send_json({"type": "end_turn"})
        ended = _drain_until(ws, "awaiting_input")
        after = _last(ended, "state_update")
        assert _current(after) == "goblin_1"
        assert after["game_state"]["characters"]["thorin"]["turn_held_for_bonus"] is False


def test_a_full_health_fighters_turn_still_passes_straight_after_their_attack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", _attack)
    _set_up_session("test-end-turn-not-held", hp=12)  # Second Wind would heal nothing
    with TestClient(app).websocket_connect("/ws/session/test-end-turn-not-held") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "player_action", "text": "irrelevant"})
        messages = _drain_until(ws, "awaiting_input")
        assert _current(_last(messages, "state_update")) == "goblin_1"

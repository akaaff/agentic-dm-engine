"""A genuinely unexpected exception inside _handle_client_message (a real
bug, not one of the already-handled rejections like TurnEngineError) used
to just crash the connection with nothing durable recorded anywhere except
whatever happened to be in the live terminal at that exact moment. The new
try/except around the call site in session_websocket's own receive loop
(src/api/ws/session.py) logs a kind="backend_error" record to the
centralized log stream before re-raising - this file proves that happens,
and that the ordinary WebSocketDisconnect path (not a bug) is untouched.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import create_session, reset_sessions
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.observability import log_event


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def _character() -> Character:
    return Character(
        id="oen",
        name="Oen",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=10,
        max_hp=10,
        ac=15,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 14, "DEX": 12, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
        speed=30,
        proficiency_bonus=2,
    )


def _set_up_session(session_id: str) -> None:
    oen = _character()
    game_state = GameState(
        encounter_id="backend_error_test",
        characters={oen.id: oen},
        turn_order=[oen.id],
        current_turn=0,
        round=1,
    )
    create_session(session_id, game_state)


def test_an_unexpected_exception_is_logged_and_still_closes_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a genuine bug, not a TurnEngineError")

    monkeypatch.setattr(ws_session_module, "_handle_client_message", _boom)

    _set_up_session("test-backend-error")
    client = TestClient(app)
    with pytest.raises(Exception):  # noqa: B017 - the raw server-side RuntimeError propagates
        with client.websocket_connect("/ws/session/test-backend-error") as ws:
            ws.receive_json()  # initial state_update
            ws.receive_json()  # initial awaiting_input
            ws.send_json({"type": "player_action", "text": "irrelevant"})
            ws.receive_json()

    lines = log_event.EVENTS_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    backend_errors = [r for r in records if r["kind"] == "backend_error"]
    assert len(backend_errors) == 1
    assert backend_errors[0]["session_id"] == "test-backend-error"
    assert backend_errors[0]["exc_type"] == "RuntimeError"
    assert "a genuine bug" in backend_errors[0]["message"]
    assert "Traceback" in backend_errors[0]["traceback"]


def test_an_ordinary_disconnect_is_not_logged_as_a_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _disconnect(*_args: object, **_kwargs: object) -> None:
        raise WebSocketDisconnect()

    monkeypatch.setattr(ws_session_module, "_handle_client_message", _disconnect)

    _set_up_session("test-ordinary-disconnect")
    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-ordinary-disconnect") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "player_action", "text": "irrelevant"})

    assert not log_event.EVENTS_LOG_PATH.exists()

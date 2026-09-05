"""Day 11's two verify criteria: (1) the WebSocket/graph service layer
reproduces Day 7's scripted-combat fixture exactly (proving it's
behavior-neutral over the raw engine), and (2) the multi-connection registry
actually broadcasts to every connection in a session.

(2) needs a genuinely concurrent pair of connections - Starlette's
TestClient WebSocket transport deadlocked when driven from two connections
at once (see CLAUDE.md), so that test runs against a real live uvicorn
server instead, using the real `websockets` client library.
"""

import json
import threading
import time
from collections.abc import Generator
from typing import Any

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.cli.play import (
    SCRIPTED_ACTIONS,
    build_demo_encounter,
    build_demo_party,
    demo_action_rng,
    demo_initiative_rng,
)
from src.engine.actions import ParsedAction
from src.engine.encounter import build_encounter_state


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def _send_action_and_get_state(ws: Any, action: ParsedAction) -> dict[str, Any]:
    ws.send_json({"type": "player_action", "text": action.model_dump_json()})

    narration_msg = ws.receive_json()
    assert narration_msg["type"] == "narration"

    state_msg = ws.receive_json()
    assert state_msg["type"] == "state_update"

    if state_msg["game_state"]["status"] == "in_progress":
        awaiting_msg = ws.receive_json()
        assert awaiting_msg["type"] == "awaiting_input"

    return state_msg["game_state"]  # type: ignore[no-any-return]


def test_websocket_reproduces_day7_scripted_fixture_exactly() -> None:
    encounter = build_demo_encounter()
    party = build_demo_party()
    initial_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    create_session("test-fixture-replay", initial_state, action_rng=demo_action_rng())  # type: ignore[arg-type]

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-fixture-replay") as ws:
        initial_update = ws.receive_json()
        assert initial_update["type"] == "state_update"
        awaiting = ws.receive_json()
        assert awaiting["type"] == "awaiting_input"

        final_state = None
        for action in SCRIPTED_ACTIONS:
            final_state = _send_action_and_get_state(ws, action)

    assert final_state is not None
    assert final_state["status"] == "victory"
    assert final_state["round"] == 3
    assert final_state["characters"]["thorin"]["hp"] == 6
    assert final_state["characters"]["thorin"]["position"] == {"x": 1, "y": 1}
    assert final_state["characters"]["elrond"]["hp"] == 2
    assert final_state["characters"]["elrond"]["position"] == {"x": 1, "y": 2}
    assert final_state["characters"]["goblin_1"]["hp"] == 0
    assert final_state["characters"]["goblin_2"]["hp"] == 0


@pytest.fixture
def live_server() -> Generator[int]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start in time")
        time.sleep(0.02)

    port = server.servers[0].sockets[0].getsockname()[1]
    yield port

    server.should_exit = True
    thread.join(timeout=5)


async def test_broadcast_reaches_every_connection_in_the_session(live_server: int) -> None:
    encounter = build_demo_encounter()
    party = build_demo_party()
    initial_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    create_session("test-broadcast", initial_state, action_rng=demo_action_rng())  # type: ignore[arg-type]

    url = f"ws://127.0.0.1:{live_server}/ws/session/test-broadcast"
    async with websockets.connect(url) as ws_a, websockets.connect(url) as ws_b:
        # Each connection gets its own initial state_update + awaiting_input.
        await ws_a.recv()
        await ws_a.recv()
        await ws_b.recv()
        await ws_b.recv()

        first_action = SCRIPTED_ACTIONS[0]
        await ws_a.send(
            json.dumps({"type": "player_action", "text": first_action.model_dump_json()})
        )

        a_narration = json.loads(await ws_a.recv())
        b_narration = json.loads(await ws_b.recv())
        a_state = json.loads(await ws_a.recv())
        b_state = json.loads(await ws_b.recv())
        a_awaiting = json.loads(await ws_a.recv())  # A connected first, so it owns awaiting_input

    assert a_narration["type"] == "narration"
    assert b_narration["type"] == "narration"
    assert a_state["type"] == "state_update"
    assert b_state["type"] == "state_update"
    assert a_state["game_state"] == b_state["game_state"]
    assert a_awaiting["type"] == "awaiting_input"

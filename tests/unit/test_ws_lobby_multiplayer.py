"""Issue #44's own verification ask: two connections join the same session
via the lobby/join flow, each creates (and is issued a token for) a distinct
character, each only ever receives awaiting_input for their own character;
a third connection presenting an earlier connection's token resumes that
same character - including after the in-memory session is evicted and
rebuilt from the DB, simulating a real reconnect.

Needs genuinely concurrent WebSocket connections (TestClient's own transport
deadlocks with two open at once - see CLAUDE.md), so this combines
test_ws_session.py's live_server fixture (a real uvicorn.Server on a
background thread + the `websockets` client) with test_ws_session_real_
start.py's in-memory-DB + stubbed-graph setup (a real campaign encounter
without a real LLM/GPU call).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Generator
from typing import Any

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app
from src.api.ws import session as ws_session_module
from src.engine.actions import ParsedAction
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

_THORIN_BODY = {
    "character_id": "thorin",
    "name": "Thorin",
    "race_index": "human",
    "class_index": "fighter",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
    "chosen_skills": ["skill-athletics", "skill-perception"],
    "chosen_equipment": ["chain-mail", "shield"],
}
_ELROND_BODY = {
    "character_id": "elrond",
    "name": "Elrond",
    "race_index": "elf",
    "class_index": "wizard",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
    "chosen_skills": ["skill-arcana", "skill-history"],
    "chosen_equipment": ["dagger"],
}


def _stub_narrator(_state: GraphState) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _stub_player_agent(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {"parsed_action": ParsedAction(actor=actor_id, verb="end_turn", raw_text="stub")}


@pytest.fixture
def rest_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db() -> Generator[Session]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    # api/ws/session.py calls SessionLocal() directly (not a route, so no
    # FastAPI Depends() to override) - point it at the same in-memory engine
    # the REST calls use, or the WS handler and POST /sessions/... would see
    # two disconnected databases.
    monkeypatch.setattr(ws_session_module, "SessionLocal", TestSessionLocal)

    def _stub_build_graph(**kwargs: Any) -> Any:
        return build_graph(
            rng=kwargs.get("rng"),
            srd=kwargs.get("srd"),
            narrator_fn=_stub_narrator,
            player_agent_fn=_stub_player_agent,
            scene_image_fn=_stub_scene_image,
        )

    monkeypatch.setattr(ws_session_module, "build_graph", _stub_build_graph)

    ws_session_module.reset_sessions()
    yield TestClient(app)
    app.dependency_overrides.clear()
    ws_session_module.reset_sessions()


@pytest.fixture
def live_server(rest_client: TestClient) -> Generator[int]:
    # Depends on rest_client (not just imported alongside it) so the DB
    # override/monkeypatches are already in place before the real server
    # thread starts serving requests against the same `app` object.
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


def _create_and_join(rest_client: TestClient, session_id: str, body: dict[str, Any]) -> str:
    create_response = rest_client.post("/characters", json=body)
    assert create_response.status_code == 201
    join_response = rest_client.post(
        f"/sessions/{session_id}/join", json={"character_id": body["character_id"]}
    )
    assert join_response.status_code == 200
    token: str = join_response.json()["token"]
    return token


def _set_up_two_player_lobby(rest_client: TestClient) -> tuple[str, str, str]:
    """Returns (session_id, thorin_token, elrond_token) for an already-
    started 2-human lobby."""
    lobby_response = rest_client.post(
        "/sessions/lobby", json={"campaign_id": "goblin_ambush_oneshot"}
    )
    assert lobby_response.status_code == 201
    session_id: str = lobby_response.json()["session_id"]

    thorin_token = _create_and_join(rest_client, session_id, _THORIN_BODY)
    elrond_token = _create_and_join(rest_client, session_id, _ELROND_BODY)

    start_response = rest_client.post(f"/sessions/{session_id}/start", json={})
    assert start_response.status_code == 200

    return session_id, thorin_token, elrond_token


async def _drain_awaiting_input_actors(ws: Any, seconds: float) -> list[str]:
    """Collects every awaiting_input actor this connection receives within
    the window - robust to however many narration/state_update broadcasts
    interleave with them (real, unstubbed monster turns can generate extra
    broadcasts), which a fixed expected-message-count assertion isn't."""
    deadline = asyncio.get_event_loop().time() + seconds
    actors = []
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        except TimeoutError:
            break
        if msg["type"] == "awaiting_input":
            actors.append(msg["actor"])
    return actors


async def test_each_connection_only_receives_awaiting_input_for_its_own_character(
    rest_client: TestClient, live_server: int
) -> None:
    session_id, thorin_token, elrond_token = _set_up_two_player_lobby(rest_client)

    thorin_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={thorin_token}"
    elrond_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={elrond_token}"

    async with (
        websockets.connect(thorin_url) as ws_thorin,
        websockets.connect(elrond_url) as ws_elrond,
    ):
        thorin_actors = await _drain_awaiting_input_actors(ws_thorin, 2.0)
        elrond_actors = await _drain_awaiting_input_actors(ws_elrond, 0.5)

    # Each connection may see zero or more awaiting_input messages for its
    # own character (autoplay can cycle back around within the drain
    # window) but must never see the other player's.
    assert all(a == "thorin" for a in thorin_actors)
    assert all(a == "elrond" for a in elrond_actors)
    # At least one of the two genuinely got prompted at all - otherwise this
    # test would trivially pass by both connections seeing nothing.
    assert thorin_actors or elrond_actors


async def _wait_for_awaiting_input(ws: Any, seconds: float) -> bool:
    """True once this connection receives its own awaiting_input within the
    window - the authoritative "is it genuinely this character's turn right
    now" signal, unlike a state_update's turn_order/current_turn snapshot,
    which can already be stale by the time a test acts on it (autoplay keeps
    running server-side in between)."""
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return False
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        except TimeoutError:
            return False
        if msg["type"] == "awaiting_input":
            return True


async def test_a_connection_cannot_act_for_a_character_it_does_not_control(
    rest_client: TestClient, live_server: int
) -> None:
    session_id, thorin_token, elrond_token = _set_up_two_player_lobby(rest_client)

    thorin_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={thorin_token}"
    elrond_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={elrond_token}"

    async with (
        websockets.connect(thorin_url) as ws_thorin,
        websockets.connect(elrond_url) as ws_elrond,
    ):
        # Race both connections for their own awaiting_input - exactly one
        # should win, confirming who genuinely controls the current actor
        # right now, not a possibly-stale snapshot from an earlier message.
        thorin_task = asyncio.ensure_future(_wait_for_awaiting_input(ws_thorin, 5.0))
        elrond_task = asyncio.ensure_future(_wait_for_awaiting_input(ws_elrond, 5.0))
        done, pending = await asyncio.wait(
            {thorin_task, elrond_task}, return_when=asyncio.FIRST_COMPLETED
        )
        thorin_is_up = thorin_task in done and thorin_task.result()
        for task in pending:
            task.cancel()
        # Must actually await a cancelled task before reusing the websocket
        # it was recv()-ing on - cancel() only schedules the cancellation,
        # and websockets' own recv() raises a ConcurrencyError if a second
        # recv() starts before the first one has genuinely unwound.
        await asyncio.gather(*pending, return_exceptions=True)

        # Whichever connection does NOT control the current actor tries to
        # act anyway - must be rejected, not silently let through as that
        # actor.
        wrong_ws = ws_elrond if thorin_is_up else ws_thorin

        # Drain anything already queued on wrong_ws (its own connect-time
        # state_update, or a broadcast from a monster/companion turn the
        # other connection's own awaiting_input raced ahead of) so the
        # message read right after sending is genuinely the *response* to
        # this send, not stale backlog.
        while True:
            try:
                await asyncio.wait_for(wrong_ws.recv(), timeout=0.2)
            except TimeoutError:
                break

        await wrong_ws.send(json.dumps({"type": "player_move", "to": {"x": 0, "y": 0}}))
        rejection = json.loads(await wrong_ws.recv())

    assert rejection["type"] == "error"
    assert "not your turn" in rejection["detail"].lower()


async def test_reconnecting_with_an_existing_token_resumes_the_same_character_after_eviction(
    rest_client: TestClient, live_server: int
) -> None:
    session_id, thorin_token, _elrond_token = _set_up_two_player_lobby(rest_client)

    url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={thorin_token}"
    async with websockets.connect(url) as ws:
        msg = json.loads(await ws.recv())
        while msg["type"] != "state_update":
            msg = json.loads(await ws.recv())

    # Simulates a real server restart / session-cache eviction - nothing
    # left in memory, the next connect must rebuild entirely from the DB.
    ws_session_module.reset_sessions()
    assert session_id not in ws_session_module._sessions

    async with websockets.connect(url) as ws:
        msg = json.loads(await ws.recv())
        while msg["type"] != "state_update":
            msg = json.loads(await ws.recv())
        character_ids = set(msg["game_state"]["characters"].keys())

    assert "thorin" in character_ids
    session = ws_session_module._sessions[session_id]
    assert session.human_character_ids[thorin_token] == "thorin"


async def test_reconnecting_mid_session_without_eviction_resumes_with_correct_awaiting_input(
    rest_client: TestClient, live_server: int
) -> None:
    # Issue #46's own verification ask: a connection controlling a character
    # disconnects and a new connection with the same join token reconnects
    # *while the in-memory session is still alive* (no server restart this
    # time, unlike the eviction test above) - the ordinary "phone sleeps,
    # wifi blips" case, not a server-restart edge case.
    session_id, thorin_token, elrond_token = _set_up_two_player_lobby(rest_client)
    url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={thorin_token}"

    async with websockets.connect(url) as ws:
        msg = json.loads(await ws.recv())
        while msg["type"] != "state_update":
            msg = json.loads(await ws.recv())
    # `async with` has already closed this connection cleanly by here -
    # session.connections no longer holds it (confirmed indirectly below by
    # the reconnect succeeding and getting its own personal messages).

    async with websockets.connect(url) as ws:
        thorin_state = json.loads(await ws.recv())
        while thorin_state["type"] != "state_update":
            thorin_state = json.loads(await ws.recv())
        current_actor = thorin_state["game_state"]["turn_order"][
            thorin_state["game_state"]["current_turn"]
        ]
        if current_actor == "thorin":
            awaiting = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert awaiting == {"type": "awaiting_input", "actor": "thorin"}

    session = ws_session_module._sessions[session_id]
    assert session.human_character_ids[thorin_token] == "thorin"
    assert session.human_character_ids[elrond_token] == "elrond"


async def test_other_connection_is_notified_of_a_disconnect_and_reconnect(
    rest_client: TestClient, live_server: int
) -> None:
    session_id, thorin_token, elrond_token = _set_up_two_player_lobby(rest_client)
    thorin_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={thorin_token}"
    elrond_url = f"ws://127.0.0.1:{live_server}/ws/session/{session_id}?token={elrond_token}"

    async with websockets.connect(elrond_url) as ws_elrond:
        elrond_state = json.loads(await ws_elrond.recv())
        while elrond_state["type"] != "state_update":
            elrond_state = json.loads(await ws_elrond.recv())

        async with websockets.connect(thorin_url) as ws_thorin:
            thorin_state = json.loads(await ws_thorin.recv())
            while thorin_state["type"] != "state_update":
                thorin_state = json.loads(await ws_thorin.recv())
        # ws_thorin closed cleanly here (end of the inner `async with`) -
        # elrond's connection should be told thorin's player dropped.

        disconnect_notice = None
        deadline = asyncio.get_event_loop().time() + 3
        while asyncio.get_event_loop().time() < deadline:
            msg = json.loads(
                await asyncio.wait_for(
                    ws_elrond.recv(), timeout=deadline - asyncio.get_event_loop().time()
                )
            )
            if msg["type"] == "player_disconnected":
                disconnect_notice = msg
                break
        assert disconnect_notice == {"type": "player_disconnected", "actor": "thorin"}

        # Reconnecting with the same token - elrond's connection should now
        # hear that thorin's player is back.
        async with websockets.connect(thorin_url) as ws_thorin_again:
            msg = json.loads(await ws_thorin_again.recv())
            while msg["type"] != "state_update":
                msg = json.loads(await ws_thorin_again.recv())

            reconnect_notice = None
            deadline = asyncio.get_event_loop().time() + 3
            while asyncio.get_event_loop().time() < deadline:
                elrond_msg = json.loads(
                    await asyncio.wait_for(
                        ws_elrond.recv(), timeout=deadline - asyncio.get_event_loop().time()
                    )
                )
                if elrond_msg["type"] == "player_reconnected":
                    reconnect_notice = elrond_msg
                    break

    assert reconnect_notice == {"type": "player_reconnected", "actor": "thorin"}

"""Multi-character play: one player (one browser, one WebSocket) controls several
characters. Each is still its own lobby seat with its own token; the client
sends every token it holds as a repeated ?token= param and the connection
controls the union (session_websocket). A single TestClient connection is
enough - this is one player, not two."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from src import config
from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import PendingPartyChoice
from src.engine.actions import ParsedAction
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

_THORIN = {
    "character_id": "thorin",
    "name": "Thorin",
    "race_index": "human",
    "class_index": "fighter",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
    "chosen_skills": ["skill-athletics", "skill-perception"],
    "chosen_equipment": ["chain-mail", "shield"],
}
_ELROND = {
    "character_id": "elrond",
    "name": "Elrond",
    "race_index": "elf",
    "class_index": "wizard",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
    "chosen_skills": ["skill-arcana", "skill-history"],
    "chosen_prepared_spells": ["magic-missile", "burning-hands", "mage-armor"],
    "chosen_equipment": ["dagger"],
}


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


def _stub_scene_image(state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _stub_player_agent(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {"parsed_action": ParsedAction(actor=actor_id, verb="end_turn", raw_text="stub")}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    monkeypatch.setattr(ws_session_module, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(config, "ALLOW_DEBUG_ACTIONS", True)

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


def _two_seat_lobby(client: TestClient) -> tuple[str, str, str]:
    """A started lobby where ONE player holds both human seats (thorin and
    elrond) plus a companion. Returns (session_id, thorin_token, elrond_token)."""
    assert client.post("/characters", json=_THORIN).status_code == 201
    assert client.post("/characters", json=_ELROND).status_code == 201
    session_id = client.post(
        "/sessions/lobby", json={"campaign_id": "goblin_ambush_oneshot"}
    ).json()["session_id"]
    thorin_token = client.post(
        f"/sessions/{session_id}/join", json={"character_id": "thorin"}
    ).json()["token"]
    elrond_token = client.post(
        f"/sessions/{session_id}/join", json={"character_id": "elrond"}
    ).json()["token"]
    start = client.post(f"/sessions/{session_id}/start", json={"companion_ids": ["companion_grom"]})
    assert start.status_code == 200
    return session_id, thorin_token, elrond_token


def test_a_connection_with_both_tokens_is_asked_for_input_on_either_characters_turn(
    client: TestClient,
) -> None:
    session_id, thorin_token, elrond_token = _two_seat_lobby(client)

    actors_seen: list[str] = []
    with client.websocket_connect(
        f"/ws/session/{session_id}?token={thorin_token}&token={elrond_token}"
    ) as ws:
        for _ in range(60):  # bounded: a few rounds is plenty for both to come up
            message = ws.receive_json()
            if message["type"] != "awaiting_input":
                continue
            actors_seen.append(message["actor"])
            if {"thorin", "elrond"} <= set(actors_seen):
                break
            ws.send_json(
                {
                    "type": "debug_action",
                    "action": {"actor": message["actor"], "verb": "end_turn", "raw_text": "pass"},
                }
            )

    assert {"thorin", "elrond"} <= set(actors_seen)


def test_the_connection_may_act_for_whichever_of_its_characters_is_up(
    client: TestClient,
) -> None:
    """player_move (like player_action) is rejected for a character the
    connection doesn't control - with both tokens, neither character's turn is
    refused, whichever comes up first."""
    session_id, thorin_token, elrond_token = _two_seat_lobby(client)
    handled: set[str] = set()

    with client.websocket_connect(
        f"/ws/session/{session_id}?token={thorin_token}&token={elrond_token}"
    ) as ws:
        for _ in range(60):
            message = ws.receive_json()
            if message["type"] != "awaiting_input":
                continue
            actor = message["actor"]
            ws.send_json({"type": "player_move", "to": {"x": 0, "y": 0}})
            # Whatever happens to that move, it must not be "not your turn".
            reply = ws.receive_json()
            while reply["type"] not in ("error", "state_update", "awaiting_input"):
                reply = ws.receive_json()
            if reply["type"] == "error":
                assert "not your turn" not in reply["detail"].lower()
            handled.add(actor)
            if handled >= {"thorin", "elrond"}:
                break
            ws.send_json(
                {
                    "type": "debug_action",
                    "action": {"actor": actor, "verb": "end_turn", "raw_text": "pass"},
                }
            )

    assert handled >= {"thorin", "elrond"}


def test_an_unknown_token_is_ignored_when_another_is_valid_but_alone_it_is_refused(
    client: TestClient,
) -> None:
    session_id, thorin_token, _ = _two_seat_lobby(client)

    with client.websocket_connect(
        f"/ws/session/{session_id}?token=stale&token={thorin_token}"
    ) as ws:
        first = ws.receive_json()
        assert first["type"] != "error"  # the valid token carried the connection

    with client.websocket_connect(f"/ws/session/{session_id}?token=stale") as ws:
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_a_party_choice_answer_names_which_of_the_players_characters_is_speaking(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved: list[dict[str, str]] = []

    async def _resolve(session: Any) -> None:
        # Once everyone answers the real one writes a continuation with the LLM -
        # not what this test is about; only which seat answered matters here.
        resolved.append(dict(session.pending_party_choice.responses))
        session.pending_party_choice = None

    monkeypatch.setattr(ws_session_module, "_resolve_party_choice", _resolve)
    session_id, thorin_token, elrond_token = _two_seat_lobby(client)

    with client.websocket_connect(
        f"/ws/session/{session_id}?token={thorin_token}&token={elrond_token}"
    ) as ws:
        ws.receive_json()  # connected; the session now exists
        live = ws_session_module._sessions[session_id]
        live.pending_party_choice = PendingPartyChoice(
            scene_id="s", situation="A ledger!", responses={}
        )

        ws.send_json(
            {"type": "party_choice_response", "character_id": "elrond", "text": "Read it."}
        )
        answered = _next_of_type(ws, "party_choice_responded")
        assert answered["actor"] == "elrond"

        # A seat that isn't this connection's to answer is refused...
        ws.send_json(
            {"type": "party_choice_response", "character_id": "companion_grom", "text": "x"}
        )
        assert "can't answer" in _next_of_type(ws, "error")["detail"]
        # ...and one that already answered can't answer twice.
        ws.send_json({"type": "party_choice_response", "character_id": "elrond", "text": "again"})
        assert "can't answer" in _next_of_type(ws, "error")["detail"]

        # With no character named, the remaining seat answers - and with both seats
        # answered, the pause resolves from everyone's own words.
        ws.send_json({"type": "party_choice_response", "text": "Burn it."})
        assert _next_of_type(ws, "party_choice_responded")["actor"] == "thorin"

    assert resolved == [{"elrond": "Read it.", "thorin": "Burn it."}]


def _next_of_type(ws: Any, wanted: str) -> dict[str, Any]:
    for _ in range(40):
        message: dict[str, Any] = ws.receive_json()
        if message["type"] == wanted:
            return message
    raise AssertionError(f"never received a {wanted!r} message")

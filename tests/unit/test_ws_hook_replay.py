"""The pre-combat hook narration used to go only to the first connection that
opened and was then cleared - so a page refresh, a second player, or the dev
build's StrictMode double-socket (which discards the first socket) never saw
it. Session.hook_messages now keeps the built messages (audio/image generated
once) and replays them to a later connection while the game is still in round 1
of the encounter the hook leads into - never to an auto-reconnect (`?resume=1`),
whose log still has it."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session_persistence import build_snapshot, restore_snapshot
from src.engine.actions import ParsedAction
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

_FIGHTER_BODY = {
    "character_id": "thorin",
    "name": "Thorin",
    "race_index": "human",
    "class_index": "fighter",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
    "chosen_skills": ["skill-athletics", "skill-perception"],
    "chosen_equipment": ["chain-mail", "shield"],
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


@dataclass
class _Env:
    client: TestClient
    db_factory: sessionmaker[Session]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Generator[_Env]:
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
    yield _Env(client=TestClient(app), db_factory=TestSessionLocal)
    app.dependency_overrides.clear()
    ws_session_module.reset_sessions()


def _start_real_session(env: _Env) -> str:
    assert env.client.post("/characters", json=_FIGHTER_BODY).status_code == 201
    response = env.client.post(
        "/sessions",
        json={
            "campaign_id": "goblin_ambush_oneshot",
            "character_id": "thorin",
            "companion_ids": ["companion_grom"],
        },
    )
    assert response.status_code == 201
    session_id: str = response.json()["session_id"]
    return session_id


_HOOK = ["The town has lost three caravans.", "The wreckage still smoulders."]


def _prepare_session_with_hook(env: _Env, monkeypatch: pytest.MonkeyPatch) -> tuple[str, list[int]]:
    """A real session whose first connection will find a hook to deliver.
    Returns the session id and a counter of how many images were generated."""
    session_id = _start_real_session(env)
    session = ws_session_module._get_or_create_default_session(session_id)
    session.pending_scene_narration = list(_HOOK)
    generated: list[int] = []

    def illustrate(lines: list[str]) -> str:
        generated.append(1)
        return "/media/scene-images/hook.png"

    monkeypatch.setattr(ws_session_module, "illustrate_narration", illustrate)
    return session_id, generated


def _connect(env: _Env, session_id: str, query: str = "") -> list[dict[str, Any]]:
    """Messages up to and including the first state_update."""
    seen: list[dict[str, Any]] = []
    with env.client.websocket_connect(f"/ws/session/{session_id}{query}") as ws:
        while True:
            message = ws.receive_json()
            seen.append(message)
            if message["type"] == "state_update":
                return seen


def _hook_lines(messages: list[dict[str, Any]]) -> list[str]:
    return [m["text"] for m in messages if m["type"] == "scene_narration"]


def test_a_later_connection_still_gets_the_hook_and_it_is_illustrated_once(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, generated = _prepare_session_with_hook(env, monkeypatch)

    first = _connect(env, session_id)
    second = _connect(env, session_id)  # a refresh / second player / StrictMode's real socket

    assert _hook_lines(first) == _HOOK
    assert _hook_lines(second) == _HOOK
    # The replay reuses the built messages - no second image (or TTS) run.
    assert len(generated) == 1
    assert [m.get("image_url") for m in second if m["type"] == "scene_narration"] == [
        "/media/scene-images/hook.png",
        None,
    ]


def test_an_auto_reconnect_is_not_replayed_the_hook_it_already_has(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)

    resumed = _connect(env, session_id, "?resume=1")

    assert _hook_lines(resumed) == []


def test_the_hook_is_not_replayed_once_the_game_has_moved_past_round_1(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    ws_session_module._sessions[session_id].game_state.round = 2

    later = _connect(env, session_id)

    assert _hook_lines(later) == []


def test_the_hook_is_not_replayed_for_a_different_encounter(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    ws_session_module._sessions[session_id].current_scene_id = "some_later_scene"

    assert _hook_lines(_connect(env, session_id)) == []


def test_the_hook_survives_a_snapshot_round_trip(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So a page refresh right after a backend restart still replays it."""
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]
    assert live.hook_messages  # built by the first connection

    restored = restore_snapshot(build_snapshot(live), ["thorin", "companion_grom"])

    assert [m["text"] for m in restored.hook_messages] == _HOOK
    assert restored.hook_scene_id == live.hook_scene_id

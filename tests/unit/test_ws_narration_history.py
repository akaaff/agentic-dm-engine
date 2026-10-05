"""Narration is broadcast ONCE, to whoever is connected at that instant, and the
narration log lives in the browser - so a page refresh, a second player, a
reconnect after a dropped socket, or the dev build's StrictMode double-socket
(its first, discarded socket receives everything produced during the opening
autoplay) used to be missing lines for good, leaving bare event badges.
Session.narration_history keeps every line (text + the events it narrates, no
audio) and a connecting client is sent what it hasn't seen: everything on a
fresh page, or just what came after `?since_seq=N` for a reconnect. Replaces the
earlier hook-only replay."""

from __future__ import annotations

import asyncio
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
from src.engine.events import Event
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

_HOOK = ["The town has lost three caravans.", "The wreckage still smoulders."]


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


def _prepare_session_with_hook(env: _Env, monkeypatch: pytest.MonkeyPatch) -> tuple[str, list[int]]:
    """A real session whose first connection will find a hook to deliver.
    Returns the session id and a counter of how many images were generated."""
    session_id = _start_real_session(env)
    session = ws_session_module._get_or_create_default_session(session_id)
    session.pending_scene_narration = list(_HOOK)
    # A companion always acts before the human, so the connect-time autoplay
    # narrates opening turns - the lines a late-joining socket used to lose -
    # instead of that depending on how initiative happened to roll.
    order = session.game_state.turn_order
    session.game_state.turn_order = ["companion_grom", *[c for c in order if c != "companion_grom"]]
    session.game_state.current_turn = 0
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


def _history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for m in messages:
        if m["type"] == "narration_history":
            entries.extend(m["entries"])
    return entries


def _add_event(session: Any, actor: str = "thorin") -> None:
    session.game_state.events.append(
        Event(
            round=session.game_state.round,
            turn_index=0,
            actor=actor,
            type="attack_roll",
            payload={"target": "goblin_1", "hit": True},
        )
    )


def test_the_first_connection_gets_lines_live_and_no_backfill(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, generated = _prepare_session_with_hook(env, monkeypatch)

    first = _connect(env, session_id)

    assert _history(first) == []  # nothing had happened yet to backfill
    live = [m for m in first if m["type"] == "scene_narration"]
    assert [m["text"] for m in live] == _HOOK
    assert [m["seq"] for m in live] == [1, 2]  # stamped for the client's dedupe
    assert live[0]["image_url"] == "/media/scene-images/hook.png"
    assert len(generated) == 1


def test_a_later_connection_is_backfilled_everything_it_missed_without_audio(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, generated = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)  # the connection that got it live (and then went away)
    live = ws_session_module._sessions[session_id]
    _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "Thorin strikes the goblin."))

    second = _connect(env, session_id)  # a refresh / second player / StrictMode's real socket

    entries = _history(second)
    assert [e["text"] for e in entries[:2]] == _HOOK
    assert [e["kind"] for e in entries[:2]] == ["scene", "scene"]
    # The turns auto-played while the first connection was opening the game (the
    # very lines this fix exists to recover) sit between the hook and our line.
    opening = entries[2:-1]
    assert opening and all(e["kind"] == "action" for e in opening)
    assert entries[-1]["text"] == "Thorin strikes the goblin."
    assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))
    assert entries[0]["image_url"] == "/media/scene-images/hook.png"
    assert all("audio_url" not in e for e in entries)  # silent: instant, not re-read aloud
    # The action line carries the events it narrates, so its badges still show.
    assert "attack_roll" in [ev["type"] for ev in entries[-1]["events"]]
    assert len(generated) == 1  # nothing was regenerated for the backfill
    # ...and the history arrives before the state_update, ahead of anything live.
    types = [m["type"] for m in second]
    assert types.index("narration_history") < types.index("state_update")


def test_since_seq_sends_only_what_a_reconnecting_client_has_not_seen(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]
    seen_so_far = live.narration_seq  # the reconnecting client had everything up to here
    asyncio.run(ws_session_module._broadcast_narration(live, "While you were gone."))

    resumed = _connect(env, session_id, f"?since_seq={seen_so_far}")

    assert [e["text"] for e in _history(resumed)] == ["While you were gone."]
    caught_up = _connect(env, session_id, f"?since_seq={live.narration_seq}")
    assert _history(caught_up) == []  # nothing left to send


def test_each_action_entry_carries_only_the_events_since_the_previous_one(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]

    _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "one"))
    _add_event(live)
    _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "two"))

    by_text = {e["text"]: e for e in live.narration_history}
    # The opening autoplay's narration already accounted for everything before
    # these; each new entry carries only the events added since the one before.
    assert len(by_text["one"]["events"]) == 1
    assert len(by_text["two"]["events"]) == 2
    assert by_text["two"]["events_end"] == len(live.game_state.events)


def test_the_event_cursor_restarts_with_a_new_encounters_event_list(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]
    for _ in range(5):
        _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "old fight"))
    assert live.narration_events_cursor == len(live.game_state.events)

    live.game_state.events.clear()  # what a fresh GameState's list looks like
    _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "new fight"))

    assert len(live.narration_history[-1]["events"]) == 1  # not sliced away by the stale cursor


def test_history_is_capped(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]
    monkeypatch.setattr(ws_session_module, "NARRATION_HISTORY_LIMIT", 5)

    for i in range(12):
        asyncio.run(ws_session_module._broadcast_narration(live, f"line {i}"))

    assert len(live.narration_history) == 5
    assert live.narration_history[-1]["text"] == "line 11"
    assert live.narration_history[0]["seq"] == live.narration_seq - 4  # newest kept, in order


def test_history_and_the_sequence_counter_survive_a_snapshot_round_trip(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So a refresh right after a backend restart still gets the log back, and
    new lines don't reuse sequence numbers a client already holds."""
    session_id, _ = _prepare_session_with_hook(env, monkeypatch)
    _connect(env, session_id)
    live = ws_session_module._sessions[session_id]
    _add_event(live)
    asyncio.run(ws_session_module._broadcast_narration(live, "Thorin strikes."))

    restored = restore_snapshot(build_snapshot(live), ["thorin", "companion_grom"])

    texts = [e["text"] for e in restored.narration_history]
    assert texts == [e["text"] for e in live.narration_history]
    assert texts[:2] == _HOOK and texts[-1] == "Thorin strikes."
    # The counter is the history's own length here (nothing was trimmed), however
    # many opening turns the stubbed autoplay happened to narrate before it.
    assert restored.narration_seq == live.narration_seq == len(texts)
    assert restored.narration_events_cursor == live.narration_events_cursor

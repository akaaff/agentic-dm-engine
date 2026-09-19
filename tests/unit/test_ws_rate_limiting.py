"""Issue #43's two WS-level guards: a per-session action rate limit (the
token bucket itself is unit-tested directly in test_rate_limit.py) and a
hard cap on how many distinct sessions may be live at once - both protect
the one shared local GPU/Ollama instance behind every session's narrator/
scene_image calls, not any individual cheap REST read.
"""

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.cli.play import (
    SCRIPTED_ACTIONS,
    build_demo_encounter,
    build_demo_party,
    demo_action_rng,
    demo_initiative_rng,
)
from src.engine.encounter import build_encounter_state
from src.graph.graph_builder import build_graph


def _stub_narrator(_state: Any) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


@pytest.fixture(autouse=True)
def _allow_debug_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ALLOW_DEBUG_ACTIONS", True)


def _create_demo_session(session_id: str) -> None:
    encounter = build_demo_encounter()
    party = build_demo_party()
    initial_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    action_rng = demo_action_rng()
    create_session(
        session_id,
        initial_state,
        action_rng=action_rng,  # type: ignore[arg-type]
        graph=build_graph(rng=action_rng, narrator_fn=_stub_narrator),  # type: ignore[arg-type]
    )


def test_nth_plus_one_action_is_rejected_within_the_rate_limit_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tiny capacity with zero refill makes this deterministic regardless
    # of how fast the test itself runs - no real sleeping needed.
    monkeypatch.setattr(config, "ACTION_RATE_LIMIT_CAPACITY", 2.0)
    monkeypatch.setattr(config, "ACTION_RATE_LIMIT_PER_MINUTE", 0.0)
    _create_demo_session("test-rate-limit")

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-rate-limit") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # initial awaiting_input

        for action in SCRIPTED_ACTIONS[:2]:
            ws.send_json({"type": "debug_action", "action": action.model_dump(mode="json")})
            narration = ws.receive_json()
            assert narration["type"] == "narration"
            ws.receive_json()  # state_update
            ws.receive_json()  # awaiting_input (or an auto-played turn's - not asserted here)

        # The bucket is now empty (capacity 2, zero refill) - a 3rd action
        # is rejected without ever reaching the graph.
        third = SCRIPTED_ACTIONS[2] if len(SCRIPTED_ACTIONS) > 2 else SCRIPTED_ACTIONS[0]
        ws.send_json({"type": "debug_action", "action": third.model_dump(mode="json")})
        rejection = ws.receive_json()
        assert rejection["type"] == "error"
        assert "slow down" in rejection["detail"]


def test_rate_limit_resets_after_the_refill_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ACTION_RATE_LIMIT_CAPACITY", 1.0)
    monkeypatch.setattr(config, "ACTION_RATE_LIMIT_PER_MINUTE", 6000.0)  # 100/sec - refills fast
    _create_demo_session("test-rate-limit-reset")

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-rate-limit-reset") as ws:
        ws.receive_json()
        ws.receive_json()

        action = SCRIPTED_ACTIONS[0]
        ws.send_json({"type": "debug_action", "action": action.model_dump(mode="json")})
        assert ws.receive_json()["type"] == "narration"
        ws.receive_json()  # state_update
        ws.receive_json()  # awaiting_input

        time.sleep(0.05)  # >5 tokens' worth at 100/sec - the bucket is not exhausted long

        ws.send_json({"type": "debug_action", "action": action.model_dump(mode="json")})
        # Actor moved on after the first action resolved, so this specific
        # ParsedAction may now be a TurnEngineError rather than a clean
        # success - either way it's NOT the rate-limit rejection, which is
        # the only thing this test is checking.
        second = ws.receive_json()
        assert second["type"] != "error" or "slow down" not in second.get("detail", "")


def test_websocket_connect_rejected_when_max_concurrent_sessions_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_CONCURRENT_SESSIONS", 1)
    _create_demo_session("test-cap-session-a")
    _create_demo_session("test-cap-session-b")

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-cap-session-a") as ws_a:
        ws_a.receive_json()
        ws_a.receive_json()

        # A second, *different* session while the cap (1) is already used -
        # rejected before the handshake completes.
        with pytest.raises(Exception):  # noqa: B017 - Starlette raises on a rejected handshake
            with client.websocket_connect("/ws/session/test-cap-session-b"):
                pass


def test_a_second_connection_to_the_same_already_active_session_is_not_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_CONCURRENT_SESSIONS", 1)
    _create_demo_session("test-cap-shared-session")

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-cap-shared-session") as ws_a:
        ws_a.receive_json()
        ws_a.receive_json()

        # Same session_id as an already-active connection - doesn't count as
        # a *new* consumer of the cap, so this must succeed.
        with client.websocket_connect("/ws/session/test-cap-shared-session") as ws_b:
            first = ws_b.receive_json()
            assert first["type"] == "state_update"

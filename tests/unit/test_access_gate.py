"""Issue #42 - the shared-passphrase gate in front of every REST/WS endpoint
except /health. SHARED_ACCESS_PASSPHRASE is unset in the ordinary test
environment (config.py's own default), so every other test file's requests
are correctly unaffected - these tests monkeypatch the *module-level* name
each gate point actually reads (src.api.main.SHARED_ACCESS_PASSPHRASE /
src.api.ws.session.config.SHARED_ACCESS_PASSPHRASE) rather than the
src.config source, since both api entry points bind/read it independently.
"""

import pytest
from fastapi.testclient import TestClient

import src.api.main as main_module
from src import config
from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.cli.play import (
    build_demo_encounter,
    build_demo_party,
    demo_action_rng,
    demo_initiative_rng,
)
from src.engine.encounter import build_encounter_state
from src.graph.graph_builder import build_graph


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def test_rest_request_passes_through_unchanged_when_passphrase_unset() -> None:
    client = TestClient(app)
    response = client.get("/characters/races")
    assert response.status_code == 200


def test_rest_request_is_rejected_without_the_passphrase_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "SHARED_ACCESS_PASSPHRASE", "hunter2")
    client = TestClient(app)

    response = client.get("/characters/races")
    assert response.status_code == 401

    wrong = client.get("/characters/races", headers={"X-Access-Passphrase": "not-it"})
    assert wrong.status_code == 401

    right = client.get("/characters/races", headers={"X-Access-Passphrase": "hunter2"})
    assert right.status_code == 200


def test_health_stays_reachable_without_the_passphrase_even_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "SHARED_ACCESS_PASSPHRASE", "hunter2")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "passphrase_required": True}


def _create_demo_session(session_id: str) -> None:
    encounter = build_demo_encounter()
    party = build_demo_party()
    initial_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    action_rng = demo_action_rng()
    create_session(
        session_id,
        initial_state,
        action_rng=action_rng,  # type: ignore[arg-type]
        graph=build_graph(rng=action_rng, narrator_fn=lambda _state: {"narration": "stub"}),  # type: ignore[arg-type]
    )


def test_websocket_connect_is_rejected_without_the_passphrase_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SHARED_ACCESS_PASSPHRASE", "hunter2")
    _create_demo_session("test-ws-gate-rejected")

    client = TestClient(app)
    with pytest.raises(Exception):  # noqa: B017 - Starlette raises on a rejected handshake
        with client.websocket_connect("/ws/session/test-ws-gate-rejected"):
            pass


def test_websocket_connects_normally_with_the_correct_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SHARED_ACCESS_PASSPHRASE", "hunter2")
    _create_demo_session("test-ws-gate-accepted")

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-ws-gate-accepted?key=hunter2") as ws:
        initial = ws.receive_json()
        assert initial["type"] == "state_update"

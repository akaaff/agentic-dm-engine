"""Issue #105 - the blocking LLM/TTS/image calls in a turn run in worker
threads, so the event loop keeps answering pings, /health and other sessions
while one is in flight, and an Ollama timeout while reading a human's free text
no longer drops the connection."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import Session, SessionConnection, create_session, reset_sessions
from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter() -> Character:
    return Character(
        id="thorin",
        name="Thorin",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=20,
        max_hp=20,
        ac=16,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
    )


def _goblin() -> Character:
    return Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=50,
        max_hp=50,
        ac=15,
        level=1,
        position=Position(x=2, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _attack() -> list[ParsedAction]:
    return [
        ParsedAction(
            actor="thorin",
            verb="attack",
            target="goblin_1",
            item_or_spell="longsword",
            raw_text="I attack",
        )
    ]


def _session(session_id: str, narrator: Any, rng: _FixedRandom) -> Session:
    thorin, goblin = _fighter(), _goblin()
    return create_session(
        session_id,
        GameState(
            encounter_id="event_loop_test",
            characters={thorin.id: thorin, goblin.id: goblin},
            turn_order=[thorin.id, goblin.id],
            current_turn=0,
            round=1,
        ),
        action_rng=rng,  # type: ignore[arg-type]
        graph=build_graph(
            rng=rng,  # type: ignore[arg-type]
            narrator_fn=narrator,
            scene_image_fn=_stub_scene_image,
        ),
    )


class _Fake:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


async def test_the_event_loop_keeps_running_while_a_turn_blocks_in_the_narrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack())

    def slow_narrator(_state: GraphState) -> dict[str, Any]:
        time.sleep(0.6)  # a real narrator call: synchronous, seconds long
        return {"narration": "[slow narration]"}

    session = _session("loop-responsive", slow_narrator, _FixedRandom([15, 4]))
    ws = _Fake()
    connection = SessionConnection(
        websocket=ws,  # type: ignore[arg-type]
        controlled_character_ids={"thorin"},
    )
    session.connections.append(connection)

    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        async with session.lock:
            await ws_session_module._handle_client_message(
                session,
                ws,  # type: ignore[arg-type]
                connection,
                {"type": "player_action", "text": "I attack"},
            )
    finally:
        beat.cancel()

    assert any(m["type"] == "narration" and m["text"] == "[slow narration]" for m in ws.sent)
    # The 0.6s blocking call never stalled the loop: no heartbeat gap anywhere
    # near its length (before the fix the largest gap was the whole call).
    assert gaps, "the heartbeat never ran"
    assert max(gaps) < 0.3


def test_a_parse_timeout_reports_an_error_and_keeps_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def flaky_parse(_state: GraphState) -> list[ParsedAction]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("ollama is slow")
        return _attack()

    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", flaky_parse)
    _session(
        "parse-timeout",
        lambda _state: {"narration": "[narration]"},
        _FixedRandom([15, 4]),
    )

    with TestClient(app).websocket_connect("/ws/session/parse-timeout") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # initial awaiting_input
        ws.send_json({"type": "player_action", "text": "I attack"})
        error = ws.receive_json()
        retry_prompt = ws.receive_json()
        # The same connection is still usable: a retry now goes through.
        ws.send_json({"type": "player_action", "text": "I attack"})
        narration = ws.receive_json()

    assert error["type"] == "error"
    assert "try again" in error["detail"]
    assert retry_prompt == {"type": "awaiting_input", "actor": "thorin"}
    assert narration["type"] == "narration"

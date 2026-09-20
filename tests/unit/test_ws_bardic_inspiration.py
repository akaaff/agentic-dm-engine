"""Issue #53 - Bardic Inspiration's recipient decides whether to spend
their banked die, after seeing the roll but before the outcome, via a real
two-message WS exchange (bardic_inspiration_offer from the server,
bardic_inspiration_response from the client). Monkeypatches
parse_intent_sequence the same way test_ws_multi_action.py does - the
model's own extraction quality is a live-verification concern
(tests/llm/), this file is about the pause-and-resume plumbing itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import Session, create_session, reset_sessions
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


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter_with_bardic_die() -> Character:
    # STR16 -> mod3, proficient longsword -> attack_bonus 5. Holds a banked
    # 1d6 (source="pip", though this test never resolves a real
    # bardic_inspiration action - the die is seeded directly).
    return Character(
        id="thorin",
        name="Thorin",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
        bardic_inspiration_die=6,
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
        # Generously above the real SRD 7 (matching this project's own
        # established fixture convention elsewhere) - the accept-the-offer
        # test's boosted hit deals 8 damage, which would otherwise kill a
        # real goblin outright and flip game_state.status to "victory,"
        # silently short-circuiting _send_awaiting_input and hanging any
        # test still waiting on one.
        hp=20,
        max_hp=20,
        ac=15,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _set_up_session(session_id: str, action_rng: _FixedRandom) -> Session:
    thorin = _fighter_with_bardic_die()
    goblin = _goblin()
    game_state = GameState(
        encounter_id="bardic_offer_test",
        characters={thorin.id: thorin, goblin.id: goblin},
        turn_order=[thorin.id, goblin.id],
        current_turn=0,
        round=1,
    )
    return create_session(
        session_id,
        game_state,
        action_rng=action_rng,  # type: ignore[arg-type]
        graph=build_graph(
            rng=action_rng,  # type: ignore[arg-type]
            narrator_fn=_stub_narrator,
            scene_image_fn=_stub_scene_image,
        ),
    )


def _attack_action() -> list[ParsedAction]:
    return [
        ParsedAction(
            actor="thorin",
            verb="attack",
            target="goblin_1",
            item_or_spell="longsword",
            raw_text="I attack goblin_1",
        )
    ]


def test_a_missing_attack_sends_an_offer_and_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack_action())
    # Natural 8 -> total 13 < AC 15 - a miss the die could still fix.
    session = _set_up_session("test-bardic-offer", _FixedRandom([8]))

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-bardic-offer") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # initial awaiting_input
        ws.send_json({"type": "player_action", "text": "I attack goblin_1"})
        offer = ws.receive_json()

    assert offer["type"] == "bardic_inspiration_offer"
    assert offer["holder"] == "thorin"
    assert offer["target"] == "goblin_1"
    assert offer["natural"] == 8
    assert offer["total_without_die"] == 13
    assert offer["defender_ac"] == 15
    assert offer["die_sides"] == 6
    # Paused - the turn hasn't moved, the die hasn't been touched, and no
    # awaiting_input/narration/state_update follows the offer.
    assert session.pending_bardic_choice is not None
    assert session.game_state.turn_order[session.game_state.current_turn] == "thorin"
    assert session.game_state.characters["thorin"].bardic_inspiration_die == 6


def test_accepting_the_offer_boosts_the_roll_and_advances_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack_action())
    # Natural 8 -> 13 (miss) offered; accepting rolls 1d6 -> 4 -> total 17,
    # a hit; damage die rolls 5 -> 5 + STR mod 3 = 8.
    session = _set_up_session("test-bardic-accept", _FixedRandom([8, 4, 5]))

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-bardic-accept") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "player_action", "text": "I attack goblin_1"})
        ws.receive_json()  # bardic_inspiration_offer

        ws.send_json({"type": "bardic_inspiration_response", "use": True})
        narration = ws.receive_json()
        state_update = ws.receive_json()
        ws.receive_json()  # awaiting_input (goblin's turn, no human controls it - no-op)

    assert narration["type"] == "narration"
    attack_events = [e for e in state_update["game_state"]["events"] if e["type"] == "attack_roll"]
    assert attack_events[-1]["payload"]["roll_total"] == 17
    assert attack_events[-1]["payload"]["hit"] is True
    assert attack_events[-1]["payload"]["bardic_inspiration_die_sides"] == 6
    damage_events = [e for e in state_update["game_state"]["events"] if e["type"] == "damage_dealt"]
    assert damage_events[-1]["payload"]["amount"] == 8
    assert state_update["game_state"]["characters"]["thorin"]["bardic_inspiration_die"] is None
    assert session.pending_bardic_choice is None
    # The attack ended the turn - it moved on to the goblin, not stuck.
    final_state = state_update["game_state"]
    assert final_state["turn_order"][final_state["current_turn"]] == "goblin_1"


def test_declining_the_offer_finalizes_the_miss_and_keeps_the_die(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack_action())
    session = _set_up_session("test-bardic-decline", _FixedRandom([8]))

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-bardic-decline") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "player_action", "text": "I attack goblin_1"})
        ws.receive_json()  # bardic_inspiration_offer

        ws.send_json({"type": "bardic_inspiration_response", "use": False})
        ws.receive_json()  # narration
        state_update = ws.receive_json()
        ws.receive_json()  # awaiting_input

    attack_events = [e for e in state_update["game_state"]["events"] if e["type"] == "attack_roll"]
    assert attack_events[-1]["payload"]["roll_total"] == 13
    assert attack_events[-1]["payload"]["hit"] is False
    assert "bardic_inspiration_die_sides" not in attack_events[-1]["payload"]
    assert state_update["game_state"]["characters"]["thorin"]["bardic_inspiration_die"] == 6
    assert session.pending_bardic_choice is None
    final_state = state_update["game_state"]
    assert final_state["turn_order"][final_state["current_turn"]] == "goblin_1"


def test_response_rejected_when_nothing_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack_action())
    _set_up_session("test-bardic-stale-response", _FixedRandom([8]))

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-bardic-stale-response") as ws:
        ws.receive_json()
        ws.receive_json()
        # No attack sent yet - nothing is pending.
        ws.send_json({"type": "bardic_inspiration_response", "use": True})
        error = ws.receive_json()

    assert error["type"] == "error"
    assert "no bardic inspiration" in error["detail"].lower()


class _FakeWebSocket:
    """Records every message sent to it - just enough of the real
    WebSocket's interface for _handle_client_message/_broadcast to work
    against, without a real ASGI connection. Two real concurrent
    connections would hit Starlette's own TestClient transport deadlock
    (see CLAUDE.md) - unnecessary here anyway, since this test is about
    _handle_client_message's own authorization check, not the WS transport
    itself."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


async def test_response_rejected_from_a_connection_that_doesnt_control_the_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issue #44's own discipline, applied here too: the decision belongs to
    # whoever controls the holder character, not just anyone connected.
    # Calls _handle_client_message directly (see _FakeWebSocket's own
    # docstring for why, not a real second WS connection).
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: _attack_action())
    session = _set_up_session("test-bardic-wrong-connection", _FixedRandom([8]))

    holder_ws = _FakeWebSocket()
    holder_connection = ws_session_module.SessionConnection(
        websocket=holder_ws,  # type: ignore[arg-type]
        controlled_character_ids={"thorin"},
    )
    session.connections.append(holder_connection)
    await ws_session_module._handle_client_message(
        session,
        holder_ws,  # type: ignore[arg-type]
        holder_connection,
        {"type": "player_action", "text": "I attack goblin_1"},
    )
    assert session.pending_bardic_choice is not None
    assert any(m["type"] == "bardic_inspiration_offer" for m in holder_ws.sent)

    other_ws = _FakeWebSocket()
    other_connection = ws_session_module.SessionConnection(
        websocket=other_ws,  # type: ignore[arg-type]
        controlled_character_ids={"goblin_1"},
    )
    await ws_session_module._handle_client_message(
        session,
        other_ws,  # type: ignore[arg-type]
        other_connection,
        {"type": "bardic_inspiration_response", "use": True},
    )

    assert other_ws.sent[-1]["type"] == "error"
    assert "not your bardic inspiration choice" in other_ws.sent[-1]["detail"].lower()
    # Still pending - the wrong connection's attempt didn't consume it.
    assert session.pending_bardic_choice is not None

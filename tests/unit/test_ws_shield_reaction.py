"""Issue #93 - the Shield reaction over the WebSocket. A monster's hit on a
human's wizard pauses the turn loop with a `shield_offer`; a `shield_response`
resumes it (cast: the hit becomes a miss at AC +5; decline: it lands). An AI
companion has no one to ask and casts automatically. The graph's narrator and
the standalone narrator the resume path calls are stubbed - the plumbing is
what's under test."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import Session, create_session, reset_sessions
from src.engine.position import Position
from src.engine.srd_loader import load_srd
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


@pytest.fixture(autouse=True)
def _stub_standalone_narrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ws_session_module, "narrator_node", lambda _state: {"narration": "[stub narration]"}
    )


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard(char_id: str = "elrond", position: Position | None = None) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        race="Elf",
        class_="Wizard",
        class_index="wizard",
        background="Acolyte",
        is_pc=True,
        hp=20,
        max_hp=20,
        ac=13,
        level=1,
        position=position or Position(x=0, y=0),
        stats={"STR": 8, "DEX": 16, "CON": 13, "INT": 15, "WIS": 12, "CHA": 10},
        speed=30,
        proficiency_bonus=2,
        prepared_spells=["shield"],
        spell_slots={1: 2},
    )


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
        position=Position(x=9, y=9),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
    )


def _goblin() -> Character:
    from src.engine.encounter import monster_to_character

    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=0, y=1))
    goblin.hp = goblin.max_hp = 50
    return goblin


def _session(
    session_id: str,
    human: Character,
    others: list[Character],
    rng: _FixedRandom,
    turn_order: list[str],
) -> Session:
    goblin = _goblin()
    characters = {c.id: c for c in [goblin, human, *others]}
    return create_session(
        session_id,
        GameState(
            encounter_id="shield_ws_test",
            characters=characters,
            turn_order=turn_order,
            current_turn=0,
            round=1,
        ),
        action_rng=rng,  # type: ignore[arg-type]
        graph=build_graph(
            rng=rng,  # type: ignore[arg-type]
            narrator_fn=_stub_narrator,
            scene_image_fn=_stub_scene_image,
        ),
        human_character_ids={"tok": human.id},
        srd=load_srd(),
    )


def _until(ws: Any, msg_type: str) -> dict[str, Any]:
    for _ in range(12):
        message: dict[str, Any] = ws.receive_json()
        if message["type"] == msg_type:
            return message
    raise AssertionError(f"never received {msg_type}")


def test_a_hit_on_the_humans_wizard_sends_an_offer_and_pauses() -> None:
    wizard = _wizard()
    # d20 12 + 4 = 16 vs AC 13: a hit that AC 18 would turn into a miss.
    session = _session("shield-offer", wizard, [], _FixedRandom([12, 4]), ["goblin_1", "elrond"])

    with TestClient(app).websocket_connect("/ws/session/shield-offer") as ws:
        offer = _until(ws, "shield_offer")

    assert offer["target"] == "elrond"
    assert offer["attacker"] == "goblin_1"
    assert offer["attack_total"] == 16
    assert offer["target_ac"] == 13
    assert offer["shield_ac"] == 18
    assert session.pending_shield_choice is not None
    assert wizard.hp == 20  # nothing applied while paused
    assert session.game_state.turn_order[session.game_state.current_turn] == "goblin_1"


def test_casting_shield_turns_the_hit_into_a_miss_and_play_resumes() -> None:
    wizard = _wizard()
    session = _session("shield-cast", wizard, [], _FixedRandom([12, 4]), ["goblin_1", "elrond"])

    with TestClient(app).websocket_connect("/ws/session/shield-cast") as ws:
        _until(ws, "shield_offer")
        ws.send_json({"type": "shield_response", "cast": True})
        _until(ws, "narration")
        state_update = _until(ws, "state_update")
        awaiting = _until(ws, "awaiting_input")

    events = state_update["game_state"]["events"]
    assert [e for e in events if e["type"] == "attack_roll"][-1]["payload"]["hit"] is False
    assert any(e["type"] == "spell_cast" and e["payload"]["spell"] == "Shield" for e in events)
    me = state_update["game_state"]["characters"]["elrond"]
    assert me["hp"] == 20
    assert me["ac"] == 18
    assert me["spell_slots"]["1"] == 1
    assert session.pending_shield_choice is None
    assert awaiting["actor"] == "elrond"  # the goblin's turn is over


def test_declining_lets_the_hit_land() -> None:
    wizard = _wizard()
    session = _session("shield-decline", wizard, [], _FixedRandom([12, 4]), ["goblin_1", "elrond"])

    with TestClient(app).websocket_connect("/ws/session/shield-decline") as ws:
        _until(ws, "shield_offer")
        ws.send_json({"type": "shield_response", "cast": False})
        _until(ws, "narration")
        state_update = _until(ws, "state_update")

    me = state_update["game_state"]["characters"]["elrond"]
    assert me["hp"] == 14  # 4 + 2 slashing
    assert me["spell_slots"]["1"] == 2
    assert session.pending_shield_choice is None


def test_response_rejected_when_nothing_is_pending() -> None:
    _session("shield-stale", _wizard(), [], _FixedRandom([2]), ["elrond", "goblin_1"])

    with TestClient(app).websocket_connect("/ws/session/shield-stale") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "shield_response", "cast": True})
        error = _until(ws, "error")

    assert "no shield choice" in error["detail"].lower()


def test_a_companion_wizard_casts_shield_without_asking() -> None:
    # The human fighter is the only seat; the goblin hits the (closer)
    # companion wizard, who casts automatically - no offer is ever sent.
    companion = _wizard("silvana")
    session = _session(
        "shield-companion",
        _fighter(),
        [companion],
        _FixedRandom([12, 4]),
        ["goblin_1", "thorin", "silvana"],
    )

    with TestClient(app).websocket_connect("/ws/session/shield-companion") as ws:
        seen: list[str] = []
        for _ in range(12):
            message = ws.receive_json()
            seen.append(message["type"])
            if message["type"] == "awaiting_input":
                break

    assert "shield_offer" not in seen
    assert session.pending_shield_choice is None
    assert companion.hp == 20
    assert companion.spell_slots[1] == 1
    assert companion.ac == 18


async def test_response_rejected_from_a_connection_that_doesnt_control_the_target() -> None:
    wizard = _wizard()
    session = _session("shield-wrong", wizard, [], _FixedRandom([12, 4]), ["goblin_1", "elrond"])

    class _Fake:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, data: dict[str, Any]) -> None:
            self.sent.append(data)

    owner = _Fake()
    owner_connection = ws_session_module.SessionConnection(
        websocket=owner,  # type: ignore[arg-type]
        controlled_character_ids={"elrond"},
    )
    session.connections.append(owner_connection)
    await ws_session_module._autoplay_non_human_turns(session)
    assert session.pending_shield_choice is not None

    other = _Fake()
    other_connection = ws_session_module.SessionConnection(
        websocket=other,  # type: ignore[arg-type]
        controlled_character_ids={"goblin_1"},
    )
    await ws_session_module._handle_client_message(
        session,
        other,  # type: ignore[arg-type]
        other_connection,
        {"type": "shield_response", "cast": True},
    )

    assert other.sent[-1]["type"] == "error"
    assert "not your shield choice" in other.sent[-1]["detail"].lower()
    assert session.pending_shield_choice is not None

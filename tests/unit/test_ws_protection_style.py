"""Issue #92 - the Protection fighting style over the WebSocket. A monster's
attack on a character a human's Protection fighter stands beside pauses the turn
loop with a `protection_offer` (before the attack is rolled); a
`protection_response` resumes it - using it re-rolls the attack with
disadvantage. An AI companion fighter reacts automatically."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import Session, create_session, reset_sessions
from src.engine.encounter import monster_to_character
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


def _pc(char_id: str, class_index: str, position: Position, **extra: Any) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        race="Human",
        class_=class_index.title(),
        class_index=class_index,
        background="Acolyte",
        is_pc=True,
        hp=20,
        max_hp=20,
        ac=13,
        level=1,
        position=position,
        stats={"STR": 14, "DEX": 14, "CON": 14, "INT": 12, "WIS": 12, "CHA": 10},
        speed=30,
        proficiency_bonus=2,
        **extra,
    )


def _fighter() -> Character:
    return _pc(
        "grom", "fighter", Position(x=1, y=0), fighting_style="protection", equipped_shield="shield"
    )


def _session(session_id: str, human: Character, other: Character, rng: _FixedRandom) -> Session:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=0, y=1))
    goblin.hp = goblin.max_hp = 50
    return create_session(
        session_id,
        GameState(
            encounter_id="protection_ws_test",
            characters={c.id: c for c in [goblin, human, other]},
            turn_order=["goblin_1", human.id, other.id],
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
    for _ in range(14):
        message: dict[str, Any] = ws.receive_json()
        if message["type"] == msg_type:
            return message
    raise AssertionError(f"never received {msg_type}")


def test_a_human_protector_is_offered_the_reaction_before_the_roll() -> None:
    fighter = _fighter()
    wizard = _pc("elrond", "wizard", Position(x=0, y=0))
    session = _session("prot-offer", fighter, wizard, _FixedRandom([]))

    with TestClient(app).websocket_connect("/ws/session/prot-offer") as ws:
        offer = _until(ws, "protection_offer")

    assert offer["protector"] == "grom"
    assert offer["target"] == "elrond"
    assert offer["attacker"] == "goblin_1"
    assert session.pending_protection_choice is not None
    assert session.game_state.events == []  # nothing rolled yet


def test_using_protection_rolls_the_attack_with_disadvantage() -> None:
    fighter = _fighter()
    wizard = _pc("elrond", "wizard", Position(x=0, y=0))
    # Disadvantage: [18, 3] keeps the 3 -> 7 vs AC 13.
    session = _session("prot-use", fighter, wizard, _FixedRandom([18, 3]))

    with TestClient(app).websocket_connect("/ws/session/prot-use") as ws:
        _until(ws, "protection_offer")
        ws.send_json({"type": "protection_response", "use": True})
        _until(ws, "narration")
        state_update = _until(ws, "state_update")
        awaiting = _until(ws, "awaiting_input")

    events = state_update["game_state"]["events"]
    assert any(e["type"] == "protection" for e in events)
    attack = [e for e in events if e["type"] == "attack_roll"][-1]
    assert attack["payload"]["natural"] == 3
    assert attack["payload"]["hit"] is False
    assert session.pending_protection_choice is None
    assert state_update["game_state"]["characters"]["grom"]["reaction_used_this_round"] is True
    assert awaiting["actor"] == "grom"


def test_declining_rolls_normally() -> None:
    fighter = _fighter()
    wizard = _pc("elrond", "wizard", Position(x=0, y=0))
    session = _session("prot-decline", fighter, wizard, _FixedRandom([15, 4]))

    with TestClient(app).websocket_connect("/ws/session/prot-decline") as ws:
        _until(ws, "protection_offer")
        ws.send_json({"type": "protection_response", "use": False})
        _until(ws, "narration")
        state_update = _until(ws, "state_update")

    attack = [e for e in state_update["game_state"]["events"] if e["type"] == "attack_roll"][-1]
    assert attack["payload"]["hit"] is True
    assert session.pending_protection_choice is None


def test_a_companion_protector_reacts_without_asking() -> None:
    # The human is the wizard being attacked; the fighter is an AI companion.
    wizard = _pc("elrond", "wizard", Position(x=0, y=0))
    fighter = _fighter()
    session = _session("prot-companion", wizard, fighter, _FixedRandom([18, 3]))

    with TestClient(app).websocket_connect("/ws/session/prot-companion") as ws:
        seen: list[str] = []
        for _ in range(14):
            message = ws.receive_json()
            seen.append(message["type"])
            if message["type"] == "awaiting_input":
                break

    assert "protection_offer" not in seen
    assert session.pending_protection_choice is None
    assert fighter.reaction_used_this_round is True
    assert wizard.hp == 20  # the disadvantaged attack missed


def test_response_rejected_when_nothing_is_pending() -> None:
    fighter = _fighter()
    wizard = _pc("elrond", "wizard", Position(x=0, y=0))
    # Fighter's own turn first, so the goblin hasn't attacked yet.
    session = _session("prot-stale", fighter, wizard, _FixedRandom([]))
    session.game_state.turn_order = ["grom", "goblin_1", "elrond"]

    with TestClient(app).websocket_connect("/ws/session/prot-stale") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "protection_response", "use": True})
        error = _until(ws, "error")

    assert "no protection choice" in error["detail"].lower()

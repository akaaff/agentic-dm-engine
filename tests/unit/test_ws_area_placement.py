"""Click-to-place over the real WebSocket: a human's area-spell cast stops to ask where it should
land (area_request, with the engine's own suggestion), the grid asks what an origin would cover
(area_preview), and the answer (area_response) places it, lets the game place it, or cancels.
Nothing about the cast is applied - no slot spent, no event - until it is placed.

parse_intent_sequence is stubbed to a canned cast, so this exercises the server's pause/resume, not
the model's parse."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import create_session, reset_sessions
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.position import BattleMap, Position
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


def _druid() -> Character:
    druid = create_character(
        character_id="sylva",
        name="Sylva",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 13, "CON": 14, "INT": 10, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-perception"],
        chosen_prepared_spells=["entangle", "goodberry", "longstrider", "faerie-fire"],
        chosen_equipment=["quarterstaff"],
    )
    druid.position = Position(x=0, y=0)
    return druid


def _goblin(goblin_id: str, x: int, y: int) -> Character:
    return Character(
        id=goblin_id,
        name=goblin_id.replace("_", " ").title(),
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=10,
        level=1,
        position=Position(x=x, y=y),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _set_up(session_id: str) -> None:
    druid, near, far = _druid(), _goblin("goblin_1", 6, 6), _goblin("goblin_2", 12, 12)
    state = GameState(
        encounter_id="area_placement_test",
        characters={druid.id: druid, near.id: near, far.id: far},
        turn_order=["sylva", "goblin_1", "goblin_2"],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=14, height=14, terrain=[["floor"] * 14 for _ in range(14)], spawn_points={}
        ),
    )
    create_session(
        session_id,
        state,
        graph=build_graph(narrator_fn=_stub_narrator, scene_image_fn=_stub_scene_image),
    )


def _cast(spell: str = "Entangle", target: str = "goblin_1") -> ParsedAction:
    return ParsedAction(
        actor="sylva",
        verb="cast_spell",
        item_or_spell=spell,
        target=target,
        targets=[target],
        raw_text="x",
    )


def _stub_parse(monkeypatch: pytest.MonkeyPatch, actions: list[ParsedAction]) -> None:
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: actions)


def _read_until(ws: Any, *types: str, limit: int = 12) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if message["type"] in types:
            return seen
    raise AssertionError(f"never saw {types}; got {[m['type'] for m in seen]}")


def _open(client: TestClient, session_id: str) -> Any:
    ws = client.websocket_connect(f"/ws/session/{session_id}")
    return ws


def _slots(session_id: str) -> int:
    return ws_session_module._sessions[session_id].game_state.characters["sylva"].spell_slots[1]


def test_an_area_cast_asks_where_to_put_it_before_anything_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-ask")
    slots = _slots("area-ask")
    with _open(TestClient(app), "area-ask") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "I cast entangle on the goblin"})
        request = _read_until(ws, "area_request")[-1]
        assert request["spell"] == "Entangle" and request["actor"] == "sylva"
        assert request["shape"] == "cube" and request["size_feet"] == 20
        assert request["range_feet"] == 90 and request["from_caster"] is False
        assert request["suggested"]["caught"] == ["goblin_1"]
        assert len(request["suggested"]["cells"]) == 16
    session = ws_session_module._sessions["area-ask"]
    assert _slots("area-ask") == slots  # nothing spent yet
    assert session.game_state.effect_areas == []
    assert session.pending_area_choice is not None
    assert not [e for e in session.game_state.events if e.type == "spell_area"]


def test_a_preview_says_what_an_origin_would_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-preview")
    with _open(TestClient(app), "area-preview") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "area_preview", "origin": {"x": 12, "y": 12}})
        result = _read_until(ws, "area_preview_result")[-1]
        assert result["origin"] == {"x": 12, "y": 12}
        assert result["caught"] == ["goblin_2"] and result["allies_caught"] == []
        assert len(result["cells"]) == 9  # a 4x4 cube clipped by the map's corner
        ws.send_json({"type": "area_preview", "origin": {"x": 0, "y": 0}})
        near_caster = _read_until(ws, "area_preview_result")[-1]
        assert near_caster["caught"] == []  # nobody there: still a valid square to place


def test_a_preview_of_a_square_out_of_range_reports_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_parse(monkeypatch, [_cast("Faerie Fire")])
    _set_up("area-preview-range")
    with _open(TestClient(app), "area-preview-range") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "area_preview", "origin": {"x": 13, "y": 13}})  # 65 ft; reach 60
        result = _read_until(ws, "area_preview_result")[-1]
        assert "out of range" in result["error"]


def test_choosing_a_square_places_the_cast_there_and_the_turn_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-place")
    slots = _slots("area-place")
    with _open(TestClient(app), "area-place") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "area_response", "origin": {"x": 12, "y": 12}})
        seen = _read_until(ws, "awaiting_input", limit=30)
        assert any(m["type"] == "area_resolved" for m in seen)
        state = [m for m in seen if m["type"] == "state_update"][-1]["game_state"]
        assert [a["spell"] for a in state["effect_areas"]] == ["Entangle"]
    session = ws_session_module._sessions["area-place"]
    area = next(e for e in session.game_state.events if e.type == "spell_area")
    assert area.payload["origin"] == {
        "x": 12,
        "y": 12,
    }  # where the player clicked, not the suggestion
    assert area.payload["caught"] == ["goblin_2"]
    assert _slots("area-place") == slots - 1
    assert session.pending_area_choice is None


def test_letting_the_game_place_it_uses_the_suggestion(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-auto")
    with _open(TestClient(app), "area-auto") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        request = _read_until(ws, "area_request")[-1]
        ws.send_json({"type": "area_response", "auto": True})
        _read_until(ws, "awaiting_input", limit=30)
    session = ws_session_module._sessions["area-auto"]
    area = next(e for e in session.game_state.events if e.type == "spell_area")
    assert area.payload["origin"] == request["suggested"]["origin"]
    assert area.payload["caught"] == ["goblin_1"]


def test_cancelling_costs_nothing_and_gives_the_turn_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-cancel")
    slots = _slots("area-cancel")
    with _open(TestClient(app), "area-cancel") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "area_response", "cancel": True})
        seen = _read_until(ws, "awaiting_input")
        assert any(m["type"] == "area_resolved" for m in seen)
    session = ws_session_module._sessions["area-cancel"]
    assert session.pending_area_choice is None and session.game_state.effect_areas == []
    assert _slots("area-cancel") == slots
    assert session.game_state.turn_order[session.game_state.current_turn] == "sylva"


def test_other_actions_wait_while_a_spell_is_being_placed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-guard")
    with _open(TestClient(app), "area-guard") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "end_turn"})
        error = _read_until(ws, "error")[-1]
        assert "placing" in error["detail"]
    assert ws_session_module._sessions["area-guard"].pending_area_choice is not None


def test_a_square_the_cast_cannot_use_is_refused_and_the_placement_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_parse(monkeypatch, [_cast("Faerie Fire")])
    _set_up("area-bad-square")
    with _open(TestClient(app), "area-bad-square") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
        ws.send_json({"type": "area_response", "origin": {"x": 13, "y": 13}})
        seen = _read_until(ws, "area_request")  # the error, then the request again
        assert any(m["type"] == "error" and "out of range" in m["detail"] for m in seen)
    assert ws_session_module._sessions["area-bad-square"].pending_area_choice is not None


def test_a_spell_without_an_area_is_not_held_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_parse(monkeypatch, [_cast("Longstrider", target="sylva")])
    _set_up("area-none")
    with _open(TestClient(app), "area-none") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        seen = _read_until(ws, "awaiting_input", limit=30)
        assert not any(m["type"] == "area_request" for m in seen)
        assert any(m["type"] == "state_update" for m in seen)


def test_an_area_cast_that_would_be_rejected_anyway_is_not_held_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No spell slots left: the player is told so straight away, not asked where to put a spell
    # that cannot be cast.
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-rejected")
    ws_session_module._sessions["area-rejected"].game_state.characters["sylva"].spell_slots[1] = 0
    with _open(TestClient(app), "area-rejected") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        seen = _read_until(ws, "error")
        assert "no level-1 spell slots" in seen[-1]["detail"]
    assert ws_session_module._sessions["area-rejected"].pending_area_choice is None


def test_a_reconnecting_client_is_shown_the_pending_placement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_parse(monkeypatch, [_cast()])
    _set_up("area-reconnect")
    client = TestClient(app)
    with _open(client, "area-reconnect") as ws:
        _read_until(ws, "awaiting_input")
        ws.send_json({"type": "player_action", "text": "x"})
        _read_until(ws, "area_request")
    with _open(client, "area-reconnect") as ws2:
        seen = _read_until(ws2, "area_request")
        assert seen[-1]["spell"] == "Entangle"
        assert not any(m["type"] == "awaiting_input" for m in seen)  # nothing to type: placing

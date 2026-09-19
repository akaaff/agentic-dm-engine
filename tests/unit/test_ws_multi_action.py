"""Issue #47 - a single player_action utterance can now resolve more than
one ParsedAction in sequence (e.g. "I rage, move to the wolf, and attack
it"). Monkeypatches parse_intent_sequence directly to supply a canned,
hand-picked list deterministically - the model's own multi-action
extraction quality is a live-verification concern (tests/llm/), this file
is about the *sequencing loop* itself: does it actually resolve everything
it's given, stop the moment the actor's turn genuinely ends, and stop
cleanly on a failing sub-action without losing whatever already resolved.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import create_session, reset_sessions
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


def _fighter(hp: int = 5) -> Character:
    return Character(
        id="thorin",
        name="Thorin",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=hp,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        class_resources={"second_wind": 1},
        equipped_weapons=["longsword"],
    )


def _monster() -> Character:
    # is_pc=False, not another PC - _check_victory_defeat treats zero
    # non-PCs as an immediate (vacuous) victory, which would flip
    # game_state.status away from "in_progress" the moment the first action
    # resolves, silently stopping _send_awaiting_input from ever firing
    # again. A live enemy keeps the encounter genuinely "in_progress" so
    # end_turn actually gets to advance the turn.
    return Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=7,
        max_hp=7,
        ac=15,
        level=1,
        position=Position(x=5, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _set_up_session(session_id: str, thorin: Character) -> None:
    goblin = _monster()
    game_state = GameState(
        encounter_id="multi_action_test",
        characters={thorin.id: thorin, goblin.id: goblin},
        turn_order=[thorin.id, goblin.id],
        current_turn=0,
        round=1,
    )
    create_session(
        session_id,
        game_state,
        graph=build_graph(narrator_fn=_stub_narrator, scene_image_fn=_stub_scene_image),
    )


def _send_player_action_and_collect(ws: Any, expected_state_updates: int) -> list[dict[str, Any]]:
    ws.send_json({"type": "player_action", "text": "irrelevant - parse_intent_sequence is stubbed"})
    messages = []
    seen_state_updates = 0
    while seen_state_updates < expected_state_updates:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == "state_update":
            seen_state_updates += 1
    # Drain the final awaiting_input - harmless no-op from
    # _autoplay_non_human_turns here, since session.human_character_ids is
    # empty (demo-fallback create_session, see module docstring), so it
    # never tries to auto-play the goblin's own turn either.
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == "awaiting_input":
            break
    return messages


def test_all_actions_in_a_sequence_resolve_when_none_end_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # second_wind (bonus action, heals, doesn't end the turn) then end_turn
    # (explicitly ends it) - two real actions from one submission.
    actions = [
        ParsedAction(actor="thorin", verb="second_wind", raw_text="I catch my breath"),
        ParsedAction(actor="thorin", verb="end_turn", raw_text="that's all"),
    ]
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: actions)

    _set_up_session("test-multi-all-resolve", _fighter(hp=5))
    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-multi-all-resolve") as ws:
        ws.receive_json()  # initial state_update
        ws.receive_json()  # initial awaiting_input
        messages = _send_player_action_and_collect(ws, expected_state_updates=2)

    state_updates = [m for m in messages if m["type"] == "state_update"]
    assert len(state_updates) == 2
    # second_wind healed thorin above the starting 5 hp.
    assert state_updates[0]["game_state"]["characters"]["thorin"]["hp"] > 5
    # end_turn actually advanced the turn to the goblin.
    final_state = state_updates[-1]["game_state"]
    assert final_state["turn_order"][final_state["current_turn"]] == "goblin_1"


def test_sequence_stops_once_the_turn_actually_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    # end_turn ends the turn immediately - a second action after it must
    # never be attempted (thorin's hp must stay untouched by the second_wind
    # that would have followed if the loop kept going).
    actions = [
        ParsedAction(actor="thorin", verb="end_turn", raw_text="I'm done"),
        ParsedAction(actor="thorin", verb="second_wind", raw_text="never reached"),
    ]
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: actions)

    _set_up_session("test-multi-stops-at-turn-end", _fighter(hp=5))
    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-multi-stops-at-turn-end") as ws:
        ws.receive_json()
        ws.receive_json()
        messages = _send_player_action_and_collect(ws, expected_state_updates=1)

    state_updates = [m for m in messages if m["type"] == "state_update"]
    assert len(state_updates) == 1
    # Only end_turn ran - hp is untouched, second_wind's own resource is
    # still there (never consumed).
    assert state_updates[0]["game_state"]["characters"]["thorin"]["hp"] == 5
    assert (
        state_updates[0]["game_state"]["characters"]["thorin"]["class_resources"]["second_wind"]
        == 1
    )


def test_sequence_stops_on_a_failing_sub_action_but_keeps_earlier_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # second_wind succeeds and heals; a second second_wind (no uses left)
    # fails - the failure must not undo the first, and nothing after it
    # (a third action) should run either.
    actions = [
        ParsedAction(actor="thorin", verb="second_wind", raw_text="I catch my breath"),
        ParsedAction(actor="thorin", verb="second_wind", raw_text="I try again"),
        ParsedAction(actor="thorin", verb="end_turn", raw_text="never reached"),
    ]
    monkeypatch.setattr(ws_session_module, "parse_intent_sequence", lambda _state: actions)

    _set_up_session("test-multi-stops-on-error", _fighter(hp=5))
    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-multi-stops-on-error") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "player_action", "text": "irrelevant"})

        first_narration = ws.receive_json()
        assert first_narration["type"] == "narration"
        first_state = ws.receive_json()
        assert first_state["type"] == "state_update"
        healed_hp = first_state["game_state"]["characters"]["thorin"]["hp"]
        assert healed_hp > 5

        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert "second wind" in error_msg["detail"].lower()

        # Still thorin's turn - the third action (end_turn) never ran.
        retry_prompt = ws.receive_json()
        assert retry_prompt["type"] == "awaiting_input"
        assert retry_prompt["actor"] == "thorin"

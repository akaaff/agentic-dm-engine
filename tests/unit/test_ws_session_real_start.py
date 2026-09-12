"""Day 19: a session started via POST /sessions (Day 18) should build the
real campaign encounter with the real character/party, not the Day-7 demo
skirmish - and every non-human turn (companions, monsters) should
auto-resolve before the human is ever asked for input. Stays offline by
monkeypatching build_graph so the real narrator/player_agent/intent_parser
LLM calls are never reached - see test_ws_session.py's own docstring for why
this project stubs at that boundary rather than mocking individual calls.
"""

from __future__ import annotations

import random
from collections.abc import Generator
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
from src.engine.actions import ParsedAction
from src.engine.campaign import load_campaign
from src.engine.companions import build_companion, load_companion_spec
from src.engine.encounter import build_encounter_state, load_encounter
from src.engine.srd_loader import load_srd
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

_VALID_FIGHTER_BODY = {
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
    # Whoever's turn it is, just end it - these tests only care about
    # *whose* turn gets auto-played and in what order, not what a companion
    # actually does.
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {"parsed_action": ParsedAction(actor=actor_id, verb="end_turn", raw_text="stub")}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    # ws/session.py calls SessionLocal() directly (it's not a route, so no
    # FastAPI Depends() to override) - point it at the same in-memory engine
    # the TestClient's own requests use, or POST /sessions and the WS
    # handler would see two different, disconnected databases.
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
    yield TestClient(app)
    app.dependency_overrides.clear()
    ws_session_module.reset_sessions()


def _start_real_session(client: TestClient) -> str:
    create_response = client.post("/characters", json=_VALID_FIGHTER_BODY)
    assert create_response.status_code == 201

    session_response = client.post(
        "/sessions",
        json={
            "campaign_id": "goblin_ambush_oneshot",
            "character_id": "thorin",
            "companion_ids": ["companion_grom"],
        },
    )
    assert session_response.status_code == 201
    session_id: str = session_response.json()["session_id"]
    return session_id


def test_real_session_uses_the_campaign_encounter_not_the_demo(client: TestClient) -> None:
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        state_msg = None
        while state_msg is None or state_msg["type"] != "state_update":
            state_msg = ws.receive_json()

    character_ids = set(state_msg["game_state"]["characters"].keys())
    # goblin_ambush's real encounter (3 goblins) + thorin + companion_grom -
    # not the Day-7 demo's thorin/elrond/goblin_1/goblin_2.
    assert character_ids == {"thorin", "companion_grom", "goblin_1", "goblin_2", "goblin_3"}


def test_human_only_controls_their_own_character(client: TestClient) -> None:
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

    assert msg["actor"] == "thorin"


def test_real_session_narrates_scenes_before_first_combat(client: TestClient) -> None:
    # Regression guard: a real session used to jump straight into the first
    # combat encounter, silently skipping any narrative_beat/skill_challenge
    # scenes before it (caught live playing kobold_warren_full - see
    # CLAUDE.md). "hook" (a narrative_beat) and "read_the_signs" (a
    # skill_challenge, whose outcome text also counts) should arrive as
    # scene_narration messages before the warren_combat state_update.
    create_response = client.post("/characters", json=_VALID_FIGHTER_BODY)
    assert create_response.status_code == 201
    session_response = client.post(
        "/sessions",
        json={
            "campaign_id": "kobold_warren_full",
            "character_id": "thorin",
            "companion_ids": ["companion_grom"],
        },
    )
    assert session_response.status_code == 201
    session_id: str = session_response.json()["session_id"]

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        messages = []
        msg = ws.receive_json()
        messages.append(msg)
        while msg["type"] != "state_update":
            msg = ws.receive_json()
            messages.append(msg)

    scene_narrations = [m["text"] for m in messages if m["type"] == "scene_narration"]
    # advance_to_next_encounter appends every scene's own narrative_intro
    # (not just narrative_beat's) plus a skill_challenge's outcome text:
    # hook's intro, read_the_signs' intro, its success/failure outcome, and
    # warren_combat's own intro (included so the caller doesn't have to
    # narrate a combat scene's start separately).
    assert len(scene_narrations) == 4
    assert "Hollowford" in scene_narrations[0]

    state_update = next(m for m in messages if m["type"] == "state_update")
    assert state_update["game_state"]["encounter_id"] == "kobold_ambush"


def test_non_human_turns_auto_resolve_before_awaiting_input(client: TestClient) -> None:
    # Deterministic regardless of the session's real (unseeded) initiative
    # roll: exactly as many non-human turns as precede thorin in turn_order
    # should have auto-resolved (one narration each) before thorin is ever
    # asked for input - proves _autoplay_non_human_turns actually ran the
    # right number of times, not just that thorin eventually got prompted.
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        messages = []
        msg = ws.receive_json()
        messages.append(msg)
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()
            messages.append(msg)

    state_updates = [m for m in messages if m["type"] == "state_update"]
    turn_order = state_updates[0]["game_state"]["turn_order"]
    thorin_index = turn_order.index("thorin")

    narrations = [m for m in messages if m["type"] == "narration"]
    assert len(narrations) == thorin_index


async def test_advance_campaign_after_victory_continues_to_the_next_encounter() -> None:
    # Regression guard for the other half of the same gap as the
    # scene_narration test above: a live session never advanced past one
    # encounter's victory into the rest of the scene chain at all (see
    # CLAUDE.md) - it would just sit on a finished fight forever. Sets
    # "warren_combat just ended in victory" directly (same "set the state
    # you need rather than scripting combat to reach it" pattern
    # test_turn_engine_new_verbs.py already uses) and confirms the session
    # picks up rising_action + scout_the_hideout's narration and lands in
    # hideout_combat's own encounter - real content, not a synthetic
    # fixture, since this is the exact campaign caught live.
    srd = load_srd()
    campaign = load_campaign("kobold_warren_full")
    party = [
        build_companion(load_companion_spec("grom_ironfist"), srd=srd),
        build_companion(load_companion_spec("silvana_wren"), srd=srd),
    ]
    warren_scene = campaign.scene_by_id("warren_combat")
    assert warren_scene.encounter_ref is not None
    game_state = build_encounter_state(
        load_encounter(warren_scene.encounter_ref), party, random.Random(), srd=srd
    )
    game_state.status = "victory"

    session = ws_session_module.Session(
        game_state=game_state,
        action_rng=random.Random(),
        graph=build_graph(
            rng=random.Random(),
            narrator_fn=_stub_narrator,
            player_agent_fn=_stub_player_agent,
            scene_image_fn=_stub_scene_image,
        ),
        campaign=campaign,
        party=party,
        srd=srd,
        current_scene_id="warren_combat",
    )

    await ws_session_module._advance_campaign_after_victory(session)

    assert session.current_scene_id == "hideout_combat"
    assert session.game_state.encounter_id == "bandit_hideout"
    assert session.game_state.status == "in_progress"

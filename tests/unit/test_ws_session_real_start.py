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
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app
from src.api.ws import session as ws_session_module
from src.engine.actions import ParsedAction
from src.engine.campaign import Campaign, Scene, load_campaign
from src.engine.companions import build_companion, load_companion_spec
from src.engine.encounter import GameStateBuildError, build_encounter_state, load_encounter
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
    # roll: every non-human actor before thorin in turn_order contributes at
    # least one narration before thorin is ever asked for input - proves
    # _autoplay_non_human_turns actually ran for each of them, not just that
    # thorin eventually got prompted. Not exactly one each any more (move no
    # longer ends the turn - a goblin that starts out of range now takes two
    # resolve_action calls, move then attack, to finish one real turn).
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
    assert len(narrations) >= thorin_index


def test_session_setup_failure_reports_error_instead_of_crashing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #29 regression: a content bug in build_encounter_state (in
    # practice, an encounter's party_spawn_points shorter than the actual
    # party size - see wolf_den's own live-found fix) used to propagate
    # straight out of session_websocket and crash the whole ASGI connection
    # before create_session ever ran. The client got no message at all and
    # sat on "Connecting..." forever, with no way to tell a content bug
    # apart from a slow or dead server.
    session_id = _start_real_session(client)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise GameStateBuildError("not enough party spawn points")

    monkeypatch.setattr(ws_session_module, "build_encounter_state", _boom)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "detail": "not enough party spawn points"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    # A failed setup must not leave a stale/broken entry a later connection
    # to the same session_id would silently reuse.
    assert session_id not in ws_session_module._sessions


def test_rest_recovers_party_resources_between_encounters(client: TestClient) -> None:
    # Issue #28: there was previously no way to rest mid-campaign outside a
    # scripted rest scene. Fast-forwards straight to "victory" by mutating
    # the live session's state directly (real combat is covered elsewhere -
    # this is about the rest message itself, same "set the state you need"
    # pattern test_advance_campaign_after_victory_continues_to_the_next_
    # encounter already uses) and confirms a "rest" message actually
    # recovers the party's resources through a real WS round trip.
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        thorin = session.game_state.characters["thorin"]
        thorin.hp = 1
        session.game_state.status = "victory"

        ws.send_json({"type": "rest", "rest_type": "long"})
        narration_msg = ws.receive_json()
        assert narration_msg["type"] == "narration"
        assert "rest" in narration_msg["text"].lower()
        state_msg = ws.receive_json()
        assert state_msg["type"] == "state_update"
        assert state_msg["game_state"]["characters"]["thorin"]["hp"] == thorin.max_hp
        # Status itself is untouched by resting - still a "victory" stop
        # the player leaves via continue_campaign, not silently advanced.
        assert state_msg["game_state"]["status"] == "victory"


def test_rest_rejected_while_an_encounter_is_still_in_progress(client: TestClient) -> None:
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        thorin = session.game_state.characters["thorin"]
        thorin.hp = 1

        ws.send_json({"type": "rest", "rest_type": "long"})
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"

    # Nothing recovered - the rejected request never touched the party.
    assert thorin.hp == 1


def test_continue_campaign_chains_to_the_next_encounter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The explicit counterpart to _advance_campaign_after_victory's own
    # direct-call test below, driven through a real WS round trip instead -
    # confirms the message wiring itself, not just the underlying function.
    # kobold_warren_full (not goblin_ambush_oneshot - see _start_real_session)
    # has real scenes after its first combat, same campaign the direct-call
    # test below uses, so "continuing" past a forced victory has somewhere
    # real to chain to instead of hanging with nothing left to broadcast.
    # Story-adaptive-encounters Phase 2: this chain now passes through
    # rising_action, a party_choice scene (see CLAUDE.md) - stubbed and
    # answered the same way test_party_choice_pauses_for_input_then_resumes_
    # the_chain does, or this would deadlock waiting on a response nobody
    # sends (caught live running the full file: exactly that hang).
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )
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
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        assert session.game_state.encounter_id == "kobold_ambush"
        session.game_state.status = "victory"

        ws.send_json({"type": "continue_campaign"})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "party_choice_offer":
            messages.append(ws.receive_json())

        ws.send_json({"type": "party_choice_response", "text": "Let's press on."})
        while messages[-1]["type"] != "awaiting_input":
            messages.append(ws.receive_json())

    state_updates = [m for m in messages if m["type"] == "state_update"]
    assert state_updates[-1]["game_state"]["encounter_id"] == "bandit_hideout"
    scene_narrations = [m["text"] for m in messages if m["type"] == "scene_narration"]
    assert scene_narrations  # rising_action + the synthesis + scout_the_hideout's own intro/outcome


def test_party_choice_pauses_for_input_then_resumes_the_chain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Story-adaptive-encounters Phase 2. kobold_warren_full's own
    # "rising_action" scene is now type=party_choice (see CLAUDE.md), sitting
    # between warren_rest and scout_the_hideout - reaching it via a forced
    # victory + continue_campaign (same pattern as
    # test_continue_campaign_chains_to_the_next_encounter) should pause
    # instead of walking straight through to hideout_combat.
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )

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
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        assert session.game_state.encounter_id == "kobold_ambush"
        session.game_state.status = "victory"

        ws.send_json({"type": "continue_campaign"})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "party_choice_offer":
            messages.append(ws.receive_json())

        offer = messages[-1]
        assert "companion_grom" in offer["companion_responses"]
        assert offer["companion_responses"]["companion_grom"] == "[Grom Ironfist stub reaction]"
        assert offer["awaiting"] == ["thorin"]
        # Nothing built a new combat encounter yet - still the finished fight.
        assert session.pending_party_choice is not None
        assert session.game_state.encounter_id == "kobold_ambush"

        ws.send_json({"type": "party_choice_response", "text": "Let's press on to the hideout."})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "awaiting_input":
            messages.append(ws.receive_json())

    responded = next(m for m in messages if m["type"] == "party_choice_responded")
    assert responded == {
        "type": "party_choice_responded",
        "actor": "thorin",
        "text": "Let's press on to the hideout.",
    }
    scene_narrations = [m["text"] for m in messages if m["type"] == "scene_narration"]
    assert "[stub synthesis narration]" in scene_narrations
    state_updates = [m for m in messages if m["type"] == "state_update"]
    assert state_updates[-1]["game_state"]["encounter_id"] == "bandit_hideout"
    assert session.pending_party_choice is None


def test_continue_campaign_and_rest_no_op_while_a_party_choice_is_pending(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live-found while verifying the pause itself: game_state.status stays
    # "victory" for a party_choice's whole duration (it never touches
    # game_state), so nothing had stopped a second continue_campaign (a
    # stray click, or a desynced client re-sending) from re-running the
    # whole chain walk from session.current_scene_id - silently overwriting
    # session.pending_party_choice's already-collected responses with a
    # fresh offer - or a rest request from going through narratively
    # mid-conversation. Confirmed live in a real browser before this guard
    # existed: the rest-controls buttons stayed visible and clickable
    # alongside the new party-choice panel.
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )

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
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        session.game_state.status = "victory"

        ws.send_json({"type": "continue_campaign"})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "party_choice_offer":
            messages.append(ws.receive_json())
        pending_before = session.pending_party_choice
        assert pending_before is not None

        # A rest request while the choice is still pending is rejected,
        # same as one mid-combat already is.
        ws.send_json({"type": "rest", "rest_type": "long"})
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert "between encounters" in error_msg["detail"]

        # A second continue_campaign is a no-op, not a re-walk - the exact
        # same pending choice object survives untouched.
        ws.send_json({"type": "continue_campaign"})
        # No message is expected from this - confirmed below by checking
        # the pending choice object identity/content is still exactly what
        # it was, not a freshly-built one with a new (stub) offer.
        assert session.pending_party_choice is pending_before
        assert session.pending_party_choice.responses == pending_before.responses


def test_party_choice_response_rejects_an_already_answered_or_unknown_sender(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: "[stub]",
    )
    session_id = _start_real_session(client)

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        # Nothing pending yet - a stray response is a clear error, not a hang.
        ws.send_json({"type": "party_choice_response", "text": "..."})
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert "pending" in error_msg["detail"].lower()


def _victory_session_at_hideout_combat(
    campaign_scene_id: str = "hideout_combat",
) -> tuple[ws_session_module.Session, Campaign]:
    """Shared setup for the direct-call Phase 3 tests below: a session
    parked right after bandit_hideout's own victory, same "set the state
    you need rather than scripting combat to reach it" pattern the rest of
    this file already uses. Returns (session, campaign) so a test can look
    up scenes it spliced in."""
    srd = load_srd()
    campaign = load_campaign("kobold_warren_full")
    party = [build_companion(load_companion_spec("grom_ironfist"), srd=srd)]
    scene = campaign.scene_by_id(campaign_scene_id)
    assert scene.encounter_ref is not None
    game_state = build_encounter_state(
        load_encounter(scene.encounter_ref), party, random.Random(), srd=srd
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
        current_scene_id=campaign_scene_id,
    )
    return session, campaign


async def test_resolve_party_choice_generates_a_live_continuation_when_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Story-adaptive-encounters Phase 3. kobold_warren_full's own
    # "hideout_aftermath" scene (see CLAUDE.md) has no authored next_
    # scene_id - the real trigger this phase adds. Direct-call test (same
    # pattern as test_advance_campaign_after_victory_continues_to_the_next_
    # encounter below) rather than a full WS round trip, since it only
    # needs to prove _resolve_party_choice's own generation wiring, not the
    # message plumbing around it (a separate WS-level test covers that).
    session, campaign = _victory_session_at_hideout_combat()
    session.pending_party_choice = ws_session_module.PendingPartyChoice(
        scene_id="hideout_aftermath",
        situation="The bandits lie defeated, their hideout ransacked and quiet.",
        responses={"companion_grom": "I say we burn the ledger and be done with it."},
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis]",
    )
    captured: list[dict[str, object]] = []

    def _fake_generate_continuation(**kwargs: object) -> list[Scene]:
        captured.append(kwargs)
        return [
            Scene(
                id="kobold_warren_full__adaptive1_1",
                type="narrative_beat",
                narrative_intro="[stub generated ending]",
                next_scene_id=None,
            )
        ]

    monkeypatch.setattr(ws_session_module, "generate_continuation", _fake_generate_continuation)

    await ws_session_module._resolve_party_choice(session)

    assert len(captured) == 1
    situation = captured[0]["situation"]
    assert isinstance(situation, str) and situation.startswith("The bandits lie defeated")
    assert captured[0]["responses"] == {
        "companion_grom": "I say we burn the ledger and be done with it."
    }
    assert captured[0]["force_ending"] is False
    assert captured[0]["generation_index"] == 1
    assert session.adaptive_generations_used == 1
    # Spliced onto the live Campaign object - a real generated scene the
    # chain walk actually reached, not just a value this function returned.
    assert campaign.scene_by_id("kobold_warren_full__adaptive1_1") is not None
    assert session.pending_party_choice is None


async def test_resolve_party_choice_respects_the_adaptive_generation_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _campaign = _victory_session_at_hideout_combat()
    session.adaptive_generations_used = config.MAX_ADAPTIVE_GENERATIONS
    session.pending_party_choice = ws_session_module.PendingPartyChoice(
        scene_id="hideout_aftermath",
        situation="The bandits lie defeated.",
        responses={"companion_grom": "One more thing to look into..."},
    )
    monkeypatch.setattr(
        ws_session_module, "synthesize_party_choice_narration", lambda *a, **k: "[stub]"
    )
    calls: list[int] = []
    monkeypatch.setattr(
        ws_session_module,
        "generate_continuation",
        lambda **kwargs: calls.append(1) or [],  # type: ignore[func-returns-value]
    )

    await ws_session_module._resolve_party_choice(session)

    # Already at the cap - no generation call spent, the campaign simply
    # ends here (same as a hand-authored dead end always has).
    assert calls == []
    assert session.adaptive_generations_used == config.MAX_ADAPTIVE_GENERATIONS


async def test_resolve_party_choice_forces_an_ending_on_the_last_allowed_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _campaign = _victory_session_at_hideout_combat()
    session.adaptive_generations_used = config.MAX_ADAPTIVE_GENERATIONS - 1
    session.pending_party_choice = ws_session_module.PendingPartyChoice(
        scene_id="hideout_aftermath",
        situation="The bandits lie defeated.",
        responses={"companion_grom": "Let's see this through to the end."},
    )
    monkeypatch.setattr(
        ws_session_module, "synthesize_party_choice_narration", lambda *a, **k: "[stub]"
    )
    captured: list[dict[str, object]] = []

    def _fake_generate_continuation(**kwargs: object) -> list[Scene]:
        captured.append(kwargs)
        return [
            Scene(
                id="kobold_warren_full__adaptive2_1",
                type="narrative_beat",
                narrative_intro="[the real conclusion]",
                next_scene_id=None,
            )
        ]

    monkeypatch.setattr(ws_session_module, "generate_continuation", _fake_generate_continuation)

    await ws_session_module._resolve_party_choice(session)

    assert captured[0]["force_ending"] is True
    assert session.adaptive_generations_used == config.MAX_ADAPTIVE_GENERATIONS


def test_open_party_choice_generates_a_live_continuation_through_a_real_ws_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The WS-message-plumbing counterpart to the direct-call tests above -
    # confirms continue_campaign -> party_choice_offer -> party_choice_
    # response actually reaches _resolve_party_choice's new generation path
    # through the real handler, not just that the function itself works in
    # isolation.
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )

    def _fake_generate_continuation(**kwargs: object) -> list[Scene]:
        return [
            Scene(
                id="kobold_warren_full__adaptive1_1",
                type="narrative_beat",
                narrative_intro="[stub generated ending]",
                next_scene_id=None,
            )
        ]

    monkeypatch.setattr(ws_session_module, "generate_continuation", _fake_generate_continuation)

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
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        # Fast-forward directly to the second (bandit hideout) combat's own
        # victory - rising_action's own party_choice (Phase 2, already
        # tested above) doesn't need re-driving here.
        session = ws_session_module._sessions[session_id]
        assert session.party is not None
        encounter = load_encounter("bandit_hideout")
        session.game_state = build_encounter_state(
            encounter, session.party, session.action_rng, srd=session.srd
        )
        session.current_scene_id = "hideout_combat"
        session.game_state.status = "victory"

        ws.send_json({"type": "continue_campaign"})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "party_choice_offer":
            messages.append(ws.receive_json())
        assert messages[-1]["awaiting"] == ["thorin"]

        ws.send_json(
            {"type": "party_choice_response", "text": "Let's turn the ledger over to the guard."}
        )
        messages = [ws.receive_json()]
        while not (
            messages[-1]["type"] == "scene_narration"
            and "[stub generated ending]" in messages[-1]["text"]
        ):
            messages.append(ws.receive_json())

    scene_narrations = [m["text"] for m in messages if m["type"] == "scene_narration"]
    assert "[stub synthesis narration]" in scene_narrations
    assert "[stub generated ending]" in scene_narrations
    assert session.adaptive_generations_used == 1
    assert session.pending_party_choice is None
    # The generated ending was a plain narrative_beat (no further
    # party_choice), so the chain genuinely ran out here - campaign_
    # complete must reflect that even though session.current_scene_id is
    # still frozen at "hideout_combat" (see the regression test below for
    # why that distinction matters).
    assert session.campaign_complete is True


def test_a_second_continue_campaign_after_the_story_concludes_is_a_no_op(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live-found: a story that concludes via a pure narrative generation
    # (no further combat scene) never advances session.current_scene_id
    # past the last combat that actually happened - it only ever moves when
    # _advance_chain_from builds a fresh GameState. Before campaign_complete
    # existed, a stray second "Continue" click (status is still "victory",
    # nothing else says otherwise) silently re-walked the whole chain from
    # that same stale point, re-resolving hideout_aftermath's party_choice
    # from scratch and spending a real generation call on an already-told
    # story. Reproduced live in a real browser session before this fix.
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )
    generation_calls: list[dict[str, object]] = []

    def _fake_generate_continuation(**kwargs: object) -> list[Scene]:
        generation_calls.append(kwargs)
        return [
            Scene(
                id="kobold_warren_full__adaptive1_1",
                type="narrative_beat",
                narrative_intro="[stub generated ending]",
                next_scene_id=None,
            )
        ]

    monkeypatch.setattr(ws_session_module, "generate_continuation", _fake_generate_continuation)

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
        msg = ws.receive_json()
        while msg["type"] != "awaiting_input":
            msg = ws.receive_json()

        session = ws_session_module._sessions[session_id]
        assert session.party is not None
        encounter = load_encounter("bandit_hideout")
        session.game_state = build_encounter_state(
            encounter, session.party, session.action_rng, srd=session.srd
        )
        session.current_scene_id = "hideout_combat"
        session.game_state.status = "victory"

        ws.send_json({"type": "continue_campaign"})
        messages = [ws.receive_json()]
        while messages[-1]["type"] != "party_choice_offer":
            messages.append(ws.receive_json())

        ws.send_json({"type": "party_choice_response", "text": "Let's head home."})
        messages = [ws.receive_json()]
        while not (
            messages[-1]["type"] == "state_update" and messages[-1]["campaign_complete"] is True
        ):
            messages.append(ws.receive_json())

        assert len(generation_calls) == 1
        assert session.campaign_complete is True
        # session.current_scene_id never moved past the last real combat -
        # the exact stale state that used to make a second click dangerous.
        assert session.current_scene_id == "hideout_combat"

        # The real regression check: a second continue_campaign must not
        # re-walk the chain, re-offer hideout_aftermath's party_choice, or
        # spend a second generation call.
        ws.send_json({"type": "continue_campaign"})

    assert len(generation_calls) == 1
    assert session.pending_party_choice is None


async def test_advance_campaign_after_victory_continues_to_the_next_encounter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    #
    # This party is all-companion (no human_character_ids at all) - rising_
    # action is now a party_choice scene (story-adaptive-encounters Phase 2,
    # see CLAUDE.md), which _start_party_choice auto-resolves immediately
    # when there's no living human seat to actually wait on, so the chain
    # still reaches hideout_combat in one call, same as before that scene
    # existed. Stubbed to avoid a real LLM call in this offline test.
    monkeypatch.setattr(
        ws_session_module,
        "generate_companion_party_choice_response",
        lambda character, situation: f"[{character.name} stub reaction]",
    )
    monkeypatch.setattr(
        ws_session_module,
        "synthesize_party_choice_narration",
        lambda situation, responses, party: "[stub synthesis narration]",
    )
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

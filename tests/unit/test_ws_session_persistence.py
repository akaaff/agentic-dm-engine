"""Live-session persistence: a backend restart should be a short reconnect,
not the loss of every live game. The Session's state is snapshotted to its
CampaignProgress row after every state-changing broadcast and rebuilt from
that snapshot when a connection arrives for a session the process doesn't
hold in memory (src/api/ws/session_persistence.py + session.py's
_persist_session/_restore_session). A "restart" in these tests is
reset_sessions() - the in-memory registry is the only thing a real restart
loses; the DB survives.

Stays offline the same way test_ws_session_real_start.py does: the graph is
stubbed so no narrator/player_agent LLM call is ever reached.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.api.db.models import Base, CampaignProgress
from src.api.db.session import get_db
from src.api.main import app
from src.api.ws import session as ws_session_module
from src.api.ws.session import PendingPartyChoice
from src.api.ws.session_persistence import SNAPSHOT_VERSION, build_snapshot, restore_snapshot
from src.engine.actions import ParsedAction
from src.engine.turn_engine import PendingBardicChoice
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState
from src.observability import log_event

_FIGHTER_BODY = {
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
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {"parsed_action": ParsedAction(actor=actor_id, verb="end_turn", raw_text="stub")}


@dataclass
class _Env:
    client: TestClient
    db_factory: sessionmaker[Session]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Generator[_Env]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    yield _Env(client=TestClient(app), db_factory=TestSessionLocal)
    app.dependency_overrides.clear()
    ws_session_module.reset_sessions()


def _start_real_session(env: _Env) -> str:
    assert env.client.post("/characters", json=_FIGHTER_BODY).status_code == 201
    response = env.client.post(
        "/sessions",
        json={
            "campaign_id": "goblin_ambush_oneshot",
            "character_id": "thorin",
            "companion_ids": ["companion_grom"],
        },
    )
    assert response.status_code == 201
    session_id: str = response.json()["session_id"]
    return session_id


def _connect_and_drain(
    env: _Env, session_id: str, until: str = "awaiting_input"
) -> list[dict[str, Any]]:
    """Connects, reads messages up to and including the first one of type
    `until`, then disconnects. The in-memory Session outlives the socket. The
    default waits for awaiting_input - the connect flow's own last step - not the
    first state_update, which a mid-autoplay broadcast also sends: closing there
    would cut the opening turns off now that the blocking work yields to the event
    loop in worker threads (issue #105)."""
    seen: list[dict[str, Any]] = []
    with env.client.websocket_connect(f"/ws/session/{session_id}") as ws:
        while True:
            message = ws.receive_json()
            seen.append(message)
            if message["type"] == until:
                return seen


def _snapshot_in_db(env: _Env, session_id: str) -> dict[str, Any] | None:
    with env.db_factory() as db:
        progress = db.get(CampaignProgress, session_id)
        assert progress is not None
        snapshot = progress.session_snapshot
        return dict(snapshot) if snapshot else None


def _simulate_restart() -> None:
    """Everything a process restart loses: the in-memory session registry."""
    ws_session_module.reset_sessions()


def test_connecting_persists_a_snapshot_of_the_live_session(env: _Env) -> None:
    session_id = _start_real_session(env)
    assert _snapshot_in_db(env, session_id) is None  # nothing before first play

    _connect_and_drain(env, session_id)
    # The connect flow's own first state_update is sent straight to the
    # socket (not via _broadcast), so what's persisted is whatever an
    # autoplayed turn broadcast - force one write the way any later action
    # would, then check what landed.
    live = ws_session_module._sessions[session_id]
    ws_session_module._persist_session(live)

    snapshot = _snapshot_in_db(env, session_id)
    assert snapshot is not None
    assert snapshot["version"] == SNAPSHOT_VERSION
    assert set(snapshot["game_state"]["characters"]) == {
        "thorin",
        "companion_grom",
        "goblin_1",
        "goblin_2",
        "goblin_3",
    }
    assert snapshot["campaign"]["id"] == "goblin_ambush_oneshot"


def test_a_restart_restores_the_exact_live_state_instead_of_rebuilding(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)

    live = ws_session_module._sessions[session_id]
    live.game_state.characters["thorin"].hp = 3
    live.game_state.round = 7
    live.campaign_complete = True
    live.adaptive_generations_used = 2
    turn_order_before = list(live.game_state.turn_order)
    events_before = len(live.game_state.events)
    ws_session_module._persist_session(live)

    _simulate_restart()
    assert session_id not in ws_session_module._sessions

    seen = _connect_and_drain(env, session_id)
    restored = ws_session_module._sessions[session_id]
    # A from-scratch rebuild re-rolls initiative and resets HP/round - none of
    # these could survive it.
    assert restored.game_state.characters["thorin"].hp == 3
    assert restored.game_state.round == 7
    assert restored.game_state.turn_order == turn_order_before
    assert len(restored.game_state.events) == events_before
    assert restored.campaign_complete is True
    assert restored.adaptive_generations_used == 2
    # ...and the client's first state_update reflects it, not a fresh game.
    state_update = [m for m in seen if m["type"] == "state_update"][-1]
    assert state_update["game_state"]["characters"]["thorin"]["hp"] == 3
    assert state_update["campaign_complete"] is True


def test_restored_party_shares_objects_with_the_restored_game_state(env: _Env) -> None:
    """Rests mutate session.party in place and rely on those being the very
    Character objects game_state.characters holds - a restore that rebuilt
    them as separate copies would silently make a rest heal nobody."""
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)
    ws_session_module._persist_session(ws_session_module._sessions[session_id])

    _simulate_restart()
    _connect_and_drain(env, session_id)

    restored = ws_session_module._sessions[session_id]
    assert restored.party is not None
    assert [c.id for c in restored.party] == ["thorin", "companion_grom"]
    for member in restored.party:
        assert member is restored.game_state.characters[member.id]


def test_live_generated_scenes_survive_a_restart(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)

    live = ws_session_module._sessions[session_id]
    assert live.campaign is not None
    generated = live.campaign.scenes[0].model_copy(update={"id": "goblin_ambush_oneshot__gen1_1"})
    live.campaign.scenes.append(generated)
    ws_session_module._persist_session(live)

    _simulate_restart()
    _connect_and_drain(env, session_id)

    restored = ws_session_module._sessions[session_id]
    assert restored.campaign is not None
    assert "goblin_ambush_oneshot__gen1_1" in {s.id for s in restored.campaign.scenes}


def test_a_pending_party_choice_is_restored_and_re_offered_on_reconnect(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)

    live = ws_session_module._sessions[session_id]
    live.pending_party_choice = PendingPartyChoice(
        scene_id="s1", situation="A ledger!", responses={"companion_grom": "Burn it."}
    )
    ws_session_module._persist_session(live)

    _simulate_restart()
    seen = _connect_and_drain(env, session_id, until="party_choice_offer")

    offer = seen[-1]
    assert offer["situation"] == "A ledger!"
    assert offer["companion_responses"] == {"companion_grom": "Burn it."}
    assert offer["awaiting"] == ["thorin"]  # the human hasn't answered yet
    restored = ws_session_module._sessions[session_id]
    assert restored.pending_party_choice is not None
    assert restored.pending_party_choice.responses == {"companion_grom": "Burn it."}


def test_a_pending_bardic_choice_is_restored_and_re_offered_on_reconnect(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)

    live = ws_session_module._sessions[session_id]
    live.pending_bardic_choice = PendingBardicChoice(
        holder_id="thorin",
        target_id="goblin_1",
        natural=9,
        total_without_die=13,
        defender_ac=15,
        die_sides=6,
        damage_dice_count=1,
        damage_dice_sides=8,
        damage_bonus=3,
        damage_type="slashing",
        source_name="Longsword",
        attack_bonus_breakdown=[("STR mod", 3), ("proficiency", 2)],
        force_critical=False,
        is_finesse_or_ranged=False,
        had_advantage=False,
    )
    ws_session_module._persist_session(live)

    _simulate_restart()
    seen = _connect_and_drain(env, session_id, until="bardic_inspiration_offer")

    assert seen[-1]["holder"] == "thorin"
    assert seen[-1]["natural"] == 9
    restored = ws_session_module._sessions[session_id].pending_bardic_choice
    # JSON turned the (label, value) tuples into lists - restore must undo it,
    # since the finalizing code unpacks them as pairs.
    assert restored is not None
    assert restored.attack_bonus_breakdown == [("STR mod", 3), ("proficiency", 2)]


def test_a_party_choice_whose_last_human_already_answered_resolves_on_reconnect(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process can stop between "the last human answered" and the
    continuation being generated - the restored snapshot then has nobody left
    to answer, so nothing but the reconnect itself can finish the pause."""
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)

    live = ws_session_module._sessions[session_id]
    live.pending_party_choice = PendingPartyChoice(
        scene_id="s1",
        situation="A ledger!",
        responses={"companion_grom": "Burn it.", "thorin": "Read it."},
    )
    ws_session_module._persist_session(live)
    _simulate_restart()

    resolved: list[str] = []

    async def _fake_resolve(session: Any) -> None:
        resolved.append(session.pending_party_choice.situation)
        session.pending_party_choice = None

    monkeypatch.setattr(ws_session_module, "_resolve_party_choice", _fake_resolve)
    _connect_and_drain(env, session_id)

    assert resolved == ["A ledger!"]


def test_an_unusable_snapshot_falls_back_to_a_fresh_session_and_logs_it(env: _Env) -> None:
    session_id = _start_real_session(env)
    with env.db_factory() as db:
        progress = db.get(CampaignProgress, session_id)
        assert progress is not None
        progress.session_snapshot = {"version": 999}
        db.commit()

    seen = _connect_and_drain(env, session_id)

    # Still a playable, from-scratch session...
    last_state = [m for m in seen if m["type"] == "state_update"][-1]
    assert last_state["game_state"]["status"] == "in_progress"
    assert set(last_state["game_state"]["characters"]) >= {"thorin", "goblin_1"}
    # ...and the bad snapshot was recorded, not silently swallowed.
    records = [json.loads(line) for line in log_event.EVENTS_LOG_PATH.read_text().splitlines()]
    assert any("snapshot restore failed" in r.get("message", "") for r in records)


def test_a_failed_snapshot_write_never_breaks_the_live_turn(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ws_session_module, "build_snapshot", _boom)
    session_id = _start_real_session(env)

    _connect_and_drain(env, session_id)
    live = ws_session_module._sessions[session_id]
    ws_session_module._persist_session(live)  # must not raise

    records = [json.loads(line) for line in log_event.EVENTS_LOG_PATH.read_text().splitlines()]
    assert any("snapshot write failed" in r.get("message", "") for r in records)
    assert _snapshot_in_db(env, session_id) is None


def test_a_session_with_no_progress_row_is_simply_not_persisted(env: _Env) -> None:
    """The demo fallback / every offline test that calls create_session()
    directly has no CampaignProgress row to write to - must be a quiet no-op."""
    import random

    from src.cli.play import build_demo_encounter, build_demo_party
    from src.engine.encounter import build_encounter_state

    state = build_encounter_state(build_demo_encounter(), build_demo_party(), random.Random(1))
    session = ws_session_module.create_session("no-row", state)
    ws_session_module._persist_session(session)  # no campaign -> returns early


def test_snapshot_is_pure_json_and_round_trips(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)
    live = ws_session_module._sessions[session_id]

    snapshot = json.loads(json.dumps(build_snapshot(live)))  # must be JSON-serializable
    restored = restore_snapshot(snapshot, ["thorin", "companion_grom"])

    assert restored.game_state.model_dump(mode="json") == live.game_state.model_dump(mode="json")
    assert restored.campaign.model_dump(mode="json") == live.campaign.model_dump(mode="json")  # type: ignore[union-attr]
    assert restored.current_scene_id == live.current_scene_id


def test_restore_rejects_a_wrong_version_and_a_missing_party_member(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)
    snapshot = json.loads(json.dumps(build_snapshot(ws_session_module._sessions[session_id])))

    with pytest.raises(ValueError, match="version"):
        restore_snapshot({**snapshot, "version": SNAPSHOT_VERSION + 1}, ["thorin"])
    with pytest.raises(ValueError, match="missing"):
        restore_snapshot(snapshot, ["thorin", "someone_who_left"])


def test_a_state_update_broadcast_is_what_triggers_the_write(env: _Env) -> None:
    session_id = _start_real_session(env)
    _connect_and_drain(env, session_id)
    live = ws_session_module._sessions[session_id]

    with env.db_factory() as db:
        progress = db.get(CampaignProgress, session_id)
        assert progress is not None
        progress.session_snapshot = None
        db.commit()

    # A narration broadcast changes nothing the snapshot holds - no write.
    asyncio.run(ws_session_module._broadcast_narration(live, "just words"))
    assert _snapshot_in_db(env, session_id) is None

    asyncio.run(ws_session_module._broadcast(live, ws_session_module._state_update_message(live)))
    assert _snapshot_in_db(env, session_id) is not None


def test_a_real_action_over_the_socket_is_persisted_and_survives_a_restart(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ALLOW_DEBUG_ACTIONS", True)
    session_id = _start_real_session(env)

    with env.client.websocket_connect(f"/ws/session/{session_id}") as ws:
        while True:
            message = ws.receive_json()
            if message["type"] == "awaiting_input":
                break
        round_before = ws_session_module._sessions[session_id].game_state.round
        ws.send_json(
            {
                "type": "debug_action",
                "action": {"actor": message["actor"], "verb": "end_turn", "raw_text": "pass"},
            }
        )
        while ws.receive_json()["type"] != "awaiting_input":
            pass
        after = ws_session_module._sessions[session_id].game_state
        events_after = len(after.events)
        round_after = after.round
        current_after = after.current_turn

    # The snapshot already holds the post-action state (written by the
    # state_update broadcast), with no manual _persist_session call.
    snapshot = _snapshot_in_db(env, session_id)
    assert snapshot is not None
    assert len(snapshot["game_state"]["events"]) == events_after
    assert snapshot["game_state"]["round"] == round_after >= round_before

    _simulate_restart()
    _connect_and_drain(env, session_id)
    restored = ws_session_module._sessions[session_id].game_state
    assert restored.current_turn == current_after
    assert len(restored.events) == events_after

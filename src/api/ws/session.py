"""WebSocket live-play session at /ws/session/{session_id}.

Built around a multi-connection registry (session_id -> list of connections,
each owning a set of character ids) from day one, even though only a single
human ever connects through Phase 7 - see DECISIONS.md #6 and the plan's
"Forward-compatibility for multiplayer" note. `awaiting_input` is addressed
to whichever connection controls the current turn's actor; `narration`,
`state_update`, and `scene_image` broadcast to every connection in the
session.

Day-11 simplification (no real per-connection authorization yet, and no
monster-AI/companion-AI turn resolution until later days): whoever connects
to a session is registered as controlling *every* character in it, party and
monsters alike, so a single test/demo client can drive a whole encounter.
Phase 8's real multiplayer join flow narrows this to per-connection
ownership.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph

from src.api.db.models import CampaignProgress, CharacterRecord
from src.api.db.session import SessionLocal
from src.api.routes.characters import _record_to_character
from src.cli.play import build_demo_encounter, build_demo_party
from src.engine.actions import ParsedAction
from src.engine.campaign import load_campaign
from src.engine.companions import build_companion, load_companion_spec_by_character_id
from src.engine.encounter import build_encounter_state, load_encounter
from src.engine.monster_ai import choose_monster_action
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.engine.turn_engine import TurnEngineError
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

router = APIRouter()


@dataclass
class SessionConnection:
    websocket: WebSocket
    controlled_character_ids: set[str]


@dataclass
class Session:
    game_state: GameState
    action_rng: random.Random
    graph: CompiledStateGraph[GraphState, Any, Any, Any]
    connections: list[SessionConnection] = field(default_factory=list)
    human_character_id: str | None = None
    """Set only for a real session started via POST /sessions (Day 18) and
    resolved from its CampaignProgress row (Day 19) - the human connecting
    controls just this one character, and every other actor's turn
    (companions, monsters) is auto-played server-side, see
    _autoplay_non_human_turns. None preserves the original Day-11
    "whoever connects controls every character" simplification, still used
    by the demo-encounter fallback below and by every offline test that
    calls create_session() directly."""


_sessions: dict[str, Session] = {}


def create_session(
    session_id: str,
    game_state: GameState,
    action_rng: random.Random | None = None,
    graph: CompiledStateGraph[GraphState, Any, Any, Any] | None = None,
    human_character_id: str | None = None,
) -> Session:
    """Explicit constructor for tests (and real session-start flows) to
    pre-seed a session with a specific initial state/rng before any
    connection touches it. Overwrites any existing session with this id.
    `graph` lets tests substitute build_graph(narrator_fn=...) so an offline
    test doesn't hit the real narrator's live Ollama call."""
    action_rng = action_rng or random.Random()
    session = Session(
        game_state=game_state,
        action_rng=action_rng,
        graph=graph or build_graph(rng=action_rng),
        human_character_id=human_character_id,
    )
    _sessions[session_id] = session
    return session


def _build_real_session_game_state(progress: CampaignProgress) -> GameState | None:
    """Builds a real GameState from a Day-18 CampaignProgress row: the
    player's own persisted character plus their chosen companions, dropped
    into the campaign's first combat encounter. Returns None (caller falls
    back to the demo encounter) if anything expected is missing - defensive,
    not expected to trigger in practice since POST /sessions already
    validates the campaign/character/companion ids before writing the row."""
    try:
        campaign = load_campaign(progress.campaign_id)
    except FileNotFoundError:
        return None

    scene = campaign.first_scene()
    while scene.encounter_ref is None:
        if scene.next_scene_id is None:
            return None
        scene = campaign.scene_by_id(scene.next_scene_id)
    encounter = load_encounter(scene.encounter_ref)

    srd = load_srd()
    with SessionLocal() as db:
        human_record = db.get(CharacterRecord, progress.party_character_ids[0])
        if human_record is None:
            return None
        human_character = _record_to_character(human_record)

    party = [human_character]
    for companion_id in progress.party_character_ids[1:]:
        spec = load_companion_spec_by_character_id(companion_id)
        if spec is None:
            return None
        party.append(build_companion(spec, srd=srd))

    return build_encounter_state(encounter, party, random.Random(), srd=srd)


def _get_or_create_default_session(session_id: str) -> Session:
    """First connection to an unknown session_id either resumes a real
    session started via POST /sessions (looked up by its CampaignProgress
    row) or, if none exists, falls back to the Day-7 demo encounter -
    preserved as-is for any ad-hoc/manual WebSocket connection that never
    went through the real character/party/campaign flow."""
    if session_id not in _sessions:
        with SessionLocal() as db:
            progress = db.get(CampaignProgress, session_id)

        game_state = _build_real_session_game_state(progress) if progress else None
        if game_state is not None and progress is not None:
            create_session(
                session_id, game_state, human_character_id=progress.party_character_ids[0]
            )
        else:
            encounter = build_demo_encounter()
            party = build_demo_party()
            demo_state = build_encounter_state(encounter, party, random.Random())
            create_session(session_id, demo_state)
    return _sessions[session_id]


def reset_sessions() -> None:
    """Test-only: clears all in-memory sessions between test runs."""
    _sessions.clear()


async def _broadcast(session: Session, message: dict[str, object]) -> None:
    for connection in session.connections:
        await connection.websocket.send_json(message)


async def _send_awaiting_input(session: Session) -> None:
    if session.game_state.status != "in_progress":
        return
    current_actor = session.game_state.turn_order[session.game_state.current_turn]
    for connection in session.connections:
        if current_actor in connection.controlled_character_ids:
            await connection.websocket.send_json({"type": "awaiting_input", "actor": current_actor})
            return


def _state_update_message(session: Session) -> dict[str, object]:
    return {"type": "state_update", "game_state": session.game_state.model_dump(mode="json")}


async def _autoplay_non_human_turns(session: Session) -> None:
    """Resolves every actor's turn up to (not including) the human's next
    one - companions via player_agent_node (empty raw_text/parsed_action,
    the same trigger cli.play.run_autoplay uses), monsters via the
    deterministic monster_ai heuristic. No-ops immediately for a session
    with no human_character_id set (the demo-encounter fallback and every
    offline test that calls create_session() directly), so this can be
    called unconditionally from both connect and after every human action.

    Same consecutive_invalid circuit breaker as run_autoplay, and for the
    same reason: a persona-driven companion turn can still occasionally fail
    to parse into a concrete action even after the Day 15 prompt fix, and a
    live WebSocket connection has no autoplay script wrapping it to bail out
    - without this, that failure mode would hang the session forever instead
    of just wasting a few turns.
    """
    if session.human_character_id is None:
        return

    consecutive_invalid = 0
    while session.game_state.status == "in_progress":
        current_actor_id = session.game_state.turn_order[session.game_state.current_turn]
        if current_actor_id == session.human_character_id:
            return
        actor = session.game_state.characters[current_actor_id]

        if not actor.is_pc:
            parsed_action = choose_monster_action(session.game_state, actor)
        elif consecutive_invalid >= 3:
            parsed_action = ParsedAction(
                actor=current_actor_id,
                verb="end_turn",
                raw_text="(forced end_turn after repeated invalid actions)",
            )
        else:
            parsed_action = None

        events_before = len(session.game_state.events)
        graph_input: GraphState = {
            "game_state": session.game_state,
            "raw_text": "",
            "parsed_action": parsed_action,
            "events_before": events_before,
            "round_before": session.game_state.round,
            "narration": None,
            "scene_image_url": None,
        }
        try:
            result = session.graph.invoke(graph_input)
        except (TurnEngineError, NotImplementedError) as exc:
            await _broadcast(session, {"type": "error", "detail": str(exc)})
            consecutive_invalid += 1
            continue

        session.game_state = result["game_state"]
        await _broadcast(session, {"type": "narration", "text": result["narration"]})
        await _broadcast(session, _state_update_message(session))
        if result["scene_image_url"]:
            await _broadcast(session, {"type": "scene_image", "url": result["scene_image_url"]})

        new_events = session.game_state.events[events_before:]
        if len(new_events) == 1 and new_events[0].type == "action_invalid":
            consecutive_invalid += 1
        else:
            consecutive_invalid = 0


async def _handle_client_message(
    session: Session, websocket: WebSocket, raw: dict[str, object]
) -> None:
    msg_type = raw.get("type")
    raw_text = ""
    action: ParsedAction | None = None

    if msg_type == "player_action":
        raw_text = str(raw["text"])  # genuine free text - intent_parser_node calls the LLM on it
    elif msg_type == "player_move":
        current_actor = session.game_state.turn_order[session.game_state.current_turn]
        action = ParsedAction(
            actor=current_actor,
            verb="move",
            params={"path": [raw["to"]]},
            raw_text="click-to-move",
        )
    elif msg_type == "debug_action":
        # Test/dev only: injects a fully-formed ParsedAction directly,
        # bypassing intent_parser's LLM call entirely (see its pre-supplied-
        # parsed_action escape hatch). Never sent by the real frontend.
        action = ParsedAction.model_validate(raw["action"])
    else:
        return

    graph_input: GraphState = {
        "game_state": session.game_state,
        "raw_text": raw_text,
        "parsed_action": action,
        "events_before": len(session.game_state.events),
        "round_before": session.game_state.round,
        "narration": None,
        "scene_image_url": None,
    }
    try:
        result = session.graph.invoke(graph_input)
    except (TurnEngineError, NotImplementedError) as exc:
        # Caught live, two real cases: (1) a free-text action can name a
        # real-looking but invalid item/spell (e.g. the LLM extracting
        # "dagger" as item_or_spell for a monster attack, whose SRD stat
        # block has no such action) - TurnEngineError. (2) the intent-parser
        # prompt describes verbs (dodge, cast_spell, skill_check, help,
        # use_item, death_save) that turn_engine.resolve_action doesn't
        # actually implement yet (Day 7 only built attack/move/dash/
        # end_turn) - NotImplementedError. Both validate-then-raise before
        # mutating state, so it's safe to just report the error back to
        # whoever sent it and let them try again, rather than crashing the
        # whole connection.
        await websocket.send_json({"type": "error", "detail": str(exc)})
        # Found live (Day 19): without this, the turn correctly stays with
        # the same actor (nothing mutated), but the client that just tried
        # and failed was never told it's still their turn - the frontend
        # optimistically clears its own "it's my turn" state the moment it
        # sends an action (so it can't be submitted twice while waiting),
        # and had nothing to restore it, leaving the input disabled forever
        # after a single rejected action even though a retry would work.
        await _send_awaiting_input(session)
        return

    session.game_state = result["game_state"]

    await _broadcast(session, {"type": "narration", "text": result["narration"]})
    await _broadcast(session, _state_update_message(session))
    if result["scene_image_url"]:
        await _broadcast(session, {"type": "scene_image", "url": result["scene_image_url"]})

    await _autoplay_non_human_turns(session)
    await _send_awaiting_input(session)


@router.websocket("/ws/session/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = _get_or_create_default_session(session_id)

    controlled = (
        {session.human_character_id}
        if session.human_character_id is not None
        else set(session.game_state.characters.keys())
    )
    connection = SessionConnection(websocket=websocket, controlled_character_ids=controlled)
    session.connections.append(connection)

    try:
        # Resolve any monster/companion turns that come before the human's
        # first one (e.g. a monster going first in initiative) before this
        # connection's own initial state_update, so it opens on a state the
        # human can actually act on rather than one that's already stale.
        await _autoplay_non_human_turns(session)
        await websocket.send_json(_state_update_message(session))
        # Personal, not the shared _send_awaiting_input (which searches the
        # whole session for whoever should act next, after an action
        # resolves): a just-connected client needs to be told about its own
        # turn status regardless of who else is already in the session, or
        # it would never hear about it if an earlier connection happens to
        # control the same actor - which, under Day 11's "one connection
        # controls everyone" simplification, is every other connection.
        if session.game_state.status == "in_progress":
            current_actor = session.game_state.turn_order[session.game_state.current_turn]
            if current_actor in connection.controlled_character_ids:
                await websocket.send_json({"type": "awaiting_input", "actor": current_actor})

        while True:
            raw = await websocket.receive_json()
            await _handle_client_message(session, websocket, raw)
    except WebSocketDisconnect:
        pass
    finally:
        session.connections.remove(connection)

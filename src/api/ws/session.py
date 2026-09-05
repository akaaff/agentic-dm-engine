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

from src.cli.play import build_demo_encounter, build_demo_party
from src.engine.actions import ParsedAction
from src.engine.encounter import build_encounter_state
from src.engine.state import GameState
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


_sessions: dict[str, Session] = {}


def create_session(
    session_id: str,
    game_state: GameState,
    action_rng: random.Random | None = None,
) -> Session:
    """Explicit constructor for tests (and, later, real session-start flows)
    to pre-seed a session with a specific initial state/rng before any
    connection touches it. Overwrites any existing session with this id."""
    action_rng = action_rng or random.Random()
    session = Session(
        game_state=game_state, action_rng=action_rng, graph=build_graph(rng=action_rng)
    )
    _sessions[session_id] = session
    return session


def _get_or_create_default_session(session_id: str) -> Session:
    """The real (non-test) path: first connection to an unknown session_id
    spins up the Day-7 demo encounter with real randomness. A proper
    campaign/party-selection-driven session start is future work (this
    project has no such flow wired up yet)."""
    if session_id not in _sessions:
        encounter = build_demo_encounter()
        party = build_demo_party()
        game_state = build_encounter_state(encounter, party, random.Random())
        create_session(session_id, game_state)
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


async def _handle_client_message(session: Session, raw: dict[str, object]) -> None:
    msg_type = raw.get("type")

    if msg_type == "player_action":
        action = ParsedAction.model_validate_json(raw["text"])  # type: ignore[arg-type]
    elif msg_type == "player_move":
        current_actor = session.game_state.turn_order[session.game_state.current_turn]
        action = ParsedAction(
            actor=current_actor,
            verb="move",
            params={"path": [raw["to"]]},
            raw_text="click-to-move",
        )
    else:
        return

    graph_input: GraphState = {
        "game_state": session.game_state,
        "raw_text": "",
        "parsed_action": action,
        "narration": None,
        "scene_image_url": None,
    }
    result = session.graph.invoke(graph_input)
    session.game_state = result["game_state"]

    await _broadcast(session, {"type": "narration", "text": result["narration"]})
    await _broadcast(session, _state_update_message(session))
    if result["scene_image_url"]:
        await _broadcast(session, {"type": "scene_image", "url": result["scene_image_url"]})

    await _send_awaiting_input(session)


@router.websocket("/ws/session/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = _get_or_create_default_session(session_id)

    controlled = set(session.game_state.characters.keys())
    connection = SessionConnection(websocket=websocket, controlled_character_ids=controlled)
    session.connections.append(connection)

    try:
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
            await _handle_client_message(session, raw)
    except WebSocketDisconnect:
        pass
    finally:
        session.connections.remove(connection)

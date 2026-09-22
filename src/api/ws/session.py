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
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph

from src import config
from src.api.db.models import CampaignProgress, CharacterRecord
from src.api.db.session import SessionLocal
from src.api.routes.characters import _record_to_character
from src.api.ws.rate_limit import TokenBucket
from src.cli.play import build_demo_encounter, build_demo_party
from src.engine.actions import ParsedAction
from src.engine.campaign import Campaign, Scene, load_campaign
from src.engine.campaign_runner import advance_to_next_encounter
from src.engine.companions import build_companion, load_companion_spec_by_character_id
from src.engine.encounter import GameStateBuildError, build_encounter_state, load_encounter
from src.engine.monster_ai import choose_monster_action
from src.engine.resting import apply_long_rest, apply_short_rest
from src.engine.rules import ability_modifier, armor_ac_breakdown
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    BardicChoicePending,
    PendingBardicChoice,
    TurnEngineError,
    current_attack_summaries,
    resolve_pending_bardic_choice,
)
from src.graph.graph_builder import build_graph
from src.graph.nodes.intent_parser import parse_intent_sequence
from src.graph.nodes.narrator import narrator_node
from src.graph.nodes.party_choice import (
    generate_companion_party_choice_response,
    synthesize_party_choice_narration,
)
from src.graph.state_schema import GraphState

router = APIRouter()


@dataclass
class SessionConnection:
    websocket: WebSocket
    controlled_character_ids: set[str]


@dataclass
class PendingPartyChoice:
    """Story-adaptive-encounters Phase 2: everything needed to resolve a
    party_choice scene once every living human party member has responded -
    set the instant campaign_runner's chain-walk stops at one (see
    _start_party_choice), cleared the moment _resolve_party_choice fires.
    `responses` starts pre-populated with every companion's own generated
    reaction (synchronous, at pause time - see _start_party_choice) and
    fills in with human responses one at a time as party_choice_response
    messages arrive; nothing else in this session resolves until every
    living human id in Session.human_character_ids is a key here."""

    scene_id: str
    situation: str
    responses: dict[str, str] = field(default_factory=dict)


@dataclass
class Session:
    game_state: GameState
    action_rng: random.Random
    graph: CompiledStateGraph[GraphState, Any, Any, Any]
    connections: list[SessionConnection] = field(default_factory=list)
    human_character_ids: dict[str, str] = field(default_factory=dict)
    """Issue #44: personal token -> character id, replacing the old singular
    human_character_id (a real session now supports 2+ human players, not
    just one) - every character id in this dict's values is human-controlled;
    every other actor's turn (companions, monsters) is auto-played
    server-side, see _autoplay_non_human_turns. Empty preserves the original
    Day-11 "whoever connects controls every character" simplification, still
    used by the demo-encounter fallback below and by every offline test that
    calls create_session() directly. A single-entry dict (the legacy
    single-shot POST /sessions flow, or a lobby with exactly one joined
    player) additionally doesn't require the connecting client to present a
    token at all - see session_websocket - since there's only one human seat
    to disambiguate between."""
    campaign: Campaign | None = None
    """Set only for a real session - lets the session continue live past
    one encounter's victory into the rest of the scene chain (see
    _advance_campaign_after_victory), the same chaining cli.play.run_autoplay
    already does for autoplay. None means "just play this one GameState and
    stop," preserving every offline test/demo-encounter path unchanged."""
    party: list[Character] | None = None
    """The same Character objects reused across encounters within one
    campaign, not fresh copies - HP/conditions/inventory genuinely carry
    over between fights, matching run_autoplay."""
    srd: SrdIndex | None = None
    current_scene_id: str | None = None
    """Which Scene the current game_state's encounter came from - lets
    _advance_campaign_after_victory find campaign.next_scene(...) once this
    encounter resolves."""
    pending_scene_narration: list[str] = field(default_factory=list)
    """Narrative-beat/skill-challenge text collected (via campaign_runner)
    before this session's *first* encounter - delivered to the first
    connecting client as scene_narration messages, then cleared. A second
    connection joining later won't see it - the same "no narration replay
    on late join" limitation this session already has for ordinary
    per-turn narration, not something this change newly introduces."""
    action_bucket: TokenBucket = field(
        default_factory=lambda: TokenBucket(
            capacity=config.ACTION_RATE_LIMIT_CAPACITY,
            refill_per_second=config.ACTION_RATE_LIMIT_PER_MINUTE / 60,
        )
    )
    """Issue #43: shared across every connection to this session (not
    per-connection) - the thing being protected is the session's own turn
    pipeline (one shared narrator/scene_image call per resolved action), not
    any individual client."""
    connected_human_character_ids: set[str] = field(default_factory=set)
    """Issue #46: which human characters currently have a live connection -
    used to tell a genuine reconnect (this character was connected before,
    dropped, and is connecting again) apart from a character's very first
    connect ever (nothing to call a "reconnect"). Only meaningful for a real
    multi-human session (human_character_ids non-empty); the demo/single-
    connection-controls-everyone fallback never touches this."""
    ever_connected_human_character_ids: set[str] = field(default_factory=set)
    """Issue #46: every human character that has EVER connected at least
    once - never removed, unlike connected_human_character_ids above, so a
    later reconnect can still be told apart from a first connect after the
    character has since disconnected."""
    pending_bardic_choice: PendingBardicChoice | None = None
    """Issue #53: set the instant a single attack roll pauses waiting for
    its Bardic Inspiration holder to decide whether to spend their banked
    die (BardicChoicePending, raised from turn_engine) - cleared the moment
    a bardic_inspiration_response message resumes it. While set, the turn
    loop stays paused (no autoplay, no awaiting_input) - the only thing this
    session should be receiving next is that response."""
    pending_party_choice: PendingPartyChoice | None = None
    """Story-adaptive-encounters Phase 2: set the instant the campaign's
    chain-walk stops at a party_choice scene (see _start_party_choice) -
    while set, the session is paused between encounters exactly like a
    "victory" stop already is (no combat turn loop is running either way),
    waiting on party_choice_response messages instead of rest/
    continue_campaign. Cleared by _resolve_party_choice once every living
    human party member has answered."""


_sessions: dict[str, Session] = {}


def create_session(
    session_id: str,
    game_state: GameState,
    action_rng: random.Random | None = None,
    graph: CompiledStateGraph[GraphState, Any, Any, Any] | None = None,
    human_character_ids: dict[str, str] | None = None,
    campaign: Campaign | None = None,
    party: list[Character] | None = None,
    srd: SrdIndex | None = None,
    current_scene_id: str | None = None,
    pending_scene_narration: list[str] | None = None,
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
        human_character_ids=human_character_ids or {},
        campaign=campaign,
        party=party,
        srd=srd,
        current_scene_id=current_scene_id,
        pending_scene_narration=pending_scene_narration or [],
    )
    _sessions[session_id] = session
    return session


@dataclass
class _RealSessionSetup:
    game_state: GameState
    pre_scene_narration: list[str]
    campaign: Campaign
    party: list[Character]
    combat_scene_id: str
    srd: SrdIndex
    human_character_ids: dict[str, str]


def _build_real_session_setup(progress: CampaignProgress) -> _RealSessionSetup | None:
    """Builds a real session from a CampaignProgress row - either the
    legacy Day-18 single-shot POST /sessions shape (one player character,
    chosen upfront, plus their companions) or issue #44's lobby shape
    (2+ player characters, joined live via POST /sessions/{id}/join, plus
    whichever companions POST /sessions/{id}/start filled the rest with) -
    walked through the campaign's scene chain from its very first scene via
    campaign_runner.advance_to_next_encounter, narrating any
    narrative_beat/skill_challenge scenes along the way (the "hook" before
    a fight, not just dropping straight into combat - caught live, a real
    session used to skip this entirely) into its first combat encounter.

    Returns None (caller falls back to the demo encounter) if anything
    expected is missing - defensive, not expected to trigger in practice
    since every write path (POST /sessions, POST /sessions/{id}/join,
    POST /sessions/{id}/start) already validates its own ids before
    persisting, or if the campaign has no combat scene at all (not a real
    authored campaign's shape, but not this function's job to assume)."""
    try:
        campaign = load_campaign(progress.campaign_id)
    except FileNotFoundError:
        return None
    if not progress.party_character_ids:
        return None

    srd = load_srd()

    # issue #44: player_tokens (populated only via the lobby/join flow)
    # names every human-controlled character explicitly; an empty dict means
    # this row came from the legacy single-shot flow, which never recorded a
    # token at all - falls back to that flow's own original assumption
    # (party_character_ids[0] is the one human) so every pre-#44 session
    # keeps working unchanged, including the WS connect side (see
    # session_websocket's own "single human seat needs no token" handling).
    human_character_ids = (
        dict(progress.player_tokens)
        if progress.player_tokens
        else {uuid4().hex: progress.party_character_ids[0]}
    )
    human_ids = set(human_character_ids.values())

    party: list[Character] = []
    with SessionLocal() as db:
        for char_id in progress.party_character_ids:
            if char_id in human_ids:
                record = db.get(CharacterRecord, char_id)
                if record is None:
                    return None
                party.append(_record_to_character(record))
            else:
                spec = load_companion_spec_by_character_id(char_id)
                if spec is None:
                    return None
                party.append(build_companion(spec, srd=srd))

    rng = random.Random()
    combat_scene, narration = advance_to_next_encounter(
        campaign, campaign.first_scene(), party, srd, rng
    )
    if combat_scene is None or combat_scene.type != "combat":
        # Story-adaptive-encounters Phase 2 scope cut: a party_choice scene
        # reached before the session's very first combat isn't supported -
        # this function builds a combat GameState synchronously, before any
        # WebSocket connection (and so no way to broadcast an offer or
        # collect live responses) exists. No authored campaign places one
        # this early; if one ever does, this falls back to the demo
        # encounter the same way "no combat scene at all" already does,
        # rather than silently mishandling it.
        return None

    assert combat_scene.encounter_ref is not None  # guaranteed by Scene.type == "combat"
    encounter = load_encounter(combat_scene.encounter_ref)
    game_state = build_encounter_state(encounter, party, rng, srd=srd)

    return _RealSessionSetup(
        game_state=game_state,
        pre_scene_narration=narration,
        campaign=campaign,
        party=party,
        combat_scene_id=combat_scene.id,
        srd=srd,
        human_character_ids=human_character_ids,
    )


def _get_or_create_default_session(session_id: str) -> Session:
    """First connection to an unknown session_id either resumes a real
    session started via POST /sessions or the lobby flow (looked up by its
    CampaignProgress row) or, if none exists, falls back to the Day-7 demo
    encounter - preserved as-is for any ad-hoc/manual WebSocket connection
    that never went through the real character/party/campaign flow."""
    if session_id not in _sessions:
        with SessionLocal() as db:
            progress = db.get(CampaignProgress, session_id)

        setup = _build_real_session_setup(progress) if progress else None
        if setup is not None:
            create_session(
                session_id,
                setup.game_state,
                human_character_ids=setup.human_character_ids,
                campaign=setup.campaign,
                party=setup.party,
                srd=setup.srd,
                current_scene_id=setup.combat_scene_id,
                pending_scene_narration=setup.pre_scene_narration,
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
    # Issue #46: a connection that dropped without a clean disconnect (no
    # WebSocketDisconnect ever raised on this connection's own receive loop -
    # a phone sleeping or a wifi blip, not a closed tab) would otherwise sit
    # in session.connections indefinitely, and Starlette's WebSocket.send()
    # raises WebSocketDisconnect/RuntimeError on the first attempt to write
    # to it (confirmed by reading its source) - letting that propagate here
    # would abort this whole broadcast mid-loop, silently dropping the
    # message for every connection *after* the dead one too. Pruned quietly
    # (no player_disconnected notice from this path specifically - the main
    # receive loop's own clean-disconnect handling below is the primary,
    # prompt detection point; this is just a defensive backstop).
    dead: list[SessionConnection] = []
    for connection in session.connections:
        try:
            await connection.websocket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            dead.append(connection)
    for connection in dead:
        if connection in session.connections:
            session.connections.remove(connection)
        session.connected_human_character_ids -= connection.controlled_character_ids


async def _send_awaiting_input(session: Session) -> None:
    if session.game_state.status != "in_progress":
        return
    current_actor = session.game_state.turn_order[session.game_state.current_turn]
    for connection in session.connections:
        if current_actor in connection.controlled_character_ids:
            try:
                await connection.websocket.send_json(
                    {"type": "awaiting_input", "actor": current_actor}
                )
            except (WebSocketDisconnect, RuntimeError):
                session.connections.remove(connection)
                session.connected_human_character_ids -= connection.controlled_character_ids
            return


def _combat_summaries(session: Session) -> dict[str, object]:
    """Proactive UX ask, not a bug fix: a character sheet showing "AC 15"
    or "+5 to hit" as a bare number gives no way to tell whether that's
    right without re-deriving it by hand - the same class of gap issue
    #38's debug-mode roll breakdown already closed for the log, just never
    extended to the sheet's own static AC/attack-bonus display. Computed
    fresh per broadcast (cheap - at most a handful of PCs) rather than
    cached on Character, since equipped gear/fighting-style-independent
    state like Rage can change mid-encounter and this must never go stale.
    Empty when srd isn't set (the demo-encounter/offline-test fallback -
    see Session.srd's own docstring) - real sessions always have it."""
    if session.srd is None:
        return {}
    summaries: dict[str, object] = {}
    for character in session.game_state.characters.values():
        if not character.is_pc:
            continue
        dex_mod = ability_modifier(character.stats["DEX"])
        wis_mod = ability_modifier(character.stats["WIS"])
        con_mod = ability_modifier(character.stats["CON"])
        ac_breakdown = armor_ac_breakdown(
            character.equipped_armor,
            character.equipped_shield,
            dex_mod,
            character.fighting_style,
            session.srd.equipment,
            character.class_index,
            wis_mod,
            con_mod,
            character.mage_armor_active,
            character.temporary_ac_bonus,
        )
        attacks = current_attack_summaries(character, session.srd)
        summaries[character.id] = {
            "ac_breakdown": ac_breakdown,
            "attacks": [
                {
                    "source_name": a.source_name,
                    "attack_bonus": a.attack_bonus,
                    "attack_bonus_breakdown": a.attack_bonus_breakdown,
                    "damage_dice_count": a.damage_dice_count,
                    "damage_dice_sides": a.damage_dice_sides,
                    "damage_bonus": a.damage_bonus,
                    "damage_type": a.damage_type,
                }
                for a in attacks
            ],
        }
    return summaries


def _resolve_and_narrate_bardic_choice(
    session: Session, choice: PendingBardicChoice, use_die: bool
) -> str:
    """Issue #53: resolves a paused attack roll once the die-or-not decision
    is known (either a real human answer via bardic_inspiration_response, or
    autoplay's own always-yes auto-decision for an AI-controlled companion -
    see _autoplay_non_human_turns's catch, which has no human to ask) and
    generates real narration for it via the narrator LLM directly - this
    resume path doesn't go through the full graph (intent_parser/rules_engine
    already ran once for the original probe; only the mechanical finish and
    the narration for it are still needed), so narrator_node is called
    standalone rather than re-invoking the whole compiled graph."""
    events_before = len(session.game_state.events)
    srd = session.srd or load_srd()
    resolve_pending_bardic_choice(session.game_state, choice, use_die, session.action_rng, srd)
    narrator_state: GraphState = {
        "game_state": session.game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": events_before,
        "round_before": session.game_state.round,
        "narration": None,
        "scene_image_url": None,
    }
    return str(narrator_node(narrator_state)["narration"])


def _state_update_message(session: Session) -> dict[str, object]:
    return {
        "type": "state_update",
        "game_state": session.game_state.model_dump(mode="json"),
        "combat_summaries": _combat_summaries(session),
    }


async def _autoplay_non_human_turns(session: Session) -> None:
    """Resolves every actor's turn up to (not including) the next human one -
    companions via player_agent_node (empty raw_text/parsed_action, the same
    trigger cli.play.run_autoplay uses), monsters via the deterministic
    monster_ai heuristic. No-ops immediately for a session with no
    human_character_ids set (the demo-encounter fallback and every offline
    test that calls create_session() directly), so this can be called
    unconditionally from both connect and after every human action.

    Same consecutive_invalid circuit breaker as run_autoplay, and for the
    same reason: a persona-driven companion turn can still occasionally fail
    to parse into a concrete action even after the Day 15 prompt fix, and a
    live WebSocket connection has no autoplay script wrapping it to bail out
    - without this, that failure mode would hang the session forever instead
    of just wasting a few turns.
    """
    if not session.human_character_ids:
        return
    human_ids = session.human_character_ids.values()

    consecutive_invalid = 0
    while session.game_state.status == "in_progress":
        current_actor_id = session.game_state.turn_order[session.game_state.current_turn]
        actor = session.game_state.characters[current_actor_id]

        if current_actor_id in human_ids:
            if actor.hp <= 0 and not actor.is_dead:
                # Same shortcut player_agent_node already has for an
                # unconscious companion (src/graph/nodes/player_agent.py) -
                # a death save is an automatic roll, not a real decision, so
                # there's nothing meaningful for a human to type either.
                # Once stable, _advance_turn_skipping_dead's own is_stable
                # check keeps the turn pointer from ever landing here again,
                # so this only ever fires while still actively rolling.
                parsed_action = ParsedAction(
                    actor=current_actor_id,
                    verb="death_save",
                    raw_text=f"{actor.name} fights to stay conscious.",
                )
            else:
                return
        elif consecutive_invalid >= 3:
            # Applies to monsters too, not just companions - found live as
            # a real infinite loop once turn_engine started enforcing
            # attack range: choose_monster_action's own path-finding can
            # still fail to close the distance (blocked, or out of speed),
            # and with nothing ever mutating game_state, this while loop
            # never terminated on its own before this fallback existed.
            parsed_action = ParsedAction(
                actor=current_actor_id,
                verb="end_turn",
                raw_text="(forced end_turn after repeated invalid actions)",
            )
        elif not actor.is_pc:
            parsed_action = choose_monster_action(session.game_state, actor)
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
        except BardicChoicePending as exc:
            # Issue #53: an AI-controlled companion (not a human) can hold a
            # banked die too - there's no one to ask during autoplay, so it
            # auto-decides yes, exactly reproducing this project's own
            # pre-#53 behavior (the die always got applied to a miss
            # unconditionally). Only a real human's own turn (see
            # _handle_client_message) actually pauses to ask.
            narration = _resolve_and_narrate_bardic_choice(session, exc.choice, True)
            await _broadcast(session, {"type": "narration", "text": narration})
            await _broadcast(session, _state_update_message(session))
            consecutive_invalid = 0
            continue
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


async def _advance_chain_from(session: Session, start_scene: Scene) -> None:
    """Walks the campaign scene chain from start_scene (inclusive) via
    campaign_runner.advance_to_next_encounter, narrating/resolving skill_
    challenge and rest scenes along the way, until it reaches something that
    needs a live pause - a combat scene (builds a fresh GameState and
    resumes the turn loop) or a party_choice scene (see _start_party_choice)
    - or runs off the end of the chain (campaign complete, nothing further
    to do). Shared by _advance_campaign_after_victory (the player's own
    explicit "continue" after a victory) and _resolve_party_choice (once a
    party_choice's synthesis narration is broadcast) - both need the
    identical "keep walking the data until something needs a live session"
    loop, now that there are two different kinds of thing to pause for
    instead of just combat.

    Recurses rather than loops when a freshly-built encounter resolves in
    victory on its own (companions alone can sometimes finish a trivial
    fight before any human turn comes up) - reproduces this project's
    pre-Phase-2 while-loop behavior exactly, just restructured so hitting a
    party_choice mid-chain doesn't need a second copy of the walk."""
    assert session.campaign is not None
    assert session.party is not None
    assert session.srd is not None

    stop_scene, narration = advance_to_next_encounter(
        session.campaign, start_scene, session.party, session.srd, session.action_rng
    )
    for line in narration:
        await _broadcast(session, {"type": "scene_narration", "text": line})

    if stop_scene is None:
        return  # ran off the end of the chain - campaign complete

    if stop_scene.type == "party_choice":
        await _start_party_choice(session, stop_scene)
        return

    assert stop_scene.encounter_ref is not None  # guaranteed by Scene.type == "combat"
    encounter = load_encounter(stop_scene.encounter_ref)
    # Reuses the same party objects, not fresh copies - HP/conditions/
    # inventory genuinely carry over between fights, matching run_autoplay;
    # build_encounter_state only overwrites position and re-rolls initiative.
    session.game_state = build_encounter_state(
        encounter, session.party, session.action_rng, srd=session.srd
    )
    session.current_scene_id = stop_scene.id
    await _broadcast(session, _state_update_message(session))
    # A new encounter can itself open on a non-human turn (e.g. a monster
    # winning initiative) - resolve those before anyone's told it's their
    # turn, same reasoning as the very first connect.
    await _autoplay_non_human_turns(session)

    if session.game_state.status == "victory":
        next_scene = session.campaign.next_scene(stop_scene)
        if next_scene is not None:
            await _advance_chain_from(session, next_scene)
        # else: campaign complete - a real stop, nothing further to chain to.


async def _start_party_choice(session: Session, scene: Scene) -> None:
    """Story-adaptive-encounters Phase 2: reached when campaign_runner's
    chain-walk stops at a party_choice scene instead of the usual combat/
    end-of-chain. Unlike every other scene type this module's chain-walk
    resolves, a party_choice scene has no deterministic resolution of its
    own - its whole point is asking the table what THEY do about the
    situation its narrative_intro (already broadcast, by the caller's own
    narration loop) just posed.

    Every companion contributes its own in-character response immediately,
    synchronously (a plain free-text LLM call, not a real ParsedAction - a
    narrative choice isn't a mechanical turn, so player_agent_node's own
    combat-shaped prompt is the wrong tool here; see generate_companion_
    party_choice_response's own narrower prompt) - only the human seats are
    genuinely left pending. session.game_state is left completely untouched
    (still whatever it already was - the last-resolved encounter): a
    party_choice pause needs no GameState of its own, only the roster
    already on session.party and pending_party_choice for what's being
    collected."""
    assert session.party is not None

    responses: dict[str, str] = {}
    for character in session.party:
        if character.is_companion and not character.is_dead:
            responses[character.id] = generate_companion_party_choice_response(
                character, scene.narrative_intro
            )

    living_human_ids = {
        cid
        for cid in session.human_character_ids.values()
        if not any(c.id == cid and c.is_dead for c in session.party)
    }
    session.pending_party_choice = PendingPartyChoice(
        scene_id=scene.id, situation=scene.narrative_intro, responses=responses
    )
    await _broadcast(
        session,
        {
            "type": "party_choice_offer",
            "situation": scene.narrative_intro,
            "companion_responses": responses,
            "awaiting": sorted(living_human_ids),
        },
    )
    if not living_human_ids:
        # No human seat to actually ask - shouldn't happen in a real lobby/
        # single-shot session (which always has at least one), but a
        # defensive fallback so a theoretical all-companion party doesn't
        # hang forever waiting on a response nobody can ever send.
        await _resolve_party_choice(session)


async def _resolve_party_choice(session: Session) -> None:
    """Fires once every living human party member has submitted their
    party_choice_response (see _handle_client_message) - synthesizes one
    continuation narration considering every party member's stated input
    (companions' already-generated responses plus the humans' just-
    submitted ones), broadcasts it as scene_narration (reads like the next
    beat of the DM's own scene-setting text, not a mechanical action's
    outcome - the same distinction scene_narration's own frontend styling
    already established), and resumes the chain from wherever this scene's
    next_scene_id points. Not a branch: the scene chain itself stays fixed
    either way (real model-led branching is Phase 3's job) - this phase's
    whole scope is giving the party a real voice in how the story is TOLD,
    not yet in where it GOES."""
    pending = session.pending_party_choice
    assert pending is not None
    assert session.campaign is not None
    assert session.party is not None
    session.pending_party_choice = None

    narration = synthesize_party_choice_narration(
        pending.situation, pending.responses, session.party
    )
    await _broadcast(session, {"type": "scene_narration", "text": narration})

    scene = session.campaign.scene_by_id(pending.scene_id)
    next_scene = session.campaign.next_scene(scene)
    if next_scene is None:
        return  # campaign complete - no further scenes authored
    await _advance_chain_from(session, next_scene)


async def _advance_campaign_after_victory(session: Session) -> None:
    """After a combat scene resolves in victory (not defeat), continues the
    campaign's scene chain live: narrates any narrative_beat/skill_challenge
    scenes between the finished encounter and whatever comes next to every
    connection, then either drops the session into a fresh GameState for
    the next encounter or pauses for party input (see _advance_chain_from)
    - the same chaining cli.play.run_autoplay already does for autoplay
    (minus party_choice, which only exists in a live session), now live.
    No-ops immediately for a session with no campaign set (the demo-
    encounter fallback and every offline test that calls create_session()
    directly), or for one not actually at a "victory" stop.

    Issue #28: no longer called automatically the instant status becomes
    "victory" - a party that wants to rest between encounters needs a real,
    reachable pause to do it in, and this chain used to run synchronously
    within the very same server-side turn that produced the victory, before
    the client could ever see (let alone act on) the intermediate state. Now
    only reached via an explicit "continue_campaign" client message (see
    _handle_client_message), so "victory" is a real stop the player chooses
    to leave, with _handle_rest_request available to them first.

    Story-adaptive-encounters Phase 2: also no-ops while a party_choice is
    already pending - game_state.status stays "victory" for that pause's
    whole duration (party_choice never touches it), so a second
    continue_campaign click (or a stale one from a desynced client) would
    otherwise re-run this whole walk from session.current_scene_id, which
    is still the just-finished combat scene - re-triggering the SAME rest/
    party_choice scenes again and silently overwriting whatever responses
    were already collected in session.pending_party_choice."""
    if (
        session.campaign is None
        or session.party is None
        or session.srd is None
        or session.current_scene_id is None
        or session.game_state.status != "victory"
        or session.pending_party_choice is not None
    ):
        return

    current_scene = session.campaign.scene_by_id(session.current_scene_id)
    next_scene = session.campaign.next_scene(current_scene)
    if next_scene is None:
        return  # campaign complete - no further scenes authored
    await _advance_chain_from(session, next_scene)


async def _handle_rest_request(session: Session, rest_type: str) -> None:
    """Issue #28: a party-wide short/long rest, requested outside the
    turn-based action pipeline entirely - unlike every verb turn_engine
    resolves, a rest isn't one actor's turn, it acts on the whole party at
    once, so it doesn't go anywhere near ParsedAction/resolve_action. Only
    reachable between encounters (status == "victory", the real stop point
    _advance_campaign_after_victory no longer auto-leaves - see its own
    docstring), not mid-combat and not after a defeat/abort there's nothing
    left to rest for.

    Mutates session.party's Character objects in place (apply_short_rest/
    apply_long_rest's existing contract) - the exact same objects already
    referenced by session.game_state.characters (build_encounter_state
    reuses party objects, never copies them), so the very next
    state_update already reflects the recovered HP/slots/class_resources
    with no extra wiring needed.

    Story-adaptive-encounters Phase 2: also rejected while a party_choice is
    pending - session.game_state.status stays "victory" for the whole
    duration of that pause (party_choice never touches it, by design), so
    without this check a rest request would have gone through mid-
    conversation, narratively out of place, alongside whatever the party is
    still deciding."""
    if (
        session.party is None
        or session.game_state.status != "victory"
        or session.pending_party_choice is not None
    ):
        await _broadcast(
            session,
            {"type": "error", "detail": "The party can only rest between encounters."},
        )
        return

    if rest_type == "short":
        apply_short_rest(session.party, session.action_rng)
        narration = "The party takes a short rest, tending wounds and catching their breath."
    elif rest_type == "long":
        apply_long_rest(session.party)
        narration = "The party makes camp and takes a long rest, waking refreshed."
    else:
        await _broadcast(session, {"type": "error", "detail": f"Unknown rest type: {rest_type!r}"})
        return

    await _broadcast(session, {"type": "narration", "text": narration})
    await _broadcast(session, _state_update_message(session))


async def _handle_client_message(
    session: Session, websocket: WebSocket, connection: SessionConnection, raw: dict[str, object]
) -> None:
    msg_type = raw.get("type")

    if msg_type == "rest":
        await _handle_rest_request(session, str(raw.get("rest_type", "")))
        return
    if msg_type == "continue_campaign":
        # Explicit, player-chosen continuation of a "victory" stop - see
        # _advance_campaign_after_victory's own docstring for why this is no
        # longer automatic. No-ops harmlessly if status isn't "victory"
        # (e.g. a stale double-click) or there's no campaign to chain into.
        await _advance_campaign_after_victory(session)
        await _autoplay_non_human_turns(session)
        await _send_awaiting_input(session)
        return

    if msg_type == "party_choice_response":
        # Story-adaptive-encounters Phase 2: a real party member's own
        # free-text input toward a live party_choice pause (see
        # _start_party_choice/_resolve_party_choice). No character_id in
        # the payload - derived from whichever human seat this connection
        # controls that hasn't already responded, the same "derive the
        # actor from server state, never from what the client claims"
        # discipline bardic_inspiration_response already applies just below
        # (and a real session's connection only ever controls a single
        # human seat anyway - see session_websocket's own token handling).
        pending_choice = session.pending_party_choice
        if pending_choice is None:
            await websocket.send_json(
                {"type": "error", "detail": "No party choice is currently pending."}
            )
            return
        human_ids = set(session.human_character_ids.values())
        answerable = (
            connection.controlled_character_ids & human_ids - pending_choice.responses.keys()
        )
        if not answerable:
            await websocket.send_json(
                {"type": "error", "detail": "You have no pending party choice to answer."}
            )
            return
        character_id = next(iter(answerable))
        text = str(raw.get("text", "")).strip() or "(says nothing)"
        pending_choice.responses[character_id] = text
        await _broadcast(
            session, {"type": "party_choice_responded", "actor": character_id, "text": text}
        )

        living_human_ids = {
            cid
            for cid in human_ids
            if not any(c.id == cid and c.is_dead for c in (session.party or []))
        }
        if living_human_ids <= pending_choice.responses.keys():
            await _resolve_party_choice(session)
            await _autoplay_non_human_turns(session)
            await _send_awaiting_input(session)
        return

    if msg_type == "bardic_inspiration_response":
        # Issue #53: resumes the single attack roll paused by a
        # bardic_inspiration_offer (see the BardicChoicePending catch
        # below). Rejected if nothing is actually pending (a stale/repeated
        # click) or this connection doesn't control the holder - the same
        # "derive the actor from server state, not from what the client
        # claims" discipline the turn-ownership check just above already
        # applies to ordinary actions.
        pending = session.pending_bardic_choice
        if pending is None:
            await websocket.send_json(
                {"type": "error", "detail": "No Bardic Inspiration choice is pending."}
            )
            return
        if pending.holder_id not in connection.controlled_character_ids:
            await websocket.send_json(
                {"type": "error", "detail": "It is not your Bardic Inspiration choice to make."}
            )
            return
        session.pending_bardic_choice = None
        # resolve_pending_bardic_choice replicates resolve_action's own
        # victory-check/turn-advance tail itself (see its own docstring for
        # why - the original attempt never reached that tail, aborted early
        # by BardicChoicePending), so game_state is already fully caught up
        # by the time this broadcasts - no extra turn-advance logic needed
        # here, same as the ordinary action loop below.
        narration = _resolve_and_narrate_bardic_choice(session, pending, bool(raw.get("use")))
        await _broadcast(session, {"type": "narration", "text": narration})
        await _broadcast(session, _state_update_message(session))
        await _autoplay_non_human_turns(session)
        await _send_awaiting_input(session)
        return

    if msg_type in ("player_action", "player_move") and session.game_state.status == "in_progress":
        # Issue #44: with 2+ human seats now possible, nothing before this
        # stopped one player's connection from submitting an action for
        # whoever's turn it currently is, regardless of which character(s)
        # this connection was actually issued a token for - intent_parser
        # and the move-message builder below both derive the acting
        # character purely from "whoever's turn it is," never from anything
        # the client claims about itself. debug_action is deliberately
        # exempt - it's the explicit, already-gated (#41) full-override tool,
        # not a real player action.
        current_actor = session.game_state.turn_order[session.game_state.current_turn]
        if current_actor not in connection.controlled_character_ids:
            await websocket.send_json(
                {"type": "error", "detail": f"It is not your turn ({current_actor} is acting)."}
            )
            return

    actions_to_resolve: list[ParsedAction]

    if msg_type == "player_action":
        # Issue #47: a single utterance can describe more than one action
        # ("I rage, move to the wolf, and attack it") - parsed once, upfront,
        # into an ordered list; the loop below resolves each one through the
        # graph exactly like a single debug_action would (parsed_action
        # pre-supplied, so intent_parser_node's own LLM call is skipped for
        # every one of these), stopping early once the actor's turn actually
        # ends or a sub-action fails.
        parse_state: GraphState = {
            "game_state": session.game_state,
            "raw_text": str(raw["text"]),
            "parsed_action": None,
            "events_before": len(session.game_state.events),
            "round_before": session.game_state.round,
            "narration": None,
            "scene_image_url": None,
        }
        actions_to_resolve = parse_intent_sequence(parse_state)
    elif msg_type == "player_move":
        current_actor = session.game_state.turn_order[session.game_state.current_turn]
        actions_to_resolve = [
            ParsedAction(
                actor=current_actor,
                verb="move",
                params={"path": [raw["to"]]},
                raw_text="click-to-move",
            )
        ]
    elif msg_type == "debug_action":
        # Test/dev only: injects a fully-formed ParsedAction directly,
        # bypassing intent_parser's LLM call entirely (see its pre-supplied-
        # parsed_action escape hatch). Never sent by the real frontend - and,
        # since it also bypasses any check that the sender controls the
        # named actor, a real backdoor if left reachable from the internet
        # (issue #41). Gated behind an explicit, off-by-default env flag
        # rather than a silent no-op, so a client relying on it in dev gets
        # a clear error instead of a mysteriously-ignored message.
        if not config.ALLOW_DEBUG_ACTIONS:
            detail = "debug_action is disabled (set ALLOW_DEBUG_ACTIONS=1 to enable it)."
            await websocket.send_json({"type": "error", "detail": detail})
            # Same Day-19 lesson as the TurnEngineError path below: without
            # this, the frontend's own optimistic "it's my turn" flag never
            # gets restored, leaving the input looking stuck even though the
            # turn never actually moved.
            await _send_awaiting_input(session)
            return
        actions_to_resolve = [ParsedAction.model_validate(raw["action"])]
    else:
        return

    expected_actor_id = (
        session.game_state.turn_order[session.game_state.current_turn]
        if session.game_state.status == "in_progress"
        else None
    )

    for action in actions_to_resolve:
        # Issue #43: player_action/player_move/debug_action are the only
        # message types that reach session.graph.invoke below, which always
        # runs a real narrator LLM call (and, for player_action, a real
        # intent_parser LLM call too) - rest/continue_campaign never touch
        # the graph at all (campaign_runner is pure/deterministic), so
        # they're deliberately not rate-limited. Issue #47: charged per
        # sub-action actually resolved, not once per WS message - a
        # multi-action utterance genuinely triggers that many narrator/
        # scene_image calls, so bundling everything into one sentence isn't
        # a way to dodge the per-turn budget.
        if not session.action_bucket.try_consume():
            await websocket.send_json(
                {"type": "error", "detail": "Too many actions too quickly - please slow down."}
            )
            break

        graph_input: GraphState = {
            "game_state": session.game_state,
            "raw_text": "",
            "parsed_action": action,
            "events_before": len(session.game_state.events),
            "round_before": session.game_state.round,
            "narration": None,
            "scene_image_url": None,
        }
        try:
            result = session.graph.invoke(graph_input)
        except BardicChoicePending as exc:
            # Issue #53: this single attack roll would miss without the
            # holder's banked Bardic Inspiration die - pause here instead of
            # finalizing, and stay paused (skip the trailing autoplay/
            # awaiting_input below entirely via this early return) until a
            # bardic_inspiration_response message resumes it. A multi-action
            # utterance's remaining sub-actions are dropped the same way an
            # ordinary rejection already drops them (issue #47) - whatever
            # already resolved stands.
            session.pending_bardic_choice = exc.choice
            await _broadcast(
                session,
                {
                    "type": "bardic_inspiration_offer",
                    "holder": exc.choice.holder_id,
                    "target": exc.choice.target_id,
                    "natural": exc.choice.natural,
                    "total_without_die": exc.choice.total_without_die,
                    "defender_ac": exc.choice.defender_ac,
                    "die_sides": exc.choice.die_sides,
                },
            )
            return
        except (TurnEngineError, NotImplementedError) as exc:
            # Caught live, two real cases: (1) a free-text action can name a
            # real-looking but invalid item/spell (e.g. the LLM extracting
            # "dagger" as item_or_spell for a monster attack, whose SRD stat
            # block has no such action) - TurnEngineError. (2) the intent-
            # parser prompt describes verbs (dodge, cast_spell, skill_check,
            # help, use_item, death_save) that turn_engine.resolve_action
            # doesn't actually implement yet (Day 7 only built attack/move/
            # dash/end_turn) - NotImplementedError. Both validate-then-raise
            # before mutating state, so it's safe to just report the error
            # back to whoever sent it and let them try again, rather than
            # crashing the whole connection. A multi-action sequence stops
            # here (issue #47) - whatever already resolved stands, the rest
            # of this one utterance is simply not attempted.
            await websocket.send_json({"type": "error", "detail": str(exc)})
            break

        session.game_state = result["game_state"]

        await _broadcast(session, {"type": "narration", "text": result["narration"]})
        await _broadcast(session, _state_update_message(session))
        if result["scene_image_url"]:
            await _broadcast(session, {"type": "scene_image", "url": result["scene_image_url"]})

        # Issue #47: stop resolving further sub-actions the moment the
        # actor's turn has actually ended (resolve_action's own per-verb
        # ends_turn signal already advanced current_turn if so) - a bonus
        # action or a move genuinely leaves the turn open for more (a
        # rage-then-attack utterance both resolve here), but nothing after
        # a real action (or after victory/defeat) is still legal to attempt.
        if session.game_state.status != "in_progress":
            break
        current_actor_now = session.game_state.turn_order[session.game_state.current_turn]
        if current_actor_now != expected_actor_id:
            break

    # Found live (Day 19): without this, the turn correctly stays with the
    # same actor whenever nothing mutated (a rejected first sub-action, or
    # the rate limit), but the client that just tried was never told it's
    # still their turn - the frontend optimistically clears its own "it's
    # my turn" state the moment it sends anything (so it can't be submitted
    # twice while waiting), and had nothing to restore it, leaving the
    # input disabled forever even though a retry would work.
    await _autoplay_non_human_turns(session)
    await _send_awaiting_input(session)


@router.websocket("/ws/session/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    # Issue #42: the WS equivalent of main.py's _require_passphrase HTTP
    # middleware - a plain WebSocket connection from browser JS can't set a
    # custom header, so this reads a `key` query param instead. Rejected
    # *before* accept() so the handshake itself fails rather than opening a
    # connection just to immediately close it - Starlette's WebSocket.close()
    # is valid to call pre-accept (confirmed by reading its source: it just
    # sends a "websocket.close" ASGI event, which is a legal response to the
    # initial "websocket.connect" event per the ASGI spec).
    passphrase = config.SHARED_ACCESS_PASSPHRASE
    if passphrase and websocket.query_params.get("key") != passphrase:
        await websocket.close(code=4401, reason="missing or incorrect passphrase")
        return

    # Issue #43: a hard cap on how many *distinct* sessions may have a live
    # connection at once - the actual shared resource is the one local GPU/
    # Ollama instance behind every session's narrator/scene_image calls, not
    # this endpoint itself. Joining a session that's already active (another
    # connection already open on it - the multiplayer case) never counts
    # against the cap; only opening a *new* one does.
    existing = _sessions.get(session_id)
    already_active = existing is not None and len(existing.connections) > 0
    if not already_active:
        active_count = sum(1 for s in _sessions.values() if s.connections)
        if active_count >= config.MAX_CONCURRENT_SESSIONS:
            await websocket.close(code=4429, reason="too many concurrent sessions - try again soon")
            return

    await websocket.accept()
    try:
        session = _get_or_create_default_session(session_id)
    except GameStateBuildError as exc:
        # Found live (issue #29): a content bug (an encounter's
        # party_spawn_points shorter than the actual party size, in
        # practice) used to propagate straight out of this handler and crash
        # the whole ASGI connection before create_session ever ran - the
        # client got no message at all and sat on "Connecting..." forever,
        # unable to tell a content bug apart from a slow or dead server.
        # Report it and close cleanly instead.
        await websocket.send_json({"type": "error", "detail": str(exc)})
        await websocket.close(code=1011, reason="session setup failed")
        return

    if not session.human_character_ids:
        # Demo-encounter fallback / a direct create_session() call with no
        # human_character_ids at all - unchanged Day-11 behavior, whoever
        # connects controls everyone.
        controlled = set(session.game_state.characters.keys())
    else:
        token = websocket.query_params.get("token")
        if token is not None and token in session.human_character_ids:
            controlled = {session.human_character_ids[token]}
        elif token is None and len(session.human_character_ids) == 1:
            # Issue #44: a single human seat (the legacy single-shot POST
            # /sessions flow, or a lobby exactly one player ever joined)
            # doesn't need a token to disambiguate between players who
            # aren't there - the pre-#44 frontend never sends ?token= at
            # all, so this keeps every existing single-player session
            # working with zero frontend changes.
            controlled = {next(iter(session.human_character_ids.values()))}
        else:
            # 2+ human seats and no token, or a token that doesn't match any
            # of them - this connection can't be identified as any specific
            # player, so it can't be let in as one. Accepted above already
            # (the token can only be checked once the real session exists,
            # unlike #42's passphrase check), so reported and closed the
            # same way a GameStateBuildError is just above.
            await websocket.send_json(
                {
                    "type": "error",
                    "detail": "Missing or unrecognized player token for this session.",
                }
            )
            await websocket.close(code=4401, reason="missing or unrecognized player token")
            return
    connection = SessionConnection(websocket=websocket, controlled_character_ids=controlled)
    session.connections.append(connection)

    if session.human_character_ids:
        # Issue #46: "reconnected" is only meaningful set against a real
        # human seat that has genuinely dropped and come back, not this
        # character's very first-ever connect (nothing to call a "re" on) -
        # see Session.connected_/ever_connected_human_character_ids' own
        # docstrings for why both sets exist.
        human_ids = set(session.human_character_ids.values())
        reconnected = (
            controlled & human_ids & session.ever_connected_human_character_ids
        ) - session.connected_human_character_ids
        session.connected_human_character_ids |= controlled & human_ids
        session.ever_connected_human_character_ids |= controlled & human_ids
        for actor in reconnected:
            await _broadcast(session, {"type": "player_reconnected", "actor": actor})

    try:
        # The campaign's own scene-setting text (a narrative "hook" before
        # the fight, etc.) - collected once at session setup, sent to
        # whichever connection arrives first. See Session.pending_scene_
        # narration's docstring for why a second connection won't see it.
        for line in session.pending_scene_narration:
            await websocket.send_json({"type": "scene_narration", "text": line})
        session.pending_scene_narration = []

        # Resolve any monster/companion turns that come before the human's
        # first one (e.g. a monster going first in initiative) before this
        # connection's own initial state_update, so it opens on a state the
        # human can actually act on rather than one that's already stale.
        await _autoplay_non_human_turns(session)
        # Rare, but possible: companions alone finish the first encounter
        # before the human ever acts - status is "victory" already on this
        # connection's first state_update. That's fine: issue #28 made
        # "victory" a real stop the player leaves via an explicit
        # continue_campaign message (or rests first), not something this
        # connect path should silently skip past.
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
            await _handle_client_message(session, websocket, connection, raw)
    except WebSocketDisconnect:
        pass
    finally:
        # Guarded, not unconditional: a broadcast send failing earlier in
        # this same connection's lifetime (see _broadcast/_send_awaiting_
        # input's own pruning) may have already removed it from the list.
        if connection in session.connections:
            session.connections.remove(connection)
        if session.human_character_ids:
            still_connected = {
                cid for c in session.connections for cid in c.controlled_character_ids
            }
            newly_disconnected = connection.controlled_character_ids - still_connected
            session.connected_human_character_ids -= newly_disconnected
            for actor in newly_disconnected:
                await _broadcast(session, {"type": "player_disconnected", "actor": actor})

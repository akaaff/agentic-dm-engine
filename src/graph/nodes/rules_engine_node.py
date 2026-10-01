"""Wraps turn_engine.resolve_action (Day 7) as a graph node - real, not a
stub. rng/srd are captured via closure at graph-build time (make_node),
since LangGraph node functions only receive the graph state, not arbitrary
extra arguments."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from src.engine.actions import ParsedAction
from src.engine.monster_ai import build_move_toward_target
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import GameState
from src.engine.turn_engine import AttackOutOfRangeError, TurnEngineError, resolve_action
from src.graph.state_schema import GraphState
from src.observability.mechanics_log import log_mechanic
from src.training.failed_intents import log_failed_intent


def _redirect_out_of_range(
    game_state: GameState, action: ParsedAction, exc: AttackOutOfRangeError
) -> ParsedAction | None:
    """Live-found: a companion repeated the identical out-of-range attack
    ("I swing my battleaxe at kobold_1", 25ft away) 3 times in a row,
    never once trying to move first - player_agent_node has no notion of
    weapon/spell range at all, so nothing ever told it to close the
    distance instead of retrying the same declaration. Rather than lean on
    prompt wording alone (probabilistic, and this specific failure mode
    already burned 3 real turns before the circuit breaker forced an
    end_turn), redirect deterministically: any attack/cast a companion's
    own free text produced that turn_engine rejected purely for range gets
    turned into an actual move toward that same target instead of just
    failing. Scoped to companions only - a human's own explicit "attack"
    declaration should still surface an honest rejection, not silently
    become a move they didn't ask for; a monster never reaches this path
    at all (choose_monster_action already checks range itself before ever
    declaring an attack).

    Returns None (nothing to redirect to) when the actor isn't a companion,
    the target no longer exists, or build_move_toward_target itself can't
    find a real path (already adjacent some other way, blocked, or out of
    movement budget this turn) - the caller falls back to the original
    rejection in every one of those cases."""
    actor = game_state.characters.get(exc.actor_id)
    target = game_state.characters.get(exc.target_id)
    if actor is None or not actor.is_companion or target is None:
        return None
    return build_move_toward_target(game_state, actor, target)


def _log_rejected(state: GraphState, action: ParsedAction, exc: TurnEngineError) -> None:
    # A syntactically fine ParsedAction (intent_parser_node's own
    # "unparseable" check already ran and didn't fire) that turn_engine's
    # own legality checks still rejected - a different, complementary kind
    # of real hard case for src.training.failed_intents (a hallucinated
    # target/weapon, wrong actor, malformed path... not always a parser
    # mistake - the utterance itself may genuinely describe something
    # illegal - left for the human reviewer to tell apart, not guessed
    # here). Only logged when this action actually came from free text
    # (raw_text set) - debug_action/scripted/monster-AI actions never set
    # it, and logging one of those would just be recording "the test's own
    # deliberately-illegal fixture failed," not a real hard case.
    if not state["raw_text"]:
        return
    actor = state["game_state"].characters.get(action.actor)
    if actor is None:
        return
    log_failed_intent(
        reason="rejected",
        actor=actor,
        raw_text=state["raw_text"],
        prompt=None,
        produced_action=action,
        detail=str(exc),
    )


def make_rules_engine_node(
    rng: random.Random, srd: SrdIndex | None = None
) -> Callable[[GraphState], dict[str, Any]]:
    srd = srd or load_srd()

    def rules_engine_node(state: GraphState) -> dict[str, Any]:
        action = state["parsed_action"]
        if action is None:
            raise TurnEngineError("rules_engine_node requires a parsed_action")
        game_state = state["game_state"]
        # resolve_action validates before ever mutating state (a documented
        # invariant throughout turn_engine.py), so a failed first attempt
        # below never adds events - capturing this once up front, before
        # either the normal happy path or the out-of-range redirect's own
        # resolve_action call, correctly covers both.
        events_before = len(game_state.events)
        try:
            resolve_action(game_state, action, rng, srd)
        except AttackOutOfRangeError as exc:
            redirect = _redirect_out_of_range(game_state, action, exc)
            if redirect is not None:
                resolve_action(game_state, redirect, rng, srd)
                for event in game_state.events[events_before:]:
                    log_mechanic(event, game_state)
                return {"game_state": game_state}
            _log_rejected(state, action, exc)
            raise
        except TurnEngineError as exc:
            _log_rejected(state, action, exc)
            raise
        for event in game_state.events[events_before:]:
            log_mechanic(event, game_state)
        return {"game_state": game_state}

    return rules_engine_node

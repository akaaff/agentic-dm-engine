"""Wraps turn_engine.resolve_action (Day 7) as a graph node - real, not a
stub. rng/srd are captured via closure at graph-build time (make_node),
since LangGraph node functions only receive the graph state, not arbitrary
extra arguments."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.turn_engine import TurnEngineError, resolve_action
from src.graph.state_schema import GraphState
from src.training.failed_intents import log_failed_intent


def make_rules_engine_node(
    rng: random.Random, srd: SrdIndex | None = None
) -> Callable[[GraphState], dict[str, Any]]:
    srd = srd or load_srd()

    def rules_engine_node(state: GraphState) -> dict[str, Any]:
        action = state["parsed_action"]
        if action is None:
            raise TurnEngineError("rules_engine_node requires a parsed_action")
        try:
            resolve_action(state["game_state"], action, rng, srd)
        except TurnEngineError as exc:
            # A syntactically fine ParsedAction (intent_parser_node's own
            # "unparseable" check above never fired) that turn_engine's own
            # legality checks still rejected - a different, complementary
            # kind of real hard case for src.training.failed_intents (a
            # hallucinated target/weapon, wrong actor, malformed path...
            # not always a parser mistake - the utterance itself may
            # genuinely describe something illegal - left for the human
            # reviewer to tell apart, not guessed here). Only logged when
            # this action actually came from free text (raw_text set) -
            # debug_action/scripted/monster-AI actions never set it, and
            # logging one of those would just be recording "the test's own
            # deliberately-illegal fixture failed," not a real hard case.
            if state["raw_text"]:
                actor = state["game_state"].characters.get(action.actor)
                if actor is not None:
                    log_failed_intent(
                        reason="rejected",
                        actor=actor,
                        raw_text=state["raw_text"],
                        prompt=None,
                        produced_action=action,
                        detail=str(exc),
                    )
            raise
        return {"game_state": state["game_state"]}

    return rules_engine_node

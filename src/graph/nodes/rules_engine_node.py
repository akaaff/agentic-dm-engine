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


def make_rules_engine_node(
    rng: random.Random, srd: SrdIndex | None = None
) -> Callable[[GraphState], dict[str, Any]]:
    srd = srd or load_srd()

    def rules_engine_node(state: GraphState) -> dict[str, Any]:
        action = state["parsed_action"]
        if action is None:
            raise TurnEngineError("rules_engine_node requires a parsed_action")
        resolve_action(state["game_state"], action, rng, srd)
        return {"game_state": state["game_state"]}

    return rules_engine_node

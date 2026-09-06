"""Wires the per-action turn pipeline: player_agent -> intent_parser ->
rules_engine -> narrator -> scene_image. Only rules_engine is real as of Day
11 - the other four are stubs (see graph/nodes/*.py) until Days 12-15
replace them with live Ollama/SD-Turbo calls, at which point this wiring
shouldn't need to change at all.

player_agent (Day 15) sits in front of intent_parser rather than replacing
it: it only fills in `raw_text` for a companion's empty turn, then the same
LLM-backed intent_parser already used for human free text parses that
utterance into a ParsedAction. Every other actor (human, monster) reaches
intent_parser exactly as before, since player_agent_node no-ops for them.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.engine.srd_loader import SrdIndex, load_srd
from src.graph.nodes.intent_parser import intent_parser_node
from src.graph.nodes.narrator import narrator_node
from src.graph.nodes.player_agent import player_agent_node
from src.graph.nodes.rules_engine_node import make_rules_engine_node
from src.graph.nodes.scene_image_node import scene_image_node
from src.graph.state_schema import GraphState


def build_graph(
    rng: random.Random | None = None,
    srd: SrdIndex | None = None,
    narrator_fn: Callable[[GraphState], dict[str, Any]] = narrator_node,
    player_agent_fn: Callable[[GraphState], dict[str, Any]] = player_agent_node,
    scene_image_fn: Callable[[GraphState], dict[str, Any]] = scene_image_node,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """narrator_fn/player_agent_fn/scene_image_fn are overridable for tests:
    unlike intent_parser (which already skips its own LLM call whenever
    parsed_action is pre-supplied - see graph/nodes/intent_parser.py), none
    of the three has an equivalent "skip if already set" escape hatch
    reachable from outside - narrator produces narration, player_agent
    produces raw_text, scene_image produces an image file, and Day 16 made
    the last of these real too (a local SD-Turbo call). Offline tests that
    need to avoid a real Ollama/SD-Turbo call swap in a stub here - the
    default offline suite always does for scene_image_fn (see
    tests/unit/test_ws_session.py), since letting it run for real would
    download and load SD-Turbo on every offline test run.
    """
    rng = rng or random.Random()
    srd = srd or load_srd()

    graph = StateGraph(GraphState)
    # intent_parser (a concretely-typed top-level function) must be added
    # before either Callable-parameter node below - it's what lets mypy bind
    # NodeInputT=GraphState for this StateGraph at all; adding a generic
    # Callable[[GraphState], dict[str, Any]] parameter first leaves it
    # unable to pick an overload (a full call-overload error, not just an
    # arg-type mismatch the usual ignore comment covers). Node add order has
    # no effect on graph topology - only add_edge below does.
    graph.add_node("intent_parser", intent_parser_node)
    graph.add_node("player_agent", player_agent_fn)  # type: ignore[arg-type]
    # The closure's Callable[[GraphState], dict[str, Any]] type doesn't line
    # up with add_node's precise overload set the way a plain top-level
    # function reference does (mypy resolves those two shapes differently).
    graph.add_node("rules_engine", make_rules_engine_node(rng, srd))  # type: ignore[arg-type]
    graph.add_node("narrator", narrator_fn)  # type: ignore[arg-type]
    graph.add_node("scene_image", scene_image_fn)  # type: ignore[arg-type]

    graph.add_edge(START, "player_agent")
    graph.add_edge("player_agent", "intent_parser")
    graph.add_edge("intent_parser", "rules_engine")
    graph.add_edge("rules_engine", "narrator")
    graph.add_edge("narrator", "scene_image")
    graph.add_edge("scene_image", END)

    return graph.compile()

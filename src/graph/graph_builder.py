"""Wires the per-action turn pipeline: intent_parser -> rules_engine ->
narrator -> scene_image. Only rules_engine is real as of Day 11 - the other
three are stubs (see graph/nodes/*.py) until Days 12-14 replace them with
live Ollama/SD-Turbo calls, at which point this wiring shouldn't need to
change at all.
"""

from __future__ import annotations

import random
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.engine.srd_loader import SrdIndex, load_srd
from src.graph.nodes.intent_parser import intent_parser_node
from src.graph.nodes.narrator import narrator_node
from src.graph.nodes.rules_engine_node import make_rules_engine_node
from src.graph.nodes.scene_image_node import scene_image_node
from src.graph.state_schema import GraphState


def build_graph(
    rng: random.Random | None = None, srd: SrdIndex | None = None
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    rng = rng or random.Random()
    srd = srd or load_srd()

    graph = StateGraph(GraphState)
    graph.add_node("intent_parser", intent_parser_node)
    # The closure's Callable[[GraphState], dict[str, Any]] type doesn't line
    # up with add_node's precise overload set the way a plain top-level
    # function reference does (mypy resolves those two shapes differently).
    graph.add_node("rules_engine", make_rules_engine_node(rng, srd))  # type: ignore[arg-type]
    graph.add_node("narrator", narrator_node)
    graph.add_node("scene_image", scene_image_node)

    graph.add_edge(START, "intent_parser")
    graph.add_edge("intent_parser", "rules_engine")
    graph.add_edge("rules_engine", "narrator")
    graph.add_edge("narrator", "scene_image")
    graph.add_edge("scene_image", END)

    return graph.compile()

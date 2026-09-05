"""Day 11 stub - passes an already-supplied ParsedAction straight through
with zero parsing. Day 12 replaces this with a real Ollama call that reads
`raw_text` and produces `parsed_action`; until then, whatever calls this
graph (api/ws/session.py, tests) supplies parsed_action itself."""

from __future__ import annotations

from typing import Any

from src.graph.state_schema import GraphState


def intent_parser_node(state: GraphState) -> dict[str, Any]:
    return {"parsed_action": state["parsed_action"]}

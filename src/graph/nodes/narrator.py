"""Day 11 stub - a fixed placeholder, deliberately not a "reasonable-looking"
summary of the new events. Day 13 replaces this with a real Ollama call
turning newly-appended Events into prose; keeping the stub obviously fake
(rather than a plausible-looking templated summary) makes it unmistakable
in a transcript which day's code produced it."""

from __future__ import annotations

from typing import Any

from src.graph.state_schema import GraphState

_STUB_NARRATION = "[narration pending - Day 13]"


def narrator_node(state: GraphState) -> dict[str, Any]:
    return {"narration": _STUB_NARRATION}

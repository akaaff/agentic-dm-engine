"""Real as of Day 12 (moved up alongside intent_parser - both are plain
teacher-model text calls with no dependency on Day 13's companion-agent
work). Turns the Events this graph invocation actually produced into prose -
read-only, never mutates GameState."""

from __future__ import annotations

from typing import Any

from src.engine.events import Event
from src.graph.state_schema import GraphState
from src.llm.providers import chat, load_prompt


def _event_line(event: Event) -> str:
    return f"- actor={event.actor} type={event.type} payload={event.payload}"


def narrator_node(state: GraphState) -> dict[str, Any]:
    new_events = state["game_state"].events[state["events_before"] :]
    if not new_events:
        return {"narration": ""}

    events_summary = "\n".join(_event_line(e) for e in new_events)
    prompt = load_prompt("narrator").format(events_summary=events_summary)
    narration = chat(messages=[{"role": "user", "content": prompt}], temperature=0.7)
    return {"narration": narration.strip()}

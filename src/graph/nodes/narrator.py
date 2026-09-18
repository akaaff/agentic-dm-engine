"""Real as of Day 12 (moved up alongside intent_parser - both are plain
teacher-model text calls with no dependency on Day 13's companion-agent
work). Turns the Events this graph invocation actually produced into prose -
read-only, never mutates GameState."""

from __future__ import annotations

from typing import Any

from src.engine.events import Event
from src.graph.state_schema import GraphState
from src.llm.providers import chat_english_only, load_prompt


def _event_line(event: Event) -> str:
    return f"- actor={event.actor} type={event.type} payload={event.payload}"


def narrator_node(state: GraphState) -> dict[str, Any]:
    new_events = state["game_state"].events[state["events_before"] :]
    if not new_events:
        return {"narration": ""}

    events_summary = "\n".join(_event_line(e) for e in new_events)
    # Issue #33: a live report of the narrator inventing an unrelated
    # monster ("the drow's poison...") mid-fight against a wolves-only
    # encounter. Confirmed this isn't context accumulation across turns -
    # chat_english_only/chat send one stateless request per call (see
    # providers.chat's plain httpx.post, no conversation history kept
    # between calls), so a hallucination at round 12 can't be "bleed" from
    # something said many turns earlier - it never saw those calls. The
    # prompt already said "do not invent... characters... not listed
    # below," but that's a prohibition with nothing concrete to check
    # itself against; giving it the actual closed cast list (same
    # "anchor it to real data" pattern intent_parser's visible-characters
    # list already uses) is a stronger, more falsifiable grounding than a
    # generic instruction alone - not a guaranteed fix for a small model's
    # occasional hallucination, same honest framing as issue #16's CJK
    # mitigation.
    cast_names = ", ".join(sorted({c.name for c in state["game_state"].characters.values()}))
    prompt = load_prompt("narrator").format(events_summary=events_summary, cast_names=cast_names)
    narration = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.7)
    return {"narration": narration.strip()}

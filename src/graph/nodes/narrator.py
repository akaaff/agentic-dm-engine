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
    # Live-found: "actor" means the perpetrator for every other
    # damage-causing event (attack_roll/damage_dealt - actor hits target),
    # but hazard_damage inverts that (actor is the one who stepped on the
    # hazard and got hurt themselves, no attacker at all - see
    # turn_engine._apply_hazard_damage's own docstring). The model
    # apparently read "actor=qasz type=hazard_damage" the same way as a
    # combat event and invented a wolf bite to explain it, even though no
    # attack_roll event for any wolf was present. Spelling this one out
    # explicitly removes the ambiguity the generic "actor=.../type=..."
    # format otherwise leaves for the model to guess at.
    if event.type == "hazard_damage":
        return (
            f"- {event.actor} steps on hazardous terrain and takes "
            f"{event.payload.get('amount')} {event.payload.get('damage_type')} damage "
            "(environmental/terrain damage - not caused by any other character)"
        )
    return f"- actor={event.actor} type={event.type} payload={event.payload}"


def _fallback_narration(new_events: list[Event], state: GraphState) -> str:
    """Live-found: the model occasionally returns empty/whitespace-only
    text for a real event (confirmed live for a plain repositioning move
    with no combat significance) - and NarrationFeed.tsx renders nothing
    at all for an empty-text entry whose events also have no badge case
    (move events don't - see formatEvent.ts), so a blank response here
    isn't just a dropped sentence, it's a genuinely invisible log entry
    with no visible trace anything happened. This deterministic minimal
    fallback (not a second LLM call) guarantees the caller always has
    *something* to broadcast once narrator_node's own retries are
    exhausted - a plain, honest line naming the actor beats a silent gap."""
    actor_id = new_events[0].actor
    character = state["game_state"].characters.get(actor_id)
    name = character.name if character else actor_id
    return f"{name} acts."


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
    # Retried the same way chat_english_only already retries on a CJK leak
    # (issue #16) - a real independent sample, not a deterministic failure -
    # before falling back to _fallback_narration's deterministic minimum.
    for _ in range(3):
        raw = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.7)
        narration = raw.strip()
        if narration:
            return {"narration": narration}
    return {"narration": _fallback_narration(new_events, state)}

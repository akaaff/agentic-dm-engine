"""LangGraph state schema for the per-action turn pipeline:
intent_parser -> rules_engine -> narrator -> scene_image.

One graph invocation resolves exactly one action (one actor's turn) - the
outer loop of "wait for the next actor's input, invoke the graph, repeat"
lives in api/ws/session.py, not here.
"""

from __future__ import annotations

from typing import TypedDict

from src.engine.actions import ParsedAction
from src.engine.state import GameState


class GraphState(TypedDict):
    game_state: GameState
    raw_text: str
    """The actor's free-text input. Real intent parsing (LLM, Day 12) reads
    this; the Day 11 stub intent_parser ignores it in favor of
    parsed_action being pre-supplied (see graph/nodes/intent_parser.py)."""
    parsed_action: ParsedAction | None
    """If already set when the graph is invoked, intent_parser_node skips
    the LLM entirely and passes it through unchanged - the escape hatch
    scripted/offline tests use to exercise the graph deterministically."""
    events_before: int
    """len(game_state.events) captured by the caller right before this graph
    invocation - narrator_node uses it to find only the events this
    invocation actually produced."""
    round_before: int
    """game_state.round captured by the caller right before this graph
    invocation (Day 16) - scene_image_node compares it against the
    post-resolution round to detect a round boundary, since no event type
    actually marks one (EventType has "turn_started"/"turn_ended" entries,
    but nothing in turn_engine ever emits them)."""
    narration: str | None
    scene_image_url: str | None

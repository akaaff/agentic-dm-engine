"""ParsedAction - the structured output of intent parsing (Day 12+), and the
input the rules engine actually consumes. Also what scripted/autoplay tests
construct directly, bypassing the LLM entirely.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ActionVerb = Literal[
    "attack",
    "cast_spell",
    "move",
    "dash",
    "dodge",
    "disengage",
    "use_item",
    "skill_check",
    "death_save",
    "help",
    "end_turn",
    "invalid",
]


class ParsedAction(BaseModel):
    actor: str
    verb: ActionVerb
    target: str | None = None
    targets: list[str] | None = None
    item_or_spell: str | None = None
    params: dict[str, Any] = {}
    """Verb-specific extras. Convention: `move`/`dash` carry
    `params["move_to"] = {"x": int, "y": int}`; `skill_check` carries
    `params["skill"] = str`."""
    raw_text: str
    confidence: float | None = None
    """Set by the LLM intent parser; absent for scripted/hand-authored actions."""

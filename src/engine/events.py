"""Event - the append-only log the rules engine emits and the narrator reads.
The single source of truth for "what actually happened," independent of any
prose narration of it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "initiative_rolled",
    "turn_started",
    "turn_ended",
    "attack_roll",
    "damage_dealt",
    "saving_throw",
    "skill_check",
    "condition_applied",
    "condition_removed",
    "hp_change",
    "move",
    "spell_cast",
    "death",
    "action_invalid",
]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Event(BaseModel):
    id: str = Field(default_factory=_new_id)
    round: int
    turn_index: int
    actor: str
    type: EventType
    payload: dict[str, Any] = {}
    timestamp: str = Field(default_factory=_now_iso)
    narrated: bool = False

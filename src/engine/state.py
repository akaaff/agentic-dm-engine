"""GameState - the single object that flows through every LangGraph node
(Day 6+) and that the rules engine reads/mutates. Character and Condition
live here too since they're pure data, not behavior."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.engine.actions import ParsedAction
from src.engine.events import Event
from src.engine.position import Position

ConditionName = Literal[
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
    "exhaustion",
]

AbilityScore = Literal["STR", "DEX", "CON", "INT", "WIS", "CHA"]


class Condition(BaseModel):
    name: ConditionName
    duration_rounds: int | None = None
    """None means indefinite - removed by an explicit effect, not by ticking down."""
    source: str | None = None


class Character(BaseModel):
    id: str
    name: str
    is_pc: bool
    hp: int
    max_hp: int
    ac: int
    position: Position
    conditions: list[Condition] = []
    spell_slots: dict[int, int] = {}
    """Spell level -> slots remaining."""
    inventory: list[str] = []
    stats: dict[AbilityScore, int]
    proficiency_bonus: int
    speed: int
    race: str
    class_: str
    background: str
    is_companion: bool = False
    persona: str | None = None
    """Only set for simulated (companion/NPC) agents."""


class GameState(BaseModel):
    encounter_id: str
    characters: dict[str, Character]
    turn_order: list[str]
    current_turn: int
    round: int
    events: list[Event] = []
    pending_action: ParsedAction | None = None
    status: Literal["in_progress", "victory", "defeat", "aborted"] = "in_progress"

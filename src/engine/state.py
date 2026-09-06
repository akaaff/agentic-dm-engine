"""GameState - the single object that flows through every LangGraph node
(Day 6+) and that the rules engine reads/mutates. Character and Condition
live here too since they're pure data, not behavior."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.engine.actions import ParsedAction
from src.engine.events import Event
from src.engine.position import BattleMap, Position

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
    monster_index: str | None = None
    """Set only for monsters (see encounter.monster_to_character) - lets the
    turn engine re-look-up the SRD stat block's actions (attack bonus,
    damage dice) when this character attacks."""
    skill_proficiencies: list[str] = []
    """"skill-x" indices (same format as chosen_skills), populated by
    character_creation.py from chosen class skills + the background's fixed
    proficiencies - previously derived at creation time but never stored."""
    is_dodging: bool = False
    """True from resolving a "dodge" action until the start of this
    character's own next turn (cleared there, not by a fixed round count -
    it depends on whose turn it is next, which conditions.tick_conditions'
    once-per-round model doesn't represent)."""
    has_help_advantage: bool = False
    """Set by another character resolving "help" targeting this one;
    consumed (cleared) by this character's next attack or skill check,
    whichever comes first."""
    class_index: str | None = None
    """Set only for PCs/companions (mirrors monster_index) - lets the turn
    engine re-look-up the SRD class's spellcasting ability for cast_spell."""
    death_save_successes: int = 0
    death_save_failures: int = 0
    is_dead: bool = False
    """Terminal - not one of the SRD conditions, so not tracked via
    `conditions`. A PC reduced to 0 HP goes unconscious (a real condition,
    via conditions.apply_condition) rather than straight to is_dead; a
    monster reduced to 0 HP is marked is_dead immediately, same as before
    Day 14 (monsters don't make death saves)."""
    is_stable: bool = False
    """3 successful death saves: stops rolling, but stays unconscious (at 0
    HP) until healed - distinct from "still needs to roll" so the turn
    engine knows not to prompt for another death save."""


class GameState(BaseModel):
    encounter_id: str
    characters: dict[str, Character]
    turn_order: list[str]
    current_turn: int
    round: int
    events: list[Event] = []
    pending_action: ParsedAction | None = None
    status: Literal["in_progress", "victory", "defeat", "aborted"] = "in_progress"
    battle_map: BattleMap | None = None
    """None for non-combat scenes (narrative_beat/roleplay); set by
    encounter.build_encounter_state for combat scenes."""

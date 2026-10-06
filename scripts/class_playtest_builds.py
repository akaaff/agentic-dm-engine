"""Level-1 builds for the class playtest (scripts/class_playtest.py): one
character per SRD class, built through the real `create_character` the app uses
so a build that can't be made is itself a finding. Ability scores are the
standard array, assigned the way a player would for the class; equipment is the
class's usual opening kit.

Classes go in groups of three - the three share one game in the live playtest
(multi-character play), and the groups mix roles (martial / caster / support)."""

from __future__ import annotations

from typing import Any

from src.engine.character_creation import create_character
from src.engine.position import Position
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character

GROUPS: list[list[str]] = [
    ["barbarian", "bard", "cleric"],
    ["druid", "fighter", "monk"],
    ["paladin", "ranger", "rogue"],
    ["sorcerer", "warlock", "wizard"],
]

# Standard array 15/14/13/12/10/8 assigned for the class; racial bonuses are added
# on top by create_character (so the race is chosen to fit).
BUILDS: dict[str, dict[str, Any]] = {
    "barbarian": {
        "name": "Grunna",
        "race_index": "dwarf",
        "scores": {"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        "skills": ["skill-athletics", "skill-intimidation"],
        "equipment": ["greataxe"],
    },
    "bard": {
        "name": "Lark",
        "race_index": "human",
        "scores": {"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        # Bard's second proficiency pool is three instruments (see CLAUDE.md).
        "skills": [
            "skill-persuasion",
            "skill-performance",
            "skill-deception",
            "lute",
            "flute",
            "lyre",
        ],
        "equipment": ["rapier"],
        "spells": ["healing-word", "thunderwave", "sleep", "charm-person"],
    },
    "cleric": {
        "name": "Ilsa",
        "race_index": "human",
        "scores": {"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        "skills": ["skill-insight", "skill-medicine"],
        "equipment": ["mace", "scale-mail"],
        "prepared": ["bless", "cure-wounds", "guiding-bolt", "healing-word"],
    },
    "druid": {
        "name": "Fern",
        "race_index": "human",
        "scores": {"STR": 8, "DEX": 13, "CON": 14, "INT": 12, "WIS": 15, "CHA": 10},
        "skills": ["skill-nature", "skill-perception"],
        "equipment": ["quarterstaff"],
        "prepared": ["entangle", "healing-word", "faerie-fire", "goodberry"],
    },
    "fighter": {
        "name": "Brannock",
        "race_index": "human",
        "scores": {"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        "skills": ["skill-athletics", "skill-perception"],
        "equipment": ["longsword", "shield", "chain-mail"],
        "fighting_style": "defense",
    },
    "monk": {
        "name": "Wen",
        "race_index": "human",
        "scores": {"STR": 10, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 12},
        "skills": ["skill-acrobatics", "skill-stealth"],
        "equipment": [],
    },
    "paladin": {
        "name": "Aurelio",
        "race_index": "human",
        "scores": {"STR": 15, "DEX": 8, "CON": 13, "INT": 10, "WIS": 12, "CHA": 14},
        "skills": ["skill-athletics", "skill-religion"],
        "equipment": ["longsword", "shield", "chain-mail"],
    },
    "ranger": {
        "name": "Silvia",
        "race_index": "elf",
        "scores": {"STR": 10, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 12},
        "skills": ["skill-stealth", "skill-survival", "skill-perception"],
        "equipment": ["longbow"],
    },
    "rogue": {
        "name": "Fenn",
        "race_index": "halfling",
        "scores": {"STR": 8, "DEX": 15, "CON": 13, "INT": 12, "WIS": 10, "CHA": 14},
        "skills": ["skill-stealth", "skill-sleight-of-hand", "skill-perception", "skill-deception"],
        "equipment": ["shortsword", "shortbow"],
    },
    "sorcerer": {
        "name": "Ember",
        "race_index": "human",
        "scores": {"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        "skills": ["skill-arcana", "skill-persuasion"],
        "equipment": ["dagger"],
        "spells": ["magic-missile", "burning-hands"],
    },
    "warlock": {
        "name": "Vex",
        "race_index": "human",
        "scores": {"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        "skills": ["skill-arcana", "skill-intimidation"],
        "equipment": ["dagger"],
    },
    "wizard": {
        "name": "Elara",
        "race_index": "elf",
        "scores": {"STR": 8, "DEX": 14, "CON": 13, "INT": 15, "WIS": 12, "CHA": 10},
        "skills": ["skill-arcana", "skill-history"],
        "equipment": ["dagger"],
        "prepared": ["magic-missile", "shield", "sleep"],
    },
}


def build(
    class_index: str,
    srd: SrdIndex | None = None,
    character_id: str | None = None,
    **overrides: Any,
) -> Character:
    """The class's playtest character at level 1, standing at (0, 0). Any
    BUILDS key can be overridden (name, race_index, scores, skills, equipment,
    spells, prepared, fighting_style); `character_id` defaults to the class
    index so scenarios can refer to the actor by class."""
    srd = srd or load_srd()
    spec = {**BUILDS[class_index], **overrides}
    return create_character(
        character_id=character_id or class_index,
        name=spec["name"],
        race_index=spec["race_index"],
        class_index=class_index,
        background_index="acolyte",
        base_ability_scores=spec["scores"],
        chosen_skills=spec["skills"],
        chosen_equipment=spec.get("equipment") or [],
        position=Position(x=0, y=0),
        srd=srd,
        fighting_style=spec.get("fighting_style"),
        gender="female",
        chosen_spells=spec.get("spells"),
        chosen_prepared_spells=spec.get("prepared"),
    )

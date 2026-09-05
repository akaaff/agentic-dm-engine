"""Encounter definitions (battle map + monster placement) and building the
initial GameState for one. Encounters are authored as YAML under
data/campaigns/encounters/ and referenced by id from a Campaign's combat
scenes (campaign.py).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from src.engine.movement import TerrainType
from src.engine.position import Position
from src.engine.rules import ability_modifier
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import AbilityScore, Character, GameState
from src.engine.turn_order import roll_initiative

DEFAULT_ENCOUNTERS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "campaigns" / "encounters"
)


class BattleMap(BaseModel):
    width: int
    height: int
    terrain: list[list[TerrainType]]
    spawn_points: dict[str, Position]


class MonsterSpawn(BaseModel):
    monster_index: str
    """SRD monster index, e.g. "goblin"."""
    character_id: str
    """Unique id for this specific monster within the encounter, e.g. "goblin_1"."""
    spawn_point: str
    """Key into battle_map.spawn_points."""


class Encounter(BaseModel):
    id: str
    name: str
    battle_map: BattleMap
    monsters: list[MonsterSpawn]
    party_spawn_points: list[str]
    """Spawn point names for party members, assigned in order they're passed
    to build_encounter_state."""


def load_encounter(encounter_id: str, encounters_dir: Path = DEFAULT_ENCOUNTERS_DIR) -> Encounter:
    path = encounters_dir / f"{encounter_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Encounter.model_validate(data)


def _parse_speed_feet(speed_field: dict[str, Any]) -> int:
    walk = speed_field.get("walk", "30 ft.")
    return int(str(walk).split()[0])


def monster_to_character(monster: SrdEntry, character_id: str, position: Position) -> Character:
    """Monsters skip the PC creation pipeline entirely - their stats come
    directly from the SRD stat block, not from ability-score assignment or
    equipped gear. race/class_/background are placeholders (monsters don't
    have a class or background); class_ is set to "Monster" so it's obvious
    at a glance which characters in a GameState are PCs/companions vs not."""
    hp = int(monster["hit_points"])
    ac = int(monster["armor_class"][0]["value"])
    stats: dict[AbilityScore, int] = {
        "STR": monster["strength"],
        "DEX": monster["dexterity"],
        "CON": monster["constitution"],
        "INT": monster["intelligence"],
        "WIS": monster["wisdom"],
        "CHA": monster["charisma"],
    }
    return Character(
        id=character_id,
        name=monster["name"],
        is_pc=False,
        hp=hp,
        max_hp=hp,
        ac=ac,
        position=position,
        stats=stats,
        proficiency_bonus=int(monster.get("proficiency_bonus", 2)),
        speed=_parse_speed_feet(monster.get("speed", {})),
        race=monster.get("type", "monster"),
        class_="Monster",
        background="",
    )


class GameStateBuildError(ValueError):
    pass


def build_encounter_state(
    encounter: Encounter,
    party_characters: list[Character],
    rng: random.Random,
    srd: SrdIndex | None = None,
) -> GameState:
    srd = srd or load_srd()

    if len(party_characters) > len(encounter.party_spawn_points):
        raise GameStateBuildError(
            f"Encounter {encounter.id!r} has {len(encounter.party_spawn_points)} party spawn "
            f"point(s), not enough for {len(party_characters)} party character(s)"
        )

    characters: dict[str, Character] = {}
    for character, spawn_name in zip(party_characters, encounter.party_spawn_points, strict=False):
        character.position = encounter.battle_map.spawn_points[spawn_name]
        characters[character.id] = character

    for spawn in encounter.monsters:
        monster_data = srd.monsters.get(spawn.monster_index)
        if monster_data is None:
            raise GameStateBuildError(f"Unknown monster index: {spawn.monster_index}")
        position = encounter.battle_map.spawn_points[spawn.spawn_point]
        characters[spawn.character_id] = monster_to_character(
            monster_data, spawn.character_id, position
        )

    dex_modifiers = {cid: ability_modifier(c.stats["DEX"]) for cid, c in characters.items()}
    turn_order = roll_initiative(dex_modifiers, rng)

    return GameState(
        encounter_id=encounter.id,
        characters=characters,
        turn_order=turn_order,
        current_turn=0,
        round=1,
        events=[],
        pending_action=None,
        status="in_progress",
    )

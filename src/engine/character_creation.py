"""Deterministic, SRD-driven character creation - no LLM anywhere in this
path. Callers (the API layer, Day 9+) are responsible for presenting choices
to a human and collecting them; this module only validates and derives.

Deliberate simplifications, both documented here and in DECISIONS.md/CLAUDE.md:
- Subraces are not applied - only base race ability bonuses/speed/size.
- Equipment choices (the SRD's nested "starting_equipment_options" trees) are
  not parsed into a choice UI; callers pass a flat `chosen_equipment` list of
  equipment indices, validated only for existence (not against the exact
  option-tree shape a real character sheet would enforce).
- Level 1 only - no leveling, no level-dependent class tables beyond hit die
  and the level-1 spell slot count below (the SRD's level-by-level class
  tables live behind a separate API endpoint, not in the vendored JSON).
"""

from __future__ import annotations

from collections.abc import Mapping

from src.engine.position import Position
from src.engine.rules import ability_modifier
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import AbilityScore, Character

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

LEVEL_1_SPELL_SLOTS: dict[str, dict[int, int]] = {
    "wizard": {1: 2},
    "cleric": {1: 2},
    "druid": {1: 2},
    "sorcerer": {1: 2},
    "bard": {1: 2},
    "warlock": {1: 1},
}
"""Not in the vendored SRD JSON (level tables live behind a separate API
endpoint) - these are basic SRD 5.1 game facts, hardcoded rather than fetched."""


class CharacterCreationError(ValueError):
    pass


def validate_standard_array(scores: Mapping[AbilityScore, int]) -> None:
    if sorted(scores.values()) != sorted(STANDARD_ARRAY):
        raise CharacterCreationError(
            f"Ability scores {scores} must be a permutation of the standard array {STANDARD_ARRAY}"
        )


def _compute_ac(inventory: list[str], equipment: dict[str, SrdEntry], dex_mod: int) -> int:
    armor_item: SrdEntry | None = None
    shield_bonus = 0
    for idx in inventory:
        item = equipment.get(idx)
        if not item or item.get("equipment_category", {}).get("index") != "armor":
            continue
        ac_info = item.get("armor_class")
        if not ac_info:
            continue
        if item.get("armor_category") == "Shield":
            shield_bonus += int(ac_info["base"])
        else:
            armor_item = item  # multiple non-shield armor pieces: last wins, not a real scenario

    if armor_item is None:
        return 10 + dex_mod + shield_bonus

    ac_info = armor_item["armor_class"]
    base: int = ac_info["base"]
    if ac_info.get("dex_bonus"):
        bonus = dex_mod
        if "max_bonus" in ac_info:
            bonus = min(bonus, ac_info["max_bonus"])
        base += bonus
    return base + shield_bonus


def create_character(
    character_id: str,
    name: str,
    race_index: str,
    class_index: str,
    background_index: str,
    base_ability_scores: dict[AbilityScore, int],
    chosen_skills: list[str],
    chosen_equipment: list[str] | None = None,
    is_pc: bool = True,
    is_companion: bool = False,
    persona: str | None = None,
    position: Position | None = None,
    srd: SrdIndex | None = None,
) -> Character:
    srd = srd or load_srd()
    chosen_equipment = chosen_equipment or []
    position = position or Position(x=0, y=0)

    race = srd.races.get(race_index)
    if race is None:
        raise CharacterCreationError(f"Unknown race: {race_index}")
    cls = srd.classes.get(class_index)
    if cls is None:
        raise CharacterCreationError(f"Unknown class: {class_index}")
    background = srd.backgrounds.get(background_index)
    if background is None:
        raise CharacterCreationError(f"Unknown background: {background_index}")

    validate_standard_array(base_ability_scores)

    final_scores: dict[AbilityScore, int] = base_ability_scores.copy()
    for bonus in race.get("ability_bonuses", []):
        ability: AbilityScore = bonus["ability_score"]["index"].upper()
        final_scores[ability] = final_scores.get(ability, 0) + bonus["bonus"]

    _validate_skill_choices(cls, chosen_skills)

    for idx in chosen_equipment:
        if idx not in srd.equipment:
            raise CharacterCreationError(f"Unknown equipment index: {idx}")

    inventory: list[str] = []
    for item in cls.get("starting_equipment", []):
        inventory.extend([item["equipment"]["index"]] * item["quantity"])
    for item in background.get("starting_equipment", []):
        inventory.extend([item["equipment"]["index"]] * item["quantity"])
    inventory.extend(chosen_equipment)

    con_mod = ability_modifier(final_scores["CON"])
    dex_mod = ability_modifier(final_scores["DEX"])
    hp = max(1, cls["hit_die"] + con_mod)
    ac = _compute_ac(inventory, srd.equipment, dex_mod)

    return Character(
        id=character_id,
        name=name,
        is_pc=is_pc,
        is_companion=is_companion,
        persona=persona,
        hp=hp,
        max_hp=hp,
        ac=ac,
        position=position,
        conditions=[],
        spell_slots=dict(LEVEL_1_SPELL_SLOTS.get(class_index, {})),
        inventory=inventory,
        stats=final_scores,
        proficiency_bonus=2,
        speed=race["speed"],
        race=race["name"],
        class_=cls["name"],
        background=background["name"],
    )


def _validate_skill_choices(cls: SrdEntry, chosen_skills: list[str]) -> None:
    allowed: set[str] = set()
    required_count = 0
    for choice in cls.get("proficiency_choices", []):
        required_count += choice["choose"]
        for option in choice["from"]["options"]:
            allowed.add(option["item"]["index"])

    if len(chosen_skills) != required_count:
        raise CharacterCreationError(
            f"{cls['name']} requires exactly {required_count} skill choice(s), "
            f"got {len(chosen_skills)}"
        )
    if len(set(chosen_skills)) != len(chosen_skills):
        raise CharacterCreationError(f"Duplicate skill choice in {chosen_skills}")
    for skill in chosen_skills:
        if skill not in allowed:
            raise CharacterCreationError(f"{skill} is not a valid skill choice for {cls['name']}")

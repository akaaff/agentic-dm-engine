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

EXTRA_EQUIPMENT_INDICES = {"potion-of-healing"}
"""Real SRD items that aren't in 5e-SRD-Equipment.json - magic items
(potions, scrolls, wands...) live in a separate, unvendored Magic Items
endpoint whose entries don't carry machine-readable mechanical data anyway
(a potion's healing amount is prose inside a markdown table in its `desc`).
Rather than vendor a 362-entry file for one item whose numbers still need
hardcoding either way, "potion-of-healing" is allowed here explicitly -
see turn_engine.HEALING_POTION_DICE for the (hardcoded, documented) amount."""

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

    equipment_options = set(class_equipment_options(cls, srd))
    for idx in chosen_equipment:
        if idx not in srd.equipment and idx not in EXTRA_EQUIPMENT_INDICES:
            raise CharacterCreationError(f"Unknown equipment index: {idx}")
        item = srd.equipment.get(idx)
        # Only weapons/armor are proficiency-gated - EXTRA_EQUIPMENT_INDICES
        # (e.g. a healing potion) and general adventuring gear aren't SRD
        # weapon/armor entries at all, so they have no proficiency to check.
        if item is not None and (item.get("weapon_category") or item.get("armor_category")):
            if idx not in equipment_options:
                raise CharacterCreationError(
                    f"{cls['name']} isn't proficient with {item['name']} - "
                    "choose gear from a proficient category"
                )

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

    # chosen_skills can include non-skill proficiencies (e.g. Bard's musical
    # instruments - see CLAUDE.md); only "skill-*" entries count here.
    # Deduplicated (via dict.fromkeys, which preserves order) since a class
    # skill choice and a background's fixed proficiency can genuinely
    # overlap - e.g. Pip Larkspur (Bard, Acolyte) chooses skill-insight
    # *and* Acolyte grants it automatically.
    skill_proficiencies = list(
        dict.fromkeys(
            [s for s in chosen_skills if s.startswith("skill-")]
            + [
                p["index"]
                for p in background.get("starting_proficiencies", [])
                if p["index"].startswith("skill-")
            ]
        )
    )

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
        skill_proficiencies=skill_proficiencies,
        class_index=class_index,
    )


_WEAPON_PROFICIENCY_ALIASES: dict[str, str] = {
    "daggers": "dagger",
    "darts": "dart",
    "slings": "sling",
    "quarterstaffs": "quarterstaff",
    "crossbows-light": "crossbow-light",
    "clubs": "club",
    "javelins": "javelin",
    "maces": "mace",
    "sickles": "sickle",
    "spears": "spear",
    "scimitars": "scimitar",
    "longswords": "longsword",
    "rapiers": "rapier",
    "shortswords": "shortsword",
    "hand-crossbows": "crossbow-hand",
}
"""A class's `proficiencies` list names specific weapons in plural/reworded
form (e.g. Wizard's "daggers", "crossbows-light") rather than the equipment
list's own singular index ("dagger", "crossbow-light") - and one is
irregular ("hand-crossbows" -> "crossbow-hand", word order swapped).
Enumerated directly from all 12 vendored classes' actual proficiency lists,
not a general singularization rule - safer than guessing at a pattern that
might silently mismatch a class added later."""


def class_equipment_options(cls: SrdEntry, srd: SrdIndex) -> list[str]:
    """Weapon/armor equipment indices this class is actually SRD-proficient
    with - e.g. a Wizard is proficient with exactly 5 specific weapons (not
    "simple weapons" as a category) and no armor at all, while a Fighter's
    "all-armor"/"martial-weapons" entries are broad categories. Used to
    restrict the wizard's optional-extra-gear picker and to validate
    `chosen_equipment` server-side - the same "don't offer/accept a choice
    outside the real pool" discipline `class_skill_choice_pool` already
    applies to skills."""
    prof_indices = {p["index"] for p in cls.get("proficiencies", [])}
    aliased_weapons = {_WEAPON_PROFICIENCY_ALIASES.get(p, p) for p in prof_indices}

    options: list[str] = []
    for item in srd.equipment.values():
        weapon_category = item.get("weapon_category")
        armor_category = item.get("armor_category")
        if weapon_category and (
            f"{weapon_category.lower()}-weapons" in prof_indices or item["index"] in aliased_weapons
        ):
            options.append(item["index"])
        elif armor_category == "Shield" and "shields" in prof_indices:
            options.append(item["index"])
        elif armor_category and (
            "all-armor" in prof_indices or f"{armor_category.lower()}-armor" in prof_indices
        ):
            options.append(item["index"])
    return sorted(options)


def class_skill_choice_pool(cls: SrdEntry) -> tuple[int, set[str]]:
    """(required_count, allowed_indices) summed across every one of a
    class's proficiency_choices entries - a Bard's two separate pools
    (3 skills + 3 instruments) are meant to combine into one flat pool this
    way (see CLAUDE.md: chosen_skills can include non-skill proficiencies).

    Skips any entry whose options aren't flat `option_type: "reference"`
    items - confirmed live to be exactly one entry SRD-wide: Monk's "choose
    one type of artisan's tools or one musical instrument", which nests a
    *choice* inside each option (pick a category, then pick within it)
    instead of a flat item. Tool/instrument proficiencies aren't modeled by
    this project at all (no wizard step, no Character field) - same
    "don't fully parse every SRD option-tree shape" simplification already
    documented for equipment choices - so that entry is skipped entirely
    rather than raising, and a Monk only ever needs to choose their 2
    skills."""
    required_count = 0
    allowed: set[str] = set()
    for choice in cls.get("proficiency_choices", []):
        options = choice["from"]["options"]
        if any(opt["option_type"] != "reference" for opt in options):
            continue
        required_count += choice["choose"]
        allowed.update(opt["item"]["index"] for opt in options)
    return required_count, allowed


def _validate_skill_choices(cls: SrdEntry, chosen_skills: list[str]) -> None:
    required_count, allowed = class_skill_choice_pool(cls)

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

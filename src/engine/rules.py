"""Attack, damage, saving throw, and skill check resolution.

Pure functions over Character/RollResult - no GameState/Event coupling here,
so combat math can be tested in complete isolation. Day 7 wires these into
the turn loop and translates results into Events.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from src.engine.dice import RollResult, roll, roll_d20
from src.engine.srd_loader import SrdEntry, SrdIndex
from src.engine.state import AbilityScore, Character


@dataclass(frozen=True)
class AttackResult:
    attack_roll: RollResult
    hit: bool
    critical: bool
    damage: int | None
    """None on a miss."""
    damage_type: str | None


def resolve_attack(
    defender_ac: int,
    attack_bonus: int,
    damage_dice_count: int,
    damage_dice_sides: int,
    damage_bonus: int,
    damage_type: str,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """A natural 1 always misses, a natural 20 always hits and doubles the
    damage dice (not the flat bonus), per SRD rules."""
    attack_roll = roll_d20(
        modifier=attack_bonus, rng=rng, advantage=advantage, disadvantage=disadvantage
    )
    natural = attack_roll.kept[0]

    if natural == 1:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    critical = natural == 20
    hit = critical or attack_roll.total >= defender_ac
    if not hit:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    dice_count = damage_dice_count * 2 if critical else damage_dice_count
    damage_roll = roll(dice_count, damage_dice_sides, modifier=damage_bonus, rng=rng)
    damage = max(0, damage_roll.total)
    return AttackResult(
        attack_roll, hit=True, critical=critical, damage=damage, damage_type=damage_type
    )


def apply_damage(character: Character, amount: int) -> int:
    """Mutates character.hp, clamped at 0. Returns the actual HP lost
    (may be less than `amount` if it would have gone negative)."""
    actual = min(amount, character.hp)
    character.hp = max(0, character.hp - amount)
    return actual


def resolve_saving_throw(
    save_bonus: int,
    dc: int,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[RollResult, bool]:
    result = roll_d20(modifier=save_bonus, rng=rng, advantage=advantage, disadvantage=disadvantage)
    return result, result.total >= dc


def resolve_skill_check(
    modifier: int,
    dc: int,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[RollResult, bool]:
    result = roll_d20(modifier=modifier, rng=rng, advantage=advantage, disadvantage=disadvantage)
    return result, result.total >= dc


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def ability_check_modifier(
    character: Character, ability: AbilityScore, proficient: bool = False
) -> int:
    mod = ability_modifier(character.stats[ability])
    return mod + character.proficiency_bonus if proficient else mod


def normalize_skill_name(raw: str) -> str:
    """ "Perception", "skill-perception", "Sleight of Hand" -> "perception",
    "sleight-of-hand" (srd.skills' bare-index form)."""
    return raw.strip().lower().replace(" ", "-").removeprefix("skill-")


def skill_ability(skill_name: str, srd: SrdIndex) -> AbilityScore:
    """Moved here from turn_engine (Day 22) so campaign_runner's out-of-combat
    skill challenges can share the same skill->governing-ability lookup
    instead of duplicating it - this module has no GameState/Event coupling,
    which both call sites need."""
    normalized = normalize_skill_name(skill_name)
    skill_data = srd.skills.get(normalized)
    if skill_data is None:
        raise ValueError(f"Unknown skill: {skill_name!r}")
    ability: AbilityScore = skill_data["ability_score"]["index"].upper()
    return ability


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
    "all-armor"/"martial-weapons" entries are broad categories. Lives here
    (not character_creation.py, which imports it) rather than in
    turn_engine.py, same reasoning as skill_ability above: both creation-time
    validation and turn_engine's live proficiency checks below need it, and
    this module has no coupling to either caller.

    Used to restrict the wizard's optional-extra-gear picker, to validate
    `chosen_equipment` at creation, and (below) to determine whether an
    *equipped* weapon/armor actually grants its proficiency bonus / avoids
    the non-proficiency penalty during play."""
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


def is_class_proficient_with(character: Character, equipment_index: str, srd: SrdIndex) -> bool:
    """Whether this character's class is SRD-proficient with the given
    weapon/armor equipment index. Characters with no class (monsters -
    they attack via their own stat-block actions, never through this
    weapon-lookup path at all) are treated as proficient with anything,
    since there's no class proficiency list to check against."""
    if character.class_index is None:
        return True
    cls = srd.classes.get(character.class_index)
    if cls is None:
        return True
    return equipment_index in class_equipment_options(cls, srd)


def has_non_proficient_armor(character: Character, srd: SrdIndex) -> bool:
    """True if the character's inventory contains armor or a shield their
    class isn't proficient with - per SRD, wearing/using it imposes
    disadvantage on any attack roll or STR/DEX-based ability check (see
    turn_engine's _resolve_attack/_resolve_skill_check). Inventory is
    treated as "currently equipped" throughout this engine (same
    simplification character_creation._compute_ac already makes - a flat
    list, not a worn/carried distinction)."""
    if character.class_index is None:
        return False
    cls = srd.classes.get(character.class_index)
    if cls is None:
        return False
    options = set(class_equipment_options(cls, srd))
    return any(
        (item := srd.equipment.get(idx)) and item.get("armor_category") and idx not in options
        for idx in character.inventory
    )


def weapon_range_feet(weapon: SrdEntry) -> tuple[int, int | None]:
    """(normal, long) range in feet for a weapon - `long` is None for melee
    weapons (no "attack at disadvantage from farther away" concept, unlike
    ranged ones). A "reach" weapon (glaive, whip - the only two SRD-wide)
    extends melee range by 5ft; the equipment data's own `range.normal` is
    5ft for every melee weapon regardless of reach, so this is the only
    place that distinction has to be applied."""
    range_info = weapon.get("range") or {"normal": 5}
    normal = int(range_info["normal"])
    properties = {p["index"] for p in (weapon.get("properties") or [])}
    if "reach" in properties:
        normal += 5
    long = range_info.get("long")
    return normal, int(long) if long is not None else None


_SPELL_RANGE_RE = re.compile(r"(\d+)\s*feet", re.IGNORECASE)


def spell_range_feet(range_str: str) -> int:
    """A spell's SRD `range` field is a plain string ("120 feet", "Touch",
    "Self") - no {normal, long} structure like weapons, since spells have
    no "beyond normal range" disadvantage tier in 5e; you're either in
    range or you aren't. "Touch"/"Self"/anything unparseable falls back to
    5ft (melee-adjacent) - a safe default since cast_spell only resolves
    single-target attack-roll spells (Day 14 scope), which are never
    "Self"-range in practice."""
    match = _SPELL_RANGE_RE.search(range_str)
    return int(match.group(1)) if match else 5


_MONSTER_RANGE_RE = re.compile(r"range (\d+)/(\d+)\s*ft", re.IGNORECASE)


def monster_action_range_feet(action: SrdEntry) -> tuple[int, int | None]:
    """(normal, long) range in feet for a monster's stat-block action.

    Unlike weapons, monster actions don't carry structured range data - only
    a free-text `desc` ("Melee Weapon Attack: +4 to hit, reach 5 ft., one
    target..." / "Ranged Weapon Attack: +4 to hit, range 30/120 ft., one
    target..."). Every SRD monster stat block starts that description with
    exactly "Melee" or "Ranged", which is reliable enough to branch on; the
    numeric range for a ranged action is parsed from the same consistent
    "range N/N ft." phrasing SRD-wide. Falls back to a flat 5ft melee range
    (true for every monster this project curates - goblins, kobolds, wolves,
    bandits all reach no further) if a description doesn't match either
    pattern, rather than leaving a monster's attack unrestricted."""
    desc = str(action.get("desc", ""))
    if desc.lower().startswith("ranged"):
        match = _MONSTER_RANGE_RE.search(desc)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 5, None

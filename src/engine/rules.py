"""Attack, damage, saving throw, and skill check resolution.

Pure functions over Character/RollResult - no GameState/Event coupling here,
so combat math can be tested in complete isolation. Day 7 wires these into
the turn loop and translates results into Events.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, replace

from src.engine.conditions import has_condition
from src.engine.dice import RollResult, roll, roll_d20
from src.engine.srd_loader import SrdEntry, SrdIndex
from src.engine.state import AbilityScore, Character, ConditionName


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
    force_critical: bool = False,
    lucky: bool = False,
    bardic_die_sides: int | None = None,
) -> AttackResult:
    """A natural 1 always misses, a natural 20 always hits and doubles the
    damage dice (not the flat bonus), per SRD rules.

    `force_critical` (Phase 9C) is for the SRD rule that any hit against an
    unconscious creature is a critical hit - it only affects whether a hit's
    damage dice double, not whether the attack hits at all: a natural 1
    still always misses, and a non-natural-20 roll that doesn't reach
    defender_ac is still a miss even with force_critical set. Callers decide
    when it applies (turn_engine checks the target's `unconscious` condition
    before calling this); this module has no Condition/GameState coupling of
    its own to make that check itself.

    `lucky` (Halfling's Lucky trait, issue #23) rerolls a natural 1 on the
    attack roll itself - see dice.roll_d20's own reroll_on_natural_1.

    `bardic_die_sides` (Bardic Inspiration, issue #25) rolls one extra die
    and adds it to the attack roll's total *before* the natural-1/hit
    checks below - a boosted total really can turn a would-be miss into a
    hit, matching how the bonus works in real play (you add it not knowing
    yet whether the total will clear the target's AC). Rolled and added
    unconditionally whenever a caller passes it, even on a natural 1 that's
    still an automatic miss regardless - real SRD has no "only if it would
    have helped" clause; the die is spent the moment you choose to add it.
    This module has no Character to mutate, so *clearing* the banked die on
    the caster's side is the caller's job (turn_engine.py), not this
    function's."""
    attack_roll = roll_d20(
        modifier=attack_bonus,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_on_natural_1=lucky,
    )
    natural = attack_roll.kept[0]

    if bardic_die_sides:
        bonus = rng.randint(1, bardic_die_sides)
        attack_roll = replace(attack_roll, total=attack_roll.total + bonus)

    if natural == 1:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    natural_twenty = natural == 20
    hit = natural_twenty or attack_roll.total >= defender_ac
    if not hit:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    critical = natural_twenty or force_critical
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
    lucky: bool = False,
) -> tuple[RollResult, bool]:
    """`lucky` (Halfling's Lucky trait, issue #23) rerolls a natural 1 - see
    dice.roll_d20's own reroll_on_natural_1."""
    result = roll_d20(
        modifier=save_bonus,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_on_natural_1=lucky,
    )
    return result, result.total >= dc


def resolve_skill_check(
    modifier: int,
    dc: int,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
    lucky: bool = False,
) -> tuple[RollResult, bool]:
    """`lucky` (Halfling's Lucky trait, issue #23) rerolls a natural 1 - see
    dice.roll_d20's own reroll_on_natural_1."""
    result = roll_d20(
        modifier=modifier,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_on_natural_1=lucky,
    )
    return result, result.total >= dc


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def ability_check_modifier(
    character: Character, ability: AbilityScore, proficient: bool = False
) -> int:
    mod = ability_modifier(character.stats[ability])
    return mod + character.proficiency_bonus if proficient else mod


def saving_throw_bonus(character: Character, ability: AbilityScore) -> int:
    """Ability modifier, plus proficiency bonus if this character's class is
    SRD-proficient in this save (Character.saving_throw_proficiencies,
    populated at creation from the SRD class's `saving_throws` - e.g.
    Fighter: STR, CON). Empty for monsters, who save via their own stat
    block's `saving_throws` field rather than this class-based path - callers
    resolving a monster's save should prefer that when present."""
    mod = ability_modifier(character.stats[ability])
    if ability in character.saving_throw_proficiencies:
        return mod + character.proficiency_bonus
    return mod


_MONSTER_ABILITY_FIELDS: dict[AbilityScore, str] = {
    "STR": "strength",
    "DEX": "dexterity",
    "CON": "constitution",
    "INT": "intelligence",
    "WIS": "wisdom",
    "CHA": "charisma",
}
"""Monster stat blocks spell ability scores out as full words
(`strength`/`dexterity`/...), not the STR/DEX/... abbreviations
Character.stats and AbilityScore use everywhere else - needed to look up a
monster's raw score for monster_saving_throw_bonus's no-proficiency
fallback."""


def monster_saving_throw_bonus(monster_data: SrdEntry, ability: AbilityScore) -> int:
    """A monster's own saving throw bonus (Phase 9D) - e.g. for resisting a
    PC's save-based spell. Deliberately NOT saving_throw_bonus above:
    monsters have no `class_index`/`saving_throw_proficiencies` (they save
    via their own stat block, never the class-based path), and - confirmed
    by actually inspecting the vendored monster JSON via load_srd(), not
    assumed - a monster stat block's top-level `saving_throws` field is
    always null for every one of the ~300 vendored monsters. The real data
    lives in `proficiencies`, keyed "saving-throw-<ability>" (e.g.
    "saving-throw-con"), and - unlike a PC's proficiency, which is just a
    yes/no flag combined with a modifier computed elsewhere - each entry's
    `value` is already the full precomputed bonus (ability modifier +
    proficiency bonus baked in together): confirmed against the vendored
    Adult Black Dragon, whose "saving-throw-dex" value of 7 exactly equals
    its DEX modifier (2) plus its proficiency_bonus (5). Falls back to a raw
    ability modifier (no proficiency) when the monster has no entry for this
    ability - the same "no class means no proficiency" fallback pattern as
    is_class_proficient_with."""
    ability_key = f"saving-throw-{ability.lower()}"
    for prof in monster_data.get("proficiencies") or []:
        if prof.get("proficiency", {}).get("index") == ability_key:
            return int(prof["value"])
    return ability_modifier(monster_data[_MONSTER_ABILITY_FIELDS[ability]])


def has_lucky_trait(character: Character) -> bool:
    """Halfling's Lucky trait (issue #23): reroll a natural 1 on an attack
    roll, ability check, or saving throw - see resolve_attack/
    resolve_saving_throw/resolve_skill_check's own `lucky` param. Always
    False for a monster (race_index is a PC/companion-only field - see its
    docstring)."""
    return character.race_index == "halfling"


def has_relentless_endurance(character: Character) -> bool:
    """Half-Orc's Relentless Endurance trait (issue #23): when reduced to 0
    HP by damage that doesn't kill outright, drop to 1 HP instead, once per
    long rest - see turn_engine._apply_damage_and_handle_downing and
    Character.used_relentless_endurance_this_rest. Always False for a
    monster, same reasoning as has_lucky_trait."""
    return character.race_index == "half-orc"


def normalize_skill_name(raw: str) -> str:
    """ "Perception", "skill-perception", "Sleight of Hand" -> "perception",
    "sleight-of-hand" (srd.skills' bare-index form)."""
    return raw.strip().lower().replace(" ", "-").removeprefix("skill-")


_AUTO_HIT_SPELLS = {"magic-missile"}
"""Issue #35: spells with no roll of any kind - every dart/effect simply
hits. Deliberately an explicit per-spell allowlist, not inferred from
"has `damage` but no `attack_type`/`dc`" the way the other three mechanics
are: checked directly against the vendored SRD data, several *other*
spells share that exact field shape (scorching-ray, call-lightning,
flaming-sphere, branding-smite...) without actually being no-roll effects
- they're real attack-roll/save spells whose vendored entry simply lacks
the attack_type/dc field (an SRD data gap, not a genuine auto-hit rule).
Auto-including them via field inference would silently make them always
hit instead of correctly staying unsupported. Add a spell here only after
confirming directly (like Magic Missile was) that it truly has no roll in
real SRD text, not just because it happens to lack these two fields."""


def spell_mechanic(spell: SrdEntry) -> str | None:
    """Classifies a spell into one of the four mechanics cast_spell
    resolves: "attack" (SRD `attack_type` present - e.g. Fire Bolt, Guiding
    Bolt), "save" (`dc` present - e.g. Fireball, Hold Person), "heal"
    (`heal_at_slot_level` present - e.g. Cure Wounds), or "auto_hit" (an
    explicit allowlist - see _AUTO_HIT_SPELLS - for a no-roll spell like
    Magic Missile). Checked in this order since a real SRD spell only ever
    has one of the first three shapes (confirmed by inspecting several of
    each directly via load_srd()); auto_hit is checked last since an
    allowlisted spell has none of the other fields anyway. None for
    anything else - a pure buff/utility spell, or a spell whose real SRD
    mechanic (attack/save) the vendored data doesn't structurally capture
    - still out of scope for cast_spell to resolve. Moved here from
    turn_engine (issue #22) so monster_ai's innate-spell selection can
    share the same classification a PC's/monster's actual cast later uses,
    rather than a second, potentially drifting copy - this module has no
    TurnEngineError of its own, so a caller that needs a resolved spell
    (not just a probe) raises its own clear rejection for None."""
    if spell.get("attack_type"):
        return "attack"
    if spell.get("dc"):
        return "save"
    if spell.get("heal_at_slot_level"):
        return "heal"
    if spell.get("index") in _AUTO_HIT_SPELLS:
        return "auto_hit"
    return None


def normalize_spell_name(raw: str) -> str:
    """ "Ray of Enfeeblement" -> "ray-of-enfeeblement" (srd.spells' bare-index
    form) - the same normalization turn_engine._resolve_cast_spell already
    applies inline to a PC's item_or_spell text, shared here (issue #22) so
    monster_innate_spellcasting's own spell-name lookups use the identical
    rule rather than a second, potentially drifting copy."""
    return raw.strip().lower().replace(" ", "-")


def class_spell_indices(class_index: str, srd: SrdIndex, level: int | None = None) -> set[str]:
    """Issue #30: every real SRD spell that `class_index` can access, per the
    SRD's own per-spell `classes` list - the same filter api/routes/
    characters.py's cantrip-building loop already applies for level 0,
    generalized and shared here so character_creation._validate_spell_
    choices/level_up and the API's known_spells_pool can't drift apart on
    what counts as "a real spell for this class." `level=None` means any
    level-1+ spell (level_up's own new-spells-learned check, which - unlike
    creation's level-1-only pool - can legally reach a higher-level spell
    once the class's own table grants one)."""
    return {
        spell["index"]
        for spell in srd.spells.values()
        if (spell.get("level") == level if level is not None else spell.get("level", 0) >= 1)
        and any(c["index"] == class_index for c in spell.get("classes", []))
    }


def spell_damage_notation(spell: SrdEntry, cast_level: int) -> str:
    """The dice notation a cast of `spell` at `cast_level` actually deals -
    "1d10" for a cantrip scaling by character level (damage_at_character_
    level, keyed "1" here since notation alone doesn't change with level,
    just which key some other lookup would use) or the slot-level notation
    for a real spell (damage_at_slot_level[str(cast_level)]). Extracted from
    turn_engine._spell_attack_params's identical inline logic (issue #30) so
    a spell's *displayed* damage (api/routes/characters.py's SpellSummary)
    can never drift from what actually resolves - callers with only a spell
    entry and no live cast (a display context) pass the spell's own base
    `spell["level"]` as cast_level."""
    damage_info = spell["damage"]
    notation: str = (
        damage_info["damage_at_character_level"]["1"]
        if cast_level == 0
        else damage_info["damage_at_slot_level"][str(cast_level)]
    )
    return notation


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
    """True if the character's currently *equipped* armor or shield (issue
    #13 - equipped_armor/equipped_shield are now a real worn/carried
    distinction, the same fix the original equipped-weapons feature already
    made for the Dueling fighting-style check) is something their class
    isn't proficient with - per SRD, wearing/using it imposes disadvantage
    on any attack roll or STR/DEX-based ability check (see turn_engine's
    _resolve_attack/_resolve_skill_check). A non-proficient piece merely
    sitting in inventory, never equipped, has no mechanical effect."""
    if character.class_index is None:
        return False
    cls = srd.classes.get(character.class_index)
    if cls is None:
        return False
    options = set(class_equipment_options(cls, srd))
    equipped = [idx for idx in (character.equipped_armor, character.equipped_shield) if idx]
    return any(
        (item := srd.equipment.get(idx)) and item.get("armor_category") and idx not in options
        for idx in equipped
    )


def monster_damage_multiplier(target: Character, damage_type: str, srd: SrdIndex) -> float:
    """1.0 normal, 0.5 resistant, 0.0 immune, 2.0 vulnerable (issue #18) -
    always 1.0 for a non-monster target (PCs/companions have no SRD
    resistance data). The vendored damage_resistances/immunities/
    vulnerabilities entries are free-text strings, not a closed vocabulary
    - some are a bare damage type ("poison"), others a compound clause
    ("bludgeoning, piercing, and slashing from nonmagical weapons", e.g.
    Ghost) - matched by substring containment rather than exact equality so
    both shapes work, and the nonmagical-weapons clause is treated as an
    unconditional resistance since this project has no "magical weapon"
    concept to gate it on. Checked in immunity -> resistance -> vulnerability
    order (a type can't sensibly be both, but SRD data has no cross-list
    dedup guarantee)."""
    if target.monster_index is None:
        return 1.0
    monster = srd.monsters.get(target.monster_index)
    if monster is None:
        return 1.0
    if any(damage_type in entry for entry in monster.get("damage_immunities", [])):
        return 0.0
    if any(damage_type in entry for entry in monster.get("damage_resistances", [])):
        return 0.5
    if any(damage_type in entry for entry in monster.get("damage_vulnerabilities", [])):
        return 2.0
    return 1.0


def monster_is_immune_to_condition(
    target: Character, condition_name: ConditionName, srd: SrdIndex
) -> bool:
    """True if this monster's SRD stat block lists condition_name in its
    condition_immunities (issue #18) - always False for a non-monster
    target. Per SRD, a creature immune to a condition can't be affected by
    it at all (e.g. an ooze can't be grappled or knocked prone), so callers
    should reject the attempt outright rather than let it resolve and
    silently do nothing."""
    if target.monster_index is None:
        return False
    monster = srd.monsters.get(target.monster_index)
    if monster is None:
        return False
    return any(c["index"] == condition_name for c in monster.get("condition_immunities", []))


def monster_has_pack_tactics(character: Character, srd: SrdIndex) -> bool:
    """True if this monster's SRD stat block has the "Pack Tactics" special
    ability (issue #19, e.g. Wolf) - always False for a non-monster.
    `special_abilities` entries are {name, desc, ...} free-text traits, not
    a closed/indexed vocabulary like condition_immunities, so this matches
    on the exact `name` string SRD data actually uses rather than assuming
    an index form exists. Doesn't itself check whether an ally is actually
    adjacent to the shared target - callers combine this with
    turn_engine._ally_adjacent_to, the same helper Sneak Attack's identical
    "an ally is within 5ft of the target" trigger already uses."""
    if character.monster_index is None:
        return False
    monster = srd.monsters.get(character.monster_index)
    if monster is None:
        return False
    return any(a.get("name") == "Pack Tactics" for a in monster.get("special_abilities", []))


def monster_is_undead_or_fiend(target: Character, srd: SrdIndex) -> bool:
    """True if this monster's SRD `type` field is "undead" or "fiend"
    (issue #21, Divine Smite's "+1d8 more against undead/fiends" clause) -
    always False for a non-monster target. `type` is a plain lowercase
    string (e.g. "undead", "beast", "humanoid"), not a free-text clause like
    damage_resistances, so exact equality is correct here."""
    if target.monster_index is None:
        return False
    monster = srd.monsters.get(target.monster_index)
    if monster is None:
        return False
    return monster.get("type") in ("undead", "fiend")


def max_wild_shape_cr(level: int) -> float:
    """Druid's Wild Shape (issue #24) CR cap by level - 1/4 at levels 2-3,
    1/2 from level 4 on, matching the real SRD table within this project's
    roughly-level-1-5 scope (the table keeps rising further - 1 at level 8
    - out of scope here, same PROFICIENCY_BONUS_BY_LEVEL-style boundary).
    Callers gate `level < 2` themselves (Wild Shape doesn't exist at all
    yet at level 1)."""
    return 0.5 if level >= 4 else 0.25


def wild_shape_beast_is_allowed(beast: SrdEntry, level: int) -> bool:
    """Whether `beast` is a legal Wild Shape target at `level` (issue #24):
    CR within max_wild_shape_cr, and never a flying speed (real SRD unlocks
    that at level 8, out of this project's scope entirely) nor a swimming
    speed below level 4 (real SRD unlocks that at level 4 - which happens
    to be the same level the CR cap itself rises, per the real table)."""
    cr = beast.get("challenge_rating", 999)
    if cr > max_wild_shape_cr(level):
        return False
    speed = beast.get("speed", {})
    if "fly" in speed:
        return False
    if "swim" in speed and level < 4:
        return False
    return True


def monster_innate_spellcasting(monster_data: SrdEntry) -> SrdEntry | None:
    """The raw "Innate Spellcasting" special_ability entry's `spellcasting`
    sub-object (issue #22) - {ability, dc, modifier?, spells: [{name, level,
    usage: {type, times?}}, ...]}. `dc`/`modifier` are flat numbers the SRD
    stat block already computed, not derived from an ability score +
    proficiency bonus the way a PC's cast_spell path computes them (see
    turn_engine._spellcasting_ability_mod) - a monster's own numbers are
    used as-is; `modifier` (the spell attack bonus) is only present on
    casters whose innate list actually includes an attack-roll spell.
    Returns None if this monster has no Innate Spellcasting (most monsters,
    and full "Spellcasting" casters like Cult Fanatic, which use a real
    slot-tracked spell list - out of this issue's "start narrow" scope)."""
    for ability in monster_data.get("special_abilities", []) or []:
        spellcasting = ability.get("spellcasting")
        if ability.get("name") == "Innate Spellcasting" and spellcasting:
            result: SrdEntry = spellcasting
            return result
    return None


def monk_martial_arts_die_sides(level: int) -> int:
    """Martial Arts' scaling unarmed-strike/monk-weapon die (issue #24) -
    1d4 through level 4, 1d6 from level 5 on. The real SRD table keeps
    scaling further (1d8 at 11, 1d10 at 17), out of this project's
    roughly-level-1-5 scope (PROFICIENCY_BONUS_BY_LEVEL/SPELL_SLOTS_BY_LEVEL
    precedent), so only the one tier boundary within that range matters."""
    return 6 if level >= 5 else 4


def magic_missile_dart_count(spell_level: int) -> int:
    """3 darts at 1st level, +1 per slot level above 1st (issue #35 - SRD
    prose, "the spell creates one more dart for each slot level above
    1st," not a structured field). Confirmed against the vendored
    damage_at_slot_level table rather than assumed: every level 1-9 entry
    ("3d4 + 3" at 1, ... "11d4 + 11" at 9) factors exactly as (level+2)
    separate 1d4+1 rolls, so this closed-form formula is a verified fact
    about the real data, not a guess."""
    return spell_level + 2


def bardic_inspiration_die_sides(level: int) -> int:
    """Bardic Inspiration's scaling die (issue #25, Bard) - 1d6 through
    level 4, 1d8 from level 5 on. Same two-tier shape as
    monk_martial_arts_die_sides, for the same reason: the real table keeps
    scaling further (1d10 at 10, 1d12 at 15, 1d20 at 20), out of this
    project's roughly-level-1-5 scope."""
    return 8 if level >= 5 else 6


def is_monk_weapon(weapon: SrdEntry) -> bool:
    """True if Martial Arts (Monk, issue #24) applies to this weapon - the
    vendored SRD equipment data already tags exactly the right set with a
    "monk" property (every simple melee weapon, plus the shortsword despite
    being Martial - confirmed live against the real data), so no need to
    hand-derive "simple melee, not heavy/two-handed" from scratch."""
    return "monk" in {p["index"] for p in (weapon.get("properties") or [])}


def armor_ac(
    equipped_armor: str | None,
    equipped_shield: str | None,
    dex_mod: int,
    fighting_style: str | None,
    equipment: dict[str, SrdEntry],
    class_index: str | None = None,
    wis_mod: int = 0,
) -> int:
    """AC from a character's two armor slots (issue #13) - the same formula
    character_creation._compute_ac originally computed once at creation by
    scanning the whole inventory, now parameterized by the two explicit
    equipped-armor/equipped-shield slots so it can be recomputed whenever
    turn_engine._resolve_equip changes either one. Lives here (not
    character_creation.py) since both creation and turn_engine need it,
    matching weapon_combo_is_legal's own "shared logic lives in rules.py"
    reasoning above.

    10 + Dex modifier (capped per the worn armor's own max_bonus, e.g.
    Medium armor's +2 cap) if unarmored; the worn armor's own base + capped
    Dex bonus otherwise. Plus a shield's flat bonus, plus Defense fighting
    style's +1 (armor only - a shield alone doesn't grant it, per SRD's
    literal text, matching the original _compute_ac's rule exactly). Plus
    Monk's Unarmored Defense (issue #24, `class_index`/`wis_mod` both
    default to "not a Monk"/0 so every pre-existing caller is unaffected):
    10 + DEX mod + WIS mod instead of the plain unarmored base, but only
    while wielding no shield either, per SRD's literal "wearing no armor
    and not wielding a shield" gate - a Monk holding a shield falls back to
    the ordinary unarmored formula (still gets the shield's own flat
    bonus, same as anyone else)."""
    shield_bonus = 0
    if equipped_shield:
        shield_item = equipment.get(equipped_shield)
        if shield_item and shield_item.get("armor_class"):
            shield_bonus = int(shield_item["armor_class"]["base"])

    armor_item = equipment.get(equipped_armor) if equipped_armor else None
    defense_bonus = 1 if fighting_style == "defense" and armor_item is not None else 0

    if armor_item is None:
        unarmored_defense_bonus = (
            wis_mod if class_index == "monk" and equipped_shield is None else 0
        )
        return 10 + dex_mod + unarmored_defense_bonus + shield_bonus + defense_bonus

    ac_info = armor_item["armor_class"]
    base: int = ac_info["base"]
    if ac_info.get("dex_bonus"):
        bonus = dex_mod
        if "max_bonus" in ac_info:
            bonus = min(bonus, ac_info["max_bonus"])
        base += bonus
    return base + shield_bonus + defense_bonus


def weapon_combo_is_legal(
    weapon_indices: list[str], equipment: dict[str, SrdEntry], shield_equipped: bool = False
) -> bool:
    """Whether this set of weapon indices is legal to have simultaneously
    equipped, per a deliberately simplified subset of SRD's real rules: at
    most 2 weapons; a "two-handed"-property weapon must be the only one
    equipped (can't combine with anything else); 2 weapons together must
    both have the "light" property (SRD's actual two-weapon-fighting
    eligibility rule). Doesn't check any weapon-property combination beyond
    that (e.g. doesn't require proficiency, doesn't model versatile's
    one-handed-with-a-shield case specially) - lives here, not
    character_creation.py or turn_engine.py, since both need it: creation
    auto-populates a legal starting loadout, turn_engine's "equip" action
    validates a player-chosen one against the same rule.

    `shield_equipped` (issue #26) folds hand-occupancy into the same check
    rather than a separate one: a shield uses one of a character's two
    hands, same as any one-handed weapon does. 2 already-legal one-handed
    weapons plus a shield would be 3 hands - illegal, even though the pair
    of weapons alone is fine; a single two-handed weapon plus a shield is
    also illegal (it already needs both hands by itself); a single
    one-handed weapon plus a shield is fine (1 + 1 = 2). Found live: a
    character ending up with 2 one-handed weapons *and* a shield
    simultaneously equipped, since equipped_weapons and equipped_shield had
    never been cross-validated against each other at all."""
    if len(weapon_indices) > 2:
        return False
    found = [equipment.get(idx) for idx in weapon_indices]
    if any(item is None for item in found):
        return False
    items = [item for item in found if item is not None]
    if len(items) == 2:
        properties = [{p["index"] for p in (item.get("properties") or [])} for item in items]
        if any("two-handed" in props for props in properties):
            return False
        if not all("light" in props for props in properties):
            return False
        if shield_equipped:
            return False  # 2 one-handed weapons + a shield = 3 hands
    elif len(items) == 1 and shield_equipped:
        single_weapon_properties = {p["index"] for p in (items[0].get("properties") or [])}
        if "two-handed" in single_weapon_properties:
            return False  # a two-handed weapon already needs both hands
    return True


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

_SPELL_RANGE_OVERRIDES_FEET: dict[str, int] = {
    # Issue #37: Produce Flame's SRD `range` field is literally "Self" (it
    # describes where the conjured flame first appears in your hand) - the
    # real 30ft hurl-the-flame attack range only exists in the spell's
    # free-text desc, not in any structured field, so the regex/5ft fallback
    # below silently under-ranges it. Confirmed this is the only spell that
    # needs one: every other attack-roll spell with range "Self"
    # (vampiric-touch) genuinely is Touch-range, so the 5ft fallback is
    # already correct for it - not added here since it isn't wrong.
    "produce-flame": 30,
}


def spell_range_feet(spell: SrdEntry) -> int:
    """A spell's SRD `range` field is a plain string ("120 feet", "Touch",
    "Self") - no {normal, long} structure like weapons, since spells have
    no "beyond normal range" disadvantage tier in 5e; you're either in
    range or you aren't. "Touch"/"Self"/anything unparseable falls back to
    5ft (melee-adjacent) - a safe default for spells whose range genuinely
    is Touch/Self, but wrong for the rare spell (see
    _SPELL_RANGE_OVERRIDES_FEET) whose real attack range only exists in
    free-text flavor, checked first."""
    override = _SPELL_RANGE_OVERRIDES_FEET.get(spell.get("index", ""))
    if override is not None:
        return override
    match = _SPELL_RANGE_RE.search(str(spell.get("range", "")))
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


# --- Condition mechanics (Phase 9A) ---------------------------------------
#
# state.py's ConditionName enumerates all 15 SRD conditions and conditions.py
# can apply/remove/tick them, but until now nothing actually checked for one
# outside "unconscious" (death saves) - blinded, prone, restrained, etc. were
# schema-only. These functions compute what each condition actually does to
# an attack roll, an ability check, or movement speed; callers (turn_engine)
# OR the results into whatever other advantage/disadvantage sources they
# already track (help, dodging, non-proficient armor, attack range).


def condition_attack_advantage(actor: Character, target: Character, distance_feet: int) -> bool:
    """Whether `actor`'s attack against `target` has advantage purely from
    SRD conditions. Blinded/paralyzed/petrified/restrained/stunned/
    unconscious targets are always easier to hit; a prone target is easier
    to hit only from melee range (SRD: ranged attacks against a prone target
    have disadvantage instead - see condition_attack_disadvantage). An
    invisible actor also gets advantage on their own attacks."""
    return (
        has_condition(actor, "invisible")
        or has_condition(target, "blinded")
        or has_condition(target, "paralyzed")
        or has_condition(target, "petrified")
        or has_condition(target, "restrained")
        or has_condition(target, "stunned")
        or has_condition(target, "unconscious")
        or (has_condition(target, "prone") and distance_feet <= 5)
    )


def condition_attack_disadvantage(actor: Character, target: Character, distance_feet: int) -> bool:
    """The disadvantage-side mirror of condition_attack_advantage - an
    actor's own blinded/poisoned/restrained/prone/frightened status, an
    invisible target, a prone target attacked from beyond melee range, or
    exhaustion level 3+ (SRD: disadvantage on attack rolls and saving
    throws)."""
    return (
        has_condition(actor, "blinded")
        or has_condition(actor, "poisoned")
        or has_condition(actor, "restrained")
        or has_condition(actor, "prone")
        or has_condition(actor, "frightened")
        or actor.exhaustion_level >= 3
        or has_condition(target, "invisible")
        or (has_condition(target, "prone") and distance_feet > 5)
    )


def condition_save_disadvantage(character: Character) -> bool:
    """SRD: a restrained creature has disadvantage on DEX saves specifically;
    exhaustion level 3+ gives disadvantage on every saving throw."""
    return character.exhaustion_level >= 3


def condition_check_disadvantage(character: Character) -> bool:
    """SRD: poisoned and frightened both impose disadvantage on ability
    checks (not just attack rolls); exhaustion level 1+ does too."""
    return (
        has_condition(character, "poisoned")
        or has_condition(character, "frightened")
        or character.exhaustion_level >= 1
    )


def effective_speed(character: Character) -> int:
    """Character.speed as authored, adjusted for conditions that reduce it:
    grappled or exhaustion level 5+ reduces speed to 0; exhaustion level 2+
    halves it (SRD rounds down, matching plain integer division)."""
    if has_condition(character, "grappled") or character.exhaustion_level >= 5:
        return 0
    if character.exhaustion_level >= 2:
        return character.speed // 2
    return character.speed


# --- Multiattack sub-action parsing (Phase 9F) ----------------------------
#
# A monster's "Multiattack" action has no structured sub-attack data - only a
# free-text `desc` naming which of the monster's other actions it rolls and
# how many times (e.g. giant-badger: "The badger makes two attacks: one with
# its bite and one with its claws."). Every curated low-CR monster with a
# Multiattack action that names sub-actions this way uses the same "<count>
# with its <name>" phrasing (checked directly against the vendored SRD JSON,
# not assumed) - this is a narrow, explicitly-scoped parser for exactly that
# shape, not a general English parser.

_MULTIATTACK_COUNT_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
}

_MULTIATTACK_SUBACTION_RE = re.compile(
    r"(?P<count>one|two|three|four|five|six|\d+)\s+with\s+its\s+"
    r"(?P<name>[a-z][a-z\s]*?)(?=,|\.|and\b|$)",
    re.IGNORECASE,
)


def multiattack_sub_actions(desc: str, action_names: list[str]) -> list[tuple[str, int]]:
    """Parses a Multiattack action's `desc` text for "<count> with its
    <name>" phrasing and matches each named noun phrase against the
    monster's other real action names (case-insensitive substring match,
    since the desc's noun phrase - "bite" - doesn't always exactly equal the
    action's Title-Case name - "Bite"). Returns (action_name, count) pairs
    in the order they appear in `desc`; a phrase that doesn't match any name
    in `action_names` is silently skipped rather than raising, since a
    monster's Multiattack desc can also mention things this engine has no
    action for (e.g. "or two ranged attacks" as an alternative, not another
    sub-action to add) - callers should treat an empty result as "couldn't
    parse this one" and handle it explicitly."""
    lookup = {name.lower(): name for name in action_names}
    results: list[tuple[str, int]] = []
    for match in _MULTIATTACK_SUBACTION_RE.finditer(desc):
        count_raw = match.group("count").lower()
        count = _MULTIATTACK_COUNT_WORDS.get(count_raw)
        if count is None:
            try:
                count = int(count_raw)
            except ValueError:
                continue
        candidate = match.group("name").strip().lower()
        matched_name = next(
            (orig for lower, orig in lookup.items() if lower in candidate or candidate in lower),
            None,
        )
        if matched_name is not None:
            results.append((matched_name, count))
    return results


def set_exhaustion_level(character: Character, level: int) -> None:
    """Clamps to [0, 6] and applies the one level-6 side effect this engine
    models directly (SRD: a 6th level of exhaustion is death). Levels 1/2/3/5
    are read live by condition_check_disadvantage/condition_attack_
    disadvantage/condition_save_disadvantage/effective_speed rather than
    mutating anything else here. Level 4's "hit point maximum is halved" is
    deliberately not implemented in this pass - it would need a second,
    original max_hp value preserved somewhere to undo cleanly when exhaustion
    later drops back below 4, which is more bookkeeping than this function
    should take on silently; flagged here as a documented gap, not a silent
    one, same spirit as this project's other narrow-scope simplifications."""
    character.exhaustion_level = max(0, min(6, level))
    if character.exhaustion_level >= 6:
        character.is_dead = True

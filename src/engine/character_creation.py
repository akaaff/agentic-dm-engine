"""Deterministic, SRD-driven character creation - no LLM anywhere in this
path. Callers (the API layer, Day 9+) are responsible for presenting choices
to a human and collecting them; this module only validates and derives.

Deliberate simplifications, both documented here and in DECISIONS.md/CLAUDE.md:
- Subraces are not applied - only base race ability bonuses/speed/size.
- Equipment choices (the SRD's nested "starting_equipment_options" trees) are
  not parsed into a choice UI; callers pass a flat `chosen_equipment` list of
  equipment indices, validated only for existence (not against the exact
  option-tree shape a real character sheet would enforce).
- `create_character` always builds a level-1 sheet; level-dependent class
  tables beyond hit die and the level-1 spell slot count (below) aren't in
  the vendored SRD JSON either (`srd.classes["fighter"]["class_levels"]` is a
  bare URL string, not embedded data) - confirmed live, same as the level-1
  spell slots already were.

Phase 9J adds `level_up` (below), which advances an existing Character one
level at a time (roughly levels 1-5 - see PROFICIENCY_BONUS_BY_LEVEL/
SPELL_SLOTS_BY_LEVEL/EXTRA_ATTACK_LEVEL/ABILITY_SCORE_IMPROVEMENT_LEVELS),
hardcoding the same class of well-documented SRD 5.1 facts LEVEL_1_SPELL_SLOTS
already hardcodes, for the same reason.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from src.engine.position import Position
from src.engine.rules import (
    ability_modifier,
    armor_ac,
    class_equipment_options,
    class_spell_indices,
    normalize_skill_name,
    weapon_combo_is_legal,
)
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

HEALING_POTION_INDEX = "potion-of-healing"
"""Same literal turn_engine.HEALING_POTION_INDEX hardcodes independently -
not imported from there to avoid a cross-module dependency for one string,
matching this file's existing EXTRA_EQUIPMENT_INDICES convention above.
Every fresh character starts with exactly one (see the DEFAULT_STARTING_
CONSUMABLES use below) - use_item's healing-potion path (Day 14) was
otherwise permanently unreachable in real play, since nothing ever put one
in a character's inventory: chosen_equipment only ever grants one if a
player explicitly opts in through free-form input, and the wizard's own
equipment picker (GET /characters/equipment) can't offer it as a choice at
all, since a synthetic non-SRD index has neither a weapon_category nor an
armor_category for that endpoint to key off of."""

DEFAULT_STARTING_CONSUMABLES = [HEALING_POTION_INDEX]

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

PROFICIENCY_BONUS_BY_LEVEL: dict[int, int] = {
    1: 2,
    2: 2,
    3: 2,
    4: 2,
    5: 3,
    6: 3,
    7: 3,
    8: 3,
    9: 4,
    10: 4,
    11: 4,
    12: 4,
    13: 5,
    14: 5,
    15: 5,
    16: 5,
    17: 6,
    18: 6,
    19: 6,
    20: 6,
}
"""Not in the vendored SRD JSON (level tables live behind a separate API
endpoint) - this is the well-known SRD 5.1 proficiency-bonus-by-level table
(+2 at 1-4, +3 at 5-8, +4 at 9-12, +5 at 13-16, +6 at 17-20), hardcoded rather
than fetched. Phase 9J's scope only needs levels 1-5, but the full table costs
nothing extra to implement and documents the real fact rather than a
truncated one."""

SPELL_SLOTS_BY_LEVEL: dict[str, dict[int, dict[int, int]]] = {
    "wizard": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "cleric": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "druid": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "sorcerer": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "bard": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "warlock": {
        1: {1: 1},
        2: {1: 2},
        3: {2: 2},
        4: {2: 2},
        5: {3: 2},
    },
    "paladin": {
        1: {},
        2: {1: 2},
        3: {1: 3},
        4: {1: 3},
        5: {1: 4, 2: 2},
    },
}
"""Not in the vendored SRD JSON (level tables live behind a separate API
endpoint) - these are the real PHB full-caster and Pact Magic slot
progressions through character level 5, hardcoded as basic SRD 5.1 game
facts, same precedent as LEVEL_1_SPELL_SLOTS (whose level-1 row this table's
level-1 row exactly reproduces). Wizard/cleric/druid/sorcerer/bard are full
casters (slots per the standard multiclassing-table shape: a 2nd-level slot
first appears at character level 3, a 3rd-level slot at level 5). Warlock is
the SRD's one exception - Pact Magic grants far fewer slots, but at a higher
spell level than a full caster would have at the same character level (a
level-3 Warlock has two 2nd-level slots and nothing else, not one 2nd-level
plus leftover 1st-level slots). Paladin is a half-caster (issue #21, Divine
Smite): no slots at all until level 2, then the standard half-caster
progression - deliberately absent from LEVEL_1_SPELL_SLOTS (a level-1
Paladin really does have zero, per SRD, not a missing-data gap) and only
added here since level_up is the only path that can ever hand a Paladin a
slot."""

EXTRA_ATTACK_LEVEL = 5
"""SRD 5.1: Fighter/Barbarian/Paladin/Ranger gain Extra Attack at level 5 -
one `attack` action resolves two attack rolls instead of one (see
turn_engine._resolve_attack / is_eligible_for_extra_attack below)."""

EXTRA_ATTACK_CLASSES = {"fighter", "barbarian", "paladin", "ranger"}
"""The four base SRD classes whose level-5 class table grants Extra Attack.
(Monk also gets a version of it in the full PHB, but the SRD 5.1 Monk stat
block doesn't include it - not included here.)"""

ABILITY_SCORE_IMPROVEMENT_LEVELS = {4}
"""SRD 5.1 grants an Ability Score Improvement at levels 4/8/12/16/19 - this
pass is scoped to roughly levels 1-5 (see CLAUDE.md Phase 9J), so only level
4 is modeled; the later levels are out of scope, not silently wrong."""

CLASS_RESOURCES_AT_LEVEL_1: dict[str, dict[str, int]] = {
    "fighter": {"second_wind": 1},
    "barbarian": {"rage": 2},
    "wizard": {"arcane_recovery": 1},
}
"""Phase 9I, tier 1 - uses/day for the class features that need a limited
resource (Second Wind, Rage, and issue #25's Arcane Recovery), at level 1.
Not in the vendored SRD JSON any more than LEVEL_1_SPELL_SLOTS is (level
tables live behind a separate API endpoint) - hardcoded SRD 5.1 facts, same
precedent. Fixed at their level-1 value through level_up (Phase 9J) rather
than scaling with level - a documented simplification for this tier-1 pass,
not silently wrong; real Rage uses do scale (2 at 1-2, 3 at 3-5). Arcane
Recovery genuinely never scales (always exactly once/day, real SRD - only
the *slot budget* it grants scales with level, computed fresh each use via
arcane_recovery_slot_budget rather than stored)."""


def arcane_recovery_slot_budget(level: int) -> int:
    """Wizard's Arcane Recovery (issue #25): half your wizard level, rounded
    up - the combined spell level of slots recoverable in one use. `-(-x //
    2)` is ceiling division without importing math.ceil, for an int input."""
    return -(-level // 2)


SPELLS_KNOWN_BY_LEVEL: dict[str, dict[int, int]] = {
    "bard": {1: 4, 2: 5, 3: 6, 4: 7, 5: 8},
    "sorcerer": {1: 2, 2: 3, 3: 4, 4: 5, 5: 6},
}
"""Issue #30: the two SRD 5.1 "Spells Known" casters (as opposed to Cleric/
Druid/Wizard/Paladin, who *prepare* from their whole class list instead -
see PREPARED_CASTER_CLASSES/prepared_spell_count below). Not in the
vendored SRD JSON any more than LEVEL_1_SPELL_SLOTS/SPELL_SLOTS_BY_LEVEL are
(level tables live behind a separate API endpoint) - hardcoded SRD 5.1
facts, same precedent, same levels-1-5 scope. Counts cantrips-known
separately in the real SRD table; this project's cantrips have never been
restricted by a "known" count (ClassDetail.cantrips already lists every
cantrip a class can access, unconditionally) and stay that way here -
deliberately out of this issue's scope, only level-1+ spells are gated."""

PREPARED_CASTER_CLASSES = {"cleric", "druid", "wizard", "paladin"}
"""Issue #30's follow-up phase: unlike a "Spells Known" caster's fixed
per-level table (SPELLS_KNOWN_BY_LEVEL above), a Prepared caster has
standing access to their *entire* class spell list and instead chooses a
working subset - see prepared_spell_count. Paladin is a half-caster
(issue #21) with no spellcasting at all until character level 2
(SPELL_SLOTS_BY_LEVEL["paladin"][1] == {}) - included here for
completeness (level_up needs it once a Paladin reaches level 2), but
prepared_spell_count correctly returns 0 for a level-1 Paladin, so
create_character's validation below naturally requires nothing from one."""


def prepared_spell_count(class_index: str, level: int, ability_mod: int) -> int:
    """Real SRD 5.1 Prepared-caster formula: spellcasting-ability modifier +
    caster level, minimum 1. Paladin's caster level is character level // 2
    (half-caster) and the whole formula is 0 - not floored at 1 - below
    character level 2, matching the class's real "no spellcasting yet"
    rule rather than pretending a level-1 Paladin has one spell to prepare."""
    if class_index == "paladin":
        caster_level = level // 2
        return max(1, ability_mod + caster_level) if caster_level >= 1 else 0
    return max(1, ability_mod + level)


VALID_FIGHTING_STYLES = {"archery", "defense", "dueling"}
"""Phase 9I only implements the mechanical effect of these three SRD
fighting styles (Character.fighting_style, applied in turn_engine.
_pc_attack_params for Archery/Dueling and in _compute_ac below for
Defense) - Great Weapon Fighting/Protection/Two-Weapon Fighting exist in
the SRD but aren't modeled, so they're deliberately not accepted here
rather than silently accepted and then doing nothing."""

FIGHTING_STYLE_CLASSES = {"fighter", "ranger", "paladin"}
"""The three base SRD classes that choose a Fighting Style at level 1."""

VALID_GENDERS = {"male", "female"}
"""Portrait-selection only - SRD races have no gender concept at all
(confirmed against the vendored race JSON), so this carries zero mechanical
weight anywhere in the rules engine. Shared with src/cli/generate_portraits.py
so the wizard's choices and the pre-generated portrait library's filenames
can never drift apart."""

PORTRAIT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "portraits"
"""Root of the pre-generated portrait library (src/cli/generate_portraits.py
writes here; src/api/main.py mounts it as /media/portraits). Two
subdirectories: pc/ (race_class_gender.png) and monsters/ (monster_index.png)
- see generate_portraits.py for the exact naming. Keyed on race x class x
gender only (no hair color) - dropped from an earlier draft of this feature
to cut the PC/companion portrait count 3x (648 -> 216), a deliberate
cost/detail tradeoff, not an oversight."""


class CharacterCreationError(ValueError):
    pass


def validate_standard_array(scores: Mapping[AbilityScore, int]) -> None:
    if sorted(scores.values()) != sorted(STANDARD_ARRAY):
        raise CharacterCreationError(
            f"Ability scores {scores} must be a permutation of the standard array {STANDARD_ARRAY}"
        )


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
    fighting_style: str | None = None,
    gender: str | None = None,
    voice: str | None = None,
    chosen_racial_skills: list[str] | None = None,
    chosen_spells: list[str] | None = None,
    chosen_prepared_spells: list[str] | None = None,
) -> Character:
    srd = srd or load_srd()
    chosen_equipment = chosen_equipment or []
    position = position or Position(x=0, y=0)

    if fighting_style is not None:
        if class_index not in FIGHTING_STYLE_CLASSES:
            raise CharacterCreationError(
                f"{class_index} doesn't choose a Fighting Style (only "
                f"{sorted(FIGHTING_STYLE_CLASSES)} do)"
            )
        if fighting_style not in VALID_FIGHTING_STYLES:
            raise CharacterCreationError(
                f"Unknown or unimplemented fighting style: {fighting_style!r} "
                f"(implemented: {sorted(VALID_FIGHTING_STYLES)})"
            )

    if gender is not None and gender not in VALID_GENDERS:
        raise CharacterCreationError(f"Unknown gender: {gender!r} (valid: {sorted(VALID_GENDERS)})")

    # Half-Elf's Skill Versatility (issue #23): proficiency in two skills of
    # the player's choice, any skill - unlike a class's chosen_skills, SRD
    # places no restriction on which ones. Validated the same way
    # fighting_style is above: required (and only meaningful) exactly for
    # the one race that has it, not silently accepted/ignored for any other.
    if race_index == "half-elf":
        if chosen_racial_skills is None or len(chosen_racial_skills) != 2:
            raise CharacterCreationError(
                "Half-Elf's Skill Versatility requires chosen_racial_skills with exactly "
                "2 skill proficiencies"
            )
        if len(set(chosen_racial_skills)) != 2:
            raise CharacterCreationError("chosen_racial_skills must name 2 different skills")
        for skill in chosen_racial_skills:
            if normalize_skill_name(skill) not in srd.skills:
                raise CharacterCreationError(f"Unknown skill: {skill!r}")
    elif chosen_racial_skills is not None:
        raise CharacterCreationError(
            f"{race_index} doesn't choose racial skills (only half-elf does)"
        )

    race = srd.races.get(race_index)
    if race is None:
        raise CharacterCreationError(f"Unknown race: {race_index}")
    cls = srd.classes.get(class_index)
    if cls is None:
        raise CharacterCreationError(f"Unknown class: {class_index}")

    # "Spells Known" casters (issue #30) - Bard/Sorcerer choose a fixed
    # level-1 spell list at creation, validated the same required/rejected
    # shape as chosen_racial_skills above: required (and only meaningful)
    # exactly for the classes that have it, not silently accepted/ignored
    # for any other (a Fighter with a phantom chosen_spells list would be a
    # silent no-op bug, not caught until someone tried to use it).
    if class_index in SPELLS_KNOWN_BY_LEVEL:
        if chosen_spells is None:
            raise CharacterCreationError(
                f"{class_index} requires chosen_spells "
                f"(exactly {SPELLS_KNOWN_BY_LEVEL[class_index][1]} level-1 spells)"
            )
        _validate_spell_choices(class_index, chosen_spells, srd)
    elif chosen_spells is not None:
        raise CharacterCreationError(f"{class_index} doesn't choose known spells")

    background = srd.backgrounds.get(background_index)
    if background is None:
        raise CharacterCreationError(f"Unknown background: {background_index}")

    validate_standard_array(base_ability_scores)

    final_scores: dict[AbilityScore, int] = base_ability_scores.copy()
    for bonus in race.get("ability_bonuses", []):
        ability: AbilityScore = bonus["ability_score"]["index"].upper()
        final_scores[ability] = final_scores.get(ability, 0) + bonus["bonus"]

    # Prepared casters (Cleric/Druid/Wizard/Paladin) choose their working
    # spell list at creation too - mirrors the "Spells Known" block above,
    # but the required count depends on the spellcasting ability's modifier
    # (prepared_spell_count), so this has to wait until final_scores
    # (including racial bonuses) is known, unlike the fixed-table check above.
    if class_index in PREPARED_CASTER_CLASSES:
        spellcasting = cls.get("spellcasting")
        ability_mod = (
            ability_modifier(final_scores[spellcasting["spellcasting_ability"]["index"].upper()])
            if spellcasting
            else 0
        )
        required_prepared_count = prepared_spell_count(class_index, 1, ability_mod)
        if required_prepared_count > 0:
            if chosen_prepared_spells is None:
                raise CharacterCreationError(
                    f"{class_index} requires chosen_prepared_spells "
                    f"(exactly {required_prepared_count} level-1 spells)"
                )
            _validate_prepared_spell_choices(
                class_index, required_prepared_count, chosen_prepared_spells, srd
            )
        elif chosen_prepared_spells:
            raise CharacterCreationError(f"{class_index} has no spells to prepare yet at level 1")
    elif chosen_prepared_spells is not None:
        raise CharacterCreationError(f"{class_index} doesn't prepare spells")

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
    inventory.extend(DEFAULT_STARTING_CONSUMABLES)

    # chosen_equipment is already a sub-multiset of inventory (appended into
    # it a few lines up) - naively scanning "chosen_equipment, then
    # inventory" as two back-to-back ranges (the original approach) visits
    # each chosen item's own inventory slot a second time. Harmless for the
    # armor loop below (a plain "first one wins" assignment), but a real bug
    # for the weapon loop's *accumulating* candidate list: a single owned
    # light weapon named in chosen_equipment would satisfy weapon_combo_is_
    # legal's "2 weapons, both light" check against itself, leaving
    # equipped_weapons = [idx, idx] - shown wielding two copies of a weapon
    # actually owned once. A plain dedup (e.g. dict.fromkeys) would overcorrect
    # though: a class kit that legitimately grants 2 real daggers (inventory
    # has two separate "dagger" entries) must still offer both as candidates.
    # So this reorders inventory - chosen_equipment's own contributed slots
    # first, the rest following - without changing how many times any index
    # appears, tracking with a Counter which specific occurrences are the
    # ones chosen_equipment already accounts for.
    chosen_remaining = Counter(chosen_equipment)
    equip_candidates: list[str] = []
    _rest: list[str] = []
    for idx in inventory:
        if chosen_remaining[idx] > 0:
            equip_candidates.append(idx)
            chosen_remaining[idx] -= 1
        else:
            _rest.append(idx)
    equip_candidates += _rest

    # Auto-populate a legal starting weapon loadout (Phase C: equipped-weapon
    # tracking) - greedily takes weapon-category items, stopping once a 2nd
    # item wouldn't form a legal combo (rules.weapon_combo_is_legal) or 2 are
    # already equipped, so a fresh character can attack immediately without
    # an explicit "equip" action. `chosen_equipment` (the player's own
    # deliberate pick) is tried before the rest of `inventory` (a class's
    # fixed starting kit, e.g. a Barbarian's 4 javelins or a Rogue's 2
    # daggers) - a player who explicitly chose a battleaxe shouldn't end up
    # with javelins equipped by default just because javelins happen to be
    # listed first in the class's fixed kit.
    equipped_weapons: list[str] = []
    for idx in equip_candidates:
        item = srd.equipment.get(idx)
        if not item or not item.get("weapon_category"):
            continue
        candidate = [*equipped_weapons, idx]
        if weapon_combo_is_legal(candidate, srd.equipment):
            equipped_weapons = candidate
        if len(equipped_weapons) == 2:
            break

    # Same auto-populate approach as equipped_weapons above, for the two
    # armor slots (issue #13) - chosen_equipment tried first so a player's
    # own deliberate armor pick wins over a class's fixed starting kit, then
    # the first non-shield armor item found fills equipped_armor. A shield
    # is different (issue #26): it shares hand-occupancy with
    # equipped_weapons (already finalized above), so the first shield
    # candidate only actually fills equipped_shield if the weapons already
    # chosen leave a hand free (rules.weapon_combo_is_legal's
    # shield_equipped check) - the weapon loop's own pick keeps priority
    # (a player who explicitly chose 2 daggers wants to dual-wield, not
    # wear a shield they happened to also be carrying), rather than
    # reordering these two loops.
    equipped_armor: str | None = None
    equipped_shield: str | None = None
    for idx in equip_candidates:
        item = srd.equipment.get(idx)
        if not item or not item.get("armor_category"):
            continue
        if item["armor_category"] == "Shield":
            if equipped_shield is None and weapon_combo_is_legal(
                equipped_weapons, srd.equipment, shield_equipped=True
            ):
                equipped_shield = idx
        else:
            equipped_armor = equipped_armor or idx
        if equipped_armor is not None and equipped_shield is not None:
            break

    con_mod = ability_modifier(final_scores["CON"])
    dex_mod = ability_modifier(final_scores["DEX"])
    wis_mod = ability_modifier(final_scores["WIS"])
    hp = max(1, cls["hit_die"] + con_mod)
    ac = armor_ac(
        equipped_armor,
        equipped_shield,
        dex_mod,
        fighting_style,
        srd.equipment,
        class_index=class_index,
        wis_mod=wis_mod,
        con_mod=con_mod,
    )

    # chosen_skills can include non-skill proficiencies (e.g. Bard's musical
    # instruments - see CLAUDE.md); only "skill-*" entries count here.
    # Deduplicated (via dict.fromkeys, which preserves order) since a class
    # skill choice and a background's fixed proficiency can genuinely
    # overlap - e.g. Pip Larkspur (Bard, Acolyte) chooses skill-insight
    # *and* Acolyte grants it automatically. Racial skill grants (issue
    # #23) are folded into the same dedup: Elf's Keen Senses always grants
    # Perception, Half-Elf's Skill Versatility grants chosen_racial_skills
    # (already validated to be exactly 2 real skills above) - either can
    # also genuinely overlap with a class/background pick the same way.
    racial_skills: list[str] = []
    if race_index == "elf":
        racial_skills.append("skill-perception")
    elif chosen_racial_skills:
        racial_skills.extend(f"skill-{normalize_skill_name(s)}" for s in chosen_racial_skills)

    skill_proficiencies = list(
        dict.fromkeys(
            [s for s in chosen_skills if s.startswith("skill-")]
            + [
                p["index"]
                for p in background.get("starting_proficiencies", [])
                if p["index"].startswith("skill-")
            ]
            + racial_skills
        )
    )

    saving_throw_proficiencies: list[AbilityScore] = [
        s["index"].upper() for s in cls.get("saving_throws", [])
    ]

    # Bardic Inspiration uses (issue #25, Bard): the one class_resource in
    # this project keyed off an ability score instead of a flat per-level
    # table - real SRD is "your Charisma modifier, minimum of once", not a
    # fixed number the way Second Wind/Rage/Arcane Recovery all are.
    class_resources = dict(CLASS_RESOURCES_AT_LEVEL_1.get(class_index, {}))
    if class_index == "bard":
        class_resources["bardic_inspiration"] = max(1, ability_modifier(final_scores["CHA"]))

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
        equipped_weapons=equipped_weapons,
        equipped_armor=equipped_armor,
        equipped_shield=equipped_shield,
        stats=final_scores,
        proficiency_bonus=2,
        speed=race["speed"],
        race=race["name"],
        class_=cls["name"],
        background=background["name"],
        skill_proficiencies=skill_proficiencies,
        saving_throw_proficiencies=saving_throw_proficiencies,
        class_index=class_index,
        race_index=race_index,
        hit_die_sides=cls["hit_die"],
        class_resources=class_resources,
        fighting_style=fighting_style,
        gender=gender,
        voice=voice,
        known_spells=list(chosen_spells) if chosen_spells is not None else [],
        prepared_spells=list(chosen_prepared_spells) if chosen_prepared_spells is not None else [],
    )


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


def _validate_spell_choices(class_index: str, chosen_spells: list[str], srd: SrdIndex) -> None:
    """Issue #30 - mirrors _validate_skill_choices's exact shape: a level-1
    "Spells Known" caster (Bard/Sorcerer, SPELLS_KNOWN_BY_LEVEL) must choose
    exactly its level-1 count of real, distinct level-1 spells from its own
    SRD spell list. Only level 1 - a fresh character has no higher slots to
    cast anything else with."""
    required_count = SPELLS_KNOWN_BY_LEVEL[class_index][1]
    if len(chosen_spells) != required_count:
        raise CharacterCreationError(
            f"{class_index} requires exactly {required_count} spell choice(s), "
            f"got {len(chosen_spells)}"
        )
    if len(set(chosen_spells)) != len(chosen_spells):
        raise CharacterCreationError(f"Duplicate spell choice in {chosen_spells}")
    allowed = class_spell_indices(class_index, srd, level=1)
    for spell in chosen_spells:
        if spell not in allowed:
            raise CharacterCreationError(f"{spell} is not a valid level-1 spell for {class_index}")


def _validate_prepared_spell_choices(
    class_index: str, required_count: int, chosen: list[str], srd: SrdIndex
) -> None:
    """Mirrors _validate_spell_choices' exact shape for a Prepared caster
    (Cleric/Druid/Wizard/Paladin) - `required_count` is derived from the
    spellcasting ability's modifier (prepared_spell_count), already computed
    by the caller, rather than a fixed per-class table like a "Spells Known"
    caster's own count."""
    if len(chosen) != required_count:
        raise CharacterCreationError(
            f"{class_index} requires exactly {required_count} prepared spell(s), got {len(chosen)}"
        )
    if len(set(chosen)) != len(chosen):
        raise CharacterCreationError(f"Duplicate spell choice in {chosen}")
    allowed = class_spell_indices(class_index, srd, level=1)
    for spell in chosen:
        if spell not in allowed:
            raise CharacterCreationError(f"{spell} is not a valid level-1 spell for {class_index}")


def is_eligible_for_extra_attack(character: Character) -> bool:
    return character.level >= EXTRA_ATTACK_LEVEL and character.class_index in EXTRA_ATTACK_CLASSES


def _validate_ability_score_increase(increase: Mapping[AbilityScore, int]) -> None:
    """Mirrors validate_standard_array's error style. Legal SRD 5.1 ASI
    allocations are exactly +2 to one ability or +1 to two different
    abilities - never a flat +3, a +2 split across two abilities, or a bonus
    to more than two abilities."""
    total = sum(increase.values())
    legal = (len(increase) == 1 and total == 2 and all(v == 2 for v in increase.values())) or (
        len(increase) == 2 and total == 2 and all(v == 1 for v in increase.values())
    )
    if not legal:
        raise CharacterCreationError(
            f"Ability score improvement {dict(increase)} must be +2 to one ability "
            "or +1 to two different abilities"
        )


def level_up(
    character: Character,
    srd: SrdIndex,
    ability_score_increase: dict[AbilityScore, int] | None = None,
    spells_learned: list[str] | None = None,
    prepared_spells: list[str] | None = None,
) -> Character:
    """Advances `character` by exactly one level, recomputing everything the
    same way create_character derives it at level 1 (see the module
    docstring's leveling note) rather than a second, subtly-different
    formula: proficiency bonus from PROFICIENCY_BONUS_BY_LEVEL, HP gain as
    the SRD's fixed "average" value for the class hit die (die/2 + 1, e.g.
    d10 -> 6) plus CON mod - added to both hp and max_hp, not re-derived from
    scratch, since a character can already have taken damage - and spell
    slots refreshed from SPELL_SLOTS_BY_LEVEL for a casting class. An
    Ability Score Improvement is only ever applied at a level in
    ABILITY_SCORE_IMPROVEMENT_LEVELS *and* only when the caller actually
    supplies one - a level-4 character.level_up() call with no
    ability_score_increase argument just doesn't apply one (the caller
    presenting that choice to a human is out of this function's scope, same
    division of responsibility as the module docstring already states for
    equipment/skill choices at creation). `spells_learned` (issue #30,
    Bard/Sorcerer) follows the exact same shape: applied only at a level
    where SPELLS_KNOWN_BY_LEVEL's count actually increases *and* the caller
    supplied the new spell(s) - a level-up with no spells_learned just
    doesn't grow known_spells, same division of responsibility. `prepared_
    spells` (Cleric/Druid/Wizard/Paladin) is gated the same "caller's
    choice, not invented here" way, but unlike spells_learned's additive
    growth, a supplied list *replaces* character.prepared_spells wholesale -
    the real SRD rule is "re-choose your whole working list," not "learn a
    fixed few new ones."

    Calling this repeatedly takes a level-1 character to level 5 one call at
    a time - each call only ever advances by one level."""
    cls = srd.classes.get(character.class_index) if character.class_index else None
    if cls is None:
        raise CharacterCreationError(f"Unknown or missing class for character {character.id!r}")

    character.level += 1
    character.proficiency_bonus = PROFICIENCY_BONUS_BY_LEVEL[character.level]

    con_mod = ability_modifier(character.stats["CON"])
    hp_gain = cls["hit_die"] // 2 + 1 + con_mod
    character.max_hp += hp_gain
    character.hp += hp_gain

    class_slots_by_level = (
        SPELL_SLOTS_BY_LEVEL.get(character.class_index) if character.class_index else None
    )
    if class_slots_by_level is not None:
        slots_this_level = class_slots_by_level.get(character.level)
        if slots_this_level is not None:
            character.spell_slots = dict(slots_this_level)

    # Ki points (issue #24, Monk): unlike Second Wind/Rage's level-1-fixed
    # value (a documented simplification elsewhere in this file), Ki scales
    # every level starting at 2 (ki points = monk level) - without this,
    # Flurry of Blows would be permanently stuck at whatever level 1 gives
    # (0, since Monks have no Ki at all yet), unusable for any leveled Monk.
    if character.class_index == "monk":
        character.class_resources["ki"] = character.level

    # Wild Shape uses (issue #24, Druid): fixed at 2 (unlike Ki, this
    # doesn't scale further within this project's level 1-5 range - only
    # *when* it becomes available changes), but still absent below level 2,
    # mirroring Ki's own "not present at all until the feature exists"
    # shape rather than a premature 0 entry.
    if character.class_index == "druid" and character.level >= 2:
        character.class_resources["wild_shape"] = 2

    if character.level in ABILITY_SCORE_IMPROVEMENT_LEVELS and ability_score_increase is not None:
        _validate_ability_score_increase(ability_score_increase)
        for ability, bonus in ability_score_increase.items():
            character.stats[ability] = character.stats.get(ability, 0) + bonus

    # A Monk's AC depends on WIS (Unarmored Defense) and a Barbarian's on
    # CON (their own, different Unarmored Defense - see armor_ac's own
    # docstring), both on top of DEX, unlike every other AC source in this
    # project - an ASI at level 4 boosting either should actually move
    # their AC, so recompute it here. Scoped to these two classes (not
    # every class) since that's the only case level_up can actually change
    # the AC formula's inputs; a general "AC should recompute on any ASI"
    # gap for everyone else is real but pre-existing and out of scope.
    if character.class_index in ("monk", "barbarian"):
        character.ac = armor_ac(
            character.equipped_armor,
            character.equipped_shield,
            ability_modifier(character.stats["DEX"]),
            character.fighting_style,
            srd.equipment,
            class_index=character.class_index,
            wis_mod=ability_modifier(character.stats["WIS"]),
            con_mod=ability_modifier(character.stats["CON"]),
        )

    # Bardic Inspiration uses (issue #25, Bard): recomputed after the ASI
    # block above (not before, unlike Ki/Wild Shape) since CHA - the one
    # thing this resource's count actually depends on - could itself be
    # the ability an ASI just raised.
    if character.class_index == "bard":
        character.class_resources["bardic_inspiration"] = max(
            1, ability_modifier(character.stats["CHA"])
        )

    # Known-spell growth (issue #30, Bard/Sorcerer): mirrors the ASI block's
    # "gated by level, only acts if the caller supplied one" shape exactly.
    # class_spells_known_by_level.get(character.level - 1, 0) - not
    # character.level itself - since the *previous* level's count is what
    # was already known; level 1 has no "level 0" entry, so a first-ever
    # level-up (1 -> 2) correctly treats the prior count as 0 spells... but
    # a level-1 character already has SPELLS_KNOWN_BY_LEVEL[class][1] known
    # from create_character, not 0 - so the previous level's *real* entry
    # (character.level - 1) is looked up directly, never assumed to be 0
    # except for a class with no entry at all (not a "Spells Known" caster).
    class_spells_known_by_level = SPELLS_KNOWN_BY_LEVEL.get(character.class_index or "")
    if class_spells_known_by_level is not None and spells_learned is not None:
        previously_known = class_spells_known_by_level.get(character.level - 1, 0)
        now_known = class_spells_known_by_level.get(character.level, previously_known)
        expected_new = now_known - previously_known
        if expected_new > 0:
            if len(spells_learned) != expected_new:
                raise CharacterCreationError(
                    f"{character.class_index} learns exactly {expected_new} new spell(s) at "
                    f"level {character.level}, got {len(spells_learned)}"
                )
            if len(set(spells_learned)) != len(spells_learned):
                raise CharacterCreationError(f"Duplicate spell choice in {spells_learned}")
            allowed = class_spell_indices(character.class_index or "", srd)
            for spell in spells_learned:
                if spell not in allowed:
                    raise CharacterCreationError(
                        f"{spell} is not a valid spell for {character.class_index}"
                    )
                if spell in character.known_spells:
                    raise CharacterCreationError(f"{character.id} already knows {spell}")
            character.known_spells = [*character.known_spells, *spells_learned]

    # Prepared-spell re-selection (Cleric/Druid/Wizard/Paladin): computed
    # after the ASI block above for the same reason bardic_inspiration is -
    # the spellcasting ability's modifier could itself be what an ASI just
    # raised. Unlike known_spells' additive growth, a supplied list
    # *replaces* character.prepared_spells wholesale, matching the real SRD
    # rule (a Prepared caster doesn't "learn" new prepared spells - they
    # already have standing access to the whole class list, they just get
    # to prepare more of it as their count grows with level).
    if character.class_index in PREPARED_CASTER_CLASSES and prepared_spells is not None:
        spellcasting = cls.get("spellcasting")
        ability_mod = (
            ability_modifier(character.stats[spellcasting["spellcasting_ability"]["index"].upper()])
            if spellcasting
            else 0
        )
        required_count = prepared_spell_count(character.class_index, character.level, ability_mod)
        if len(prepared_spells) != required_count:
            raise CharacterCreationError(
                f"{character.class_index} prepares exactly {required_count} spell(s) at "
                f"level {character.level}, got {len(prepared_spells)}"
            )
        if len(set(prepared_spells)) != len(prepared_spells):
            raise CharacterCreationError(f"Duplicate spell choice in {prepared_spells}")
        allowed = class_spell_indices(character.class_index, srd)
        for spell in prepared_spells:
            if spell not in allowed:
                raise CharacterCreationError(
                    f"{spell} is not a valid spell for {character.class_index}"
                )
        character.prepared_spells = list(prepared_spells)

    return character

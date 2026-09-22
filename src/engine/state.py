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
    "blessed",
]
"""Exhaustion isn't in this list - SRD exhaustion is a leveled (0-6) effect
with per-level rules, not an on/off tag like these - see
Character.exhaustion_level and rules.set_exhaustion_level.

"blessed" isn't one of the SRD's 15 real conditions (it's the Bless spell's
own effect, issue #57) - added here anyway to reuse this exact apply/tick/
remove-on-expiry machinery rather than building a second, parallel one for
a single spell. rules.blessed_bonus is the only place that reads it."""

AbilityScore = Literal["STR", "DEX", "CON", "INT", "WIS", "CHA"]


class Condition(BaseModel):
    name: ConditionName
    duration_rounds: int | None = None
    """None means indefinite - removed by an explicit effect, not by ticking down."""
    source: str | None = None


class WildShapeSnapshot(BaseModel):
    """Druid's Wild Shape (issue #24) - exactly what turn_engine._resolve_
    wild_shape swaps to the beast's own values and _resolve_revert_wild_
    shape restores, taken the moment a transformation starts. `hp` is the
    Druid's own hit points at that moment (a *voluntary* revert restores
    this unchanged, per SRD - only a forced revert from dropping to 0 HP in
    beast form carries damage over, see _apply_damage_and_handle_downing)."""

    hp: int
    max_hp: int
    ac: int
    stats: dict[AbilityScore, int]
    speed: int
    equipped_weapons: list[str]
    monster_index: str | None


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
    innate_spell_uses_remaining: dict[str, int] = {}
    """Issue #22 (monster Innate Spellcasting): normalized spell index (e.g.
    "ray-of-enfeeblement") -> uses left today, populated at creation (see
    encounter.monster_to_character) from the monster's SRD stat block for
    each "N/day" innate spell only - an "at will" one is never tracked here
    at all (unlimited, no key). Empty for PCs/companions, which spend
    `spell_slots` instead - a monster's innate spells are a completely
    separate SRD mechanic with no slot concept of their own."""
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
    wild_shape_beast_index: str | None = None
    """Druid's Wild Shape (issue #24) - the SRD monster index currently
    transformed into, or None in normal form. While set, `monster_index` is
    ALSO temporarily set to this same value (turn_engine._resolve_wild_
    shape) - a wild-shaped Druid becomes, for attack-resolution purposes,
    exactly a monster character that still happens to have is_pc=True (see
    _resolve_attack's existing monster_index-only branch, which never
    checks is_pc), reusing that whole path for free instead of building a
    parallel one."""
    pre_wild_shape_snapshot: WildShapeSnapshot | None = None
    """The Druid's own hp/max_hp/ac/stats/speed/equipped_weapons/
    monster_index from the moment `wild_shape_beast_index` was set, restored
    by turn_engine._resolve_revert_wild_shape (or a forced revert - see
    _apply_damage_and_handle_downing)."""
    skill_proficiencies: list[str] = []
    """"skill-x" indices (same format as chosen_skills), populated by
    character_creation.py from chosen class skills + the background's fixed
    proficiencies - previously derived at creation time but never stored."""
    known_spells: list[str] = []
    """Issue #30: normalized SRD spell indices this character actually knows
    and can cast (level 1+ only - cantrips remain unrestricted, out of this
    issue's scope). Only populated/enforced for "Spells Known" casters
    (Bard, Sorcerer) - see character_creation.SPELLS_KNOWN_BY_LEVEL and
    turn_engine._resolve_cast_spell's restriction. Empty (and meaningless)
    for every other class, including "Prepared" casters (Cleric/Druid/
    Wizard/Paladin) - see prepared_spells below, the analogous field for
    that mechanic."""
    prepared_spells: list[str] = []
    """Issue #30's follow-up phase: normalized SRD spell indices a
    "Prepared" caster (Cleric, Druid, Wizard, Paladin) currently has
    prepared and can cast (level 1+ only, same cantrip carve-out as
    known_spells). Unlike known_spells' fixed per-level table, the required
    count depends on the spellcasting ability's modifier - see
    character_creation.prepared_spell_count and turn_engine._resolve_cast_
    spell's restriction. Empty (and meaningless) for every other class,
    including "Spells Known" casters (Bard/Sorcerer), which use
    known_spells instead - the two mechanics are mutually exclusive per
    class, never both populated for the same character."""
    saving_throw_proficiencies: list[AbilityScore] = []
    """Populated by character_creation.py from the SRD class's `saving_throws`
    (e.g. Fighter: STR, CON) - see rules.saving_throw_bonus. Empty for
    monsters (they attack/save via their own stat block, not this path)."""
    exhaustion_level: int = 0
    """0-6, per SRD's escalating exhaustion table - see
    rules.set_exhaustion_level/effective_speed/condition_check_disadvantage."""
    is_dodging: bool = False
    """True from resolving a "dodge" action until the start of this
    character's own next turn (cleared there, not by a fixed round count -
    it depends on whose turn it is next, which conditions.tick_conditions'
    once-per-round model doesn't represent)."""
    has_help_advantage: bool = False
    """Set by another character resolving "help" targeting this one;
    consumed (cleared) by this character's next attack or skill check,
    whichever comes first."""
    bardic_inspiration_die: int | None = None
    """Bardic Inspiration (issue #25, Bard) - die sides currently banked on
    this character (a Bard targeting an ally, mirroring has_help_advantage's
    "set by one actor targeting another" shape), added to and consumed by
    this character's next attack roll. Real SRD also lets it apply to an
    ability check or saving throw of the holder's choice - narrowed to
    attack rolls only here (a documented, smaller first cut rather than
    re-threading every d20 call site the way issue #23's Lucky trait did)."""
    true_strike_advantage: bool = False
    """True Strike (issue #55 spell audit): grants advantage on this
    character's own next attack roll, consumed (cleared) the same moment
    has_help_advantage is - the identical "banked, one-shot, cleared on the
    next attack" shape, just a flat flag instead of a die (True Strike has
    no die to roll, just advantage)."""
    temporary_ac_bonus: int = 0
    """Shield of Faith (issue #55 spell audit) and any future flat-AC-bonus
    spell - added on top of armor_ac's own computed total (rules.armor_ac
    reads this the same way it already reads equipped_armor/shield/
    fighting_style), cleared when the spell's duration ends (concentration
    breaking or a round-count expiry - see _resolve_cast_spell's ac_buff
    branch for how it's set)."""
    mage_armor_active: bool = False
    """Mage Armor (issue #55 spell audit): while unarmored, AC becomes
    13 + DEX mod instead of the plain 10 + DEX mod unarmored base - checked
    in rules.armor_ac_breakdown alongside Monk/Barbarian's own Unarmored
    Defense variants. Does nothing while actual armor is worn, matching
    real SRD ("while you are wearing no armor")."""
    bonus_action_used: bool = False
    """Phase 9H: True once this character has cast a bonus-action spell
    (SRD casting_time "1 bonus action", e.g. Healing Word) this turn.
    Reset in turn_engine._advance_turn_skipping_dead when a turn actually
    advances TO this character - NOT at the top of every resolve_action
    call like is_dodging, since a bonus-action spell doesn't end the turn
    (see resolve_action's ends_turn): this same actor's very next
    resolve_action call (their main action, still the same real turn) must
    still see this as True, or a second bonus-action cast that turn would
    be wrongly allowed. A second attempt while still True is rejected."""
    movement_used_feet: int = 0
    """Feet of this turn's movement budget already spent - "move" no longer
    ends the turn (a real bug fix, not this project's own simplification:
    real SRD gives every turn both a movement budget AND a separate action,
    found live when a player/monster moving adjacent to a target had no way
    to then attack the same turn). Resets in _advance_turn_skipping_dead on
    the same "only when the turn actually advances to this character"
    schedule as bonus_action_used/equip_used_this_turn, for the same
    reason: a follow-up attack in the same real turn must still see
    whatever movement this character already spent. `dash` still ends the
    turn (it's SRD's own action, unlike plain movement) - unaffected."""
    disengaged_this_turn: bool = False
    """Phase 9H: True after resolving "disengage" - gives that verb the
    real mechanical effect it lacked since Day 13 (its own module docstring
    used to note "no opportunity-attack mechanic yet for it to interact
    with"). Checked (and consumed/cleared) by turn_engine._resolve_move's
    opportunity-attack trigger, not reset alongside is_dodging/
    bonus_action_used - this engine's one-action-per-turn model has no way
    for a character to disengage and then also move within the literal
    same game-turn, so this flag has to survive every other actor's turns
    in between and is only spent the next time this character actually
    moves (whether or not a hostile was even adjacent for it to matter) -
    a documented simplification of SRD's "only protects this turn" rule."""
    reaction_used_this_round: bool = False
    """Phase 9H: at most one opportunity attack (the only reaction this
    engine models) per character per round, per SRD - reset for every
    character when the round number advances, not per-turn like the two
    flags above (a reaction's economy is round-scoped, not turn-scoped)."""
    equipped_weapons: list[str] = []
    """The character's currently active weapon set (SRD equipment indices,
    at most 2, both light if 2) - see turn_engine._resolve_equip. Populated
    automatically at creation from the character's starting inventory
    (character_creation.create_character), then only changed by an explicit
    "equip" action. attack resolution (turn_engine._pc_attack_params) only
    ever matches a weapon from this list, not the whole inventory - "own it"
    and "have it equipped" are deliberately different things."""
    equip_used_this_turn: bool = False
    """True once this character has used their one free "equip" object
    interaction this turn - reset in turn_engine._advance_turn_skipping_dead
    (fires only when a turn genuinely advances TO this character), not at
    the top of every resolve_action call, for the exact same reason
    bonus_action_used isn't (see that field's docstring): "equip" doesn't
    end the turn, so this same actor's very next resolve_action call (e.g.
    an attack right after equipping) is still the same real turn and must
    still see this as True."""
    equipped_armor: str | None = None
    """The character's currently worn armor (SRD equipment index, non-shield
    armor_category), or None if unarmored - issue #12. Mirrors
    equipped_weapons's role but as a single slot, not a list: armor has no
    two-handed/light-pairing legality question the way weapons do, just "at
    most one piece worn." Populated at creation the same greedy chosen-
    equipment-then-inventory scan as equipped_weapons (character_creation.
    create_character), changed only by an explicit "equip" action
    (turn_engine._resolve_equip) - which also recomputes `ac` via
    rules.armor_ac whenever this or equipped_shield changes, since AC can no
    longer be a fixed-at-creation value once armor is swappable."""
    equipped_shield: str | None = None
    """Same as equipped_armor but for a shield (armor_category == "Shield")
    - a separate slot per SRD, not mutually exclusive with equipped_armor."""
    class_index: str | None = None
    """Set only for PCs/companions (mirrors monster_index) - lets the turn
    engine re-look-up the SRD class's spellcasting ability for cast_spell."""
    race_index: str | None = None
    """Set only for PCs/companions - mirrors class_index's pattern, added
    for the portrait feature: `race` stores the SRD display name ("Human"),
    not the lowercase index ("human") a portrait filename needs, and
    there's no reliable string transform from one to the other for every
    vendored race (e.g. "Half-Elf" -> "half-elf" happens to work, but
    guessing is fragile where a stored index isn't)."""
    gender: str | None = None
    """Set only for PCs/companions - one of character_creation.
    VALID_GENDERS. Purely a portrait-selection field: SRD races have no
    gender concept at all (confirmed against the vendored race JSON), so
    this carries no mechanical weight anywhere in the rules engine."""
    hit_die_sides: int = 8
    """The class's hit die size (e.g. 8 for a d8 class) - set at creation
    from the SRD class's `hit_die` (character_creation.create_character).
    Default of 8 is an arbitrary placeholder for characters that never go
    through that path (monsters never rest - a fresh encounter respawns
    them, not this mechanic) - see src/engine/resting.py."""
    hit_dice_remaining: int = 1
    """Level-1-only project (Phase 9J adds real leveling): exactly one hit
    die per character, spent on a short rest (src/engine/resting.py) and
    restored to 1 on a long rest."""
    class_resources: dict[str, int] = {}
    """Phase 9I: uses remaining for a class feature keyed by a short name
    (e.g. {"rage": 2}, {"second_wind": 1}) - populated at creation from
    character_creation.CLASS_RESOURCES_AT_LEVEL_1, spent by that feature's
    verb, restored by short/long rest (src/engine/resting.py) per whichever
    rest type the specific resource recovers on."""
    fighting_style: str | None = None
    """Phase 9I: one of character_creation.VALID_FIGHTING_STYLES, chosen at
    creation for a class in FIGHTING_STYLE_CLASSES (Fighter/Ranger/
    Paladin) - a static bonus applied in turn_engine._pc_attack_params
    (Archery, Dueling) or baked into `ac` directly at creation (Defense,
    since this engine computes AC once rather than deriving it per-attack)."""
    is_raging: bool = False
    """Phase 9I: True while raging (Barbarian) - grants resistance to
    bludgeoning/piercing/slashing damage (turn_engine._apply_damage_and_
    handle_downing) and a flat melee-STR damage bonus (_pc_attack_params).
    Unlike is_dodging, this engine doesn't model rage's real duration/
    maintenance conditions (1 minute, ends early if you don't attack or
    take damage) - a documented simplification, not silent - it persists
    until explicitly cleared by a rest (src/engine/resting.py)."""
    used_relentless_endurance_this_rest: bool = False
    """Half-Orc's Relentless Endurance trait (issue #23) - True once this
    character has dropped to 1 HP instead of 0 this way, until their next
    long rest (see resting.apply_long_rest and rules.has_relentless_
    endurance). Always False for anyone but a Half-Orc PC/companion, since
    nothing ever sets it otherwise."""
    sneak_attack_used_this_turn: bool = False
    """Phase 9I: True once this character's Sneak Attack (Rogue) has
    triggered this turn - SRD allows it once per turn, only on an actual
    hit. Reset at the top of resolve_action like is_dodging (safe here,
    unlike bonus_action_used/disengaged_this_turn: a plain `attack` action
    only ever produces one resolve_action call per real turn - this engine
    has no two-weapon-fighting bonus-action offhand attack yet for a Rogue
    to trigger a second attack roll within the same turn)."""
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
    level: int = 1
    """Character level (Phase 9J) - 1 at creation, incremented one at a time
    by character_creation.level_up. Scoped to roughly 1-5 for this pass (see
    character_creation.PROFICIENCY_BONUS_BY_LEVEL/SPELL_SLOTS_BY_LEVEL)."""
    concentrating_on: str | None = None
    """The name of the concentration spell this character is currently
    sustaining (e.g. "Hold Person"), or None. Set by turn_engine._resolve_cast_spell
    when a spell with the SRD's `concentration: true` field is cast (clearing
    whatever was set before - casting a new concentration spell always drops
    the old one, per SRD); cleared either there or when a CON save forced by
    taking damage while concentrating (Phase 9D) fails. This engine doesn't
    yet model an actual ongoing effect for any concentration spell (no
    buff/debuff-over-time mechanic exists to remove) - this field only
    tracks *that* a character is concentrating and *on what*, not what
    removing it would undo."""


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

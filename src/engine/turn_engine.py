"""Dispatches a ParsedAction against a GameState: validates it's the actor's
turn, derives attack/damage parameters (from equipped weapon + stats for
PCs, from the SRD stat block's actions for monsters), calls into rules.py
for the actual dice math, appends Events, checks victory/defeat, and
advances the turn. This is the one place per-turn orchestration lives - Day
11 wraps this exact function as the LangGraph rules_engine node.

Weapon/armor proficiency is enforced here against the actor's class (see
rules.class_equipment_options/is_class_proficient_with/
has_non_proficient_armor): an equipped weapon the class isn't proficient
with drops the proficiency bonus from the attack roll, and non-proficient
armor/shield imposes disadvantage on attack rolls and STR/DEX skill checks,
per SRD. In practice this can only trigger from hand-authored data (e.g. a
companion YAML) since the character creator only ever offers proficient
gear - added specifically because that exact mistake shipped once (Sister
Mira's chain-mail) and the engine had no way to notice.

Attack range is enforced too (see rules.weapon_range_feet/
monster_action_range_feet): a target beyond a melee weapon's reach (5ft,
10ft with the "reach" property) or a ranged weapon's long range is rejected
outright; beyond normal but within long range imposes disadvantage, per
SRD. Caught live (a screenshot of a combat grid showing the attacker and
target several squares apart, with a melee hit narrated anyway) - monster_ai
now closes distance before attacking instead of hitting from anywhere on
the map; a companion's free-text-parsed attack can still be rejected as
out-of-range if the LLM doesn't reason about position (same class of gap as
the already-documented "closest goblin" one - not fixed here).

A move/dash is also rejected if its destination square is already occupied
by another living character - caught live right after the range-enforcement
fix above shipped: a companion's free-text-declared move landed exactly on
the human player's own square (both characters visually stacked on one
combat-grid token). Only the final destination is checked, not squares
passed through mid-path - this engine has no opportunity-attack mechanic
and doesn't distinguish ally from enemy squares for pass-through purposes,
so checking every intermediate square would be effort spent on a
distinction nothing else in the engine cares about yet.

Phase 9A (a full 5e-rules-completeness audit, not a live-play find) wired
real mechanical effects into the SRD conditions - previously schema-only
tags (state.ConditionName) that nothing but "unconscious" ever checked. See
rules.condition_attack_advantage/condition_attack_disadvantage/
condition_check_disadvantage/effective_speed/saving_throw_bonus for exactly
what each condition (and exhaustion, now a leveled 0-6 field rather than one
of those tags) does to an attack roll, an ability check, or movement speed.
Deliberately still narrow: the "can this actor act at all" side of
paralyzed/petrified/stunned/incapacitated (they should force an automatic
end_turn, not just modify rolls) is Phase 9B, not this one; exhaustion
level 4's "hit point maximum is halved" is explicitly not implemented (see
rules.set_exhaustion_level's docstring for why).

Phase 9J (character leveling) adds Extra Attack: an eligible PC (see
character_creation.is_eligible_for_extra_attack - level 5+ Fighter/
Barbarian/Paladin/Ranger) resolves two attack rolls for a single `attack`
action instead of one. Phase 9F adds the monster equivalent: a "Multiattack"
action (see _resolve_multiattack/rules.multiattack_sub_actions) rolls each
of its named sub-attacks as its own attack_roll event. Both funnel through
the same _resolve_single_attack helper (one full attack roll: range check,
advantage/disadvantage, damage, downing) so unconscious-hit handling,
condition effects, and every other per-attack-roll rule apply identically
regardless of which of the two multi-roll mechanisms is in play. Phase 9F
also adds ranged-attack-while-engaged disadvantage (_has_adjacent_hostile,
SRD's "Ranged Attacks in Close Combat" - not previously enforced at all) and
gives "hazard" terrain (previously declared in TerrainType but with zero
mechanical effect - see _resolve_move) a small fixed damage-on-entry effect,
matching the caltrops-like "littered with bones" flavor of the one hazard
square this project has authored so far (data/campaigns/encounters/
wolf_den.yaml).

Phase 9H adds a narrow slice of real action economy on top of the
one-action-per-turn model above: a bonus-action spell (SRD casting_time "1
bonus action", e.g. Healing Word - see _is_bonus_action_spell) doesn't end
the turn, so the actor still gets their main action afterward, gated by a
new Character.bonus_action_used so only one such cast is allowed per turn.
"disengage" finally has a real effect too (Character.disengaged_this_turn),
checked by the one reaction this engine models: an opportunity attack,
triggered in _resolve_move when a character moves out of a hostile's 5ft
reach without having disengaged, capped at one reaction per reactor per
round (Character.reaction_used_this_round) per SRD. General reactions
(Shield, Counterspell, a readied action) remain out of scope - resolving
one on someone else's turn mid-resolution is a bigger structural change
than this pass takes on.

Phase 9I adds four representative level-1 class features, each gated on
Character.class_index so they only ever apply to the class that grants
them: Fighting Style (Archery/Defense/Dueling - a static bonus chosen at
creation, applied in _pc_attack_params or baked into `ac` directly for
Defense), Second Wind and Rage (both new bonus-action verbs sharing 9H's
ends_turn=False mechanism, each spending a Character.class_resources use
restored by a short or long rest respectively - see resting.py), and
Sneak Attack (Rogue, automatic on a qualifying hit rather than its own
verb - see _resolve_single_attack). Rage's resistance/damage-bonus and
Sneak Attack's dice are the only Phase 9I mechanics that touch the shared
attack-resolution path; Fighting Style and the two new verbs are otherwise
self-contained.

Deliberate simplifications (documented, not silent):
- Movement takes an explicit path (list of intermediate squares) in
  params["path"], not just a destination - real pathfinding around
  obstacles is a future concern, not what this engine validates.
- Skill checks (Day 13) use a single default DC (no per-scene DC data
  exists yet - that's campaign/scene content, not engine scope).
- "cast_spell" (Day 14: attack-roll spells only; Phase 9D added save-based,
  heal, multi-target, and concentration) now resolves attack-roll spells
  (SRD `attack_type`), save-based spells (`dc`, full/half/no damage per
  `dc_success`), and heal spells (`heal_at_slot_level`, e.g. Cure Wounds) -
  each independently across every id in `ParsedAction.targets` when present.
  A save-based spell with no damage at all (e.g. Hold Person) rolls the save
  and stops there - Phase 9D doesn't apply the spell's actual condition on a
  failure, since no verb/spell grants one yet. A genuinely no-roll,
  non-heal spell like Magic Missile (none of the three fields) still raises
  a clear error. Concentration (`Character.concentrating_on`) is tracked
  and can break from a failed CON save on taking damage, but this engine
  still doesn't model removing an ongoing effect - no concentration spell
  applies one yet for that to matter.
- "use_item" (Day 14) only resolves a single hardcoded item
  (potion-of-healing) - any other item name raises a clear error.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace

from src.engine.actions import ParsedAction
from src.engine.character_creation import (
    PREPARED_CASTER_CLASSES,
    SPELLS_KNOWN_BY_LEVEL,
    is_eligible_for_extra_attack,
)
from src.engine.conditions import apply_condition, has_condition, remove_condition, tick_conditions
from src.engine.dice import RollResult, roll
from src.engine.encounter import monster_to_character
from src.engine.events import Event
from src.engine.movement import can_afford_move, move_cost_feet
from src.engine.position import Position, distance_feet
from src.engine.rules import (
    AttackResult,
    ability_check_modifier,
    ability_modifier,
    apply_damage,
    armor_ac,
    bardic_inspiration_die_sides,
    blessed_bonus,
    condition_attack_advantage,
    condition_attack_disadvantage,
    condition_check_disadvantage,
    condition_save_disadvantage,
    condition_spell_spec,
    effective_speed,
    has_lucky_trait,
    has_non_proficient_armor,
    has_relentless_endurance,
    is_class_proficient_with,
    is_monk_weapon,
    magic_missile_dart_count,
    max_wild_shape_cr,
    monk_martial_arts_die_sides,
    monster_action_range_feet,
    monster_damage_multiplier,
    monster_has_pack_tactics,
    monster_innate_spellcasting,
    monster_is_immune_to_condition,
    monster_is_undead_or_fiend,
    monster_saving_throw_bonus,
    multiattack_sub_actions,
    normalize_skill_name,
    normalize_spell_name,
    resolve_attack,
    resolve_saving_throw,
    resolve_skill_check,
    saving_throw_bonus,
    skill_ability,
    spell_damage_notation,
    spell_dc_info,
    spell_mechanic,
    spell_range_feet,
    weapon_combo_is_legal,
    weapon_range_feet,
    weapon_throw_range_feet,
    wild_shape_beast_is_allowed,
)
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import (
    AbilityScore,
    Character,
    Condition,
    ConditionName,
    GameState,
    WildShapeSnapshot,
)
from src.engine.turn_order import next_turn

_DICE_NOTATION_RE = re.compile(r"(\d+)d(\d+)([+-]\d+)?")

DEFAULT_SKILL_CHECK_DC = 13
""""Medium" difficulty per the DMG's DC guidelines - used whenever a scene
doesn't specify its own DC, which is always true right now (no scene/
skill_challenge content wires a DC through yet)."""

HEALING_POTION_INDEX = "potion-of-healing"
HEALING_POTION_DICE = "2d4+2"
"""Not in the vendored SRD JSON - see character_creation.EXTRA_EQUIPMENT_INDICES
for why (the Magic Items endpoint isn't vendored, and its entries don't carry
machine-readable mechanical data anyway - this amount is straight from the
SRD 5.1 text)."""

HAZARD_DAMAGE = 1
HAZARD_DAMAGE_TYPE = "piercing"
"""Phase 9F: "hazard" terrain (TerrainType, position.py) had zero mechanical
effect until now - confirmed via `grep -rn '"hazard"' src/` finding only the
type declaration. The one hazard square this project has authored so far
(data/campaigns/encounters/wolf_den.yaml, in a den described as "littered
with bones") is functionally a bed of sharp bone/rock shards underfoot -
close enough to the SRD's own Caltrops item (vendored in
data/srd/5e-SRD-Equipment.json) to reuse its exact numbers rather than
invent new ones: "Any creature that enters the area... take[s] 1 piercing
damage." This engine doesn't model the caltrops' DC 15 DEX save (no hazard
authoring format carries a save DC yet) - entering a hazard square always
deals this flat, undodgeable amount, a deliberate simplification of the
full caltrops rule, not a different game fact."""

_RAGE_RESISTANT_DAMAGE_TYPES = {"bludgeoning", "piercing", "slashing"}
"""Phase 9I: Rage grants resistance to these three damage types per SRD -
checked in _apply_damage_and_handle_downing against a raging target."""

SECOND_WIND_DICE = "1d10"
"""SRD 5.1: Second Wind (Fighter) heals 1d10 + fighter level - the die size
is a fixed game fact, not derivable from anything else on Character."""

RAGE_DAMAGE_BONUS = 2
"""SRD 5.1: Rage's flat melee-STR damage bonus at low levels (+2 through
character level 8; it increases at higher levels, out of this pass's
roughly-1-5 scope, same boundary as PROFICIENCY_BONUS_BY_LEVEL/
SPELL_SLOTS_BY_LEVEL in character_creation.py)."""


class TurnEngineError(ValueError):
    pass


@dataclass(frozen=True)
class PendingBardicChoice:
    """Issue #53: everything needed to resume a single attack roll once the
    Bardic Inspiration holder decides whether to spend their banked die -
    captured the instant a probe roll (made WITHOUT the die) comes back a
    miss that isn't a natural 1 (a natural 1 is an automatic miss no bonus
    can fix, so there's nothing to offer). Deliberately scoped to a PC's
    single, non-Extra-Attack `attack` action (see _resolve_attack's own
    `defer_bardic_choice` wiring) - Extra Attack/Multiattack/spell attacks
    still auto-apply the die immediately, exactly as before this issue, a
    documented narrowing rather than threading the pause through every
    attack-roll call site in one pass."""

    holder_id: str
    target_id: str
    natural: int
    total_without_die: int
    defender_ac: int
    die_sides: int
    damage_dice_count: int
    damage_dice_sides: int
    damage_bonus: int
    damage_type: str
    source_name: str
    attack_bonus_breakdown: list[tuple[str, int]]
    force_critical: bool
    is_finesse_or_ranged: bool
    had_advantage: bool
    smite_slot_level: int | None = None


class BardicChoicePending(Exception):  # noqa: N818 - a control-flow signal, not an error
    """Raised instead of finalizing an attack roll when the actor holds a
    Bardic Inspiration die and the roll (without it) would otherwise miss -
    caught specifically in api/ws/session.py, which stores `.choice` on the
    Session, sends the offer to whichever connection controls the holder,
    and pauses the turn loop until a bardic_inspiration_response message
    resumes it via resolve_pending_bardic_choice. Not a TurnEngineError:
    this isn't a rejection, so it must never be caught by the generic
    "something went wrong" handling every other action rejection uses."""

    def __init__(self, choice: PendingBardicChoice) -> None:
        super().__init__("Bardic Inspiration choice pending")
        self.choice = choice


def parse_dice_notation(notation: str) -> tuple[int, int, int]:
    """ "1d6+2" -> (count=1, sides=6, bonus=2). Bonus defaults to 0."""
    match = _DICE_NOTATION_RE.fullmatch(notation.replace(" ", ""))
    if not match:
        raise TurnEngineError(f"Invalid dice notation: {notation!r}")
    count, sides, bonus = match.groups()
    return int(count), int(sides), int(bonus) if bonus else 0


@dataclass(frozen=True)
class AttackParams:
    attack_bonus: int
    damage_dice_count: int
    damage_dice_sides: int
    damage_bonus: int
    damage_type: str
    source_name: str
    """Weapon or monster-action name, for the Event payload/narration."""
    range_normal_feet: int
    range_long_feet: int | None
    """None for melee (no "beyond normal range" concept) - see
    rules.weapon_range_feet/monster_action_range_feet."""
    thrown_range_normal_feet: int | None = None
    thrown_range_long_feet: int | None = None
    """Live-reported bug fix: only ever set from _pc_attack_params, when
    the equipped weapon has the SRD "thrown" property (dagger/handaxe/
    javelin/light-hammer/spear/trident) - the real throw range from
    rules.weapon_throw_range_feet, distinct from range_normal_feet/
    range_long_feet above (which stay the plain 5ft melee reach for these
    weapons, matching how they're actually held). _resolve_single_attack
    picks whichever pair actually applies from the target's real distance:
    within melee reach, swung as normal (these two fields are ignored
    entirely); beyond it, thrown (the real ability-modifier/damage math is
    identical either way - only the usable range and the "ranged attack in
    close combat" disadvantage check differ, see _resolve_single_attack)."""
    is_finesse_or_ranged: bool = False
    """Phase 9I: only ever True from _pc_attack_params, when the weapon has
    the SRD "finesse" property or is a ranged weapon - Sneak Attack's
    (Rogue) weapon-type requirement. Always False for monster/spell attack
    params, which Sneak Attack never applies to."""
    is_melee_str_weapon: bool = False
    """Phase 9I: only ever True from _pc_attack_params, when the weapon is
    melee and STR governs its attack roll (not finesse-as-DEX) - Rage's
    flat melee damage bonus applies only to these, per SRD."""
    smite_slot_level: int | None = None
    """Issue #21 (Paladin Divine Smite): only ever set from _pc_attack_params
    when the player declared a smite on this attack. Not consumed/rolled
    here - _resolve_single_attack does that, and only if this specific
    attack roll actually hits and a slot of this level is still available
    at that moment (an earlier swing in the same Extra Attack action may
    already have spent it)."""
    attack_bonus_breakdown: list[tuple[str, int]] = field(default_factory=list)
    """Debug-mode UI aid (issue #38): the named components that sum to
    `attack_bonus` (e.g. [("DEX mod", 3), ("proficiency", 2), ("archery", 2)]),
    attached to the attack_roll/spell_cast event payload alongside the roll
    total so a live combat log can show *why* a roll came out the way it did
    - specifically to make a dropped/wrongly-included proficiency bonus or
    class feature visible without re-deriving it from a test fixture, which
    is how every such gap so far has actually been caught. A monster's own
    stat-block attack bonus is one precomputed SRD number with no further
    breakdown available (confirmed - the vendored data has no per-component
    figure), so _monster_attack_params/_monster_innate_attack_params report
    it as a single "attack bonus" entry rather than fabricating a split."""


def _match_weapon_by_name(weapon_name: str, candidates: list[SrdEntry]) -> SrdEntry | None:
    """Word-set match, not exact-index match, as a fallback when
    weapon_name isn't already a real SRD equipment index. The same lesson
    Day 14's healing-potion fix established for item names applies here:
    a persona-driven companion's own free-text turn declaration is just as
    likely to dress up a real weapon with a flavor adjective ("her silvered
    longbow") as to say its bare canonical name, and intent_parser has no
    way to know that "silvered longbow" isn't a real SRD entry - it's just
    relaying what the player/companion said. Matches if a weapon's own name
    (word-split) is fully contained in the given text's words, so extra
    descriptive words are tolerated but a wrong/unrelated weapon name still
    isn't matched by accident. `candidates` (Phase C: equipped-weapon
    tracking) scopes the search to a specific weapon list - previously this
    always searched the whole SRD weapon list regardless of ownership; now
    every caller passes `actor.equipped_weapons` so a name only matches
    something actually equipped."""
    words = set(weapon_name.strip().lower().replace("-", " ").replace(",", " ").split())
    for item in candidates:
        item_words = set(item["name"].lower().replace("-", " ").replace(",", " ").split())
        if item_words and item_words <= words:
            return item
    return None


def _pc_attack_params(
    actor: Character,
    weapon_index: str | None,
    srd: SrdIndex,
    include_ability_damage_bonus: bool = True,
    smite_slot_level: int | None = None,
    force_unarmed: bool = False,
) -> AttackParams:
    """`include_ability_damage_bonus=False` (Two-Weapon Fighting's off-hand
    attack, `_resolve_offhand_attack`) drops the ability modifier from
    `damage_bonus` only - the attack roll itself is unaffected - per SRD's
    "you don't add your ability modifier to the damage of [the off-hand]
    attack" rule.

    `smite_slot_level` (issue #21, Divine Smite) is validated eagerly here,
    at declare-time, even though the slot itself isn't spent until
    _resolve_single_attack confirms a hit - a Paladin with no such slot, or
    a non-Paladin, or a ranged-weapon attack should be rejected outright
    rather than silently doing nothing on a miss-or-hit.

    `force_unarmed` (issue #24, Flurry of Blows) skips the equipped-weapon
    lookup entirely, even if the actor has one or two weapons equipped -
    Flurry is always two *unarmed* strikes specifically, unlike a plain
    `attack` (which falls back to unarmed only when nothing is equipped)."""
    if smite_slot_level is not None:
        if actor.class_index != "paladin":
            raise TurnEngineError(f"{actor.id} is not a Paladin and cannot use Divine Smite")
        if actor.spell_slots.get(smite_slot_level, 0) <= 0:
            raise TurnEngineError(
                f"{actor.id} has no level {smite_slot_level} spell slots remaining for Divine Smite"
            )
    equipped = (
        []
        if force_unarmed
        else [item for idx in actor.equipped_weapons if (item := srd.equipment.get(idx))]
    )
    weapon: SrdEntry | None = None
    if weapon_index and not force_unarmed:
        weapon = next((item for item in equipped if item["index"] == weapon_index), None)
        if weapon is None:
            weapon = _match_weapon_by_name(weapon_index, equipped)
        if weapon is None:
            # Found live: a ranged-weapon user's own free-text turn (here a
            # companion Ranger's) is just as likely to name their
            # *ammunition* ("I nock an arrow and fire") as the bow itself -
            # intent_parser has no way to know "arrow" isn't a weapon, only
            # that it's the concrete noun in the sentence. Only reject when
            # weapon_index resolves to a real SRD entry that's genuinely a
            # weapon the actor isn't currently holding (the intended "wrong
            # weapon, equip first" case, e.g. naming a longsword while only
            # a dagger is equipped) - a real SRD entry that isn't a weapon
            # at all (ammunition, adventuring gear) falls back to whatever's
            # equipped instead, same as naming none. Gibberish that matches
            # no real SRD item either way (e.g. "my fireproof toaster")
            # keeps raising - that's a genuinely confused declaration, not
            # a same-hand-different-noun case like ammunition.
            real_item = srd.equipment.get(weapon_index)
            if real_item is not None and not real_item.get("weapon_category"):
                weapon = equipped[0] if equipped else None
            else:
                equipped_names = ", ".join(item["name"] for item in equipped) or "nothing (unarmed)"
                raise TurnEngineError(
                    f"{weapon_index!r} isn't in {actor.id}'s equipped weapon set "
                    f"(currently: {equipped_names}) - use 'equip' to switch weapons first"
                )
    elif equipped:
        weapon = equipped[0]

    str_mod = ability_modifier(actor.stats["STR"])
    dex_mod = ability_modifier(actor.stats["DEX"])
    # Rage (Phase 9I): a flat melee-STR damage bonus, per SRD - applies to
    # an unarmed strike too (it's a melee attack using Strength).
    rage_bonus = RAGE_DAMAGE_BONUS if actor.is_raging else 0

    if weapon is None:
        if actor.class_index == "monk":
            # Martial Arts (issue #24): the scaling die *replaces* the flat
            # 1 bludgeoning damage entirely (not added on top), and DEX can
            # be used instead of STR for both the attack and damage rolls.
            monk_mod = max(str_mod, dex_mod)
            monk_ability = "DEX" if dex_mod > str_mod else "STR"
            return AttackParams(
                attack_bonus=monk_mod + actor.proficiency_bonus,
                damage_dice_count=1,
                damage_dice_sides=monk_martial_arts_die_sides(actor.level),
                damage_bonus=(monk_mod if include_ability_damage_bonus else 0) + rage_bonus,
                damage_type="bludgeoning",
                source_name="unarmed strike",
                range_normal_feet=5,
                range_long_feet=None,
                is_melee_str_weapon=True,
                smite_slot_level=smite_slot_level,
                attack_bonus_breakdown=[
                    (f"{monk_ability} mod", monk_mod),
                    ("proficiency", actor.proficiency_bonus),
                ],
            )
        # Unarmed strike (PHB): 1 bludgeoning damage + STR mod, no damage die,
        # 5ft reach like any other melee attack.
        return AttackParams(
            attack_bonus=str_mod + actor.proficiency_bonus,
            damage_dice_count=0,
            damage_dice_sides=4,
            damage_bonus=(str_mod if include_ability_damage_bonus else 0) + 1 + rage_bonus,
            damage_type="bludgeoning",
            source_name="unarmed strike",
            range_normal_feet=5,
            range_long_feet=None,
            is_melee_str_weapon=True,
            smite_slot_level=smite_slot_level,
            attack_bonus_breakdown=[
                ("STR mod", str_mod),
                ("proficiency", actor.proficiency_bonus),
            ],
        )

    properties = {p["index"] for p in (weapon.get("properties") or [])}
    is_finesse = "finesse" in properties
    is_ranged = weapon.get("weapon_range") == "Ranged"
    if smite_slot_level is not None and is_ranged:
        raise TurnEngineError("Divine Smite requires a melee weapon attack")
    # Martial Arts (issue #24, Monk): DEX is usable for a monk weapon's
    # attack and damage rolls too, same as finesse already allows - a
    # class-specific option, not a property of the weapon alone, so it's
    # checked here rather than folded into is_finesse itself.
    is_monk_weapon_for_actor = actor.class_index == "monk" and is_monk_weapon(weapon)
    if is_finesse or is_monk_weapon_for_actor:
        ability_mod = max(str_mod, dex_mod)
        ability_label = "DEX" if dex_mod > str_mod else "STR"
    elif is_ranged:
        ability_mod = dex_mod
        ability_label = "DEX"
    else:
        ability_mod = str_mod
        ability_label = "STR"
    # A plain (non-finesse) melee weapon is the only case Rage's flat melee
    # damage bonus and Sneak Attack's weapon-type check need to tell apart -
    # a finesse weapon numerically using STR (rare - DEX ties or loses) is
    # still finesse, not "melee-STR", so this checks the property directly
    # rather than which ability_mod branch fired above.
    is_melee_str_weapon = not is_finesse and not is_ranged

    # Fighting Style (Phase 9I): Archery (+2 ranged attack rolls) and
    # Dueling (+2 damage, one-handed melee weapon with no other weapon
    # equipped - now that Phase C's equipped_weapons is a real
    # worn/carried distinction, this checks the equipped set directly
    # rather than scanning the whole inventory, so a stashed second weapon
    # in the backpack no longer wrongly denies the bonus) apply here;
    # Defense's +1 AC is baked into Character.ac at creation instead (see
    # character_creation._compute_ac), since this engine computes AC once,
    # not per-attack.
    archery_bonus = 2 if actor.fighting_style == "archery" and is_ranged else 0
    dueling_bonus = (
        2
        if actor.fighting_style == "dueling" and not is_ranged and len(actor.equipped_weapons) <= 1
        else 0
    )
    rage_bonus = RAGE_DAMAGE_BONUS if actor.is_raging and is_melee_str_weapon else 0

    proficient = is_class_proficient_with(actor, weapon["index"], srd)
    prof_bonus = actor.proficiency_bonus if proficient else 0
    range_normal_feet, range_long_feet = weapon_range_feet(weapon)
    throw_range = weapon_throw_range_feet(weapon)

    dice_count, dice_sides, notation_bonus = parse_dice_notation(weapon["damage"]["damage_dice"])
    if is_monk_weapon_for_actor:
        # Martial Arts (issue #24): "roll the Martial Arts die in place of
        # the weapon's own damage" - only when it's actually bigger (dice
        # count stays 1; every monk-tagged weapon in this data is a
        # single-die weapon, and this engine already only ever reads the
        # one-handed `damage` field, never a versatile weapon's
        # two_handed_damage, so there's no second die-count case to weigh
        # here).
        dice_sides = max(dice_sides, monk_martial_arts_die_sides(actor.level))
    return AttackParams(
        attack_bonus=ability_mod + prof_bonus + archery_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=(ability_mod if include_ability_damage_bonus else 0)
        + notation_bonus
        + dueling_bonus
        + rage_bonus,
        damage_type=weapon["damage"]["damage_type"]["index"],
        source_name=weapon["name"],
        range_normal_feet=range_normal_feet,
        range_long_feet=range_long_feet,
        thrown_range_normal_feet=throw_range[0] if throw_range else None,
        thrown_range_long_feet=throw_range[1] if throw_range else None,
        is_finesse_or_ranged=is_finesse or is_ranged,
        is_melee_str_weapon=is_melee_str_weapon,
        smite_slot_level=smite_slot_level,
        attack_bonus_breakdown=[
            (f"{ability_label} mod", ability_mod),
            *([("proficiency", prof_bonus)] if proficient else [("proficiency (none)", 0)]),
            *([("archery", archery_bonus)] if archery_bonus else []),
        ],
    )


@dataclass(frozen=True)
class AttackSummary:
    """Display-only aid for a proactive UX ask (not tied to any bug): shows
    a PC's own current attack bonus/damage - base + modifiers - on the
    character sheet, before they've committed to an attack. Built by
    current_attack_summaries below, which reuses _pc_attack_params directly
    (the exact function a real attack resolves through) rather than a
    second, hand-derived computation - same "extract once, reuse for both
    resolution and display" precedent as rules.spell_damage_notation
    (issue #30), so this can never drift from what an actual attack rolls."""

    source_name: str
    attack_bonus: int
    attack_bonus_breakdown: list[tuple[str, int]]
    damage_dice_count: int
    damage_dice_sides: int
    damage_bonus: int
    damage_type: str


def current_attack_summaries(actor: Character, srd: SrdIndex) -> list[AttackSummary]:
    """One entry for the main hand (or unarmed, if nothing's equipped) and,
    only if genuinely dual-wielding, a second for the off-hand - its own
    reduced damage bonus already applied via include_ability_damage_bonus=
    False, matching offhand_attack's real resolution exactly. Monsters have
    nothing meaningful to preview here (their stat-block attack bonus is
    already a single precomputed number, not something that varies by
    equipment choice), so this is a no-op for anyone but a PC."""
    if not actor.is_pc:
        return []
    summaries = [_attack_summary_from_params(_pc_attack_params(actor, None, srd))]
    if len(actor.equipped_weapons) == 2:
        off_params = _pc_attack_params(
            actor, actor.equipped_weapons[1], srd, include_ability_damage_bonus=False
        )
        summaries.append(_attack_summary_from_params(off_params, name_suffix=" (off-hand)"))
    return summaries


def _attack_summary_from_params(params: AttackParams, name_suffix: str = "") -> AttackSummary:
    return AttackSummary(
        source_name=f"{params.source_name}{name_suffix}",
        attack_bonus=params.attack_bonus,
        attack_bonus_breakdown=params.attack_bonus_breakdown,
        damage_dice_count=params.damage_dice_count,
        damage_dice_sides=params.damage_dice_sides,
        damage_bonus=params.damage_bonus,
        damage_type=params.damage_type,
    )


def _monster_action(actor: Character, action_name: str | None, srd: SrdIndex) -> SrdEntry:
    """Looks up the raw SRD action dict a monster's attack should use - the
    named action if given, else the stat block's first action (every
    curated monster lists its signature/most relevant action first, which
    is why _monster_attack_params defaults to it too). Split out from
    _monster_attack_params so _resolve_attack can inspect the chosen
    action's name (to detect "Multiattack") before committing to building a
    single-attack's damage params from it."""
    if actor.monster_index is None:
        raise TurnEngineError(f"{actor.id} is not a monster (no monster_index)")
    monster_data = srd.monsters[actor.monster_index]
    actions: list[SrdEntry] = monster_data.get("actions") or []
    if not actions:
        raise TurnEngineError(f"{monster_data['name']} has no actions")

    if action_name:
        action: SrdEntry | None = next(
            (a for a in actions if a["name"].lower() == action_name.lower()), None
        )
        if action is None:
            raise TurnEngineError(f"{monster_data['name']} has no action named {action_name!r}")
        return action
    return actions[0]


def _monster_attack_params(
    actor: Character, action_name: str | None, srd: SrdIndex
) -> AttackParams:
    action = _monster_action(actor, action_name, srd)
    damage_entry = action["damage"][0]
    dice_count, dice_sides, notation_bonus = parse_dice_notation(damage_entry["damage_dice"])
    range_normal_feet, range_long_feet = monster_action_range_feet(action)
    return AttackParams(
        attack_bonus=action["attack_bonus"],
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=notation_bonus,
        damage_type=damage_entry["damage_type"]["index"],
        source_name=action["name"],
        range_normal_feet=range_normal_feet,
        range_long_feet=range_long_feet,
        attack_bonus_breakdown=[("attack bonus (stat block)", action["attack_bonus"])],
    )


def _validate_attack_target(actor: Character, target: Character) -> None:
    """Rejects friendly fire: `is_pc` doubles as "which side" a character is
    on (party vs monsters), so a same-side target is never a legitimate
    attack. Found live on Day 15's first autoplay run - a companion's
    free-text turn ("I attack Grom") named an ally, since intent_parser's
    character list has no notion of allegiance for the LLM to reason about
    (the same underlying gap Day 12 flagged for monster free text, now
    confirmed to also hit companion-vs-companion). Validating here, at the
    one place both _resolve_attack and _resolve_cast_spell funnel through,
    closes it regardless of which LLM call picked the bad target - no
    prompt engineering can guarantee zero-shot compliance from a small
    model, so the deterministic engine enforces the actual game rule."""
    if target.is_pc == actor.is_pc:
        raise TurnEngineError(f"{actor.id} cannot attack {target.id} - same side")
    charmed_by = next(
        (c.source for c in actor.conditions if c.name == "charmed" and c.source), None
    )
    if charmed_by == target.id:
        raise TurnEngineError(f"{actor.id} is charmed by {target.id} and cannot attack them")


def _has_adjacent_hostile(state: GameState, actor: Character) -> bool:
    """True if any living hostile (opposite `is_pc`) creature is within 5ft
    of `actor` - used by _resolve_single_attack to impose the SRD's
    ranged-attack-while-engaged-in-melee disadvantage (Phase 9F)."""
    return any(
        other.id != actor.id
        and not other.is_dead
        and other.is_pc != actor.is_pc
        and distance_feet(actor.position, other.position) <= 5
        for other in state.characters.values()
    )


def _ally_adjacent_to(state: GameState, attacker: Character, target: Character) -> bool:
    """True if any living ally of `attacker` (same `is_pc`, excluding
    `attacker` and `target` themselves) is within 5ft of `target` - Sneak
    Attack's (Phase 9I, Rogue) alternate trigger alongside advantage."""
    return any(
        other.id != attacker.id
        and other.id != target.id
        and not other.is_dead
        and other.is_pc == attacker.is_pc
        and distance_feet(target.position, other.position) <= 5
        for other in state.characters.values()
    )


def _resolve_single_attack(
    state: GameState,
    actor: Character,
    target: Character,
    params: AttackParams,
    rng: random.Random,
    srd: SrdIndex,
    defer_bardic_choice: bool = False,
) -> None:
    """One full attack roll (range check through hit/damage/downing) against
    `target` - the body every single `attack` action resolves, and what a
    Multiattack action (Phase 9F, see _resolve_multiattack) calls once per
    named sub-attack within the same turn.

    `defer_bardic_choice` (issue #53, default False so every pre-existing
    caller - Multiattack, Extra Attack's own loop, monster attacks, spell
    attacks - keeps the original "auto-apply the die immediately" behavior
    unchanged): when True and the actor holds a Bardic Inspiration die, a
    roll that would miss without the die raises BardicChoicePending instead
    of finalizing, letting the holder decide whether to spend it - see
    PendingBardicChoice's own docstring for the full reasoning and scope."""
    # Caught live: a combat grid can show attacker and target several
    # squares apart while a melee attack still resolved as a hit - this
    # engine never checked range at all. Beyond the weapon/action's max
    # reach (long range if ranged, else normal) is rejected outright;
    # beyond normal but within long range (ranged only - melee has no such
    # tier) imposes disadvantage, per SRD.
    distance = distance_feet(actor.position, target.position)
    # Live-reported bug fix: a thrown-property weapon (javelin, dagger,
    # handaxe, spear, trident, light hammer) has its own separate, much
    # longer throw range - beyond simple melee reach, resolve this specific
    # attack as a throw instead of rejecting it outright. Swinging the same
    # weapon at an adjacent target is completely unaffected (still the
    # plain melee range/is_ranged=False below) - only a target actually
    # beyond melee reach switches to the throw numbers.
    range_normal_feet = params.range_normal_feet
    range_long_feet = params.range_long_feet
    if params.thrown_range_normal_feet is not None and distance > params.range_normal_feet:
        range_normal_feet = params.thrown_range_normal_feet
        range_long_feet = params.thrown_range_long_feet
    max_range = range_long_feet or range_normal_feet
    if distance > max_range:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {params.source_name} "
            f"(max {max_range}ft)"
        )
    long_range_disadvantage = range_long_feet is not None and distance > range_normal_feet
    # Phase 9F: a ranged attack (anything with a "long" range tier - melee
    # weapons/actions have none, see weapon_range_feet/monster_action_range_
    # feet) rolls with disadvantage while a hostile creature is within 5ft
    # of the attacker, per SRD ("Ranged Attacks in Close Combat") - a thrown
    # weapon attack (just switched to above) counts too, matching real SRD
    # (the disadvantage is about making a ranged attack at all, not which
    # weapon category it started from).
    is_ranged = range_long_feet is not None
    engaged_disadvantage = is_ranged and _has_adjacent_hostile(state, actor)

    # Advantage from being helped (Day 13) is consumed by this roll whether
    # or not it changes the outcome; disadvantage from the target dodging
    # applies for as long as the target is dodging (until their own next
    # turn) rather than being consumed - roll_d20 already cancels the two
    # out together when both apply, per SRD rules. The attacker's own
    # non-proficient armor, attacking beyond normal range, being engaged
    # while shooting, and SRD condition effects (Phase 9A -
    # blinded/prone/restrained/invisible/etc. on either side) are further
    # independent sources.
    # Issue #19: Pack Tactics (Wolf and 16 other CR<=5 monsters) - advantage
    # when an ally is within 5ft of the shared target, the exact same
    # adjacency check Sneak Attack's own alternate trigger already uses.
    pack_tactics_advantage = monster_has_pack_tactics(actor, srd) and _ally_adjacent_to(
        state, actor, target
    )
    advantage = (
        actor.has_help_advantage
        or condition_attack_advantage(actor, target, distance)
        or pack_tactics_advantage
        or actor.true_strike_advantage
    )
    actor.has_help_advantage = False
    actor.true_strike_advantage = False

    # Phase 9C: per SRD, any hit against an unconscious creature is a
    # critical hit - checked before resolve_attack runs (not after) since it
    # must reflect whether the target was ALREADY down before THIS attack
    # roll, not whether this very hit is the one that drops them. This
    # matters for both of _resolve_attack's multi-roll callers (Phase 9J
    # Extra Attack, Phase 9F Multiattack): each call to _resolve_single_attack
    # re-checks fresh, so a roll that itself knocks the target unconscious
    # correctly makes a LATER roll in the same action get the helpless
    # treatment the earlier one didn't.
    already_unconscious = has_condition(target, "unconscious")

    # Bardic Inspiration (issue #25): banked on the actor by an earlier
    # bardic_inspiration action (see Character.bardic_inspiration_die's own
    # docstring for why this is narrowed to attack rolls only) - captured
    # before the call since resolve_attack itself has no Character to clear
    # it on.
    bardic_die_sides = actor.bardic_inspiration_die

    # Bless (issue #57): rolled once here (not inside resolve_attack), so
    # the same value backs both the total and the debug-mode breakdown -
    # see rules.blessed_bonus's own docstring for why this can't be two
    # separate rolls. 0 (no RNG consumed) when the actor isn't blessed.
    bless = blessed_bonus(actor, rng)
    if bless:
        params = replace(
            params,
            attack_bonus=params.attack_bonus + bless,
            attack_bonus_breakdown=[*params.attack_bonus_breakdown, ("blessed (1d4)", bless)],
        )

    disadvantage = (
        target.is_dodging
        or has_non_proficient_armor(actor, srd)
        or long_range_disadvantage
        or engaged_disadvantage
        or condition_attack_disadvantage(actor, target, distance)
    )

    if defer_bardic_choice and bardic_die_sides:
        # Probe: roll WITHOUT the die first - this genuinely is the roll
        # (real RNG draws, not a discardable peek), just not yet decided
        # whether the die gets added to it.
        probe = resolve_attack(
            defender_ac=target.ac,
            attack_bonus=params.attack_bonus,
            damage_dice_count=params.damage_dice_count,
            damage_dice_sides=params.damage_dice_sides,
            damage_bonus=params.damage_bonus,
            damage_type=params.damage_type,
            rng=rng,
            advantage=advantage,
            disadvantage=disadvantage,
            force_critical=already_unconscious,
            lucky=has_lucky_trait(actor),
        )
        natural = probe.attack_roll.kept[0]
        if not probe.hit and natural != 1:
            # A miss the die could still turn into a hit - pause here
            # instead of finalizing; api/ws/session.py catches this,
            # offers the choice to whoever controls the holder, and
            # resumes via resolve_pending_bardic_choice once they answer.
            raise BardicChoicePending(
                PendingBardicChoice(
                    holder_id=actor.id,
                    target_id=target.id,
                    natural=natural,
                    total_without_die=probe.attack_roll.total,
                    defender_ac=target.ac,
                    die_sides=bardic_die_sides,
                    damage_dice_count=params.damage_dice_count,
                    damage_dice_sides=params.damage_dice_sides,
                    damage_bonus=params.damage_bonus,
                    damage_type=params.damage_type,
                    source_name=params.source_name,
                    attack_bonus_breakdown=params.attack_bonus_breakdown,
                    force_critical=already_unconscious,
                    is_finesse_or_ranged=params.is_finesse_or_ranged,
                    had_advantage=advantage,
                    smite_slot_level=params.smite_slot_level,
                )
            )
        # Already a hit, or a natural 1 the die couldn't have fixed anyway -
        # nothing to offer, finalize with this real roll. The die stays
        # banked (never added, never consumed) for a future roll.
        _finalize_attack_result(
            state, actor, target, params, probe, advantage, already_unconscious, None, rng, srd
        )
        return

    result = resolve_attack(
        defender_ac=target.ac,
        attack_bonus=params.attack_bonus,
        damage_dice_count=params.damage_dice_count,
        damage_dice_sides=params.damage_dice_sides,
        damage_bonus=params.damage_bonus,
        damage_type=params.damage_type,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        force_critical=already_unconscious,
        lucky=has_lucky_trait(actor),
        bardic_die_sides=bardic_die_sides,
    )
    if bardic_die_sides:
        actor.bardic_inspiration_die = None
    _finalize_attack_result(
        state,
        actor,
        target,
        params,
        result,
        advantage,
        already_unconscious,
        bardic_die_sides,
        rng,
        srd,
    )


def _finalize_attack_result(
    state: GameState,
    actor: Character,
    target: Character,
    params: AttackParams,
    result: AttackResult,
    advantage: bool,
    already_unconscious: bool,
    bardic_die_sides: int | None,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """The event/Sneak-Attack/Smite/damage/unconscious-death-save tail every
    attack roll ends with, regardless of whether it got here via the normal
    synchronous path or issue #53's resume-after-a-Bardic-Inspiration-
    choice path - extracted so the two can never drift, the same "one real
    implementation, not two copies that happen to agree" precedent this
    project always follows for a shared tail. `bardic_die_sides` is only
    non-None when the die was actually added to this specific roll (for the
    event payload/narration), not merely available."""
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="attack_roll",
            payload={
                "target": target.id,
                "source": params.source_name,
                "roll_total": result.attack_roll.total,
                "natural": result.attack_roll.kept[0],
                "target_ac": target.ac,
                "hit": result.hit,
                "critical": result.critical,
                "attack_bonus_breakdown": params.attack_bonus_breakdown,
                **({"bardic_inspiration_die_sides": bardic_die_sides} if bardic_die_sides else {}),
            },
        )
    )

    # Sneak Attack (Phase 9I, Rogue): once per turn, on a hit with a
    # finesse/ranged weapon, when this roll had advantage or an ally is
    # adjacent to the target - bonus damage rolled separately (not folded
    # into AttackParams, since its dice size (d6) generally differs from
    # the weapon's own) and doubled on a crit exactly like resolve_attack
    # already doubles the weapon's own dice.
    if (
        result.hit
        and actor.class_index == "rogue"
        and params.is_finesse_or_ranged
        and not actor.sneak_attack_used_this_turn
        and (advantage or _ally_adjacent_to(state, actor, target))
    ):
        actor.sneak_attack_used_this_turn = True
        sneak_dice = 2 if result.critical else 1
        sneak_damage = roll(sneak_dice, 6, modifier=0, rng=rng).total
        result = replace(result, damage=(result.damage or 0) + sneak_damage)
        state.events[-1].payload["sneak_attack_damage"] = sneak_damage

    # Divine Smite (issue #21, Paladin): declared upfront on the attack (see
    # ParsedAction.params's docstring for why - this engine has no
    # mid-resolution pause to ask "it hit, smite now?"), but only actually
    # spent/rolled if this specific swing hits and a slot of that level is
    # still available right now - an Extra Attack's second swing can still
    # smite again as long as another slot of the declared level remains,
    # even if the first swing already spent one. 2d8 for a 1st-level slot,
    # +1d8 per slot level above 1st (capped at 5d8), +1 more d8 against
    # undead/fiends (capped at 6d8 combined, per SRD) - doubled on a crit
    # like Sneak Attack's own dice above, since it's extra damage on the
    # same weapon attack, not a separate spell attack roll.
    if (
        result.hit
        and params.smite_slot_level is not None
        and actor.spell_slots.get(params.smite_slot_level, 0) > 0
    ):
        actor.spell_slots[params.smite_slot_level] -= 1
        smite_dice = min(1 + params.smite_slot_level, 5)
        if monster_is_undead_or_fiend(target, srd):
            smite_dice += 1
        if result.critical:
            smite_dice *= 2
        smite_damage = roll(smite_dice, 8, modifier=0, rng=rng).total
        result = replace(result, damage=(result.damage or 0) + smite_damage)
        state.events[-1].payload["divine_smite_damage"] = smite_damage

    if result.hit and result.damage is not None:
        _apply_damage_and_handle_downing(
            state, actor, target, result.damage, params.damage_type, rng, srd
        )

    # Separate from normal damage (per SRD): a hit against an already-
    # unconscious PC also inflicts 2 automatic death-save failures, on top of
    # whatever damage did (usually nothing further, since the target is
    # already clamped at 0 HP). Gated on result.hit the same way a miss used
    # to short-circuit this whole tail via an early return, before this
    # function was split out of a single, non-reusable _resolve_attack body.
    if result.hit and already_unconscious and target.is_pc and not target.is_dead:
        _apply_unconscious_hit_death_save_failures(state, target)


def resolve_pending_bardic_choice(
    state: GameState,
    choice: PendingBardicChoice,
    use_die: bool,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """Issue #53: resumes a single attack roll that paused waiting for the
    Bardic Inspiration holder's decision (see BardicChoicePending). Declining
    finalizes the already-known miss exactly as it was, at no cost - the die
    stays banked, since real SRD only spends it the moment it's actually
    added to a roll. Accepting rolls the die fresh (a real, new RNG draw
    made now, not reused from the original probe - the probe deliberately
    never rolled it at all) and rebuilds an AttackResult from the boosted
    total, then hands off to the exact same _finalize_attack_result every
    other attack roll in this engine ends through - Sneak Attack, Divine
    Smite, damage, and the unconscious-hit death-save rule all still apply
    normally if the die turns this into a hit, with no separate copy of
    that logic to keep in sync."""
    actor = state.characters[choice.holder_id]
    target = state.characters[choice.target_id]
    params = AttackParams(
        attack_bonus=0,  # unused below - the roll already happened
        damage_dice_count=choice.damage_dice_count,
        damage_dice_sides=choice.damage_dice_sides,
        damage_bonus=choice.damage_bonus,
        damage_type=choice.damage_type,
        source_name=choice.source_name,
        range_normal_feet=0,  # unused - range was already validated during the probe
        range_long_feet=None,
        is_finesse_or_ranged=choice.is_finesse_or_ranged,
        attack_bonus_breakdown=choice.attack_bonus_breakdown,
        smite_slot_level=choice.smite_slot_level,
    )

    if not use_die:
        roll_result = RollResult(
            dice=[choice.natural], kept=[choice.natural], modifier=0, total=choice.total_without_die
        )
        result = AttackResult(
            attack_roll=roll_result, hit=False, critical=False, damage=None, damage_type=None
        )
        _finalize_attack_result(
            state,
            actor,
            target,
            params,
            result,
            choice.had_advantage,
            choice.force_critical,
            None,
            rng,
            srd,
        )
    else:
        actor.bardic_inspiration_die = None
        bonus = rng.randint(1, choice.die_sides)
        total = choice.total_without_die + bonus
        hit = total >= choice.defender_ac
        roll_result = RollResult(
            dice=[choice.natural], kept=[choice.natural], modifier=0, total=total
        )
        if not hit:
            result = AttackResult(
                attack_roll=roll_result, hit=False, critical=False, damage=None, damage_type=None
            )
        else:
            # A natural 20 always already hit on the probe roll (before the
            # die was even offered), so it never reaches this branch -
            # force_critical here is purely the pre-existing "already
            # unconscious" auto-crit rule.
            critical = choice.force_critical
            dice_count = choice.damage_dice_count * 2 if critical else choice.damage_dice_count
            damage_roll = roll(
                dice_count, choice.damage_dice_sides, modifier=choice.damage_bonus, rng=rng
            )
            result = AttackResult(
                attack_roll=roll_result,
                hit=True,
                critical=critical,
                damage=max(0, damage_roll.total),
                damage_type=choice.damage_type,
            )
        _finalize_attack_result(
            state,
            actor,
            target,
            params,
            result,
            choice.had_advantage,
            choice.force_critical,
            choice.die_sides,
            rng,
            srd,
        )

    # Issue #53: this call sits outside resolve_action's own dispatch (the
    # original attempt was fully aborted by BardicChoicePending before its
    # tail ever ran - see resolve_action's own victory-check/turn-advance
    # lines at the very end of its dispatch), so this resume needs to
    # replicate that same tail itself. An `attack` action always ends the
    # turn (never one of the bonus-action-verb exceptions), so there's no
    # ends_turn branching to reproduce here, just the two calls themselves.
    _check_victory_defeat(state)
    if state.status == "in_progress":
        _advance_turn_skipping_dead(state)


def _apply_unconscious_hit_death_save_failures(state: GameState, target: Character) -> None:
    """The other half of the Phase 9C unconscious-hit rule (see
    _resolve_attack's force_critical call) - 2 automatic death-save failures,
    inflicted directly on the target's own death-save fields rather than
    requiring them to roll anything. Shares _check_death_save_failure_
    threshold with _resolve_death_save so a hit pushing failures to 3 kills
    exactly like 3 self-rolled failures would, not a second, drifting copy
    of that threshold check."""
    target.death_save_failures += 2
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=target.id,
            type="saving_throw",
            payload={
                "kind": "death_save",
                "natural": None,
                "success": False,
                "cause": "hit_while_unconscious",
                "successes": target.death_save_successes,
                "failures": target.death_save_failures,
            },
        )
    )
    _check_death_save_failure_threshold(state, target)


def _resolve_multiattack(
    state: GameState,
    actor: Character,
    target: Character,
    multiattack_action: SrdEntry,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """Phase 9F: a monster's "Multiattack" action names (in free-text
    `desc`) which of its other actions to actually roll, and how many times
    each - e.g. giant-badger: "The badger makes two attacks: one with its
    bite and one with its claws." Resolves each named sub-attack as its own
    full attack roll (rules.multiattack_sub_actions does the desc parsing;
    _resolve_single_attack is the same per-attack-roll logic a plain
    single-action attack uses) within this one turn - still one `attack`
    verb/action, multiple `attack_roll` events. Stops early if the target
    dies partway through, since there's nothing left to attack."""
    if actor.monster_index is None:
        raise TurnEngineError(f"{actor.id} is not a monster (no monster_index)")
    monster_data = srd.monsters[actor.monster_index]
    other_action_names = [
        a["name"] for a in (monster_data.get("actions") or []) if a.get("name") != "Multiattack"
    ]
    sub_actions = multiattack_sub_actions(
        str(multiattack_action.get("desc", "")), other_action_names
    )
    if not sub_actions:
        raise TurnEngineError(
            f"Could not parse Multiattack sub-actions from {multiattack_action.get('desc', '')!r}"
        )
    for sub_action_name, count in sub_actions:
        for _ in range(count):
            if target.is_dead:
                return
            params = _monster_attack_params(actor, sub_action_name, srd)
            _resolve_single_attack(state, actor, target, params, rng, srd)


def _resolve_attack(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if action.target is None:
        raise TurnEngineError("attack action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown attack target: {action.target}")
    _validate_attack_target(actor, target)

    if actor.monster_index is not None:
        monster_action = _monster_action(actor, action.item_or_spell, srd)
        if monster_action.get("name") == "Multiattack":
            _resolve_multiattack(state, actor, target, monster_action, rng, srd)
            return
        params = _monster_attack_params(actor, action.item_or_spell, srd)
        _resolve_single_attack(state, actor, target, params, rng, srd)
        return

    params = _pc_attack_params(
        actor, action.item_or_spell, srd, smite_slot_level=action.params.get("smite_slot_level")
    )
    # Extra Attack (Phase 9J): an eligible PC (level 5+ Fighter/Barbarian/
    # Paladin/Ranger) makes two attack rolls for this one `attack` action
    # instead of one - the PC-side equivalent of a monster's Multiattack
    # above, sharing the same _resolve_single_attack per-roll logic. Stops
    # early if the target dies partway through the second roll. A declared
    # Divine Smite (issue #21) is eligible on every roll here, not just the
    # first - _resolve_single_attack only actually spends/rolls it if that
    # specific swing hits and a slot is still available at that moment.
    num_attacks = 2 if is_eligible_for_extra_attack(actor) else 1
    # Issue #53: the Bardic-Inspiration pause-and-ask is only offered for a
    # plain single swing (num_attacks == 1) - an Extra-Attack-eligible PC's
    # two rolls still auto-apply the die immediately on the first one that
    # needs it, a documented, narrower scope for this first pass (see
    # PendingBardicChoice's own docstring).
    defer_bardic_choice = num_attacks == 1
    for _ in range(num_attacks):
        if target.is_dead:
            return
        _resolve_single_attack(
            state, actor, target, params, rng, srd, defer_bardic_choice=defer_bardic_choice
        )


def _resolve_offhand_attack(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Two-Weapon Fighting's SRD bonus-action off-hand attack (issue #12) -
    a second attack with the actor's *other* equipped weapon, no ability
    modifier added to its damage (see _pc_attack_params's
    include_ability_damage_bonus). Gated by the same bonus_action_used flag
    as Second Wind/Rage/a bonus-action spell (SRD keeps them one shared
    resource), and requires exactly 2 equipped weapons - which, thanks to
    weapon_combo_is_legal already enforced at equip time, always means both
    are light, exactly SRD's eligibility rule for this attack. Returns
    False (doesn't end the turn), letting the actor still take their main
    action - an ordinary `attack` with either equipped weapon."""
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - "
            "cannot make an off-hand attack"
        )
    if len(actor.equipped_weapons) != 2:
        raise TurnEngineError(
            f"{actor.id} needs two light weapons equipped to make an off-hand attack "
            "- use 'equip' first"
        )
    if action.target is None:
        raise TurnEngineError("offhand_attack action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown offhand_attack target: {action.target}")
    _validate_attack_target(actor, target)

    params = _pc_attack_params(
        actor, actor.equipped_weapons[1], srd, include_ability_damage_bonus=False
    )
    _resolve_single_attack(state, actor, target, params, rng, srd)
    actor.bonus_action_used = True
    return False


def _resolve_cunning_action(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Rogue's Cunning Action (issue #21): Dash or Disengage as a bonus
    action instead of a full action, gated the same way Second Wind/Rage/the
    off-hand attack are (bonus_action_used), plus SRD's own level-2+ Rogue
    gate. Hide is deliberately not offered here - this engine has no
    stealth/hidden-state mechanic at all yet, a separate, bigger feature
    that issue #21's cheap-add scope explicitly didn't take on. Delegates to
    the exact same resolvers a full-action `dash`/`disengage` already use
    (a synthetic verb="dash" copy so _resolve_move's speed-doubling check
    fires, or _resolve_disengage directly) rather than duplicating either's
    logic. Returns False (doesn't end the turn), so the actor can still take
    their real action afterward."""
    if actor.class_index != "rogue" or actor.level < 2:
        raise TurnEngineError(f"{actor.id} doesn't have Cunning Action")
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - cannot use Cunning Action"
        )
    sub_action = action.params.get("action")
    if sub_action == "dash":
        _resolve_move(state, actor, action.model_copy(update={"verb": "dash"}), rng, srd)
    elif sub_action == "disengage":
        _resolve_disengage(state, actor)
    else:
        raise TurnEngineError(
            "cunning_action requires params['action'] to be 'dash' or 'disengage'"
        )
    actor.bonus_action_used = True
    return False


def _resolve_flurry_of_blows(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Monk's Flurry of Blows (issue #24): spend 1 Ki point to make two
    unarmed strikes as a bonus action, same gate shape as Second Wind/Rage
    (bonus_action_used + _use_class_resource), plus SRD's own level-2+ Ki
    gate. Unlike Two-Weapon Fighting's off-hand attack, both Flurry strikes
    are full unarmed strikes - neither drops the ability modifier - so
    _pc_attack_params is called with force_unarmed=True and no other
    adjustment, reusing Martial Arts' scaling die/DEX option automatically
    (see _pc_attack_params's Monk branch)."""
    if actor.class_index != "monk" or actor.level < 2:
        raise TurnEngineError(f"{actor.id} doesn't have Flurry of Blows")
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - cannot use Flurry of Blows"
        )
    if action.target is None:
        raise TurnEngineError("flurry_of_blows action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown flurry_of_blows target: {action.target}")
    _validate_attack_target(actor, target)

    _use_class_resource(actor, "ki")
    params = _pc_attack_params(actor, None, srd, force_unarmed=True)
    for _ in range(2):
        if target.is_dead:
            break
        _resolve_single_attack(state, actor, target, params, rng, srd)
    actor.bonus_action_used = True
    return False


def _resolve_martial_arts_strike(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Monk's Martial Arts bonus-action unarmed strike (issue #36) - the
    third piece of level-1 Martial Arts, alongside the DEX-option and
    scaling damage die already handled unconditionally by
    _pc_attack_params's Monk branch. A single free unarmed strike, no Ki
    cost, available from level 1 - a genuinely different feature from
    Flurry of Blows above (2 strikes, costs 1 Ki, needs level 2+ Ki to
    exist at all). Same bonus_action_used gate shape as Flurry/Second Wind/
    Rage. Real SRD gates this on "immediately after the Attack action with
    an unarmed strike or a monk weapon" - not enforced here, same
    documented simplification this engine already accepts for Flurry's own
    identical real-SRD prerequisite (this engine doesn't track "which verb
    was used earlier this turn" in a way a check like that could consume)."""
    if actor.class_index != "monk":
        raise TurnEngineError(f"{actor.id} doesn't have Martial Arts")
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - "
            "cannot make a Martial Arts strike"
        )
    if action.target is None:
        raise TurnEngineError("martial_arts_strike action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown martial_arts_strike target: {action.target}")
    _validate_attack_target(actor, target)

    params = _pc_attack_params(actor, None, srd, force_unarmed=True)
    _resolve_single_attack(state, actor, target, params, rng, srd)
    actor.bonus_action_used = True
    return False


def _resolve_wild_shape(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Druid's Wild Shape (issue #24): transforms into a beast statblock.
    A full action (ends the turn) - SRD's bonus-action version doesn't
    unlock until level 20, out of this project's scope. Design: a wild-
    shaped Druid becomes, for attack-resolution purposes, exactly a
    monster character that still happens to have is_pc=True - setting
    `monster_index` to the beast reuses _resolve_attack's entire existing
    monster-attack path for free (it only ever branches on monster_index,
    never is_pc), rather than building a parallel one. Duration isn't
    tracked as real elapsed time (this engine has no hour-granularity clock
    at all) - it persists until _resolve_revert_wild_shape, a forced revert
    from dropping to 0 HP (_apply_damage_and_handle_downing), or a rest -
    the same documented simplification already accepted for Rage's real
    duration."""
    del rng  # accepted only so this resolver's signature matches its siblings
    if actor.class_index != "druid" or actor.level < 2:
        raise TurnEngineError(f"{actor.id} doesn't have Wild Shape")
    if actor.wild_shape_beast_index is not None:
        raise TurnEngineError(f"{actor.id} is already wild-shaped")
    beast_index = action.params.get("beast_index")
    if not beast_index:
        raise TurnEngineError("wild_shape action requires params['beast_index']")
    beast = srd.monsters.get(beast_index)
    if beast is None:
        raise TurnEngineError(f"Unknown monster: {beast_index!r}")
    if not wild_shape_beast_is_allowed(beast, actor.level):
        raise TurnEngineError(
            f"{actor.id} cannot Wild Shape into {beast['name']} at level {actor.level} "
            f"(max CR {max_wild_shape_cr(actor.level)}, no flying speed, no swimming speed "
            "below level 4)"
        )

    _use_class_resource(actor, "wild_shape")

    actor.pre_wild_shape_snapshot = WildShapeSnapshot(
        hp=actor.hp,
        max_hp=actor.max_hp,
        ac=actor.ac,
        stats=dict(actor.stats),
        speed=actor.speed,
        equipped_weapons=list(actor.equipped_weapons),
        monster_index=actor.monster_index,
    )
    beast_character = monster_to_character(beast, actor.id, actor.position)
    actor.hp = beast_character.hp
    actor.max_hp = beast_character.max_hp
    actor.ac = beast_character.ac
    actor.stats = beast_character.stats
    actor.speed = beast_character.speed
    actor.equipped_weapons = []
    actor.monster_index = beast_index
    actor.wild_shape_beast_index = beast_index

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="wild_shape",
            payload={"beast": beast["name"]},
        )
    )
    return True


def _apply_wild_shape_snapshot(actor: Character) -> None:
    """Restores everything Wild Shape swapped except hp, which each caller
    sets on its own first - unchanged for a voluntary revert, overflow-
    adjusted for a forced one (_resolve_revert_wild_shape /
    _apply_damage_and_handle_downing)."""
    snapshot = actor.pre_wild_shape_snapshot
    assert snapshot is not None
    actor.max_hp = snapshot.max_hp
    actor.ac = snapshot.ac
    actor.stats = snapshot.stats
    actor.speed = snapshot.speed
    actor.equipped_weapons = snapshot.equipped_weapons
    actor.monster_index = snapshot.monster_index
    actor.wild_shape_beast_index = None
    actor.pre_wild_shape_snapshot = None


def _resolve_revert_wild_shape(state: GameState, actor: Character) -> bool:
    """Voluntary revert (issue #24) - a bonus action per SRD, restoring
    hp exactly as it was before transforming (only a forced 0-HP revert
    carries damage over - see _apply_damage_and_handle_downing)."""
    if actor.wild_shape_beast_index is None:
        raise TurnEngineError(f"{actor.id} is not wild-shaped")
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - cannot revert Wild Shape"
        )
    snapshot = actor.pre_wild_shape_snapshot
    assert snapshot is not None
    actor.hp = snapshot.hp
    _apply_wild_shape_snapshot(actor)
    actor.bonus_action_used = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="wild_shape_ended",
            payload={"forced": False},
        )
    )
    return False


def _target_saving_throw_bonus(target: Character, ability: AbilityScore, srd: SrdIndex) -> int:
    """The one saving-throw lookup every Phase 9D call site needs (a
    target's own save, whether resisting a spell or resisting concentration
    break): a monster (has monster_index) saves via its stat block
    (rules.monster_saving_throw_bonus), everyone else (PCs/companions) via
    their class proficiencies (rules.saving_throw_bonus) - same branch
    turn_engine already uses for attack params (_pc_attack_params vs
    _monster_attack_params)."""
    if target.monster_index is not None:
        return monster_saving_throw_bonus(srd.monsters[target.monster_index], ability)
    return saving_throw_bonus(target, ability)


def _target_saving_throw_breakdown(
    target: Character, ability: AbilityScore, srd: SrdIndex
) -> list[tuple[str, int]]:
    """Debug-mode UI aid (issue #38), sibling to _target_saving_throw_bonus
    above (same PC-vs-monster branch) - a monster's stat-block save bonus is
    one precomputed SRD number with no further breakdown available, same
    reasoning as AttackParams.attack_bonus_breakdown's monster case."""
    if target.monster_index is not None:
        bonus = monster_saving_throw_bonus(srd.monsters[target.monster_index], ability)
        return [("saving throw (stat block)", bonus)]
    mod = ability_modifier(target.stats[ability])
    proficient = ability in target.saving_throw_proficiencies
    return [
        (f"{ability} mod", mod),
        ("proficiency", target.proficiency_bonus) if proficient else ("proficiency (none)", 0),
    ]


def _check_concentration_break(
    state: GameState, character: Character, damage: int, rng: random.Random, srd: SrdIndex
) -> None:
    """Phase 9D: a character concentrating on a spell who takes damage must
    make a CON save (DC = max(10, damage // 2), per SRD) or lose
    concentration. `damage` is the amount actually dealt (before HP
    clamping), matching the SRD rule ("half the damage you take"), not the
    possibly-smaller actual_loss apply_damage returns for an overkill hit.
    This engine doesn't yet model removing an ongoing effect on a failed
    save - no concentration spell applies one yet (Phase 9D scope; see
    Character.concentrating_on's docstring) - so a failure here only clears
    the tracking field."""
    if character.concentrating_on is None or damage <= 0:
        return
    dc = max(10, damage // 2)
    # Bless (issue #57) applies here too - a concentration save is a real
    # saving throw, not a special case - see _resolve_single_attack's
    # identical handling.
    bless = blessed_bonus(character, rng)
    save_bonus = _target_saving_throw_bonus(character, "CON", srd) + bless
    result, success = resolve_saving_throw(
        save_bonus=save_bonus, dc=dc, rng=rng, lucky=has_lucky_trait(character)
    )
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=character.id,
            type="saving_throw",
            payload={
                "kind": "concentration",
                "spell": character.concentrating_on,
                "dc": dc,
                "roll_total": result.total,
                "natural": result.kept[0],
                "success": success,
                "modifier_breakdown": [
                    *_target_saving_throw_breakdown(character, "CON", srd),
                    *([("blessed (1d4)", bless)] if bless else []),
                ],
            },
        )
    )
    if not success:
        character.concentrating_on = None


def _apply_damage_and_handle_downing(
    state: GameState,
    attacker: Character,
    target: Character,
    damage: int,
    damage_type: str,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """Shared by attack and cast_spell (Day 14; Phase 9D added the rng/srd
    params for the concentration check below) - both can reduce a character
    to 0 HP and need the same monster-dies-outright-vs-PC-goes-unconscious
    handling."""
    # Rage (Phase 9I): resistance halves bludgeoning/piercing/slashing
    # damage (rounded down, plain integer division) before anything else
    # sees it - the concentration check below uses "half the damage you
    # take" per SRD, meaning the post-resistance amount, not the raw hit.
    if target.is_raging and damage_type in _RAGE_RESISTANT_DAMAGE_TYPES:
        damage //= 2
    # Issue #18: a monster's own SRD resistances/immunities/vulnerabilities
    # - always a 1.0 no-op for a PC/companion target, see
    # rules.monster_damage_multiplier's own docstring for the free-text
    # substring-matching rationale. int() truncation matches SRD's
    # "resistance halves damage, rounded down" for the 0.5 case.
    damage = int(damage * monster_damage_multiplier(target, damage_type, srd))

    # Sleep (issue #55): unlike the ordinary "downed at 0 HP" unconscious,
    # which never lifts on its own, a Sleep-induced one ends the instant
    # the sleeper takes ANY damage, per SRD - checked before apply_damage
    # mutates HP (this only cares whether real damage landed, not the
    # resulting HP total) and gated on the distinct _SLEEP_UNCONSCIOUS_
    # SOURCE tag so this can never fire for the "0 HP"/"hazard" sources
    # that already use the same "unconscious" condition name.
    if damage > 0:
        sleeping = next(
            (
                c
                for c in target.conditions
                if c.name == "unconscious" and c.source == _SLEEP_UNCONSCIOUS_SOURCE
            ),
            None,
        )
        if sleeping is not None:
            remove_condition(target, "unconscious")

    actual_loss = apply_damage(target, damage)

    # Druid's Wild Shape (issue #24): the beast form, not the Druid's real
    # body, just hit 0 HP - force a revert (SRD: "you revert if you drop to
    # 0 hit points... any excess damage carries over to your normal form"),
    # using the same damage/actual_loss overflow math issue #23's
    # Relentless Endurance hook below already needed. Falls through (no
    # return) rather than handling death/unconsciousness itself - once
    # reverted, target.monster_index is back to whatever it really was
    # (None for a PC Druid), so the ordinary is_pc/unconscious logic below,
    # and even Relentless Endurance right below this if the Druid also
    # happens to be a Half-Orc, now run correctly against the real,
    # reverted character rather than the beast.
    if target.hp == 0 and target.wild_shape_beast_index is not None:
        overflow = damage - actual_loss
        snapshot = target.pre_wild_shape_snapshot
        assert snapshot is not None
        target.hp = max(0, snapshot.hp - overflow)
        _apply_wild_shape_snapshot(target)
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=target.id,
                type="wild_shape_ended",
                payload={"forced": True, "overflow_damage": overflow},
            )
        )

    # Half-Orc's Relentless Endurance (issue #23): reduced to 0 HP, not
    # already unconscious from an earlier hit this fight (that "already
    # down" check mirrors the one further below - a second hit against an
    # already-downed Half-Orc shouldn't re-trigger this), not used since
    # their last long rest. Applied before the damage_dealt event below so
    # its target_hp_remaining reflects the real outcome (1, not 0).
    if (
        target.hp == 0
        and target.is_pc
        and not has_condition(target, "unconscious")
        and has_relentless_endurance(target)
        and not target.used_relentless_endurance_this_rest
    ):
        target.hp = 1
        target.used_relentless_endurance_this_rest = True
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=target.id,
                type="relentless_endurance",
                payload={"target": target.id},
            )
        )

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=attacker.id,
            type="damage_dealt",
            payload={
                "target": target.id,
                "amount": actual_loss,
                "damage_type": damage_type,
                "target_hp_remaining": target.hp,
            },
        )
    )
    _check_concentration_break(state, target, damage, rng, srd)
    if target.hp > 0 or target.is_dead:
        return

    if not target.is_pc:
        target.is_dead = True
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=target.id,
                type="death",
                payload={"killed_by": attacker.id},
            )
        )
        return

    if has_condition(target, "unconscious"):
        # Already down before this hit landed (Phase 9C: a hit against an
        # already-unconscious PC) - do NOT re-run the "just went unconscious"
        # setup below, which would reset death_save_successes/failures/
        # is_stable to a fresh start every single time. That reset is only
        # correct the *first* time a PC drops to 0 HP; before this check
        # existed, every subsequent hit against an already-downed PC would
        # have silently wiped whatever death-save progress they'd
        # accumulated, which would also wipe the 2 automatic failures
        # _resolve_attack applies right after this function returns. Still
        # at 0 HP, still unconscious - nothing new to narrate here.
        return

    # PC at 0 HP for the first time this hit: unconscious, not dead -
    # death_save (below) decides its fate.
    apply_condition(target, Condition(name="unconscious", source="0 HP"))
    target.death_save_successes = 0
    target.death_save_failures = 0
    target.is_stable = False
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=target.id,
            type="condition_applied",
            payload={"condition": "unconscious"},
        )
    )


def _apply_hazard_damage(state: GameState, actor: Character, position: Position) -> None:
    """A character that moves onto a "hazard" square takes HAZARD_DAMAGE
    immediately - see that constant's docstring for the SRD-adjacent amount
    and flavor. A distinct event type ("hazard_damage", not "damage_dealt")
    since there's no attacking character here, just terrain; mirrors
    _apply_damage_and_handle_downing's monster-dies/PC-goes-unconscious
    handling for the (unlikely, at 1 flat damage) case this finishes off an
    already-critical character.

    Doesn't call _check_concentration_break (Phase 9D) - per SRD, any
    damage should force that check, not just an attack roll's, but doing so
    here would mean threading rng/srd through _resolve_move's whole call
    chain for a narrow edge case (a concentrating spellcaster stepping onto
    hazard terrain). Documented gap, not a silent one."""
    actual_loss = apply_damage(actor, HAZARD_DAMAGE)
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="hazard_damage",
            payload={
                "amount": actual_loss,
                "damage_type": HAZARD_DAMAGE_TYPE,
                "position": {"x": position.x, "y": position.y},
                "hp_remaining": actor.hp,
            },
        )
    )
    if actor.hp > 0 or actor.is_dead:
        return

    if not actor.is_pc:
        actor.is_dead = True
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=actor.id,
                type="death",
                payload={"killed_by": "hazard"},
            )
        )
        return

    apply_condition(actor, Condition(name="unconscious", source="hazard"))
    actor.death_save_successes = 0
    actor.death_save_failures = 0
    actor.is_stable = False
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="condition_applied",
            payload={"condition": "unconscious"},
        )
    )


def _hostiles_leaving_reach(
    state: GameState, mover: Character, origin: Position, destination: Position
) -> list[Character]:
    """Living hostile creatures (opposite `is_pc`) within 5ft of `origin`
    but no longer within 5ft of `destination` - Phase 9H's opportunity-
    attack trigger. Only origin/destination are checked, not squares passed
    through mid-path, matching this function's existing "only the
    destination matters" stance for occupancy/hazard checks above."""
    return [
        other
        for other in state.characters.values()
        if other.id != mover.id
        and not other.is_dead
        and other.is_pc != mover.is_pc
        and distance_feet(origin, other.position) <= 5
        and distance_feet(destination, other.position) > 5
    ]


def _resolve_opportunity_attacks(
    state: GameState,
    mover: Character,
    origin: Position,
    destination: Position,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """Phase 9H's one modeled reaction: each hostile `mover` was adjacent to
    at `origin` but won't be at `destination` gets one free attack, unless
    `mover` disengaged this turn or the reactor has already used their one
    reaction this round (SRD: one reaction per round, not per creature
    moved away from). Resolved with `mover` still AT `origin` (this is
    called before actor.position is updated) so _resolve_single_attack's
    own range check sees them as still adjacent - an opportunity attack is
    a free swing at the exact moment of leaving reach, not at the
    already-moved-away final position. Stops early if `mover` dies."""
    if mover.disengaged_this_turn:
        return
    for reactor in _hostiles_leaving_reach(state, mover, origin, destination):
        if reactor.reaction_used_this_round or _is_incapacitated(reactor):
            continue
        reactor.reaction_used_this_round = True
        params = (
            _monster_attack_params(reactor, None, srd)
            if reactor.monster_index is not None
            else _pc_attack_params(reactor, None, srd)
        )
        _resolve_single_attack(state, reactor, mover, params, rng, srd)
        if mover.is_dead:
            return


def _resolve_move(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if state.battle_map is None:
        raise TurnEngineError("Cannot resolve movement without a battle_map on GameState")
    raw_path = action.params.get("path")
    if not raw_path:
        raise TurnEngineError("move/dash action requires params['path']")

    steps = [Position(x=p["x"], y=p["y"]) for p in raw_path]
    full_path = [actor.position, *steps]
    # effective_speed (Phase 9A) accounts for grappled (speed 0) and
    # exhaustion (halved at level 2+, 0 at level 5+) - dash then doubles
    # whatever that reduced budget is, per SRD (dash doesn't restore speed
    # exhaustion/grappling has already taken away).
    base_speed = effective_speed(actor)
    total_budget = base_speed * 2 if action.verb == "dash" else base_speed
    # A plain move no longer ends the turn (found live - see
    # Character.movement_used_feet's own docstring), so a second move this
    # same turn only gets whatever's left of the budget, not a fresh one.
    remaining_budget = max(0, total_budget - actor.movement_used_feet)

    if not can_afford_move(remaining_budget, full_path, state.battle_map.terrain):
        cost = move_cost_feet(full_path, state.battle_map.terrain)
        raise TurnEngineError(
            f"{actor.id} cannot afford this move (cost={cost}, speed budget={remaining_budget})"
        )
    actor.movement_used_feet += move_cost_feet(full_path, state.battle_map.terrain) or 0

    destination = steps[-1]
    occupant = next(
        (
            c
            for c in state.characters.values()
            if not c.is_dead
            and c.id != actor.id
            and c.position.x == destination.x
            and c.position.y == destination.y
        ),
        None,
    )
    if occupant is not None:
        raise TurnEngineError(
            f"({destination.x}, {destination.y}) is already occupied by {occupant.name}"
        )

    origin = actor.position
    _resolve_opportunity_attacks(state, actor, origin, destination, rng, srd)
    # Consumed here, not reset alongside is_dodging/bonus_action_used at the
    # start of a turn (see Character.disengaged_this_turn's docstring) -
    # this engine's one-verb-per-turn model means disengage and the move it
    # protects can only ever happen on two of this character's separate
    # real turns, so the flag has to survive every other actor's turns in
    # between and is spent the next time THIS character actually moves,
    # whether or not a hostile was even adjacent to make it matter.
    actor.disengaged_this_turn = False
    if actor.is_dead:
        return

    actor.position = destination
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="move",
            payload={
                "from": {"x": origin.x, "y": origin.y},
                "to": {"x": actor.position.x, "y": actor.position.y},
                "dashed": action.verb == "dash",
            },
        )
    )

    # Phase 9F: "hazard" terrain previously had zero mechanical effect - see
    # HAZARD_DAMAGE's docstring for the amount/flavor reasoning. Only the
    # final destination is checked, matching this function's pre-existing
    # "only the destination matters, not squares passed through" stance for
    # occupancy above.
    if state.battle_map.terrain[destination.y][destination.x] == "hazard":
        _apply_hazard_damage(state, actor, destination)


def _ability_check_breakdown(
    actor: Character, ability: AbilityScore, proficient: bool
) -> list[tuple[str, int]]:
    """Debug-mode UI aid (issue #38), mirrors AttackParams.attack_bonus_
    breakdown's own reasoning - the named components ability_check_modifier
    sums into one int, for a skill-check event's payload. Explicit
    "proficiency (none)" zero-entry when not proficient, same as the weapon-
    attack breakdown, so a live combat log can distinguish "correctly
    withheld" from "silently missing"."""
    mod = ability_modifier(actor.stats[ability])
    return [
        (f"{ability} mod", mod),
        ("proficiency", actor.proficiency_bonus) if proficient else ("proficiency (none)", 0),
    ]


def _resolve_skill_check(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    skill = action.params.get("skill")
    if not skill:
        raise TurnEngineError("skill_check action requires params['skill']")

    try:
        ability = skill_ability(skill, srd)
    except ValueError as exc:
        raise TurnEngineError(str(exc)) from exc
    proficient = f"skill-{normalize_skill_name(skill)}" in actor.skill_proficiencies
    modifier = ability_check_modifier(actor, ability, proficient=proficient)

    advantage = actor.has_help_advantage
    actor.has_help_advantage = False
    # Per SRD, non-proficient armor imposes disadvantage on STR/DEX checks
    # specifically (not INT/WIS/CHA ones like Perception or Persuasion);
    # poisoned/frightened/exhaustion (Phase 9A) apply to every ability check.
    disadvantage = (
        ability in ("STR", "DEX") and has_non_proficient_armor(actor, srd)
    ) or condition_check_disadvantage(actor)

    result, success = resolve_skill_check(
        modifier=modifier,
        dc=DEFAULT_SKILL_CHECK_DC,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        lucky=has_lucky_trait(actor),
    )
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="skill_check",
            payload={
                "skill": skill,
                "ability": ability,
                "dc": DEFAULT_SKILL_CHECK_DC,
                "roll_total": result.total,
                "natural": result.kept[0],
                "success": success,
                "modifier_breakdown": _ability_check_breakdown(actor, ability, proficient),
            },
        )
    )


def _resolve_dodge(state: GameState, actor: Character) -> None:
    actor.is_dodging = True
    state.events.append(
        Event(round=state.round, turn_index=state.current_turn, actor=actor.id, type="dodge")
    )


def _resolve_disengage(state: GameState, actor: Character) -> None:
    # Phase 9H: finally gives this verb a real mechanical effect - see
    # Character.disengaged_this_turn's docstring for the simplification
    # around exactly how long it protects, and _resolve_move for how it's
    # actually checked against opportunity attacks.
    actor.disengaged_this_turn = True
    state.events.append(
        Event(round=state.round, turn_index=state.current_turn, actor=actor.id, type="disengage")
    )


def _use_class_resource(actor: Character, resource: str) -> None:
    """Consumes one use of a Character.class_resources entry (Phase 9I),
    raising a clear error if none remain - shared by Second Wind and Rage
    so the "out of uses" message stays worded consistently."""
    remaining = actor.class_resources.get(resource, 0)
    if remaining <= 0:
        raise TurnEngineError(f"{actor.id} has no {resource.replace('_', ' ')} uses remaining")
    actor.class_resources[resource] = remaining - 1


def _resolve_second_wind(state: GameState, actor: Character, rng: random.Random) -> bool:
    """Phase 9I (Fighter): a bonus-action self-heal, 1d10 + level, per SRD.
    Returns False (doesn't end the turn) like a bonus-action spell -
    resolve_action's ends_turn flag treats it identically."""
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - cannot use Second Wind"
        )
    _use_class_resource(actor, "second_wind")
    dice_count, dice_sides, _ = parse_dice_notation(SECOND_WIND_DICE)
    healed = min(
        roll(dice_count, dice_sides, modifier=actor.level, rng=rng).total,
        actor.max_hp - actor.hp,
    )
    actor.hp += healed
    actor.bonus_action_used = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="hp_change",
            payload={
                "amount": healed,
                "source": "Second Wind",
                "target": actor.id,
                "hp_remaining": actor.hp,
            },
        )
    )
    return False


def _resolve_rage(state: GameState, actor: Character, rng: random.Random) -> bool:
    """Phase 9I (Barbarian): a bonus-action that activates a transient
    is_raging flag (resistance to bludgeoning/piercing/slashing damage,
    plus a flat melee-STR damage bonus - see _pc_attack_params/
    _apply_damage_and_handle_downing). Returns False (doesn't end the turn)
    like Second Wind/a bonus-action spell. No dice to roll - `rng` is
    accepted only so this resolver's signature matches its siblings and
    resolve_action's dispatch doesn't need a special case."""
    del rng
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - cannot Rage"
        )
    if actor.is_raging:
        raise TurnEngineError(f"{actor.id} is already raging")
    _use_class_resource(actor, "rage")
    actor.is_raging = True
    actor.bonus_action_used = True
    state.events.append(
        Event(round=state.round, turn_index=state.current_turn, actor=actor.id, type="rage")
    )
    return False


def _recompute_ac(actor: Character, srd: SrdIndex) -> None:
    """Rebuilds `actor.ac` from every currently-live input - extracted
    (issue #55 spell audit) since armor_ac's own param list has grown with
    each new AC-affecting feature (Monk/Barbarian Unarmored Defense, Mage
    Armor, Shield of Faith...) and every call site needs to pass all of
    them or silently drop one - a real, easy-to-miss drift risk this
    collects in one place instead. Called whenever something that could
    change AC happens after creation: equipping/unequipping armor or a
    shield, and any spell that sets/clears mage_armor_active or
    temporary_ac_bonus."""
    actor.ac = armor_ac(
        actor.equipped_armor,
        actor.equipped_shield,
        ability_modifier(actor.stats["DEX"]),
        actor.fighting_style,
        srd.equipment,
        class_index=actor.class_index,
        wis_mod=ability_modifier(actor.stats["WIS"]),
        con_mod=ability_modifier(actor.stats["CON"]),
        mage_armor_active=actor.mage_armor_active,
        temporary_ac_bonus=actor.temporary_ac_bonus,
    )


def _resolve_equip(state: GameState, actor: Character, action: ParsedAction, srd: SrdIndex) -> bool:
    """Phase C: SRD's free "object interaction" to draw/switch weapons -
    changes which of the actor's owned weapons _pc_attack_params will
    actually match against. Widened for issue #13 to also cover armor and a
    shield (same free-action treatment as weapons - explicitly not modeling
    the SRD's real armor-donning time, an accepted simplification already
    established for weapon-switching): equipping either or both changes
    `equipped_armor`/`equipped_shield` and recomputes `ac` via
    rules.armor_ac, since AC can no longer be a fixed-at-creation value.
    `params["items"]` can name weapons, armor, a shield, or any mix in one
    call - each kind only overwrites its own slot(s), so equipping just a
    weapon never clears currently-worn armor and vice versa. Returns False
    (doesn't end the turn), same treatment as Second Wind/Rage/a bonus-
    action spell, but gated by its own equip_used_this_turn flag rather
    than bonus_action_used (equip isn't a bonus action, and shouldn't
    consume one - SRD keeps them separate resources)."""
    if actor.equip_used_this_turn:
        raise TurnEngineError(f"{actor.id} has already equipped something this turn")
    names_or_indices = action.params.get("items")
    if not names_or_indices:
        raise TurnEngineError("equip action requires params['items']")
    # Same fuzzy-name-matching discipline as _pc_attack_params/
    # _match_weapon_by_name (Day 14's healing-potion fix, the "silvered
    # longbow" fix): the intent parser passes whatever item phrase the
    # player used, not necessarily an exact SRD index - scoped to the
    # actor's own inventory (what they could plausibly equip), not the
    # whole SRD equipment list. _match_weapon_by_name's own matching logic
    # is generic (word-set match against a candidate list) despite its
    # name - reused as-is for armor, not duplicated.
    owned_weapons = [
        item
        for idx in actor.inventory
        if (item := srd.equipment.get(idx)) and item.get("weapon_category")
    ]
    owned_armor = [
        item
        for idx in actor.inventory
        if (item := srd.equipment.get(idx)) and item.get("armor_category")
    ]

    resolved_weapons: list[str] = []
    resolved_armor: str | None = None
    resolved_shield: str | None = None
    for name in names_or_indices:
        exact = srd.equipment.get(name)
        if exact is not None and not (exact.get("weapon_category") or exact.get("armor_category")):
            raise TurnEngineError(f"{name!r} is not a weapon or armor")
        item = exact if exact is not None and exact["index"] in actor.inventory else None
        if item is None:
            item = _match_weapon_by_name(name, owned_weapons) or _match_weapon_by_name(
                name, owned_armor
            )
        if item is None:
            raise TurnEngineError(f"{actor.id} doesn't own {name!r} - cannot equip it")

        if item.get("weapon_category"):
            resolved_weapons.append(item["index"])
        elif item["armor_category"] == "Shield":
            if resolved_shield is not None:
                raise TurnEngineError("Cannot equip two shields at once")
            resolved_shield = item["index"]
        else:
            if resolved_armor is not None:
                raise TurnEngineError("Cannot equip two suits of armor at once")
            resolved_armor = item["index"]

    # Issue #26: validated as one combined loadout, not weapons and shield
    # independently - a live bug found this exact call resolving just
    # `params["items"] = ["shield"]` (weapons untouched from whatever was
    # already equipped) and equipping the shield with zero cross-check
    # against the 2 one-handed weapons already worn, ending up with 3
    # hands' worth of gear. `final_weapons`/`final_shield` fold in whatever
    # this call *isn't* touching (unchanged from the actor's current
    # loadout) so the check always covers the real resulting state, not
    # just what's newly named in this one call.
    final_weapons = resolved_weapons if resolved_weapons else actor.equipped_weapons
    final_shield = resolved_shield if resolved_shield is not None else actor.equipped_shield
    if not weapon_combo_is_legal(
        final_weapons, srd.equipment, shield_equipped=final_shield is not None
    ):
        names = ", ".join(srd.equipment[idx]["name"] for idx in final_weapons)
        shield_note = f" plus {srd.equipment[final_shield]['name']}" if final_shield else ""
        raise TurnEngineError(
            f"Cannot equip {names}{shield_note} together - at most 2 hands' worth of "
            "weapons/shield: a two-handed weapon needs both hands, 2 one-handed weapons "
            "must both be light, and a shield takes a hand of its own"
        )

    changed_items: list[str] = list(resolved_weapons)
    if resolved_weapons:
        actor.equipped_weapons = resolved_weapons
    if resolved_armor is not None:
        actor.equipped_armor = resolved_armor
        changed_items.append(resolved_armor)
    if resolved_shield is not None:
        actor.equipped_shield = resolved_shield
        changed_items.append(resolved_shield)
    if resolved_armor is not None or resolved_shield is not None:
        _recompute_ac(actor, srd)

    actor.equip_used_this_turn = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="equip",
            payload={"items": changed_items},
        )
    )
    return False


def _resolve_help(state: GameState, actor: Character, action: ParsedAction) -> None:
    if action.target is None:
        raise TurnEngineError("help action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown help target: {action.target}")

    target.has_help_advantage = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="help",
            payload={"target": target.id},
        )
    )


def _resolve_bardic_inspiration(state: GameState, actor: Character, action: ParsedAction) -> bool:
    """Bard's Bardic Inspiration (issue #25): a bonus action, same gate
    shape as Second Wind (_use_class_resource + bonus_action_used). Real
    SRD text is "a creature other than yourself" - rejects self-targeting
    outright. Banks a die on the target, consumed by their next attack
    roll only - see Character.bardic_inspiration_die's own docstring for
    why this is narrower than full SRD scope (checks/saves too). Returns
    False (doesn't end the turn)."""
    if actor.class_index != "bard":
        raise TurnEngineError(f"{actor.id} doesn't have Bardic Inspiration")
    if actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - "
            "cannot use Bardic Inspiration"
        )
    if action.target is None:
        raise TurnEngineError("bardic_inspiration action requires a target")
    if action.target == actor.id:
        raise TurnEngineError("Bardic Inspiration can only target a creature other than yourself")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown bardic_inspiration target: {action.target}")

    _use_class_resource(actor, "bardic_inspiration")
    target.bardic_inspiration_die = bardic_inspiration_die_sides(actor.level)
    actor.bonus_action_used = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="bardic_inspiration",
            payload={"target": target.id, "die_sides": target.bardic_inspiration_die},
        )
    )
    return False


def _grapple_shove_contest(
    actor: Character, target: Character, rng: random.Random
) -> tuple[int, int]:
    """The contested check shared by grapple and shove (Phase 9E): per SRD
    both use the attacker's Athletics check against the target's choice of
    Athletics or Acrobatics.

    RNG consumption order - exactly 3 d20s, always consumed in this order
    regardless of outcome (both of the target's two defense checks are
    always rolled, even though only the higher one ends up counting, so a
    fixed-rng test fixture must always supply exactly 3 values for a single
    grapple/shove attempt):
      1. actor's Athletics check (STR, +proficiency bonus if
         "skill-athletics" is in actor.skill_proficiencies)
      2. target's own Athletics check (STR, same proficiency rule)
      3. target's Acrobatics check (DEX, same proficiency rule)

    Returns (actor_total, target_total), where target_total is whichever of
    (2)/(3) came out higher. `resolve_skill_check`'s `dc` parameter is
    unused for this purpose (passed as 0) - pass/fail is a plain comparison
    of the two totals, decided by the caller, not a fixed DC.

    Deliberately out of scope for this pass (documented, not silent, same
    spirit as this engine's other narrow simplifications): no 5ft range/
    reach check on the attempt (grapple/shove are SRD melee-only, but
    nothing here enforces adjacency), and a successful shove never
    implements the optional "push 5ft away" - it only ever knocks prone.
    Halfling's Lucky trait (issue #23) is also deliberately not wired in
    here, unlike every other d20 roll in this engine - a reroll would
    consume an extra, conditional d20, breaking this function's own fixed
    "always exactly 3 d20s" contract that callers/tests rely on."""
    actor_modifier = ability_check_modifier(
        actor, "STR", proficient="skill-athletics" in actor.skill_proficiencies
    )
    actor_result, _ = resolve_skill_check(modifier=actor_modifier, dc=0, rng=rng)

    target_athletics_modifier = ability_check_modifier(
        target, "STR", proficient="skill-athletics" in target.skill_proficiencies
    )
    target_athletics_result, _ = resolve_skill_check(
        modifier=target_athletics_modifier, dc=0, rng=rng
    )

    target_acrobatics_modifier = ability_check_modifier(
        target, "DEX", proficient="skill-acrobatics" in target.skill_proficiencies
    )
    target_acrobatics_result, _ = resolve_skill_check(
        modifier=target_acrobatics_modifier, dc=0, rng=rng
    )

    target_total = max(target_athletics_result.total, target_acrobatics_result.total)
    return actor_result.total, target_total


def _resolve_grapple(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if action.target is None:
        raise TurnEngineError("grapple action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown grapple target: {action.target}")
    _validate_attack_target(actor, target)
    # Issue #18: SRD - a creature immune to the grappled condition (oozes,
    # most incorporeal undead, elementals...) can't be grappled at all, so
    # the attempt is rejected outright rather than rolled and silently
    # doing nothing on a "success".
    if monster_is_immune_to_condition(target, "grappled", srd):
        raise TurnEngineError(f"{target.id} is immune to the grappled condition")

    actor_total, target_total = _grapple_shove_contest(actor, target, rng)
    # SRD contested-check resolution: the higher total wins outright, but a
    # tie leaves the situation as it was before the contest ("the situation
    # remains the same as it was before the contest" - PHB) - since a
    # grapple attempt is trying to *change* the target's state, a tie means
    # it fails, so this needs a strict ">" rather than ">=".
    success = actor_total > target_total

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="grapple_attempt",
            payload={
                "target": target.id,
                "actor_total": actor_total,
                "target_total": target_total,
                "success": success,
            },
        )
    )
    if not success:
        return

    apply_condition(target, Condition(name="grappled", source=actor.id))
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="condition_applied",
            payload={"condition": "grappled", "target": target.id},
        )
    )


def _resolve_shove(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if action.target is None:
        raise TurnEngineError("shove action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown shove target: {action.target}")
    _validate_attack_target(actor, target)
    # Issue #18: same reasoning as _resolve_grapple - immune means the
    # attempt can't succeed, so it's rejected outright, not rolled.
    if monster_is_immune_to_condition(target, "prone", srd):
        raise TurnEngineError(f"{target.id} is immune to the prone condition")

    actor_total, target_total = _grapple_shove_contest(actor, target, rng)
    # Same contest and same tie-goes-to-the-defender resolution as grapple -
    # see _resolve_grapple's comment above.
    success = actor_total > target_total

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="shove_attempt",
            payload={
                "target": target.id,
                "actor_total": actor_total,
                "target_total": target_total,
                "success": success,
            },
        )
    )
    if not success:
        return

    # Prone-only (SRD's other shove option, pushing the target 5ft away, is
    # out of scope for this pass - see _grapple_shove_contest's docstring).
    apply_condition(target, Condition(name="prone", source=actor.id))
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="condition_applied",
            payload={"condition": "prone", "target": target.id},
        )
    )


def _spellcasting_ability_mod(actor: Character, srd: SrdIndex) -> tuple[AbilityScore, int]:
    """(spellcasting ability, its modifier) for `actor`'s class - shared by
    all three cast_spell mechanics (Phase 9D: attack/save/heal all need it),
    previously computed inline only for the attack-roll path (Day 14, the
    only mechanic that existed then)."""
    if actor.class_index is None:
        raise TurnEngineError(
            f"{actor.id} has no class_index - cannot determine spellcasting ability"
        )
    cls = srd.classes.get(actor.class_index)
    spellcasting = cls.get("spellcasting") if cls else None
    if not spellcasting:
        raise TurnEngineError(f"{actor.class_} has no spellcasting ability")
    ability: AbilityScore = spellcasting["spellcasting_ability"]["index"].upper()
    return ability, ability_modifier(actor.stats[ability])


def _spell_attack_params(
    actor: Character, spell: SrdEntry, spell_level: int, srd: SrdIndex
) -> AttackParams:
    """Attack-roll spell parameters (e.g. Fire Bolt, Guiding Bolt) - `spell`
    is already looked up and classified by the caller (_resolve_cast_spell)."""
    ability, ability_mod = _spellcasting_ability_mod(actor, srd)
    damage_info = spell["damage"]
    notation = spell_damage_notation(spell, spell_level)
    dice_count, dice_sides, notation_bonus = parse_dice_notation(notation)

    return AttackParams(
        attack_bonus=ability_mod + actor.proficiency_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        # 5e spell damage doesn't add the spellcasting ability modifier
        # (unlike weapon damage) - only whatever bonus is in the notation
        # itself (e.g. Magic Missile's embedded "+3", not applicable here
        # since it's a no-roll spell excluded by rules.spell_mechanic; attack-roll
        # spells in the SRD generally have none).
        damage_bonus=notation_bonus,
        damage_type=damage_info["damage_type"]["index"],
        source_name=spell["name"],
        range_normal_feet=spell_range_feet(spell),
        range_long_feet=None,  # spells have no "beyond normal" disadvantage tier
        attack_bonus_breakdown=[
            (f"{ability} mod", ability_mod),
            ("proficiency", actor.proficiency_bonus),
        ],
    )


def _cast_attack_spell_at_target(
    state: GameState,
    actor: Character,
    target: Character,
    params: AttackParams,
    spell_level: int,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """One target's independent attack roll (Phase 9D multi-target: called
    once per id in action.targets, or once for the single legacy `target`).

    Mirrors _resolve_single_attack's Phase 9C unconscious-hit handling (auto-
    crit + 2 automatic death-save failures against an already-unconscious
    target) - the SRD rule is "any attack roll," not "any weapon attack,"
    so an attack-roll spell against a downed ally gets the same treatment a
    melee/ranged weapon attack does."""
    distance = distance_feet(actor.position, target.position)
    advantage = (
        actor.has_help_advantage
        or condition_attack_advantage(actor, target, distance)
        or actor.true_strike_advantage
    )
    actor.has_help_advantage = False
    actor.true_strike_advantage = False

    already_unconscious = has_condition(target, "unconscious")

    # Bardic Inspiration (issue #25) - see _resolve_single_attack's
    # identical handling for why this is captured before the call.
    bardic_die_sides = actor.bardic_inspiration_die

    # Bless (issue #57) - see _resolve_single_attack's identical handling.
    bless = blessed_bonus(actor, rng)
    if bless:
        params = replace(
            params,
            attack_bonus=params.attack_bonus + bless,
            attack_bonus_breakdown=[*params.attack_bonus_breakdown, ("blessed (1d4)", bless)],
        )

    result = resolve_attack(
        defender_ac=target.ac,
        attack_bonus=params.attack_bonus,
        damage_dice_count=params.damage_dice_count,
        damage_dice_sides=params.damage_dice_sides,
        damage_bonus=params.damage_bonus,
        damage_type=params.damage_type,
        rng=rng,
        advantage=advantage,
        disadvantage=target.is_dodging or condition_attack_disadvantage(actor, target, distance),
        force_critical=already_unconscious,
        lucky=has_lucky_trait(actor),
        bardic_die_sides=bardic_die_sides,
    )
    if bardic_die_sides:
        actor.bardic_inspiration_die = None

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={
                "spell": params.source_name,
                "target": target.id,
                "spell_level": spell_level,
                "roll_total": result.attack_roll.total,
                "hit": result.hit,
                "critical": result.critical,
                "attack_bonus_breakdown": params.attack_bonus_breakdown,
                **({"bardic_inspiration_die_sides": bardic_die_sides} if bardic_die_sides else {}),
            },
        )
    )

    if result.hit and result.damage is not None:
        _apply_damage_and_handle_downing(
            state, actor, target, result.damage, params.damage_type, rng, srd
        )

    if result.hit and already_unconscious and target.is_pc and not target.is_dead:
        _apply_unconscious_hit_death_save_failures(state, target)


@dataclass(frozen=True)
class SaveSpellParams:
    dc: int
    dc_ability: AbilityScore
    dc_success: str
    """"half" or "none", from the SRD's `dc_success` field: a failed save
    always takes full damage; "half" halves it (rounded down, plain integer
    division) on a success, "none" means a success avoids the effect
    entirely (used by no-damage control spells like Hold Person, where
    `damage_type` below is None)."""
    damage_dice_count: int
    damage_dice_sides: int
    damage_bonus: int
    damage_type: str | None
    """None for a no-damage save spell (e.g. Hold Person, which only
    restrains on a failed save - no HP loss at all). Phase 9D resolves the
    save roll itself for these but doesn't apply the spell's actual
    condition on a failure - no verb/spell grants restrained/paralyzed/etc.
    yet for that to hook into (same documented boundary as
    Character.concentrating_on not modeling an ongoing effect to remove)."""
    source_name: str


def _spell_save_params(
    actor: Character, spell: SrdEntry, spell_level: int, srd: SrdIndex
) -> SaveSpellParams:
    """Save-based spell parameters (e.g. Fireball, Hold Person). `spell_dc_
    info` (not a raw `spell["dc"]` read) so a spell in rules._DC_OVERRIDES
    (issue #55 - e.g. Call Lightning, whose vendored entry omits `dc`
    despite being a real DEX-save spell) resolves identically to one with
    the field natively present."""
    dc_info = spell_dc_info(spell)
    dc_ability: AbilityScore = dc_info["dc_type"]["index"].upper()
    _, ability_mod = _spellcasting_ability_mod(actor, srd)
    # Spell save DC = 8 + proficiency bonus + spellcasting ability modifier -
    # a fixed PHB rule (confirmed against the SRD Wizard class's own
    # spellcasting description text), not a vendored data field like a
    # weapon's or monster action's numbers are.
    dc = 8 + actor.proficiency_bonus + ability_mod

    damage_info = spell.get("damage")
    if damage_info:
        notation = (
            damage_info["damage_at_character_level"]["1"]
            if spell_level == 0
            else damage_info["damage_at_slot_level"][str(spell_level)]
        )
        dice_count, dice_sides, notation_bonus = parse_dice_notation(notation)
        damage_type: str | None = damage_info["damage_type"]["index"]
    else:
        dice_count = dice_sides = notation_bonus = 0
        damage_type = None

    return SaveSpellParams(
        dc=dc,
        dc_ability=dc_ability,
        dc_success=dc_info["dc_success"],
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=notation_bonus,
        damage_type=damage_type,
        source_name=spell["name"],
    )


def _cast_save_spell_at_target(
    state: GameState,
    actor: Character,
    target: Character,
    params: SaveSpellParams,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """One target's independent saving throw (Phase 9D multi-target - e.g.
    Fireball hitting 3 targets rolls 3 separate saves)."""
    # Bless (issue #57) - see _resolve_single_attack's identical handling.
    bless = blessed_bonus(target, rng)
    save_bonus = _target_saving_throw_bonus(target, params.dc_ability, srd) + bless
    result, success = resolve_saving_throw(
        save_bonus=save_bonus,
        dc=params.dc,
        rng=rng,
        # Phase 9A's condition_save_disadvantage (exhaustion 3+) had no real
        # call site until now - the only saving throw previously rolled
        # (death saves) is deliberately flat/unmodified per SRD.
        disadvantage=condition_save_disadvantage(target),
        # Lucky (issue #23) applies to any saving throw the target makes,
        # including one forced on them by an enemy's spell - not just their
        # own actions.
        lucky=has_lucky_trait(target),
    )
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="saving_throw",
            payload={
                "kind": "spell_save",
                "spell": params.source_name,
                "target": target.id,
                "ability": params.dc_ability,
                "dc": params.dc,
                "roll_total": result.total,
                "natural": result.kept[0],
                "success": success,
                "modifier_breakdown": [
                    *_target_saving_throw_breakdown(target, params.dc_ability, srd),
                    *([("blessed (1d4)", bless)] if bless else []),
                ],
            },
        )
    )

    if params.damage_type is None:
        return  # no-damage control spell - Phase 9D only resolves the save

    damage_roll = roll(
        params.damage_dice_count, params.damage_dice_sides, modifier=params.damage_bonus, rng=rng
    )
    damage = max(0, damage_roll.total)
    if success:
        if params.dc_success == "none":
            return
        damage //= 2  # SRD: half damage on a successful save, rounded down

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={
                "spell": params.source_name,
                "target": target.id,
                "save_success": success,
                "damage": damage,
            },
        )
    )
    _apply_damage_and_handle_downing(state, actor, target, damage, params.damage_type, rng, srd)


_HEAL_NOTATION_RE = re.compile(r"(\d+)d(\d+)\s*\+\s*MOD", re.IGNORECASE)


@dataclass(frozen=True)
class HealSpellParams:
    dice_count: int
    dice_sides: int
    ability_mod: int
    source_name: str


def _spell_heal_params(
    actor: Character, spell: SrdEntry, spell_level: int, srd: SrdIndex
) -> HealSpellParams:
    """Heal spell parameters (Cure Wounds and its kin). Unlike Day 14's
    healing potion - which needed a hardcoded HEALING_POTION_DICE constant
    because the vendored Magic Items data has no mechanical fields at all -
    the SRD's `heal_at_slot_level` field really is machine-readable: each
    slot level maps to a dice notation like "1d8 + MOD" (confirmed against
    the real vendored Cure Wounds entry via load_srd()). "MOD" is literal
    text meaning "the caster's spellcasting ability modifier" - parsed here
    rather than via parse_dice_notation, whose regex only understands
    numeric +N bonuses, not that word."""
    heal_table = spell["heal_at_slot_level"]
    notation = heal_table[str(spell_level)]
    match = _HEAL_NOTATION_RE.fullmatch(notation.strip())
    if not match:
        raise TurnEngineError(f"Unrecognized heal notation for {spell['name']!r}: {notation!r}")
    _, ability_mod = _spellcasting_ability_mod(actor, srd)
    return HealSpellParams(
        dice_count=int(match.group(1)),
        dice_sides=int(match.group(2)),
        ability_mod=ability_mod,
        source_name=spell["name"],
    )


def _cast_heal_spell_at_target(
    state: GameState,
    actor: Character,
    target: Character,
    params: HealSpellParams,
    rng: random.Random,
) -> None:
    """One target's heal - no roll to hit, just dice for the amount (Day
    14's use_item healing potion is the existing precedent for this
    clamp-at-max_hp shape)."""
    healed = min(
        roll(params.dice_count, params.dice_sides, modifier=params.ability_mod, rng=rng).total,
        target.max_hp - target.hp,
    )
    target.hp += healed
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="hp_change",
            payload={
                "amount": healed,
                "source": params.source_name,
                "target": target.id,
                "hp_remaining": target.hp,
            },
        )
    )


_SPECIAL_CAST_SPELLS = {"spare-the-dying", "sleep", "true-strike", "mage-armor", "shield-of-faith"}
"""Issue #55 spell audit: spells whose real mechanic doesn't fit any of the
generic attack/save/heal/auto_hit/condition buckets `spell_mechanic`
classifies (an HP-pool targeting rule, a banked-advantage buff, a flat AC
buff, a no-roll stabilize) - each resolved by its own dedicated function,
dispatched by name inside _resolve_cast_spell's `mechanic is None` branch
rather than forced into a shape that doesn't fit."""


def _resolve_spare_the_dying(
    state: GameState, actor: Character, action: ParsedAction, spell: SrdEntry, srd: SrdIndex
) -> None:
    """Spare the Dying (issue #55 spell audit): mechanically identical to
    the existing "stabilize" verb's *effect* (a dying creature becomes
    stable) but with none of its roll - real SRD has no check at all here,
    it just works on a touch. Bucket 1 of the audit's own finding: this
    didn't need a new mechanic, just a new entry point into logic that
    already exists (target.is_stable = True, the same field 3 death-save
    successes or a successful stabilize check already set)."""
    if action.target is None:
        raise TurnEngineError("Spare the Dying requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown spell target: {action.target}")
    if target.is_dead or target.is_stable or not has_condition(target, "unconscious"):
        raise TurnEngineError(
            f"{target.id} is not a valid Spare the Dying target - must be unconscious, "
            "not already stable, and not dead"
        )
    distance = distance_feet(actor.position, target.position)
    range_normal_feet = spell_range_feet(spell)
    if distance > range_normal_feet:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {spell['name']} "
            f"(max {range_normal_feet}ft)"
        )
    target.is_stable = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=target.id,
            type="condition_applied",
            payload={"condition": "stable"},
        )
    )


_SLEEP_UNCONSCIOUS_SOURCE = "sleep"
"""Distinct source tag (issue #55) so _apply_damage_and_handle_downing can
tell a Sleep-induced unconsciousness apart from the ordinary "0 HP" one
(which must never be removed just because the character took more
damage - dropping further below 0 doesn't wake anyone up) and wake the
target the instant it takes any damage, per SRD."""


def _resolve_sleep_spell(
    state: GameState,
    actor: Character,
    action: ParsedAction,
    spell: SrdEntry,
    spell_level: int,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """Sleep (issue #55 spell audit): structurally unlike every other spell
    mechanic this engine resolves - no attack roll, no saving throw. Real
    SRD: roll 5d8 as a shared "hit point pool," creatures within 20ft of a
    chosen point fall unconscious in ascending order of their *current* HP,
    each one's HP subtracted from the pool, until it runs out; undead and
    charm-immune creatures are unaffected.

    This engine has no point/AOE targeting concept (every other spell
    targets specific character ids), so `action.targets` stands in for
    "creatures within range of the chosen point" - a documented adaptation,
    not the literal SRD area-of-effect. Undead/charm-immune targets in the
    list are silently skipped (not rejected outright - a real caster
    naming a mixed group of enemies shouldn't have the whole spell fail
    over one immune creature, matching how a real DM would just narrate
    "the skeleton is unaffected").

    Duration 1 minute -> 10 rounds. Unlike an ordinary 0-HP unconscious
    (which never lifts on its own), this specifically ends the instant the
    sleeper takes ANY damage - see _apply_damage_and_handle_downing's own
    check for _SLEEP_UNCONSCIOUS_SOURCE.

    Found live: "I cast sleep on the goblins" reliably set only the
    singular `target` field, not `targets`, even with several goblins
    visible - the same single-vs-list ambiguity every other multi-target
    spell already falls back for (see the generic target_ids computation
    in _resolve_cast_spell), so this does the same rather than rejecting a
    perfectly reasonable-sounding cast."""
    target_ids = action.targets or ([action.target] if action.target else None)
    if not target_ids:
        raise TurnEngineError("Sleep requires at least one target (creatures within its area)")

    range_normal_feet = spell_range_feet(spell)
    candidates: list[Character] = []
    for target_id in target_ids:
        target = state.characters.get(target_id)
        if target is None:
            raise TurnEngineError(f"Unknown spell target: {target_id}")
        _validate_attack_target(actor, target)
        distance = distance_feet(actor.position, target.position)
        if distance > range_normal_feet:
            raise TurnEngineError(
                f"{target.id} is {distance}ft away - out of range for {spell['name']} "
                f"(max {range_normal_feet}ft)"
            )
        candidates.append(target)

    pool = roll(5, 8, rng=rng).total
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={"spell": spell["name"], "hp_pool": pool},
        )
    )

    # Undead/charm-immune creatures are unaffected - checked directly
    # against the vendored monster `type` field (not
    # monster_is_undead_or_fiend, which also exempts fiends - Sleep's own
    # SRD text only exempts undead) and the existing condition-immunity
    # helper for charm.
    eligible = [
        c
        for c in candidates
        if not c.is_dead
        and not (
            c.monster_index is not None
            and srd.monsters.get(c.monster_index, {}).get("type") == "undead"
        )
        and not monster_is_immune_to_condition(c, "charmed", srd)
    ]
    eligible.sort(key=lambda c: c.hp)

    remaining = pool
    for target in eligible:
        if remaining <= 0:
            break
        if target.hp > remaining:
            continue
        remaining -= target.hp
        apply_condition(
            target,
            Condition(name="unconscious", duration_rounds=10, source=_SLEEP_UNCONSCIOUS_SOURCE),
        )
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=target.id,
                type="condition_applied",
                payload={"condition": "unconscious", "source": "sleep"},
            )
        )


def _resolve_true_strike(state: GameState, actor: Character, spell: SrdEntry) -> None:
    """True Strike (issue #55 spell audit): real SRD grants advantage on
    your next attack roll *against the specific target you pointed at*,
    before the end of your next turn. Narrowed here to "your very next
    attack roll, whoever it's against" - the same class of documented
    simplification Bardic Inspiration's own die already makes (no
    per-target tracking, just a banked flag cleared on next use) - not
    tracking a specific target id keeps this consistent with
    has_help_advantage's existing "banked, one-shot" shape rather than
    inventing a second, narrower kind of banked bonus."""
    actor.true_strike_advantage = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={"spell": spell["name"], "effect": "advantage on next attack roll"},
        )
    )


def _resolve_ac_buff_spell(
    state: GameState, actor: Character, action: ParsedAction, spell: SrdEntry, srd: SrdIndex
) -> None:
    """Mage Armor / Shield of Faith (issue #55 spell audit): both reduce to
    "set a flag/bonus on the target, recompute their AC" - Mage Armor
    (self/touch only in practice, but real SRD range is Touch, not
    Self - modeled as any target within touch range) flips
    Character.mage_armor_active; Shield of Faith adds a flat +2 to
    Character.temporary_ac_bonus. Both read by rules.armor_ac_breakdown,
    recomputed here via _recompute_ac the same way equipping gear already
    triggers a recompute - no separate "AC is stale" bug class to
    introduce.

    Found live: "I cast mage armor on myself" left `target` unset entirely
    (the model apparently treats a reflexive "myself" as needing no
    explicit target the way an unambiguous ally name would) - both spells
    are overwhelmingly self-cast in practice, so a missing target falls
    back to the caster rather than rejecting a perfectly reasonable
    self-buff."""
    target_id = action.target or actor.id
    target = state.characters.get(target_id)
    if target is None:
        raise TurnEngineError(f"Unknown spell target: {target_id}")
    distance = distance_feet(actor.position, target.position)
    range_normal_feet = spell_range_feet(spell)
    if distance > range_normal_feet:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {spell['name']} "
            f"(max {range_normal_feet}ft)"
        )

    if spell["index"] == "mage-armor":
        target.mage_armor_active = True
    else:  # shield-of-faith
        target.temporary_ac_bonus = 2
    _recompute_ac(target, srd)

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={"spell": spell["name"], "target": target.id, "target_ac": target.ac},
        )
    )


@dataclass(frozen=True)
class AutoHitSpellParams:
    """A no-roll, always-hits spell effect (issue #35) - genuinely
    different from the attack/save/heal mechanics: there's no d20 roll of
    any kind, just a fixed per-instance damage roll applied directly.
    Magic Missile is the only vendored spell this project treats this way
    - see spell_mechanic's own docstring for why this is an explicit
    per-spell allowlist rather than an inferred "has damage but no
    attack_type/dc" bucket (several other SRD spells share that same
    field shape - scorching-ray, call-lightning, flaming-sphere - but are
    real attack-roll/save spells whose vendored SRD entry simply lacks
    the attack_type/dc field, not genuine no-roll effects; auto-including
    them would silently make them always hit instead of correctly staying
    unsupported)."""

    dice_count: int
    dice_sides: int
    damage_bonus: int
    damage_type: str
    source_name: str


def _auto_hit_spell_params(spell: SrdEntry) -> AutoHitSpellParams:
    """Magic Missile's one dart: 1d4 + 1 force damage, fixed regardless of
    slot level - the level scaling is entirely in *how many* darts are
    cast (magic_missile_dart_count), not in each dart's own size."""
    return AutoHitSpellParams(
        dice_count=1,
        dice_sides=4,
        damage_bonus=1,
        damage_type="force",
        source_name=spell["name"],
    )


def _cast_auto_hit_dart_at_target(
    state: GameState,
    actor: Character,
    target: Character,
    params: AutoHitSpellParams,
    rng: random.Random,
    srd: SrdIndex,
) -> None:
    """One dart's independent, unavoidable hit - no roll of any kind, per
    SRD ("each dart hits a creature of your choice... automatically").
    Called once per dart (see _resolve_cast_spell's auto_hit branch,
    which expands the target list to one entry per dart before this is
    ever reached), so a target hit by 2 darts gets 2 separate events/
    damage applications, matching how a real Magic Missile cast against
    one creature deals its damage as discrete dart hits, not one lump
    sum - consistent with _apply_damage_and_handle_downing's downing/
    concentration-break/Relentless-Endurance handling being correct to
    run once per dart rather than once per cast."""
    damage = max(
        0, roll(params.dice_count, params.dice_sides, modifier=params.damage_bonus, rng=rng).total
    )
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="spell_cast",
            payload={
                "spell": params.source_name,
                "target": target.id,
                "damage": damage,
                "auto_hit": True,
            },
        )
    )
    _apply_damage_and_handle_downing(state, actor, target, damage, params.damage_type, rng, srd)


def _is_bonus_action_spell(spell: SrdEntry) -> bool:
    """Phase 9H: SRD's `casting_time` is a plain string ("1 action", "1
    bonus action", "1 reaction", "1 minute", ...) - Healing Word is the
    project-relevant example. Reactions ("1 reaction", e.g. Shield) aren't
    modeled as castable at all yet - only the action-vs-bonus-action
    distinction matters for turn-advancement purposes here."""
    return str(spell.get("casting_time", "")) == "1 bonus action"


def _monster_innate_attack_params(
    innate: SrdEntry, spell: SrdEntry, spell_level: int, range_normal_feet: int
) -> AttackParams:
    """AttackParams for a monster's Innate Spellcasting attack-roll spell
    (issue #22) - mirrors _spell_attack_params's shape/level-lookup exactly,
    but the attack bonus is the monster's own precomputed `modifier` field
    (rules.monster_innate_spellcasting) rather than an ability score +
    proficiency bonus derivation, since a monster has no class_index for
    that PC-only formula to use."""
    attack_bonus = innate.get("modifier")
    if attack_bonus is None:
        raise TurnEngineError(f"{spell['name']} has no spell attack modifier in this stat block")
    damage_info = spell.get("damage")
    if not damage_info:
        raise TurnEngineError(f"{spell['name']} isn't supported - no damage data to resolve")
    notation = (
        damage_info["damage_at_character_level"]["1"]
        if spell_level == 0
        else damage_info["damage_at_slot_level"][str(spell_level)]
    )
    dice_count, dice_sides, notation_bonus = parse_dice_notation(notation)
    return AttackParams(
        attack_bonus=attack_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=notation_bonus,
        damage_type=damage_info["damage_type"]["index"],
        source_name=spell["name"],
        range_normal_feet=range_normal_feet,
        range_long_feet=None,
        attack_bonus_breakdown=[("attack bonus (stat block)", attack_bonus)],
    )


def _monster_innate_save_params(
    innate: SrdEntry, spell: SrdEntry, spell_level: int
) -> SaveSpellParams:
    """SaveSpellParams for a monster's Innate Spellcasting save-based spell
    (issue #22) - mirrors _spell_save_params's shape/level-lookup exactly,
    but the DC is the monster's own precomputed `dc` field rather than
    8 + proficiency bonus + ability modifier, for the same reason
    _monster_innate_attack_params's attack_bonus is."""
    dc_info = spell["dc"]
    damage_info = spell.get("damage")
    if damage_info:
        notation = (
            damage_info["damage_at_character_level"]["1"]
            if spell_level == 0
            else damage_info["damage_at_slot_level"][str(spell_level)]
        )
        dice_count, dice_sides, notation_bonus = parse_dice_notation(notation)
        damage_type: str | None = damage_info["damage_type"]["index"]
    else:
        dice_count = dice_sides = notation_bonus = 0
        damage_type = None
    return SaveSpellParams(
        dc=innate["dc"],
        dc_ability=dc_info["dc_type"]["index"].upper(),
        dc_success=dc_info["dc_success"],
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=notation_bonus,
        damage_type=damage_type,
        source_name=spell["name"],
    )


def _resolve_monster_innate_spell(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """A monster's Innate Spellcasting entry point for the `cast_spell` verb
    (issue #22, "start narrow" scope: Innate Spellcasting only, not a full
    slot-tracked prepared caster like Cult Fanatic - see
    rules.monster_innate_spellcasting). Reuses the exact same
    _cast_attack_spell_at_target/_cast_save_spell_at_target a PC's cast
    already resolves through - only how the params are built differs.
    Always ends the turn (True) - every CR<=5 Innate Spellcasting monster in
    this project's curated roster casts as its Action, never a bonus
    action, so there's no PC-style ends_turn=False case to handle here."""
    if actor.monster_index is None:
        raise TurnEngineError(f"{actor.id} is not a monster (no monster_index)")
    if not action.item_or_spell:
        raise TurnEngineError("cast_spell action requires item_or_spell (the spell name)")
    monster_data = srd.monsters.get(actor.monster_index)
    innate = monster_innate_spellcasting(monster_data) if monster_data else None
    if innate is None:
        raise TurnEngineError(f"{actor.id} has no Innate Spellcasting")

    normalized = normalize_spell_name(action.item_or_spell)
    spell_ref = next(
        (s for s in innate.get("spells", []) if normalize_spell_name(s["name"]) == normalized),
        None,
    )
    if spell_ref is None:
        raise TurnEngineError(
            f"{actor.id} doesn't know an innate spell named {action.item_or_spell!r}"
        )

    spell = srd.spells.get(normalized)
    if spell is None:
        raise TurnEngineError(f"Unknown spell: {spell_ref['name']!r}")
    mechanic = spell_mechanic(spell)
    if mechanic not in ("attack", "save"):
        raise TurnEngineError(
            f"{spell_ref['name']} isn't an attack-roll or save-based spell - monster Innate "
            "Spellcasting only resolves those (issue #22's scope)"
        )

    if action.target is None:
        raise TurnEngineError("cast_spell action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown spell target: {action.target}")
    _validate_attack_target(actor, target)

    range_normal_feet = spell_range_feet(spell)
    distance = distance_feet(actor.position, target.position)
    if distance > range_normal_feet:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {spell['name']} "
            f"(max {range_normal_feet}ft)"
        )

    usage = spell_ref.get("usage", {})
    if usage.get("type") == "per day":
        remaining = actor.innate_spell_uses_remaining.get(normalized, 0)
        if remaining <= 0:
            raise TurnEngineError(f"{actor.id} has no uses of {spell_ref['name']} remaining today")
        actor.innate_spell_uses_remaining[normalized] = remaining - 1

    spell_level = spell_ref["level"]
    if mechanic == "attack":
        attack_params = _monster_innate_attack_params(innate, spell, spell_level, range_normal_feet)
        _cast_attack_spell_at_target(state, actor, target, attack_params, spell_level, rng, srd)
    else:
        save_params = _monster_innate_save_params(innate, spell, spell_level)
        _cast_save_spell_at_target(state, actor, target, save_params, rng, srd)

    return True


def _resolve_cast_spell(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> bool:
    """Returns whether this action ends the actor's turn - True for an
    ordinary (action) spell, False for a bonus-action spell that resolved
    successfully (the actor still has their main action left this turn)."""
    if actor.monster_index is not None:
        return _resolve_monster_innate_spell(state, actor, action, rng, srd)
    if not action.item_or_spell:
        raise TurnEngineError("cast_spell action requires item_or_spell (the spell name)")
    normalized = normalize_spell_name(action.item_or_spell)
    spell = srd.spells.get(normalized)
    if spell is None:
        raise TurnEngineError(f"Unknown spell: {action.item_or_spell!r}")

    # "Spells Known" restriction (issue #30) - Bard/Sorcerer may only cast a
    # level-1+ spell they actually know. Cantrips (level 0) stay unrestricted
    # - out of this issue's scope, matches ClassDetail.cantrips already
    # being an unconditional "every cantrip this class can access" list.
    if (
        spell.get("level", 0) > 0
        and actor.class_index in SPELLS_KNOWN_BY_LEVEL
        and normalized not in actor.known_spells
    ):
        raise TurnEngineError(f"{actor.id} doesn't know {spell['name']}")

    # "Prepared" restriction (issue #30's follow-up phase) - Cleric/Druid/
    # Wizard/Paladin may only cast a level-1+ spell currently in their
    # prepared_spells, even though their *access* spans the whole class
    # list (class_spell_indices, used to build the picker/validate a
    # choice) - the two are deliberately different sets, matching real SRD.
    if (
        spell.get("level", 0) > 0
        and actor.class_index in PREPARED_CASTER_CLASSES
        and normalized not in actor.prepared_spells
    ):
        raise TurnEngineError(f"{actor.id} hasn't prepared {spell['name']}")

    is_bonus_action = _is_bonus_action_spell(spell)
    if is_bonus_action and actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - "
            f"cannot also cast {spell['name']}"
        )

    mechanic = spell_mechanic(spell)
    spell_level = spell["level"]

    if mechanic is None:
        # Issue #55 spell audit, buckets 1/3/5: a small set of spells whose
        # real mechanic doesn't fit any of the generic attack/save/heal/
        # auto_hit/condition shapes above (Sleep's HP-pool targeting, a
        # banked-advantage buff, a flat AC buff) - each gets its own
        # resolver instead. Slot consumption/concentration still apply
        # generically (every real spell needs both, regardless of which
        # mechanic resolves the actual effect), so those two steps happen
        # here too rather than being duplicated inside each resolver.
        if normalized in _SPECIAL_CAST_SPELLS:
            if spell_level > 0:
                remaining = actor.spell_slots.get(spell_level, 0)
                if remaining <= 0:
                    raise TurnEngineError(
                        f"{actor.id} has no level-{spell_level} spell slots remaining"
                    )
                actor.spell_slots[spell_level] = remaining - 1
            if spell.get("concentration"):
                actor.concentrating_on = spell["name"]

            if normalized == "spare-the-dying":
                _resolve_spare_the_dying(state, actor, action, spell, srd)
            elif normalized == "sleep":
                _resolve_sleep_spell(state, actor, action, spell, spell_level, rng, srd)
            elif normalized == "true-strike":
                _resolve_true_strike(state, actor, spell)
            else:  # mage-armor, shield-of-faith
                _resolve_ac_buff_spell(state, actor, action, spell, srd)

            if is_bonus_action:
                actor.bonus_action_used = True
                return False
            return True

        raise TurnEngineError(
            f"{spell['name']} is not supported - cast_spell resolves attack-roll, save-based, "
            "heal, auto-hit, and condition spells, plus a small set of individually-"
            "implemented ones (Sleep, True Strike, Mage Armor, Shield of Faith, Spare the "
            "Dying); other no-roll/no-damage effects (most buffs/utility) aren't implemented"
        )

    if mechanic == "auto_hit":
        # Issue #35 (Magic Missile): darts, not independently-rolled
        # targets - action.targets (if given) is one entry per dart
        # (repeat an id to send multiple darts at the same creature),
        # capped at how many darts this slot level actually creates. A
        # bare action.target (no explicit list) sends every available
        # dart at that one target, matching SRD's "you can direct them to
        # hit one creature or several" default of "all darts, one
        # creature" when the caster doesn't split them up.
        dart_count = magic_missile_dart_count(spell_level)
        if action.targets:
            if len(action.targets) > dart_count:
                raise TurnEngineError(
                    f"{spell['name']} only creates {dart_count} dart(s) at this slot level - "
                    f"named {len(action.targets)} targets"
                )
            target_ids: list[str] | None = action.targets
        elif action.target:
            target_ids = [action.target] * dart_count
        else:
            target_ids = None
    else:
        # Multi-target (Phase 9D): action.targets (a list of character
        # ids) takes priority when present; falling back to a single-
        # element [action.target] list keeps every pre-existing single-
        # target action (and test) resolving identically to before.
        target_ids = (
            action.targets if action.targets else ([action.target] if action.target else None)
        )
    if not target_ids:
        raise TurnEngineError("cast_spell action requires a target")

    targets: list[Character] = []
    for target_id in target_ids:
        target = state.characters.get(target_id)
        if target is None:
            raise TurnEngineError(f"Unknown spell target: {target_id}")
        # A heal or condition (issue #55 - e.g. Invisibility) spell targets
        # an ally by design - only the offensive mechanics need the
        # friendly-fire/charmed guard. Condition spells get the opposite-
        # polarity check instead: a beneficial buff on an unwilling enemy
        # makes no real-world sense (real SRD's own "willing creature"
        # targeting), so same-side is required, not rejected.
        if mechanic == "condition" and target.is_pc != actor.is_pc:
            raise TurnEngineError(
                f"{actor.id} cannot cast {spell['name']} on {target.id} - not an ally"
            )
        if mechanic not in ("heal", "condition"):
            _validate_attack_target(actor, target)
        targets.append(target)

    range_normal_feet = spell_range_feet(spell)
    for target in targets:
        distance = distance_feet(actor.position, target.position)
        if distance > range_normal_feet:
            raise TurnEngineError(
                f"{target.id} is {distance}ft away - out of range for {spell['name']} "
                f"(max {range_normal_feet}ft)"
            )

    if spell_level > 0:
        remaining = actor.spell_slots.get(spell_level, 0)
        if remaining <= 0:
            raise TurnEngineError(f"{actor.id} has no level-{spell_level} spell slots remaining")
        actor.spell_slots[spell_level] = remaining - 1

    # Concentration (Phase 9D): starting a new concentration spell always
    # drops whatever the caster was concentrating on before, per SRD - plain
    # reassignment does that for free. A spell without `concentration`
    # (every attack-roll spell in the SRD, and some save/heal ones) leaves
    # any prior concentration untouched.
    if spell.get("concentration"):
        actor.concentrating_on = spell["name"]

    if mechanic == "attack":
        attack_params = _spell_attack_params(actor, spell, spell_level, srd)
        for target in targets:
            _cast_attack_spell_at_target(state, actor, target, attack_params, spell_level, rng, srd)
    elif mechanic == "save":
        save_params = _spell_save_params(actor, spell, spell_level, srd)
        for target in targets:
            _cast_save_spell_at_target(state, actor, target, save_params, rng, srd)
    elif mechanic == "auto_hit":
        # `targets` already has one entry per dart (expanded above), not
        # one entry per independently-chosen target - so this loop, unlike
        # attack/save/heal's, resolves dart_count times even for a single
        # named target.
        auto_hit_params = _auto_hit_spell_params(spell)
        for target in targets:
            _cast_auto_hit_dart_at_target(state, actor, target, auto_hit_params, rng, srd)
    elif mechanic == "condition":
        # Issue #55 spell audit: e.g. Invisibility - no roll, apply the
        # spec'd ConditionName to every named (willing) target.
        condition_spec = condition_spell_spec(spell)
        for target in targets:
            apply_condition(
                target,
                Condition(
                    name=condition_spec.condition,
                    duration_rounds=condition_spec.duration_rounds,
                    source=actor.id,
                ),
            )
            state.events.append(
                Event(
                    round=state.round,
                    turn_index=state.current_turn,
                    actor=target.id,
                    type="condition_applied",
                    payload={"condition": condition_spec.condition, "source": spell["name"]},
                )
            )
    else:  # heal
        heal_params = _spell_heal_params(actor, spell, spell_level, srd)
        for target in targets:
            _cast_heal_spell_at_target(state, actor, target, heal_params, rng)

    if is_bonus_action:
        actor.bonus_action_used = True
        return False
    return True


def _is_healing_potion(item_name: str) -> bool:
    """Word-set match, not exact-string: a free-text parser is just as
    likely to produce "healing potion" (natural adjective-noun order) as
    the canonical "potion of healing" (SRD item-name order) - confirmed
    live, qwen2.5:7b-instruct said the former. Exact-matching against
    HEALING_POTION_INDEX rejected a perfectly good request."""
    words = set(item_name.strip().lower().replace("-", " ").split())
    return {"healing", "potion"} <= words


def _resolve_use_item(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random
) -> None:
    item_name = action.item_or_spell
    if not item_name:
        raise TurnEngineError("use_item action requires item_or_spell (the item name)")
    if not _is_healing_potion(item_name):
        raise TurnEngineError(
            f"Don't know how to use {item_name!r} - only a healing potion "
            "is supported (Day 14 scope)"
        )
    if HEALING_POTION_INDEX not in actor.inventory:
        raise TurnEngineError(f"{actor.id} has no {HEALING_POTION_INDEX} to use")

    actor.inventory.remove(HEALING_POTION_INDEX)
    dice_count, dice_sides, bonus = parse_dice_notation(HEALING_POTION_DICE)
    healed = min(
        roll(dice_count, dice_sides, modifier=bonus, rng=rng).total, actor.max_hp - actor.hp
    )
    actor.hp += healed

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="hp_change",
            payload={"amount": healed, "source": HEALING_POTION_INDEX, "hp_remaining": actor.hp},
        )
    )


def _check_death_save_failure_threshold(state: GameState, actor: Character) -> None:
    """3 total death_save_failures kills, per SRD - factored out so
    _resolve_death_save's own 3-failures case (a real roll, or a natural 1's
    2 automatic ones) and _apply_unconscious_hit_death_save_failures'
    (Phase 9C: 2 automatic failures from a hit while already unconscious)
    can't drift apart on this one threshold. Only handles the failure side -
    the 3-successes-stabilizes case has no equivalent outside a real death
    save roll, so it stays in _resolve_death_save."""
    if actor.death_save_failures >= 3:
        actor.is_dead = True
        remove_condition(actor, "unconscious")
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=actor.id,
                type="death",
                payload={"cause": "failed death saves"},
            )
        )


def _resolve_death_save(state: GameState, actor: Character, rng: random.Random) -> None:
    if not has_condition(actor, "unconscious"):
        raise TurnEngineError(f"{actor.id} is not unconscious - no death save needed")
    if actor.is_stable:
        raise TurnEngineError(f"{actor.id} is already stable - no death save needed")

    # Flat d20, no modifiers, no advantage/disadvantage support - per SRD.
    # Lucky (issue #23) still applies - a death save is a saving throw.
    result, _ = resolve_saving_throw(save_bonus=0, dc=10, rng=rng, lucky=has_lucky_trait(actor))
    natural = result.kept[0]

    if natural == 20:
        remove_condition(actor, "unconscious")
        actor.hp = 1
        actor.death_save_successes = 0
        actor.death_save_failures = 0
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=actor.id,
                type="hp_change",
                payload={"amount": 1, "source": "natural 20 death save", "hp_remaining": 1},
            )
        )
        return

    if natural == 1:
        actor.death_save_failures += 2
    elif natural >= 10:
        actor.death_save_successes += 1
    else:
        actor.death_save_failures += 1

    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="saving_throw",
            payload={
                "kind": "death_save",
                "natural": natural,
                "success": natural >= 10,
                "successes": actor.death_save_successes,
                "failures": actor.death_save_failures,
            },
        )
    )

    _check_death_save_failure_threshold(state, actor)
    if not actor.is_dead and actor.death_save_successes >= 3:
        actor.is_stable = True
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=actor.id,
                type="condition_applied",
                payload={"condition": "stable"},
            )
        )


def _resolve_stabilize(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    """New Phase 9C verb: a DC 10 Medicine check (SRD "stabilizing a
    creature"), rolled with the ACTOR's own stats/proficiency, not the dying
    target's - the target rolls nothing, matching how a real death save's 3
    successes already sets `is_stable` without any further roll needed. Lets
    an ally proactively stop a downed party member's death-save clock
    instead of just hoping their own rolls go well."""
    if action.target is None:
        raise TurnEngineError("stabilize action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown stabilize target: {action.target}")
    if target.is_dead or target.is_stable or not has_condition(target, "unconscious"):
        raise TurnEngineError(
            f"{target.id} is not a valid stabilize target - must be unconscious, "
            "not already stable, and not dead"
        )

    skill = "medicine"
    ability = skill_ability(skill, srd)  # WIS, per SRD
    proficient = f"skill-{normalize_skill_name(skill)}" in actor.skill_proficiencies
    modifier = ability_check_modifier(actor, ability, proficient=proficient)

    advantage = actor.has_help_advantage
    actor.has_help_advantage = False
    disadvantage = condition_check_disadvantage(actor)

    result, success = resolve_skill_check(
        modifier=modifier,
        dc=10,
        rng=rng,
        advantage=advantage,
        disadvantage=disadvantage,
        lucky=has_lucky_trait(actor),
    )
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="skill_check",
            payload={
                "skill": skill,
                "ability": ability,
                "dc": 10,
                "roll_total": result.total,
                "natural": result.kept[0],
                "success": success,
                "target": target.id,
                "modifier_breakdown": _ability_check_breakdown(actor, ability, proficient),
            },
        )
    )
    if not success:
        return

    target.is_stable = True
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=target.id,
            type="condition_applied",
            payload={"condition": "stable"},
        )
    )


def _check_victory_defeat(state: GameState) -> None:
    # Day 14: keyed off is_dead, not hp<=0 - an unconscious-but-not-dead PC
    # (still making death saves, or already stable) is still "in the fight"
    # for defeat purposes. Monsters have no death-save subsystem and are
    # marked is_dead immediately at 0 HP, so this is equivalent to the old
    # hp>0 check for them.
    party_alive = any(not c.is_dead for c in state.characters.values() if c.is_pc)
    monsters_alive = any(not c.is_dead for c in state.characters.values() if not c.is_pc)
    if not monsters_alive:
        state.status = "victory"
    elif not party_alive:
        state.status = "defeat"


_INCAPACITATING_CONDITIONS: tuple[ConditionName, ...] = (
    "incapacitated",
    "paralyzed",
    "petrified",
    "stunned",
    "unconscious",
)


def _is_incapacitated(character: Character) -> bool:
    """True if the character has a condition that stops them from taking a
    normal action on their turn at all (Phase 9B - the "can this actor act"
    side of these conditions, as opposed to Phase 9A's "how well does their
    roll go" side already wired into _resolve_attack/_resolve_cast_spell/
    _resolve_skill_check/_resolve_move).

    "unconscious" is included here too, but doesn't need special-casing
    against the death-save flow: a PC at 0 HP is unconscious and
    resolve_action's own hp<=0 guard already either forces death_save
    through (verb == "death_save", which the caller of this helper excludes)
    or rejects any other verb outright before this helper is ever reached -
    so this function only ever gets to force an end_turn for an unconscious
    actor in the hypothetical case of unconsciousness from a source other
    than 0 HP (not reachable today - no verb/spell/monster action grants it
    - but handled correctly here for free, with no extra branch needed, since
    such an actor would have no death-save context to prompt for anyway)."""
    return any(has_condition(character, name) for name in _INCAPACITATING_CONDITIONS)


def _skip_this_turn(character: Character) -> bool:
    """Dead characters never act again. An unconscious-but-not-yet-stable
    character DOES need a turn (to attempt a death save) - only skip once
    dead or once stabilized (3 successes: no longer needs to roll, but
    still can't act while unconscious at 0 HP)."""
    return character.is_dead or (character.hp <= 0 and character.is_stable)


def _advance_turn_skipping_dead(state: GameState) -> None:
    """A character killed mid-round (e.g. on an earlier actor's turn) must
    not be prompted for its own turn later that same round - skip forward
    until landing on a combatant who still needs one. Guarded by
    len(turn_order) since _check_victory_defeat already ends combat before
    every combatant could ever be skippable simultaneously."""
    for _ in range(len(state.turn_order)):
        next_index, next_round = next_turn(state.turn_order, state.current_turn, state.round)
        if next_round != state.round:
            for character in state.characters.values():
                tick_conditions(character)
                # Phase 9H: a reaction (opportunity attacks, the only one
                # this engine models) is a per-round resource, not per-turn.
                character.reaction_used_this_round = False
        state.current_turn = next_index
        state.round = next_round
        next_actor = state.characters[state.turn_order[state.current_turn]]
        if not _skip_this_turn(next_actor):
            # Phase 9H: bonus_action_used resets here, when a turn actually
            # advances TO this character, rather than at the top of every
            # resolve_action call - a bonus-action spell doesn't advance the
            # turn (see resolve_action's ends_turn), so this same actor's
            # very next resolve_action call (their main action, still the
            # same real turn) must NOT see this reset again, or a second
            # bonus-action cast that same turn would be wrongly allowed.
            next_actor.bonus_action_used = False
            # UX affordance: action_used_this_turn resets on the identical
            # schedule, for the identical reason (a bonus-action verb this
            # same actor used earlier in this real turn must not un-flag
            # their now-spent main action).
            next_actor.action_used_this_turn = False
            # Phase C: equip_used_this_turn resets on the same "turn
            # actually advances TO this character" schedule, for the same
            # reason - equip doesn't end the turn either.
            next_actor.equip_used_this_turn = False
            # movement_used_feet resets on the same schedule too (found
            # live) - a plain move doesn't end the turn either, so a
            # follow-up attack in the same real turn must still see
            # whatever movement this character already spent this turn.
            next_actor.movement_used_feet = 0
            return


def resolve_action(
    state: GameState,
    action: ParsedAction,
    rng: random.Random,
    srd: SrdIndex | None = None,
) -> GameState:
    srd = srd or load_srd()

    if not state.turn_order:
        raise TurnEngineError("GameState has no turn order")
    expected_actor = state.turn_order[state.current_turn]
    if action.actor != expected_actor:
        raise TurnEngineError(f"It is {expected_actor}'s turn, not {action.actor}'s")
    actor = state.characters.get(action.actor)
    if actor is None:
        raise TurnEngineError(f"Unknown actor: {action.actor}")
    if actor.is_dead:
        raise TurnEngineError(f"{actor.id} is dead and cannot act")
    if actor.hp <= 0 and action.verb != "death_save":
        raise TurnEngineError(f"{actor.id} is unconscious and can only attempt a death save")

    # Phase 9B: an actor who is paralyzed/petrified/stunned/incapacitated/
    # unconscious can't act at all, but that's not a mistake on their part
    # (same philosophy as the "invalid" verb below) - so it's a forced
    # end_turn, not a raised error. death_save is excluded so the hp<=0
    # unconscious-PC flow above (and player_agent's forced death_save
    # declaration) keeps working exactly as before.
    if action.verb != "death_save" and _is_incapacitated(actor):
        action = action.model_copy(update={"verb": "end_turn"})

    # Dodging protects "until the start of your next turn" - that window
    # ends right now, since this actor's next turn is the one being
    # resolved. Cleared before dispatch so a fresh "dodge" this turn (which
    # re-sets it to True) isn't immediately undone. Safe to reset
    # unconditionally on every call, unlike bonus_action_used (Phase 9H,
    # reset in _advance_turn_skipping_dead instead - see its comment for
    # why this generic per-call reset would break it) - is_dodging being
    # cleared an extra time when a bonus-action spell precedes this actor's
    # own main action in the same turn is harmless, since it wasn't going
    # to read True again this turn anyway. sneak_attack_used_this_turn
    # (Phase 9I) resets the same safe way - a Rogue's plain `attack` never
    # produces more than one resolve_action call per real turn under this
    # engine (no Extra Attack, no two-weapon-fighting bonus-action offhand
    # attack), so there's no equivalent risk to bonus_action_used's.
    actor.is_dodging = False
    actor.sneak_attack_used_this_turn = False

    if action.verb == "invalid":
        # The DM didn't understand the action - not a system error. No
        # event advances the turn or gets a hp/status check: the actor
        # didn't actually do anything, so their turn isn't over and they
        # can just try again with different phrasing. (Previously this fell
        # all the way through to the same NotImplementedError raised for a
        # genuinely-unimplemented verb - a real UX rough edge noted in
        # CLAUDE.md on Day 12; fixed here since it's the same dispatch code.)
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=actor.id,
                type="action_invalid",
                payload={"raw_text": action.raw_text},
            )
        )
        return state

    # Phase 9H: every verb ends the turn except a bonus-action spell cast
    # (SRD casting_time "1 bonus action", e.g. Healing Word) that
    # successfully resolves - _resolve_cast_spell reports back whether it
    # was one via this flag, so the actor gets to act again (their main
    # action, or another bonus action attempt, which _resolve_cast_spell
    # itself rejects via actor.bonus_action_used). "move" is the other
    # exception (found live): real SRD gives every turn a movement budget
    # separate from the action, so moving alone must not cost the action -
    # "dash" stays turn-ending since Dash genuinely *is* the action (it
    # trades your action for extra movement, per SRD).
    ends_turn = True

    if action.verb == "attack":
        _resolve_attack(state, actor, action, rng, srd)
    elif action.verb == "move":
        _resolve_move(state, actor, action, rng, srd)
        # An opportunity attack triggered by leaving a threatened square can
        # kill or down the actor mid-move - they can't keep acting either
        # way, so the turn still has to advance past them (matches the old
        # unconditional-ends_turn behavior for exactly this case).
        ends_turn = actor.is_dead or actor.hp <= 0
    elif action.verb == "dash":
        _resolve_move(state, actor, action, rng, srd)
    elif action.verb == "skill_check":
        _resolve_skill_check(state, actor, action, rng, srd)
    elif action.verb == "dodge":
        _resolve_dodge(state, actor)
    elif action.verb == "disengage":
        _resolve_disengage(state, actor)
    elif action.verb == "help":
        _resolve_help(state, actor, action)
    elif action.verb == "grapple":
        _resolve_grapple(state, actor, action, rng, srd)
    elif action.verb == "shove":
        _resolve_shove(state, actor, action, rng, srd)
    elif action.verb == "cast_spell":
        ends_turn = _resolve_cast_spell(state, actor, action, rng, srd)
    elif action.verb == "second_wind":
        ends_turn = _resolve_second_wind(state, actor, rng)
    elif action.verb == "rage":
        ends_turn = _resolve_rage(state, actor, rng)
    elif action.verb == "equip":
        ends_turn = _resolve_equip(state, actor, action, srd)
    elif action.verb == "offhand_attack":
        ends_turn = _resolve_offhand_attack(state, actor, action, rng, srd)
    elif action.verb == "cunning_action":
        ends_turn = _resolve_cunning_action(state, actor, action, rng, srd)
    elif action.verb == "flurry_of_blows":
        ends_turn = _resolve_flurry_of_blows(state, actor, action, rng, srd)
    elif action.verb == "martial_arts_strike":
        ends_turn = _resolve_martial_arts_strike(state, actor, action, rng, srd)
    elif action.verb == "wild_shape":
        ends_turn = _resolve_wild_shape(state, actor, action, rng, srd)
    elif action.verb == "revert_wild_shape":
        ends_turn = _resolve_revert_wild_shape(state, actor)
    elif action.verb == "bardic_inspiration":
        ends_turn = _resolve_bardic_inspiration(state, actor, action)
    elif action.verb == "use_item":
        _resolve_use_item(state, actor, action, rng)
    elif action.verb == "death_save":
        _resolve_death_save(state, actor, rng)
    elif action.verb == "stabilize":
        _resolve_stabilize(state, actor, action, rng, srd)
    elif action.verb == "end_turn":
        pass
    else:
        raise NotImplementedError(f"Verb not yet supported by the turn engine: {action.verb}")

    # UX affordance: ends_turn already IS "did this verb consume the main
    # action" for every verb except "move" (which spends movement, not the
    # action, and already leaves ends_turn False for a plain move) - see
    # Character.action_used_this_turn's own docstring for why this doesn't
    # need a verb-by-verb special case.
    actor.action_used_this_turn = ends_turn if action.verb != "move" else False

    _check_victory_defeat(state)

    if state.status == "in_progress" and ends_turn:
        _advance_turn_skipping_dead(state)

    return state

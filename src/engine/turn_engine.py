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
from dataclasses import dataclass, replace

from src.engine.actions import ParsedAction
from src.engine.character_creation import is_eligible_for_extra_attack
from src.engine.conditions import apply_condition, has_condition, remove_condition, tick_conditions
from src.engine.dice import roll
from src.engine.events import Event
from src.engine.movement import can_afford_move, move_cost_feet
from src.engine.position import Position, distance_feet
from src.engine.rules import (
    ability_check_modifier,
    ability_modifier,
    apply_damage,
    armor_ac,
    condition_attack_advantage,
    condition_attack_disadvantage,
    condition_check_disadvantage,
    condition_save_disadvantage,
    effective_speed,
    has_non_proficient_armor,
    is_class_proficient_with,
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
    spell_mechanic,
    spell_range_feet,
    weapon_combo_is_legal,
    weapon_range_feet,
)
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import AbilityScore, Character, Condition, ConditionName, GameState
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
    rather than silently doing nothing on a miss-or-hit."""
    if smite_slot_level is not None:
        if actor.class_index != "paladin":
            raise TurnEngineError(f"{actor.id} is not a Paladin and cannot use Divine Smite")
        if actor.spell_slots.get(smite_slot_level, 0) <= 0:
            raise TurnEngineError(
                f"{actor.id} has no level {smite_slot_level} spell slots remaining for Divine Smite"
            )
    equipped = [item for idx in actor.equipped_weapons if (item := srd.equipment.get(idx))]
    weapon: SrdEntry | None = None
    if weapon_index:
        weapon = next((item for item in equipped if item["index"] == weapon_index), None)
        if weapon is None:
            weapon = _match_weapon_by_name(weapon_index, equipped)
        if weapon is None:
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
        )

    properties = {p["index"] for p in (weapon.get("properties") or [])}
    is_finesse = "finesse" in properties
    is_ranged = weapon.get("weapon_range") == "Ranged"
    if smite_slot_level is not None and is_ranged:
        raise TurnEngineError("Divine Smite requires a melee weapon attack")
    if is_finesse:
        ability_mod = max(str_mod, dex_mod)
    elif is_ranged:
        ability_mod = dex_mod
    else:
        ability_mod = str_mod
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

    dice_count, dice_sides, notation_bonus = parse_dice_notation(weapon["damage"]["damage_dice"])
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
        is_finesse_or_ranged=is_finesse or is_ranged,
        is_melee_str_weapon=is_melee_str_weapon,
        smite_slot_level=smite_slot_level,
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
) -> None:
    """One full attack roll (range check through hit/damage/downing) against
    `target` - the body every single `attack` action resolves, and what a
    Multiattack action (Phase 9F, see _resolve_multiattack) calls once per
    named sub-attack within the same turn."""
    # Caught live: a combat grid can show attacker and target several
    # squares apart while a melee attack still resolved as a hit - this
    # engine never checked range at all. Beyond the weapon/action's max
    # reach (long range if ranged, else normal) is rejected outright;
    # beyond normal but within long range (ranged only - melee has no such
    # tier) imposes disadvantage, per SRD.
    distance = distance_feet(actor.position, target.position)
    max_range = params.range_long_feet or params.range_normal_feet
    if distance > max_range:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {params.source_name} "
            f"(max {max_range}ft)"
        )
    long_range_disadvantage = (
        params.range_long_feet is not None and distance > params.range_normal_feet
    )
    # Phase 9F: a ranged attack (anything with a "long" range tier - melee
    # weapons/actions have none, see weapon_range_feet/monster_action_range_
    # feet) rolls with disadvantage while a hostile creature is within 5ft
    # of the attacker, per SRD ("Ranged Attacks in Close Combat").
    is_ranged = params.range_long_feet is not None
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
    )
    actor.has_help_advantage = False

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

    result = resolve_attack(
        defender_ac=target.ac,
        attack_bonus=params.attack_bonus,
        damage_dice_count=params.damage_dice_count,
        damage_dice_sides=params.damage_dice_sides,
        damage_bonus=params.damage_bonus,
        damage_type=params.damage_type,
        rng=rng,
        advantage=advantage,
        disadvantage=target.is_dodging
        or has_non_proficient_armor(actor, srd)
        or long_range_disadvantage
        or engaged_disadvantage
        or condition_attack_disadvantage(actor, target, distance),
        force_critical=already_unconscious,
    )

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
    for _ in range(num_attacks):
        if target.is_dead:
            return
        _resolve_single_attack(state, actor, target, params, rng, srd)


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
    save_bonus = _target_saving_throw_bonus(character, "CON", srd)
    result, success = resolve_saving_throw(save_bonus=save_bonus, dc=dc, rng=rng)
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
                "success": success,
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
    actual_loss = apply_damage(target, damage)
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
    speed = base_speed * 2 if action.verb == "dash" else base_speed

    if not can_afford_move(speed, full_path, state.battle_map.terrain):
        cost = move_cost_feet(full_path, state.battle_map.terrain)
        raise TurnEngineError(
            f"{actor.id} cannot afford this move (cost={cost}, speed budget={speed})"
        )

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
                "success": success,
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

    if resolved_weapons and not weapon_combo_is_legal(resolved_weapons, srd.equipment):
        names = ", ".join(srd.equipment[idx]["name"] for idx in resolved_weapons)
        raise TurnEngineError(
            f"Cannot equip {names} together - at most 2 weapons, a two-handed "
            "weapon must be alone, and 2 weapons together must both be light"
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
        dex_mod = ability_modifier(actor.stats["DEX"])
        actor.ac = armor_ac(
            actor.equipped_armor,
            actor.equipped_shield,
            dex_mod,
            actor.fighting_style,
            srd.equipment,
        )

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
    """
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
    _, ability_mod = _spellcasting_ability_mod(actor, srd)
    damage_info = spell["damage"]
    notation = (
        damage_info["damage_at_character_level"]["1"]
        if spell_level == 0
        else damage_info["damage_at_slot_level"][str(spell_level)]
    )
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
        range_normal_feet=spell_range_feet(str(spell.get("range", ""))),
        range_long_feet=None,  # spells have no "beyond normal" disadvantage tier
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
    advantage = actor.has_help_advantage or condition_attack_advantage(actor, target, distance)
    actor.has_help_advantage = False

    already_unconscious = has_condition(target, "unconscious")

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
                "spell_level": spell_level,
                "roll_total": result.attack_roll.total,
                "hit": result.hit,
                "critical": result.critical,
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
    """Save-based spell parameters (e.g. Fireball, Hold Person)."""
    dc_info = spell["dc"]
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
    save_bonus = _target_saving_throw_bonus(target, params.dc_ability, srd)
    result, success = resolve_saving_throw(
        save_bonus=save_bonus,
        dc=params.dc,
        rng=rng,
        # Phase 9A's condition_save_disadvantage (exhaustion 3+) had no real
        # call site until now - the only saving throw previously rolled
        # (death saves) is deliberately flat/unmodified per SRD.
        disadvantage=condition_save_disadvantage(target),
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
                "success": success,
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

    range_normal_feet = spell_range_feet(str(spell.get("range", "")))
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

    is_bonus_action = _is_bonus_action_spell(spell)
    if is_bonus_action and actor.bonus_action_used:
        raise TurnEngineError(
            f"{actor.id} has already used their bonus action this turn - "
            f"cannot also cast {spell['name']}"
        )

    # Multi-target (Phase 9D): action.targets (a list of character ids)
    # takes priority when present; falling back to a single-element
    # [action.target] list keeps every pre-existing single-target action
    # (and test) resolving identically to before.
    target_ids = action.targets if action.targets else ([action.target] if action.target else None)
    if not target_ids:
        raise TurnEngineError("cast_spell action requires a target")

    mechanic = spell_mechanic(spell)
    if mechanic is None:
        raise TurnEngineError(
            f"{spell['name']} is not supported - cast_spell resolves attack-roll, save-based, "
            "and heal spells (Phase 9D); other no-roll effects (e.g. Magic Missile's automatic "
            "hits) aren't implemented"
        )

    targets: list[Character] = []
    for target_id in target_ids:
        target = state.characters.get(target_id)
        if target is None:
            raise TurnEngineError(f"Unknown spell target: {target_id}")
        # A heal spell targets an ally by design - only the two offensive
        # mechanics need the friendly-fire/charmed guard.
        if mechanic != "heal":
            _validate_attack_target(actor, target)
        targets.append(target)

    range_normal_feet = spell_range_feet(str(spell.get("range", "")))
    for target in targets:
        distance = distance_feet(actor.position, target.position)
        if distance > range_normal_feet:
            raise TurnEngineError(
                f"{target.id} is {distance}ft away - out of range for {spell['name']} "
                f"(max {range_normal_feet}ft)"
            )

    spell_level = spell["level"]
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
    result, _ = resolve_saving_throw(save_bonus=0, dc=10, rng=rng)
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
        modifier=modifier, dc=10, rng=rng, advantage=advantage, disadvantage=disadvantage
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
                "success": success,
                "target": target.id,
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
            # Phase C: equip_used_this_turn resets on the same "turn
            # actually advances TO this character" schedule, for the same
            # reason - equip doesn't end the turn either.
            next_actor.equip_used_this_turn = False
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
    # itself rejects via actor.bonus_action_used).
    ends_turn = True

    if action.verb == "attack":
        _resolve_attack(state, actor, action, rng, srd)
    elif action.verb in ("move", "dash"):
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

    _check_victory_defeat(state)

    if state.status == "in_progress" and ends_turn:
        _advance_turn_skipping_dead(state)

    return state

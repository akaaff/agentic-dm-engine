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

Deliberate simplifications (documented, not silent):
- Movement takes an explicit path (list of intermediate squares) in
  params["path"], not just a destination - real pathfinding around
  obstacles is a future concern, not what this engine validates.
- Skill checks (Day 13) use a single default DC (no per-scene DC data
  exists yet - that's campaign/scene content, not engine scope).
- "disengage" (Day 13) has no mechanical effect - this engine has no
  opportunity-attack mechanic yet for it to interact with.
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
from dataclasses import dataclass

from src.engine.actions import ParsedAction
from src.engine.conditions import apply_condition, has_condition, remove_condition, tick_conditions
from src.engine.dice import roll
from src.engine.events import Event
from src.engine.movement import can_afford_move, move_cost_feet
from src.engine.position import Position, distance_feet
from src.engine.rules import (
    ability_check_modifier,
    ability_modifier,
    apply_damage,
    condition_attack_advantage,
    condition_attack_disadvantage,
    condition_check_disadvantage,
    condition_save_disadvantage,
    effective_speed,
    has_non_proficient_armor,
    is_class_proficient_with,
    monster_action_range_feet,
    monster_saving_throw_bonus,
    normalize_skill_name,
    resolve_attack,
    resolve_saving_throw,
    resolve_skill_check,
    saving_throw_bonus,
    skill_ability,
    spell_range_feet,
    weapon_range_feet,
)
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import AbilityScore, Character, Condition, GameState
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


def _pc_attack_params(actor: Character, weapon_index: str | None, srd: SrdIndex) -> AttackParams:
    weapon: SrdEntry | None = None
    if weapon_index:
        weapon = srd.equipment.get(weapon_index)
        if weapon is None or not weapon.get("weapon_category"):
            raise TurnEngineError(f"{weapon_index!r} is not a valid weapon")
    else:
        for idx in actor.inventory:
            item = srd.equipment.get(idx)
            if item and item.get("weapon_category"):
                weapon = item
                break

    str_mod = ability_modifier(actor.stats["STR"])
    dex_mod = ability_modifier(actor.stats["DEX"])

    if weapon is None:
        # Unarmed strike (PHB): 1 bludgeoning damage + STR mod, no damage die,
        # 5ft reach like any other melee attack.
        return AttackParams(
            attack_bonus=str_mod + actor.proficiency_bonus,
            damage_dice_count=0,
            damage_dice_sides=4,
            damage_bonus=str_mod + 1,
            damage_type="bludgeoning",
            source_name="unarmed strike",
            range_normal_feet=5,
            range_long_feet=None,
        )

    properties = {p["index"] for p in (weapon.get("properties") or [])}
    if "finesse" in properties:
        ability_mod = max(str_mod, dex_mod)
    elif weapon.get("weapon_range") == "Ranged":
        ability_mod = dex_mod
    else:
        ability_mod = str_mod

    proficient = is_class_proficient_with(actor, weapon["index"], srd)
    prof_bonus = actor.proficiency_bonus if proficient else 0
    range_normal_feet, range_long_feet = weapon_range_feet(weapon)

    dice_count, dice_sides, notation_bonus = parse_dice_notation(weapon["damage"]["damage_dice"])
    return AttackParams(
        attack_bonus=ability_mod + prof_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=ability_mod + notation_bonus,
        damage_type=weapon["damage"]["damage_type"]["index"],
        source_name=weapon["name"],
        range_normal_feet=range_normal_feet,
        range_long_feet=range_long_feet,
    )


def _monster_attack_params(
    actor: Character, action_name: str | None, srd: SrdIndex
) -> AttackParams:
    if actor.monster_index is None:
        raise TurnEngineError(f"{actor.id} is not a monster (no monster_index)")
    monster_data = srd.monsters[actor.monster_index]
    actions = monster_data.get("actions") or []
    if not actions:
        raise TurnEngineError(f"{monster_data['name']} has no actions")

    if action_name:
        action = next((a for a in actions if a["name"].lower() == action_name.lower()), None)
        if action is None:
            raise TurnEngineError(f"{monster_data['name']} has no action named {action_name!r}")
    else:
        action = actions[0]

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


def _resolve_attack(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if action.target is None:
        raise TurnEngineError("attack action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown attack target: {action.target}")
    _validate_attack_target(actor, target)

    params = (
        _monster_attack_params(actor, action.item_or_spell, srd)
        if actor.monster_index
        else _pc_attack_params(actor, action.item_or_spell, srd)
    )

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

    # Advantage from being helped (Day 13) is consumed by this roll whether
    # or not it changes the outcome; disadvantage from the target dodging
    # applies for as long as the target is dodging (until their own next
    # turn) rather than being consumed - roll_d20 already cancels the two
    # out together when both apply, per SRD rules. The attacker's own
    # non-proficient armor, attacking beyond normal range, and SRD condition
    # effects (Phase 9A - blinded/prone/restrained/invisible/etc. on either
    # side) are further independent sources.
    advantage = actor.has_help_advantage or condition_attack_advantage(actor, target, distance)
    actor.has_help_advantage = False

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
        or condition_attack_disadvantage(actor, target, distance),
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

    if not result.hit or result.damage is None:
        return

    _apply_damage_and_handle_downing(
        state, actor, target, result.damage, params.damage_type, rng, srd
    )


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

    # PC at 0 HP: unconscious, not dead - death_save (below) decides its fate.
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


def _resolve_move(state: GameState, actor: Character, action: ParsedAction) -> None:
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
    actor.position = steps[-1]
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
    state.events.append(
        Event(round=state.round, turn_index=state.current_turn, actor=actor.id, type="disengage")
    )


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


def _spell_mechanic(spell: SrdEntry) -> str:
    """Classifies a spell into one of the three mechanics cast_spell
    resolves (Phase 9D): "attack" (SRD `attack_type` present - e.g. Fire
    Bolt, Guiding Bolt), "save" (`dc` present - e.g. Fireball, Hold Person),
    or "heal" (`heal_at_slot_level` present - e.g. Cure Wounds). Checked in
    this order since a real SRD spell only ever has one of the three shapes
    (confirmed by inspecting several of each directly via load_srd()).
    Anything else - a no-roll, non-heal effect like Magic Missile's
    automatic-hit force damage, or a pure buff/utility spell with none of
    these fields - is still out of scope and raises the same clear rejection
    Day 14 always has for an unsupported spell."""
    if spell.get("attack_type"):
        return "attack"
    if spell.get("dc"):
        return "save"
    if spell.get("heal_at_slot_level"):
        return "heal"
    raise TurnEngineError(
        f"{spell['name']} is not supported - cast_spell resolves attack-roll, save-based, "
        "and heal spells (Phase 9D); other no-roll effects (e.g. Magic Missile's automatic "
        "hits) aren't implemented"
    )


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
        # since it's a no-roll spell excluded by _spell_mechanic; attack-roll
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
    once per id in action.targets, or once for the single legacy `target`)."""
    distance = distance_feet(actor.position, target.position)
    advantage = actor.has_help_advantage or condition_attack_advantage(actor, target, distance)
    actor.has_help_advantage = False

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

    if not result.hit or result.damage is None:
        return
    _apply_damage_and_handle_downing(
        state, actor, target, result.damage, params.damage_type, rng, srd
    )


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


def _resolve_cast_spell(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if not action.item_or_spell:
        raise TurnEngineError("cast_spell action requires item_or_spell (the spell name)")
    normalized = action.item_or_spell.strip().lower().replace(" ", "-")
    spell = srd.spells.get(normalized)
    if spell is None:
        raise TurnEngineError(f"Unknown spell: {action.item_or_spell!r}")

    # Multi-target (Phase 9D): action.targets (a list of character ids)
    # takes priority when present; falling back to a single-element
    # [action.target] list keeps every pre-existing single-target action
    # (and test) resolving identically to before.
    target_ids = action.targets if action.targets else ([action.target] if action.target else None)
    if not target_ids:
        raise TurnEngineError("cast_spell action requires a target")

    mechanic = _spell_mechanic(spell)

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
    elif actor.death_save_successes >= 3:
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
        state.current_turn = next_index
        state.round = next_round
        if not _skip_this_turn(state.characters[state.turn_order[state.current_turn]]):
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

    # Dodging protects "until the start of your next turn" - that window
    # ends right now, since this actor's next turn is the one being
    # resolved. Cleared before dispatch so a fresh "dodge" this turn (which
    # re-sets it to True) isn't immediately undone.
    actor.is_dodging = False

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

    if action.verb == "attack":
        _resolve_attack(state, actor, action, rng, srd)
    elif action.verb in ("move", "dash"):
        _resolve_move(state, actor, action)
    elif action.verb == "skill_check":
        _resolve_skill_check(state, actor, action, rng, srd)
    elif action.verb == "dodge":
        _resolve_dodge(state, actor)
    elif action.verb == "disengage":
        _resolve_disengage(state, actor)
    elif action.verb == "help":
        _resolve_help(state, actor, action)
    elif action.verb == "cast_spell":
        _resolve_cast_spell(state, actor, action, rng, srd)
    elif action.verb == "use_item":
        _resolve_use_item(state, actor, action, rng)
    elif action.verb == "death_save":
        _resolve_death_save(state, actor, rng)
    elif action.verb == "end_turn":
        pass
    else:
        raise NotImplementedError(f"Verb not yet supported by the turn engine: {action.verb}")

    _check_victory_defeat(state)

    if state.status == "in_progress":
        _advance_turn_skipping_dead(state)

    return state

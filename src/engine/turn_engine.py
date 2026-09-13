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
action instead of one, via a small loop inside _resolve_attack - Phase 9F's
Multiattack (monsters resolving several named sub-actions in one action)
hadn't landed in this worktree yet when this was written, so there was no
existing "resolve N attacks in one action" helper to reuse.

Deliberate simplifications (documented, not silent):
- Movement takes an explicit path (list of intermediate squares) in
  params["path"], not just a destination - real pathfinding around
  obstacles is a future concern, not what this engine validates.
- Skill checks (Day 13) use a single default DC (no per-scene DC data
  exists yet - that's campaign/scene content, not engine scope).
- "disengage" (Day 13) has no mechanical effect - this engine has no
  opportunity-attack mechanic yet for it to interact with.
- "cast_spell" (Day 14) only resolves single-target attack-roll spells (the
  SRD's `attack_type` field present) - save-based spells (a `dc` field
  instead) and no-roll spells like Magic Missile (neither field) raise a
  clear error rather than being silently mishandled.
- "use_item" (Day 14) only resolves a single hardcoded item
  (potion-of-healing) - any other item name raises a clear error.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

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
    condition_attack_advantage,
    condition_attack_disadvantage,
    condition_check_disadvantage,
    effective_speed,
    has_non_proficient_armor,
    is_class_proficient_with,
    monster_action_range_feet,
    normalize_skill_name,
    resolve_attack,
    resolve_saving_throw,
    resolve_skill_check,
    skill_ability,
    spell_range_feet,
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

    # Extra Attack (Phase 9J): an eligible PC makes two attack rolls for this
    # one `attack` action instead of one - still a single action, no
    # bonus-action/reaction machinery (Phase 9H) needed. Each iteration
    # appends its own `attack_roll` event, exactly like a single attack
    # already does; the loop stops early if the target dies partway through
    # (attacking a corpse with the second roll would be meaningless).
    num_attacks = 2 if is_eligible_for_extra_attack(actor) else 1
    for _ in range(num_attacks):
        # Advantage from being helped (Day 13) is consumed by this roll
        # whether or not it changes the outcome - only the first of two
        # Extra Attack rolls can ever benefit from it, matching the SRD
        # (Help grants advantage on "the next attack roll," singular);
        # disadvantage from the target dodging applies for as long as the
        # target is dodging (until their own next turn) rather than being
        # consumed - roll_d20 already cancels the two out together when both
        # apply, per SRD rules. The attacker's own non-proficient armor,
        # attacking beyond normal range, and SRD condition effects (Phase
        # 9A - blinded/prone/restrained/invisible/etc. on either side) are
        # further independent sources, unchanged across every attack roll
        # this action makes.
        advantage = actor.has_help_advantage or condition_attack_advantage(actor, target, distance)
        actor.has_help_advantage = False

        # Phase 9C: per SRD, any hit against an unconscious creature is a
        # critical hit - checked before resolve_attack runs (not after)
        # since it must reflect whether the target was ALREADY down before
        # THIS attack roll, not whether this very hit is the one that drops
        # them. Re-checked fresh every iteration (not just once before the
        # loop): an Extra Attack's first roll can itself knock the target
        # unconscious, in which case the second roll of this same action
        # correctly gets the helpless treatment the first one didn't.
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

        if result.hit and result.damage is not None:
            _apply_damage_and_handle_downing(
                state, actor, target, result.damage, params.damage_type
            )

        # Separate from normal damage (per SRD): a hit against an already-
        # unconscious PC also inflicts 2 automatic death-save failures, on
        # top of whatever damage did (usually nothing further, since the
        # target is already clamped at 0 HP). Checked per iteration, not
        # once after the whole loop - Extra Attack can trigger this more
        # than once in the same action if both rolls hit an already-down
        # target. Gated on result.hit the same way the pre-Extra-Attack
        # code gated it via an early return on a miss.
        if result.hit and already_unconscious and target.is_pc and not target.is_dead:
            _apply_unconscious_hit_death_save_failures(state, target)

        if target.is_dead:
            break


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


def _apply_damage_and_handle_downing(
    state: GameState, attacker: Character, target: Character, damage: int, damage_type: str
) -> None:
    """Shared by attack and cast_spell (Day 14) - both can reduce a
    character to 0 HP and need the same monster-dies-outright-vs-
    PC-goes-unconscious handling."""
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
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random
) -> None:
    if action.target is None:
        raise TurnEngineError("grapple action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown grapple target: {action.target}")
    _validate_attack_target(actor, target)

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
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random
) -> None:
    if action.target is None:
        raise TurnEngineError("shove action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown shove target: {action.target}")
    _validate_attack_target(actor, target)

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


def _spell_attack_params(
    actor: Character, spell_name: str, srd: SrdIndex
) -> tuple[AttackParams, int]:
    """Returns (attack params, spell level - 0 for a cantrip). Only
    single-target attack-roll spells are supported (Day 14 scope) - the
    SRD's `attack_type` field is present only for those."""
    normalized = spell_name.strip().lower().replace(" ", "-")
    spell = srd.spells.get(normalized)
    if spell is None:
        raise TurnEngineError(f"Unknown spell: {spell_name!r}")
    if not spell.get("attack_type"):
        raise TurnEngineError(
            f"{spell['name']} is not supported - cast_spell only resolves single-target "
            "attack-roll spells (Day 14 scope); save-based and no-roll spells aren't implemented"
        )
    if actor.class_index is None:
        raise TurnEngineError(
            f"{actor.id} has no class_index - cannot determine spellcasting ability"
        )
    cls = srd.classes.get(actor.class_index)
    spellcasting = cls.get("spellcasting") if cls else None
    if not spellcasting:
        raise TurnEngineError(f"{actor.class_} has no spellcasting ability")
    ability: AbilityScore = spellcasting["spellcasting_ability"]["index"].upper()
    ability_mod = ability_modifier(actor.stats[ability])

    spell_level = spell["level"]
    damage_info = spell["damage"]
    notation = (
        damage_info["damage_at_character_level"]["1"]
        if spell_level == 0
        else damage_info["damage_at_slot_level"][str(spell_level)]
    )
    dice_count, dice_sides, notation_bonus = parse_dice_notation(notation)

    params = AttackParams(
        attack_bonus=ability_mod + actor.proficiency_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        # 5e spell damage doesn't add the spellcasting ability modifier
        # (unlike weapon damage) - only whatever bonus is in the notation
        # itself (e.g. Magic Missile's embedded "+3", not applicable here
        # since it's a no-roll spell excluded above; attack-roll spells in
        # the SRD generally have none).
        damage_bonus=notation_bonus,
        damage_type=damage_info["damage_type"]["index"],
        source_name=spell["name"],
        range_normal_feet=spell_range_feet(str(spell.get("range", ""))),
        range_long_feet=None,  # spells have no "beyond normal" disadvantage tier
    )
    return params, spell_level


def _resolve_cast_spell(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if not action.item_or_spell:
        raise TurnEngineError("cast_spell action requires item_or_spell (the spell name)")
    if action.target is None:
        raise TurnEngineError("cast_spell action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown spell target: {action.target}")
    _validate_attack_target(actor, target)

    params, spell_level = _spell_attack_params(actor, action.item_or_spell, srd)

    distance = distance_feet(actor.position, target.position)
    if distance > params.range_normal_feet:
        raise TurnEngineError(
            f"{target.id} is {distance}ft away - out of range for {params.source_name} "
            f"(max {params.range_normal_feet}ft)"
        )

    if spell_level > 0:
        remaining = actor.spell_slots.get(spell_level, 0)
        if remaining <= 0:
            raise TurnEngineError(f"{actor.id} has no level-{spell_level} spell slots remaining")
        actor.spell_slots[spell_level] = remaining - 1

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
    _apply_damage_and_handle_downing(state, actor, target, result.damage, params.damage_type)


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
    elif action.verb == "grapple":
        _resolve_grapple(state, actor, action, rng)
    elif action.verb == "shove":
        _resolve_shove(state, actor, action, rng)
    elif action.verb == "cast_spell":
        _resolve_cast_spell(state, actor, action, rng, srd)
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

    if state.status == "in_progress":
        _advance_turn_skipping_dead(state)

    return state

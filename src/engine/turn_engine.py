"""Dispatches a ParsedAction against a GameState: validates it's the actor's
turn, derives attack/damage parameters (from equipped weapon + stats for
PCs, from the SRD stat block's actions for monsters), calls into rules.py
for the actual dice math, appends Events, checks victory/defeat, and
advances the turn. This is the one place per-turn orchestration lives - Day
11 wraps this exact function as the LangGraph rules_engine node.

Deliberate simplifications (documented, not silent):
- Proficiency with equipped weapons is assumed for PCs (no weapon-specific
  proficiency tracking) - attack_bonus always includes proficiency_bonus.
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
from src.engine.conditions import apply_condition, has_condition, remove_condition, tick_conditions
from src.engine.dice import roll
from src.engine.events import Event
from src.engine.movement import can_afford_move, move_cost_feet
from src.engine.position import Position
from src.engine.rules import (
    ability_check_modifier,
    ability_modifier,
    apply_damage,
    resolve_attack,
    resolve_saving_throw,
    resolve_skill_check,
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
        # Unarmed strike (PHB): 1 bludgeoning damage + STR mod, no damage die.
        return AttackParams(
            attack_bonus=str_mod + actor.proficiency_bonus,
            damage_dice_count=0,
            damage_dice_sides=4,
            damage_bonus=str_mod + 1,
            damage_type="bludgeoning",
            source_name="unarmed strike",
        )

    properties = {p["index"] for p in (weapon.get("properties") or [])}
    if "finesse" in properties:
        ability_mod = max(str_mod, dex_mod)
    elif weapon.get("weapon_range") == "Ranged":
        ability_mod = dex_mod
    else:
        ability_mod = str_mod

    dice_count, dice_sides, notation_bonus = parse_dice_notation(weapon["damage"]["damage_dice"])
    return AttackParams(
        attack_bonus=ability_mod + actor.proficiency_bonus,
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=ability_mod + notation_bonus,
        damage_type=weapon["damage"]["damage_type"]["index"],
        source_name=weapon["name"],
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
    return AttackParams(
        attack_bonus=action["attack_bonus"],
        damage_dice_count=dice_count,
        damage_dice_sides=dice_sides,
        damage_bonus=notation_bonus,
        damage_type=damage_entry["damage_type"]["index"],
        source_name=action["name"],
    )


def _resolve_attack(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    if action.target is None:
        raise TurnEngineError("attack action requires a target")
    target = state.characters.get(action.target)
    if target is None:
        raise TurnEngineError(f"Unknown attack target: {action.target}")

    params = (
        _monster_attack_params(actor, action.item_or_spell, srd)
        if actor.monster_index
        else _pc_attack_params(actor, action.item_or_spell, srd)
    )

    # Advantage from being helped (Day 13) is consumed by this roll whether
    # or not it changes the outcome; disadvantage from the target dodging
    # applies for as long as the target is dodging (until their own next
    # turn) rather than being consumed - roll_d20 already cancels the two
    # out together when both apply, per SRD rules.
    advantage = actor.has_help_advantage
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
        disadvantage=target.is_dodging,
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

    _apply_damage_and_handle_downing(state, actor, target, result.damage, params.damage_type)


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
    speed = actor.speed * 2 if action.verb == "dash" else actor.speed

    if not can_afford_move(speed, full_path, state.battle_map.terrain):
        cost = move_cost_feet(full_path, state.battle_map.terrain)
        raise TurnEngineError(
            f"{actor.id} cannot afford this move (cost={cost}, speed budget={speed})"
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


def _normalize_skill_name(raw: str) -> str:
    """ "Perception", "skill-perception", "Sleight of Hand" -> "perception",
    "skill-perception", "sleight-of-hand" (srd.skills' bare-index form)."""
    return raw.strip().lower().replace(" ", "-").removeprefix("skill-")


def _skill_ability(skill_name: str, srd: SrdIndex) -> AbilityScore:
    normalized = _normalize_skill_name(skill_name)
    skill_data = srd.skills.get(normalized)
    if skill_data is None:
        raise TurnEngineError(f"Unknown skill: {skill_name!r}")
    ability: AbilityScore = skill_data["ability_score"]["index"].upper()
    return ability


def _resolve_skill_check(
    state: GameState, actor: Character, action: ParsedAction, rng: random.Random, srd: SrdIndex
) -> None:
    skill = action.params.get("skill")
    if not skill:
        raise TurnEngineError("skill_check action requires params['skill']")

    ability = _skill_ability(skill, srd)
    proficient = f"skill-{_normalize_skill_name(skill)}" in actor.skill_proficiencies
    modifier = ability_check_modifier(actor, ability, proficient=proficient)

    advantage = actor.has_help_advantage
    actor.has_help_advantage = False

    result, success = resolve_skill_check(
        modifier=modifier,
        dc=DEFAULT_SKILL_CHECK_DC,
        rng=rng,
        advantage=advantage,
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

    params, spell_level = _spell_attack_params(actor, action.item_or_spell, srd)

    if spell_level > 0:
        remaining = actor.spell_slots.get(spell_level, 0)
        if remaining <= 0:
            raise TurnEngineError(f"{actor.id} has no level-{spell_level} spell slots remaining")
        actor.spell_slots[spell_level] = remaining - 1

    advantage = actor.has_help_advantage
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
        disadvantage=target.is_dodging,
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

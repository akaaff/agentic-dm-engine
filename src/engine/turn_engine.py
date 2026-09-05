"""Dispatches a ParsedAction against a GameState: validates it's the actor's
turn, derives attack/damage parameters (from equipped weapon + stats for
PCs, from the SRD stat block's actions for monsters), calls into rules.py
for the actual dice math, appends Events, checks victory/defeat, and
advances the turn. This is the one place per-turn orchestration lives - Day
11 wraps this exact function as the LangGraph rules_engine node.

Deliberate Day 7 simplifications (documented, not silent):
- Proficiency with equipped weapons is assumed for PCs (no proficiency
  tracking yet) - attack_bonus always includes proficiency_bonus.
- Movement takes an explicit path (list of intermediate squares) in
  params["path"], not just a destination - real pathfinding around
  obstacles is a future concern, not what this engine validates.
- Only "attack", "move"/"dash", and "end_turn" verbs are handled; other
  verbs (cast_spell, skill_check, etc.) raise NotImplementedError until a
  later day needs them.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from src.engine.actions import ParsedAction
from src.engine.conditions import tick_conditions
from src.engine.events import Event
from src.engine.movement import can_afford_move, move_cost_feet
from src.engine.position import Position
from src.engine.rules import ability_modifier, apply_damage, resolve_attack
from src.engine.srd_loader import SrdEntry, SrdIndex, load_srd
from src.engine.state import Character, GameState
from src.engine.turn_order import next_turn

_DICE_NOTATION_RE = re.compile(r"(\d+)d(\d+)([+-]\d+)?")


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

    result = resolve_attack(
        defender_ac=target.ac,
        attack_bonus=params.attack_bonus,
        damage_dice_count=params.damage_dice_count,
        damage_dice_sides=params.damage_dice_sides,
        damage_bonus=params.damage_bonus,
        damage_type=params.damage_type,
        rng=rng,
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

    actual_loss = apply_damage(target, result.damage)
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=actor.id,
            type="damage_dealt",
            payload={
                "target": target.id,
                "amount": actual_loss,
                "damage_type": params.damage_type,
                "target_hp_remaining": target.hp,
            },
        )
    )
    if target.hp == 0:
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=target.id,
                type="death",
                payload={"killed_by": actor.id},
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


def _check_victory_defeat(state: GameState) -> None:
    party_alive = any(c.hp > 0 for c in state.characters.values() if c.is_pc)
    monsters_alive = any(c.hp > 0 for c in state.characters.values() if not c.is_pc)
    if not monsters_alive:
        state.status = "victory"
    elif not party_alive:
        state.status = "defeat"


def _advance_turn_skipping_dead(state: GameState) -> None:
    """A character killed mid-round (e.g. on an earlier actor's turn) must
    not be prompted for its own turn later that same round - skip forward
    until landing on a living combatant. Guarded by len(turn_order) since
    _check_victory_defeat already ends combat before every combatant could
    ever be dead simultaneously."""
    for _ in range(len(state.turn_order)):
        next_index, next_round = next_turn(state.turn_order, state.current_turn, state.round)
        if next_round != state.round:
            for character in state.characters.values():
                tick_conditions(character)
        state.current_turn = next_index
        state.round = next_round
        if state.characters[state.turn_order[state.current_turn]].hp > 0:
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
    if actor.hp <= 0:
        raise TurnEngineError(f"{actor.id} is down and cannot act")

    if action.verb == "attack":
        _resolve_attack(state, actor, action, rng, srd)
    elif action.verb in ("move", "dash"):
        _resolve_move(state, actor, action)
    elif action.verb == "end_turn":
        pass
    else:
        raise NotImplementedError(f"Verb not yet supported by the turn engine: {action.verb}")

    _check_victory_defeat(state)

    if state.status == "in_progress":
        _advance_turn_skipping_dead(state)

    return state

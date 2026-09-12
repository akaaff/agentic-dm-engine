"""Minimal deterministic monster-turn AI: no LLM call, no persona - a
monster attacks the nearest living party member if within its weapon's
range, or closes the distance toward them if not (ties broken by character
id for determinism/testability).

This exists only to unblock full zero-human-input autoplay (Day 15's verify
gate): the campaign's monsters still need to act every round, but building
real monster intelligence is explicitly out of scope for this project (see
CLAUDE.md's Day 12 note on the "who controls monster turns" design gap).
This heuristic doesn't touch that gap - it just replaces "no action at all"
with a reasonable deterministic one, and does nothing to prevent the
friendly-fire scenario noted there (that only arises from free-text intent
parsing, which this heuristic never uses).

The movement half was added live (see CLAUDE.md): turn_engine started
enforcing attack range, and this heuristic - always attacking the nearest
target regardless of actual distance - would have made every out-of-range
monster fail every attack forever, since it never moves. Greedy step-by-
step approach, not real pathfinding (matches turn_engine's own documented
stance that real pathfinding is a future concern) - good enough for this
project's small, mostly-open battle maps."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import (
    FEET_PER_SQUARE,
    Position,
    TerrainType,
    chebyshev_distance,
    distance_feet,
)
from src.engine.rules import monster_action_range_feet
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState


def _monster_range_feet(actor: Character) -> int:
    """The (normal) range of a monster's default attack (turn_engine's own
    _monster_attack_params falls back to the same actions[0] when no
    specific action is named) - falls back to a flat 5ft melee range if the
    monster has no stat block or no actions, same as an unparseable action
    description would (see rules.monster_action_range_feet)."""
    if actor.monster_index is None:
        return 5
    monster_data = load_srd().monsters.get(actor.monster_index)
    actions = (monster_data or {}).get("actions") or []
    if not actions:
        return 5
    range_normal_feet, _ = monster_action_range_feet(actions[0])
    return range_normal_feet


def _best_step(
    current: Position, target: Position, terrain: list[list[TerrainType]]
) -> Position | None:
    """One square toward `target` - whichever of the 8 adjacent squares
    most reduces distance, skipping walls/out-of-bounds. A simple greedy
    heuristic, not real pathfinding: can get stuck going the "wrong" way
    around a wall it can't see past. Returns None if every adjacent square
    is blocked."""
    candidates = [
        Position(x=current.x + dx, y=current.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    ]
    in_bounds = [p for p in candidates if 0 <= p.y < len(terrain) and 0 <= p.x < len(terrain[p.y])]
    open_squares = [p for p in in_bounds if terrain[p.y][p.x] != "wall"]
    if not open_squares:
        return None
    return min(open_squares, key=lambda p: chebyshev_distance(p, target))


def _approach_path(
    start: Position,
    target: Position,
    speed: int,
    range_feet: int,
    terrain: list[list[TerrainType]],
) -> list[Position]:
    """Greedy step-by-step path toward `target`, stopping once within
    `range_feet` or the speed budget runs out. May be empty (already in
    range) or short of `range_feet` (blocked, or not enough speed) - the
    caller attacks anyway either way; turn_engine's own range check is the
    honest final word on whether that lands."""
    path: list[Position] = []
    current = start
    remaining = speed
    while distance_feet(current, target) > range_feet:
        step = _best_step(current, target, terrain)
        if step is None:
            break
        cost = FEET_PER_SQUARE * (2 if terrain[step.y][step.x] == "difficult" else 1)
        if cost > remaining:
            break
        path.append(step)
        remaining -= cost
        current = step
    return path


def choose_monster_action(game_state: GameState, actor: Character) -> ParsedAction:
    living_targets = [
        c for c in game_state.characters.values() if c.is_pc and not c.is_dead and c.id != actor.id
    ]
    if not living_targets:
        return ParsedAction(
            actor=actor.id, verb="end_turn", raw_text=f"{actor.name} has no target left."
        )

    target = min(
        living_targets, key=lambda c: (chebyshev_distance(actor.position, c.position), c.id)
    )
    range_feet = _monster_range_feet(actor)

    if (
        distance_feet(actor.position, target.position) <= range_feet
        or game_state.battle_map is None
    ):
        return ParsedAction(
            actor=actor.id,
            verb="attack",
            target=target.id,
            raw_text=f"{actor.name} attacks {target.name}.",
        )

    path = _approach_path(
        actor.position, target.position, actor.speed, range_feet, game_state.battle_map.terrain
    )
    if not path:
        # Can't get any closer (blocked, or no speed left) - attack anyway.
        # turn_engine's own range check gives an honest rejection rather
        # than this heuristic silently doing nothing.
        return ParsedAction(
            actor=actor.id,
            verb="attack",
            target=target.id,
            raw_text=f"{actor.name} attacks {target.name}.",
        )

    return ParsedAction(
        actor=actor.id,
        verb="move",
        target=target.id,  # not used by _resolve_move - documents intent
        params={"path": [{"x": p.x, "y": p.y} for p in path]},
        raw_text=f"{actor.name} closes in on {target.name}.",
    )

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
project's small, mostly-open battle maps. The approach path also avoids
squares already occupied by another living character (a snapshot taken
once per monster turn, see _approach_path's docstring) - added live after
two monsters converging on the same target ended up stacked on the exact
same square, invisible as two separate tokens on the combat grid.

Issue #22: before falling back to a plain weapon attack, this heuristic now
checks whether the actor has a currently-castable Innate Spellcasting spell
(see _choose_innate_spell) - the highest-priority stat-block spell that's a
resolvable mechanic (attack/save), still available (at-will, or a "per day"
one with uses left), and has the target in range, cast instead of melee.
Still deterministic and still just a heuristic (first-in-list, not
tactical), same spirit as the rest of this module."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import (
    FEET_PER_SQUARE,
    Position,
    TerrainType,
    chebyshev_distance,
    distance_feet,
)
from src.engine.rules import (
    effective_speed,
    monster_action_range_feet,
    monster_innate_spellcasting,
    normalize_spell_name,
    spell_mechanic,
    spell_range_feet,
)
from src.engine.srd_loader import SrdIndex, load_srd
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
    current: Position,
    target: Position,
    terrain: list[list[TerrainType]],
    occupied: set[tuple[int, int]],
) -> Position | None:
    """One square toward `target` - whichever of the 8 adjacent squares
    most reduces distance, skipping walls/out-of-bounds/`occupied` squares.
    A simple greedy heuristic, not real pathfinding: can get stuck going the
    "wrong" way around a wall it can't see past. Returns None if every
    adjacent square is blocked."""
    candidates = [
        Position(x=current.x + dx, y=current.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    ]
    in_bounds = [p for p in candidates if 0 <= p.y < len(terrain) and 0 <= p.x < len(terrain[p.y])]
    open_squares = [
        p for p in in_bounds if terrain[p.y][p.x] != "wall" and (p.x, p.y) not in occupied
    ]
    if not open_squares:
        return None
    return min(open_squares, key=lambda p: chebyshev_distance(p, target))


def _approach_path(
    start: Position,
    target: Position,
    speed: int,
    range_feet: int,
    terrain: list[list[TerrainType]],
    occupied: set[tuple[int, int]],
) -> list[Position]:
    """Greedy step-by-step path toward `target`, stopping once within
    `range_feet` or the speed budget runs out. May be empty (already in
    range) or short of `range_feet` (blocked, occupied, or not enough
    speed) - the caller attacks anyway either way; turn_engine's own range
    check is the honest final word on whether that lands.

    `occupied` is a snapshot taken once at the start of this monster's turn
    (every other living character's square) - not updated as this path is
    built, since a single monster only ever moves once per turn anyway.
    Caught live: two monsters converging on the same target from different
    starting squares could independently choose the identical "best"
    intermediate square and end up stacked exactly on top of each other -
    invisible as two tokens on the combat grid, and a real (if minor) break
    of the "no two creatures share a square" rule."""
    path: list[Position] = []
    current = start
    remaining = speed
    while distance_feet(current, target) > range_feet:
        step = _best_step(current, target, terrain, occupied)
        if step is None:
            break
        cost = FEET_PER_SQUARE * (2 if terrain[step.y][step.x] == "difficult" else 1)
        if cost > remaining:
            break
        path.append(step)
        remaining -= cost
        current = step
    return path


def _choose_innate_spell(actor: Character, target: Character, srd: SrdIndex) -> ParsedAction | None:
    """The first of `actor`'s Innate Spellcasting spells (stat-block order)
    that's currently castable against `target` right now - a resolvable
    mechanic (attack/save; skips heal/utility, the same scope
    turn_engine._resolve_monster_innate_spell enforces), still available
    (at-will, or a "per day" one with uses left), and within its range - or
    None if no such spell exists (most monsters, or a caster who's used up
    today's options). Not tactical (doesn't weigh save-vs-attack odds or
    pick the "best" spell) - first-castable-in-list, same deliberately
    simple spirit as the rest of this heuristic."""
    if actor.monster_index is None:
        return None
    monster_data = srd.monsters.get(actor.monster_index)
    innate = monster_innate_spellcasting(monster_data) if monster_data else None
    if innate is None:
        return None
    distance = distance_feet(actor.position, target.position)
    for spell_ref in innate.get("spells", []):
        normalized = normalize_spell_name(spell_ref["name"])
        spell = srd.spells.get(normalized)
        if spell is None or spell_mechanic(spell) not in ("attack", "save"):
            continue
        usage = spell_ref.get("usage", {})
        if (
            usage.get("type") == "per day"
            and actor.innate_spell_uses_remaining.get(normalized, 0) <= 0
        ):
            continue
        if distance > spell_range_feet(str(spell.get("range", ""))):
            continue
        return ParsedAction(
            actor=actor.id,
            verb="cast_spell",
            target=target.id,
            item_or_spell=spell_ref["name"],
            raw_text=f"{actor.name} casts {spell_ref['name']} at {target.name}.",
        )
    return None


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

    spell_action = _choose_innate_spell(actor, target, load_srd())
    if spell_action is not None:
        return spell_action

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

    occupied = {
        (c.position.x, c.position.y)
        for c in game_state.characters.values()
        if not c.is_dead and c.id != actor.id
    }
    path = _approach_path(
        actor.position,
        target.position,
        effective_speed(actor),
        range_feet,
        game_state.battle_map.terrain,
        occupied,
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

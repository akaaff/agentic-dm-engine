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

from typing import Literal

from src.engine.actions import ParsedAction
from src.engine.conditions import has_condition
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


def approach_path(
    start: Position,
    target: Position,
    speed: int,
    range_feet: int,
    terrain: list[list[TerrainType]],
    blocked: set[tuple[int, int]],
    ally_occupied: set[tuple[int, int]] | None = None,
) -> list[Position]:
    """Greedy step-by-step path toward `target`, stopping once within
    `range_feet` or the speed budget runs out. May be empty (already in
    range) or short of `range_feet` (blocked, occupied, or not enough
    speed) - the caller attacks anyway either way; turn_engine's own range
    check is the honest final word on whether that lands.

    `blocked` (hostile-occupied squares, plus walls via _best_step) is a
    snapshot taken once at the start of this monster's turn (not updated as
    this path is built, since a single monster only ever moves once per
    turn anyway) - genuinely impassable, matching real SRD (no moving
    through a hostile creature's space without a special ability). Caught
    live: two monsters converging on the same target from different
    starting squares could independently choose the identical "best"
    intermediate square and end up stacked exactly on top of each other -
    invisible as two tokens on the combat grid, and a real (if minor) break
    of the "no two creatures share a square" rule.

    `ally_occupied` (live-requested, separate from `blocked`): real SRD lets
    you move *through* an ally's space, just not end your move standing on
    it - so these squares are passable as intermediate steps (not excluded
    from _best_step's candidates) but trimmed off the end of the finished
    path if the greedy walk happened to stop on one, leaving the mover one
    square short rather than illegally landing on an ally (turn_engine.
    _resolve_move's own destination-occupancy check is the hard backstop
    either way).

    Public (issue #48) so graph/nodes/intent_parser.py can reuse the exact
    same algorithm for a player's own "move toward X" free text, instead of
    asking the model to compute a valid multi-square path itself - the cost
    accounting here already matches turn_engine._resolve_move's own
    affordability check exactly, so a path built against the actor's real
    remaining speed budget is guaranteed to still be affordable when
    _resolve_move re-validates it."""
    ally_occupied = ally_occupied or set()
    path: list[Position] = []
    current = start
    remaining = speed
    while distance_feet(current, target) > range_feet:
        step = _best_step(current, target, terrain, blocked)
        if step is None:
            break
        cost = FEET_PER_SQUARE * (2 if terrain[step.y][step.x] == "difficult" else 1)
        if cost > remaining:
            break
        path.append(step)
        remaining -= cost
        current = step
    while path and (path[-1].x, path[-1].y) in ally_occupied:
        path.pop()
    return path


def occupied_squares_by_side(
    game_state: GameState, actor: Character
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Splits every other living character's square into (hostile, ally)
    relative to `actor`'s own side - shared by choose_monster_action and
    intent_parser._resolve_move_target so both pass approach_path the same
    "block hostiles, allow passing through allies" occupancy, rather than
    each rebuilding the same is_pc comparison independently."""
    hostile: set[tuple[int, int]] = set()
    ally: set[tuple[int, int]] = set()
    for c in game_state.characters.values():
        if c.is_dead or c.id == actor.id:
            continue
        bucket = ally if c.is_pc == actor.is_pc else hostile
        bucket.add((c.position.x, c.position.y))
    return hostile, ally


def build_move_toward_target(
    game_state: GameState,
    actor: Character,
    target: Character,
    verb: Literal["move", "dash"] = "move",
) -> ParsedAction | None:
    """A real move/dash ParsedAction closing the distance from `actor`
    toward `target`, stopping at 5ft/adjacent - the exact approach_path/
    occupied_squares_by_side combination choose_monster_action already uses
    for monster movement, now shared so a companion's own out-of-range
    attack/cast declaration can be redirected into an actual move instead of
    just failing (see rules_engine_node's own AttackOutOfRangeError catch)
    - previously that path just failed 3 times and forced an end_turn
    (found live: the same out-of-range attack repeated verbatim, never once
    trying to move). intent_parser._resolve_move_target's own "move toward a
    named target" free-text case (issue #48) uses this too, rather than
    duplicating the exact same budget/path logic a second time.

    Returns None if genuinely no progress can be made (blocked, or out of
    movement budget this turn) - the caller decides what to do about that
    (fall back to the original rejection, in both current callers). If the
    actor is *already* within range, returns a real ParsedAction with an
    explicitly empty `params["path"]` instead of None - a different,
    deliberate outcome from "couldn't get there": turn_engine._resolve_move
    treats an explicitly-empty path as a genuine no-op (nothing to move,
    not a malformed declaration), while treating a *missing* path key as
    the real error it's always been. Live-found: a companion saying "I
    move toward my ally to back them up" while already standing right next
    to them used to hard-fail with a confusing "requires params['path']"
    error instead of just... already being there."""
    if game_state.battle_map is None:
        return None
    if distance_feet(actor.position, target.position) <= 5:
        return ParsedAction(
            actor=actor.id,
            verb=verb,
            raw_text=f"{actor.name} is already close enough to {target.name}.",
            params={"path": []},
        )
    base_speed = effective_speed(actor)
    total_budget = base_speed * 2 if verb == "dash" else base_speed
    remaining_budget = max(0, total_budget - actor.movement_used_feet)
    hostile_squares, ally_squares = occupied_squares_by_side(game_state, actor)
    path = approach_path(
        actor.position,
        target.position,
        remaining_budget,
        5,
        game_state.battle_map.terrain,
        hostile_squares,
        ally_squares,
    )
    if not path:
        return None
    return ParsedAction(
        actor=actor.id,
        verb=verb,
        raw_text=f"{actor.name} moves toward {target.name}.",
        params={"path": [{"x": p.x, "y": p.y} for p in path]},
    )


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
        if distance > spell_range_feet(spell):
            continue
        return ParsedAction(
            actor=actor.id,
            verb="cast_spell",
            target=target.id,
            item_or_spell=spell_ref["name"],
            raw_text=f"{actor.name} casts {spell_ref['name']} at {target.name}.",
        )
    return None


def _action_against(
    game_state: GameState, actor: Character, target: Character, srd: SrdIndex
) -> tuple[ParsedAction, bool]:
    """What `actor` does about `target` this turn, and whether it is
    *blocked*: had movement budget left, wasn't already in range, and still
    found no way to close the distance (a hostile body or wall in the way -
    approach_path is greedy, not real pathfinding). The action is the same
    either way (an attack that turn_engine will reject as out of range when
    blocked - an honest rejection beats silently doing nothing); `blocked`
    lets choose_monster_action try someone else instead of repeating a doomed
    attack forever. Having already spent this turn's movement is NOT blocked:
    nothing is in the way, there's just no distance left to cover."""
    spell_action = _choose_innate_spell(actor, target, srd)
    if spell_action is not None:
        return spell_action, False

    range_feet = _monster_range_feet(actor)
    attack = ParsedAction(
        actor=actor.id,
        verb="attack",
        target=target.id,
        raw_text=f"{actor.name} attacks {target.name}.",
    )

    if (
        distance_feet(actor.position, target.position) <= range_feet
        or game_state.battle_map is None
    ):
        return attack, False

    hostile_squares, ally_squares = occupied_squares_by_side(game_state, actor)
    # Move no longer ends the turn (found live), so this can now be called a
    # second time for the same actor within one real turn, after an earlier
    # partial move already spent some of its budget - use what's actually
    # left, not a fresh full speed, or a monster with exactly enough speed
    # to close half the gap would waste an attempt on a now-unaffordable move.
    remaining_speed = max(0, effective_speed(actor) - actor.movement_used_feet)
    path = approach_path(
        actor.position,
        target.position,
        remaining_speed,
        range_feet,
        game_state.battle_map.terrain,
        hostile_squares,
        ally_squares,
    )
    if not path:
        if remaining_speed <= 0:
            # Issue #106: the monster already spent its movement (or can't move
            # at all) and nothing is in reach. An attack here is certain to be
            # rejected as out of range - three rejections in a row tripped the
            # autoplay breaker and every approach turn was narrated as "hesitates,
            # unable to settle on an action". Ending the turn is just what it did.
            return (
                ParsedAction(
                    actor=actor.id,
                    verb="end_turn",
                    raw_text=f"{actor.name} has no movement left and nothing in reach.",
                ),
                False,
            )
        # Can't get any closer (blocked) - attack anyway: turn_engine's own
        # range check gives an honest rejection rather than this heuristic
        # silently doing nothing, and `blocked` lets the caller try someone else.
        return attack, True

    return (
        ParsedAction(
            actor=actor.id,
            verb="move",
            target=target.id,  # not used by _resolve_move - documents intent
            params={"path": [{"x": p.x, "y": p.y} for p in path]},
            raw_text=f"{actor.name} closes in on {target.name}.",
        ),
        False,
    )


def choose_monster_action(game_state: GameState, actor: Character) -> ParsedAction:
    living_targets = [
        c
        for c in game_state.characters.values()
        if c.is_pc != actor.is_pc and not c.is_dead and c.id != actor.id
    ]
    if not living_targets:
        return ParsedAction(
            actor=actor.id, verb="end_turn", raw_text=f"{actor.name} has no target left."
        )

    # Issue #66: an unconscious-but-alive party member (down at 0 HP, or put
    # to sleep) is still "alive" for victory/defeat, but a monster that keeps
    # swinging at whoever's nearest can pile onto one downed character -
    # every hit against an unconscious target is an auto-crit plus 2
    # automatic death-save failures (Phase 9C), so 1-2 more hits finish them
    # while the rest of the party stands untouched. Prefer anyone still
    # conscious; fall back to the downed once nobody conscious is left...
    #
    # ...or once the conscious ones can't be reached. Found by the full
    # suite hanging intermittently: with a downed-but-stable human (skipped)
    # and the only conscious party member stuck behind the human's body in a
    # corridor, a monster that refuses to touch the downed one has nothing it
    # can actually do - every round is a doomed attack and a forced end_turn,
    # nobody else acts, and the fight never ends (a livelock that blocks the
    # server's event loop). A monster that has no route to its preferred
    # target therefore moves on to the next pool instead.
    conscious = [c for c in living_targets if not has_condition(c, "unconscious")]
    downed = [c for c in living_targets if has_condition(c, "unconscious")]

    def nearest(pool: list[Character]) -> Character:
        return min(pool, key=lambda c: (chebyshev_distance(actor.position, c.position), c.id))

    srd = load_srd()
    first_choice: ParsedAction | None = None
    for pool in (conscious, downed):
        if not pool:
            continue
        action, blocked = _action_against(game_state, actor, nearest(pool), srd)
        if not blocked:
            return action
        first_choice = first_choice or action
    # Every pool was blocked: the preferred target's (rejected) attack is as
    # honest an answer as this heuristic has - turn_engine's breaker handles
    # the rest, exactly as it did before pools existed.
    assert first_choice is not None  # living_targets was non-empty
    return first_choice

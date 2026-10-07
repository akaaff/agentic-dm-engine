"""Adding creatures to, and removing them from, an encounter that is already running
(issue #56, phase A) - the mechanism every summoning spell needs.

Until now a fight's cast was fixed the moment `encounter.build_encounter_state` rolled
initiative: `GameState.turn_order` was never appended to, and `_advance_turn_skipping_dead`
only cycled the list it was given. A summoned creature has to join (and later leave)
mid-combat without breaking `current_turn`, which is an index into that list - so the
index arithmetic lives here as small pure helpers with hand-computed tests, not inline in
a spell's resolver where an off-by-one would be hard to see.

A summoned creature is an ordinary `Character` plus three facts: `summoned_by` (the
summoner's id), `summon_spell` (so ending the spell's concentration can find it) and
`is_pc` copied from the summoner - `is_pc` is the engine's "which side" flag (friendly-fire
guard, hostile checks, monster_ai targeting), so a conjured wolf on the party's side has
`is_pc=True`. `summoned_by` is what keeps it from being treated as a *party member*
(no death saves, driven by monster_ai rather than a player, not counted for defeat) -
see `rules.is_party_member`.

Simplifications, deliberate: a summon acts immediately after its summoner each round
instead of rolling its own initiative (the SRD's per-summon roll adds real bookkeeping
for little gain); and a dismissed summon is deleted from `characters` rather than kept as
a corpse, because it didn't die - it was unsummoned (the event log keeps its id)."""

from __future__ import annotations

from src.engine.events import Event
from src.engine.position import Position, chebyshev_distance
from src.engine.state import Character, GameState


class SummonError(ValueError):
    pass


def add_combatant(state: GameState, summoned: Character, summoner: Character, spell: str) -> None:
    """Puts `summoned` into the fight, acting right after `summoner` (after any of that
    summoner's earlier summons, so a batch keeps the order it was added in). Stamps the
    summon fields; the caller has already built the creature and chosen its square."""
    if summoned.id in state.characters:
        raise SummonError(f"{summoned.id} is already in this encounter")
    if summoner.id not in state.turn_order:
        raise SummonError(f"{summoner.id} is not in the turn order")
    summoned.summoned_by = summoner.id
    summoned.summon_spell = spell
    summoned.is_pc = summoner.is_pc
    state.characters[summoned.id] = summoned

    index = state.turn_order.index(summoner.id) + 1
    while (
        index < len(state.turn_order)
        and state.characters[state.turn_order[index]].summoned_by == summoner.id
    ):
        index += 1
    state.turn_order.insert(index, summoned.id)
    if index <= state.current_turn:
        state.current_turn += 1  # the current actor moved one slot later in the list
    state.events.append(
        Event(
            round=state.round,
            turn_index=state.current_turn,
            actor=summoner.id,
            type="summoned",
            payload={"summoned": summoned.id, "name": summoned.name, "spell": spell},
        )
    )


def remove_combatant(state: GameState, character_id: str) -> None:
    """Takes a creature out of the fight entirely (not the same as dying - a dead monster
    stays in the turn order and is skipped). Keeps `current_turn` pointing at the same
    actor, or - if the removed creature was the one acting - at the actor just before it,
    so the next advance lands on whoever followed it."""
    if character_id not in state.turn_order:
        state.characters.pop(character_id, None)
        return
    removed = state.turn_order.index(character_id)
    state.turn_order.pop(removed)
    state.characters.pop(character_id, None)
    if not state.turn_order:
        state.current_turn = 0
    elif removed < state.current_turn:
        state.current_turn -= 1
    elif removed == state.current_turn:
        if removed == 0:
            # No one precedes it: park on the last slot and give the round back, so the
            # advance that follows wraps to slot 0 without counting a round twice.
            state.current_turn = len(state.turn_order) - 1
            state.round -= 1
        else:
            state.current_turn = removed - 1


def dismiss_summons(state: GameState, summoner_id: str, spell: str) -> list[str]:
    """Removes every creature `summoner_id` conjured with `spell` - what losing
    concentration on the spell does. Returns the removed ids and logs one
    `summon_ended` event for each."""
    ids = [
        c.id
        for c in state.characters.values()
        if c.summoned_by == summoner_id and c.summon_spell == spell
    ]
    for character_id in ids:
        name = state.characters[character_id].name
        remove_combatant(state, character_id)
        state.events.append(
            Event(
                round=state.round,
                turn_index=state.current_turn,
                actor=summoner_id,
                type="summon_ended",
                payload={"summoned": character_id, "name": name, "spell": spell},
            )
        )
    return ids


def find_open_square(state: GameState, near: Position, taken: set[tuple[int, int]]) -> Position:
    """The nearest floor-like square to `near` that no living creature stands on and isn't
    in `taken` (squares already promised to other creatures in the same cast), searched in
    growing rings so a summon appears next to its summoner when there's room. Raises
    SummonError if the map has no free square at all."""
    battle_map = state.battle_map
    if battle_map is None:
        raise SummonError("this scene has no battle map to place a creature on")
    occupied = {(c.position.x, c.position.y) for c in state.characters.values() if not c.is_dead}
    best: tuple[int, int, int] | None = None
    for y in range(battle_map.height):
        for x in range(battle_map.width):
            if battle_map.terrain[y][x] == "wall" or (x, y) in occupied or (x, y) in taken:
                continue
            key = (chebyshev_distance(near, Position(x=x, y=y)), y, x)
            if best is None or key < best:
                best = key
    if best is None:
        raise SummonError("there is no free square to conjure a creature onto")
    return Position(x=best[2], y=best[1])

from src.engine.conditions import apply_condition
from src.engine.monster_ai import choose_monster_action
from src.engine.position import BattleMap, Position
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _make_character(
    char_id: str,
    *,
    is_pc: bool,
    position: Position,
    is_dead: bool = False,
    monster_index: str | None = None,
    speed: int = 30,
) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        is_dead=is_dead,
        hp=10,
        max_hp=10,
        ac=15,
        position=position,
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=speed,
        race="Human",
        class_="Fighter",
        background="Acolyte",
        monster_index=monster_index,
    )


def _make_state(characters: list[Character], battle_map: BattleMap | None = None) -> GameState:
    return GameState(
        encounter_id="test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
        battle_map=battle_map,
    )


def _open_map(width: int, height: int) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def test_attacks_the_nearest_living_pc() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    near_pc = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    far_pc = _make_character("elrond", is_pc=True, position=Position(x=5, y=5))
    state = _make_state([goblin, near_pc, far_pc])

    action = choose_monster_action(state, goblin)

    assert action.verb == "attack"
    assert action.target == "thorin"
    assert action.actor == "goblin_1"


def test_ties_broken_by_character_id() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    pc_b = _make_character("pc_b", is_pc=True, position=Position(x=1, y=0))
    pc_a = _make_character("pc_a", is_pc=True, position=Position(x=0, y=1))
    state = _make_state([goblin, pc_b, pc_a])

    action = choose_monster_action(state, goblin)

    assert action.target == "pc_a"


def test_ignores_dead_pcs() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    dead_pc = _make_character("thorin", is_pc=True, position=Position(x=1, y=0), is_dead=True)
    alive_pc = _make_character("elrond", is_pc=True, position=Position(x=5, y=5))
    state = _make_state([goblin, dead_pc, alive_pc])

    action = choose_monster_action(state, goblin)

    assert action.target == "elrond"


def test_ends_turn_when_no_living_targets_remain() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    dead_pc = _make_character("thorin", is_pc=True, position=Position(x=1, y=0), is_dead=True)
    state = _make_state([goblin, dead_pc])

    action = choose_monster_action(state, goblin)

    assert action.verb == "end_turn"


def test_attacks_directly_when_already_in_weapon_range() -> None:
    # goblin (SRD default action: Scimitar, melee 5ft) 5ft from thorin -
    # already in range, no need to move first.
    goblin = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    state = _make_state([goblin, thorin], battle_map=_open_map(10, 10))

    action = choose_monster_action(state, goblin)

    assert action.verb == "attack"
    assert action.target == "thorin"


def test_moves_toward_target_when_out_of_range() -> None:
    # Caught live: monster_ai used to always attack regardless of distance,
    # which broke instantly once turn_engine started enforcing attack
    # range (every out-of-range monster would fail forever, with no
    # circuit breaker to unstick it - see CLAUDE.md). 6 squares (30ft) away,
    # speed 30 - should move, not attack, and land adjacent (5ft) after.
    goblin = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=6, y=0))
    state = _make_state([goblin, thorin], battle_map=_open_map(10, 10))

    action = choose_monster_action(state, goblin)

    assert action.verb == "move"
    assert action.target == "thorin"
    assert action.params["path"]
    last_step = action.params["path"][-1]
    assert last_step["x"] == 5  # one square short of thorin - now 5ft/adjacent


def test_moves_then_attacks_within_the_same_real_turn_once_move_no_longer_ends_it() -> None:
    # Live-reported: a monster that starts out of range used to take two
    # real turns to close distance and attack (move used to end the turn -
    # see turn_engine.resolve_action's dispatch). Same setup as
    # test_moves_toward_target_when_out_of_range, but now resolves the move
    # for real and confirms a second choose_monster_action call for the
    # *same* actor (turn_order/current_turn unchanged - exactly what the
    # live autoplay loop does) returns attack, not another move.
    goblin = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=6, y=0))
    state = _make_state([goblin, thorin], battle_map=_open_map(10, 10))

    first_action = choose_monster_action(state, state.characters["goblin_1"])
    assert first_action.verb == "move"
    resolve_action(state, first_action, _FixedRandom([]))  # type: ignore[arg-type]

    # The move alone didn't end the goblin's turn.
    assert state.turn_order[state.current_turn] == "goblin_1"
    assert state.characters["goblin_1"].position == Position(x=5, y=0)

    second_action = choose_monster_action(state, state.characters["goblin_1"])
    assert second_action.verb == "attack"
    assert second_action.target == "thorin"


def test_move_path_respects_speed_budget() -> None:
    # 12 squares away (60ft), speed 30 (6 squares) - can't reach range 5ft
    # in one turn, but should still close as much distance as it can afford
    # rather than not moving at all.
    goblin = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin", speed=30
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=12, y=0))
    state = _make_state([goblin, thorin], battle_map=_open_map(20, 5))

    action = choose_monster_action(state, goblin)

    assert action.verb == "move"
    assert len(action.params["path"]) == 6  # 30ft budget / 5ft per square
    last_step = action.params["path"][-1]
    assert last_step["x"] == 6


def test_move_path_avoids_a_square_already_occupied_by_another_character() -> None:
    # Caught live: two monsters converging on the same target from
    # different directions could independently pick the identical "best"
    # square and end up stacked on top of each other - invisible as two
    # tokens on the combat grid. goblin_2 already sits at (5,0), directly on
    # goblin_1's straight-line path toward thorin at (6,0) - goblin_1 must
    # route around it (e.g. via (5,1)) rather than stepping onto it.
    goblin_1 = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    goblin_2 = _make_character(
        "goblin_2", is_pc=False, position=Position(x=5, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=6, y=0))
    state = _make_state([goblin_1, goblin_2, thorin], battle_map=_open_map(10, 10))

    action = choose_monster_action(state, goblin_1)

    assert action.verb == "move"
    first_step = action.params["path"][0]
    assert (first_step["x"], first_step["y"]) != (5, 0)
    assert all((p["x"], p["y"]) != (5, 0) for p in action.params["path"])


def test_move_path_passes_through_an_ally_occupied_square_but_never_ends_there() -> None:
    # Live-requested: real SRD lets you move THROUGH an ally's space, just
    # not end your move standing on it - unlike a hostile's square, which
    # stays genuinely impassable (see the test above). A 1-row corridor
    # forces the path directly through goblin_2 (an ally - both are
    # monsters) at (3,0); there's no detour available (no second row to
    # route around it through), so this also proves the path doesn't just
    # get stuck/blocked the way a hostile-occupied square correctly does.
    goblin_1 = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    goblin_2 = _make_character(
        "goblin_2", is_pc=False, position=Position(x=3, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=6, y=0))
    state = _make_state([goblin_1, goblin_2, thorin], battle_map=_open_map(7, 1))

    action = choose_monster_action(state, goblin_1)

    assert action.verb == "move"
    path = [(p["x"], p["y"]) for p in action.params["path"]]
    assert (3, 0) in path  # genuinely passed through the ally's square
    assert path[-1] != (3, 0)  # but didn't end the move standing on it


def test_casts_an_available_innate_spell_instead_of_attacking_when_in_range() -> None:
    # Green-hag (CR3, Innate Spellcasting: Vicious Mockery at-will, 60ft
    # range) - issue #22's whole point: a spellcasting monster shouldn't
    # always walk up and swing regardless of its stat block.
    hag = _make_character(
        "hag_1", is_pc=False, position=Position(x=0, y=0), monster_index="green-hag"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=10, y=0))  # 50ft
    state = _make_state([hag, thorin], battle_map=_open_map(20, 20))

    action = choose_monster_action(state, hag)

    assert action.verb == "cast_spell"
    assert action.item_or_spell == "Vicious Mockery"
    assert action.target == "thorin"


def test_falls_back_to_closing_distance_when_no_innate_spell_is_in_range() -> None:
    # Same hag, but the target is beyond Vicious Mockery's 60ft range (and
    # far beyond melee too) - closes distance instead of casting or
    # attacking blindly, same as a non-caster monster would.
    hag = _make_character(
        "hag_1", is_pc=False, position=Position(x=0, y=0), monster_index="green-hag"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=20, y=0))  # 100ft
    state = _make_state([hag, thorin], battle_map=_open_map(30, 30))

    action = choose_monster_action(state, hag)

    assert action.verb == "move"


def test_falls_back_to_melee_when_the_only_per_day_innate_spell_is_exhausted() -> None:
    # Magma-mephit's only innate spell (Heat Metal) is 1/day - with 0 uses
    # left, it should fall back to a plain attack instead of erroring or
    # trying to cast anyway.
    mephit = _make_character(
        "mephit_1", is_pc=False, position=Position(x=0, y=0), monster_index="magma-mephit"
    )
    mephit.innate_spell_uses_remaining = {"heat-metal": 0}
    thorin = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    state = _make_state([mephit, thorin], battle_map=_open_map(10, 10))

    action = choose_monster_action(state, mephit)

    assert action.verb == "attack"


def test_attacks_anyway_when_movement_is_impossible() -> None:
    # No battle_map at all (e.g. an ad-hoc test GameState) - can't compute
    # a path, so fall back to the pre-existing "just attack" behavior
    # rather than crash. turn_engine's own range check is the honest final
    # word on whether it actually lands.
    goblin = _make_character(
        "goblin_1", is_pc=False, position=Position(x=0, y=0), monster_index="goblin"
    )
    thorin = _make_character("thorin", is_pc=True, position=Position(x=6, y=0))
    state = _make_state([goblin, thorin])  # no battle_map

    action = choose_monster_action(state, goblin)

    assert action.verb == "attack"


def _down(character: Character, *, source: str = "0 HP") -> None:
    apply_condition(character, Condition(name="unconscious", source=source))


def test_prefers_a_conscious_target_over_a_nearer_unconscious_one() -> None:
    # Issue #66: the downed PC is the nearest (5ft vs 25ft), but a monster
    # shouldn't keep finishing off one downed character while a conscious
    # one is still standing.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    downed = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    standing = _make_character("elrond", is_pc=True, position=Position(x=5, y=0))
    _down(downed)
    state = _make_state([goblin, downed, standing])

    action = choose_monster_action(state, goblin)

    assert action.target == "elrond"


def test_falls_back_to_the_nearest_downed_pc_once_nobody_is_conscious() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    near_downed = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    far_downed = _make_character("elrond", is_pc=True, position=Position(x=5, y=0))
    _down(near_downed)
    _down(far_downed)
    state = _make_state([goblin, near_downed, far_downed])

    action = choose_monster_action(state, goblin)

    assert action.verb == "attack"
    assert action.target == "thorin"


def test_a_sleeping_pc_counts_as_unconscious_for_targeting_too() -> None:
    # Same "unconscious" condition name, different source (Sleep, not 0 HP) -
    # the monster still shouldn't prefer helpless over conscious.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    sleeper = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    awake = _make_character("elrond", is_pc=True, position=Position(x=4, y=0))
    _down(sleeper, source="sleep")
    state = _make_state([goblin, sleeper, awake])

    action = choose_monster_action(state, goblin)

    assert action.target == "elrond"


def test_a_conscious_but_dead_free_target_set_still_ends_the_turn() -> None:
    # Regression guard for the unchanged "no living target" branch: dead PCs
    # are filtered before the conscious/unconscious split ever happens.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    dead = _make_character("thorin", is_pc=True, position=Position(x=1, y=0), is_dead=True)
    state = _make_state([goblin, dead])

    action = choose_monster_action(state, goblin)

    assert action.verb == "end_turn"


def test_falls_back_to_the_downed_target_when_the_conscious_one_is_unreachable() -> None:
    # The livelock the "prefer conscious" rule (issue #66) introduced and the
    # full suite caught intermittently: a 1-wide corridor, a goblin, the
    # downed (stable, so skipped) human right in front of it, and the one
    # conscious companion stuck behind the human's body. approach_path treats
    # a hostile-occupied square as impassable and is greedy, so the goblin
    # has no way to close on the companion - its doomed out-of-range attack
    # just trips the forced-end_turn breaker every round, and since nobody
    # else acts the fight never ends (the autoplay loop spins forever, which
    # freezes the server). Before the rule it would simply have finished the
    # adjacent downed human. A target the monster genuinely can't approach
    # must not stop it from acting on one it can.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=0))
    downed = _make_character("thorin", is_pc=True, position=Position(x=1, y=0))
    companion = _make_character("grom", is_pc=True, position=Position(x=3, y=0))
    _down(downed)
    state = _make_state([goblin, downed, companion], battle_map=_open_map(5, 1))

    action = choose_monster_action(state, goblin)

    assert action.verb == "attack"
    assert action.target == "thorin"


def test_still_pursues_a_reachable_conscious_target_rather_than_the_adjacent_downed_one() -> None:
    # Same arrangement on an open map: there IS a way around the body, so the
    # monster should keep pursuing the conscious target (issue #66's intent)
    # rather than stopping to finish the downed one.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=1))
    downed = _make_character("thorin", is_pc=True, position=Position(x=1, y=1))
    companion = _make_character("grom", is_pc=True, position=Position(x=4, y=1))
    _down(downed)
    state = _make_state([goblin, downed, companion], battle_map=_open_map(5, 3))

    action = choose_monster_action(state, goblin)

    assert action.verb == "move"
    path = action.params["path"]
    assert path, "expected a real path toward the conscious companion"
    assert (path[0]["x"], path[0]["y"]) != (1, 1), "must not path through the downed body"


def test_a_monster_that_has_spent_its_movement_is_not_treated_as_blocked() -> None:
    # No movement left this turn is not the same as "no route exists": the
    # fallback to a downed target is only for a monster that had budget and
    # still found no way. One that already moved keeps its conscious target
    # rather than turning on the downed one - and, with nothing in reach and no
    # movement left, simply ends its turn (issue #106) instead of declaring an
    # attack that is certain to be rejected.
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=1))
    downed = _make_character("thorin", is_pc=True, position=Position(x=1, y=1))
    companion = _make_character("grom", is_pc=True, position=Position(x=4, y=1))
    goblin.movement_used_feet = goblin.speed
    _down(downed)
    state = _make_state([goblin, downed, companion], battle_map=_open_map(5, 3))

    action = choose_monster_action(state, goblin)

    assert action.verb == "end_turn"
    assert action.target is None  # not the downed neighbour, not an attack on grom


def test_ends_the_turn_when_the_movement_is_spent_and_nothing_is_in_reach() -> None:
    # Issue #106: a goblin that already moved its full speed toward a target
    # 30ft away used to declare an attack that turn_engine rejected as out of
    # range - three rejections in a row tripped the autoplay breaker and the
    # turn was narrated as "hesitates, unable to settle on an action".
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=1))
    thorin = _make_character("thorin", is_pc=True, position=Position(x=7, y=1))
    goblin.movement_used_feet = goblin.speed
    state = _make_state([goblin, thorin], battle_map=_open_map(10, 3))

    assert choose_monster_action(state, goblin).verb == "end_turn"


def test_still_attacks_a_target_in_reach_after_the_movement_is_spent() -> None:
    goblin = _make_character("goblin_1", is_pc=False, position=Position(x=0, y=1))
    thorin = _make_character("thorin", is_pc=True, position=Position(x=1, y=1))
    goblin.movement_used_feet = goblin.speed
    state = _make_state([goblin, thorin], battle_map=_open_map(10, 3))

    action = choose_monster_action(state, goblin)

    assert (action.verb, action.target) == ("attack", "thorin")


def test_a_second_attacker_sidesteps_an_ally_instead_of_stopping_short() -> None:
    """Found with summoned wolves: two creatures converging on one target along the same
    row - the second reached range on its ally's square, was trimmed back out of range
    and never attacked. It should step onto a free square that is still in range."""
    from src.engine.monster_ai import approach_path

    terrain = [["floor"] * 8 for _ in range(3)]
    path = approach_path(
        start=Position(x=1, y=0),
        target=Position(x=6, y=0),
        speed=50,
        range_feet=5,
        terrain=terrain,  # type: ignore[arg-type]
        blocked={(6, 0)},
        ally_occupied={(5, 0)},
    )
    assert path
    end = path[-1]
    assert (end.x, end.y) != (5, 0)
    assert max(abs(end.x - 6), abs(end.y - 0)) == 1  # adjacent to the target


def test_no_sidestep_is_invented_when_the_corridor_has_no_free_square() -> None:
    from src.engine.monster_ai import approach_path

    terrain = [["floor"] * 8]
    path = approach_path(
        start=Position(x=1, y=0),
        target=Position(x=6, y=0),
        speed=50,
        range_feet=5,
        terrain=terrain,  # type: ignore[arg-type]
        blocked={(6, 0)},
        ally_occupied={(5, 0)},
    )
    assert path[-1] == Position(x=4, y=0)  # trimmed, as before

from src.engine.monster_ai import choose_monster_action
from src.engine.position import BattleMap, Position
from src.engine.state import Character, GameState


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

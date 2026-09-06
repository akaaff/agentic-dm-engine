from src.engine.monster_ai import choose_monster_action
from src.engine.position import Position
from src.engine.state import Character, GameState


def _make_character(
    char_id: str, *, is_pc: bool, position: Position, is_dead: bool = False
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
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _make_state(characters: list[Character]) -> GameState:
    return GameState(
        encounter_id="test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
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

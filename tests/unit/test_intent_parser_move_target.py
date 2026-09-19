"""_resolve_move_target (graph/nodes/intent_parser.py) is a pure,
deterministic post-processing step - fully testable offline, unlike the
node's own LLM-calling behavior (see tests/llm/test_intent_parser.py's
live golden-set case for the real multi-square-move round trip this
exists to patch up).
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.monster_ai import approach_path
from src.engine.position import BattleMap, Position
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import _resolve_move_target


def _actor(**overrides: object) -> Character:
    defaults: dict[str, object] = {
        "id": "thorin",
        "name": "Thorin",
        "race": "Human",
        "class_": "Fighter",
        "background": "Acolyte",
        "is_pc": True,
        "hp": 12,
        "max_hp": 12,
        "ac": 16,
        "position": Position(x=0, y=0),
        "stats": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        "speed": 30,
        "proficiency_bonus": 2,
    }
    defaults.update(overrides)
    return Character.model_validate(defaults)


def _wolf(position: Position) -> Character:
    return Character(
        id="wolf_1",
        name="Wolf 1",
        race="beast",
        class_="Monster",
        monster_index="wolf",
        background="",
        is_pc=False,
        hp=11,
        max_hp=11,
        ac=13,
        position=position,
        stats={"STR": 12, "DEX": 15, "CON": 12, "INT": 3, "WIS": 12, "CHA": 6},
        speed=40,
        proficiency_bonus=2,
    )


def _open_battle_map(width: int = 10, height: int = 10) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def _game_state(actor: Character, wolf: Character) -> GameState:
    return GameState(
        encounter_id="move_target_test",
        characters={actor.id: actor, wolf.id: wolf},
        turn_order=[actor.id, wolf.id],
        current_turn=0,
        round=1,
        battle_map=_open_battle_map(),
    )


def test_computes_a_real_multi_square_path_toward_a_named_target() -> None:
    actor = _actor()
    wolf = _wolf(Position(x=3, y=0))  # 3 squares (15ft) away, well within 30ft speed
    game_state = _game_state(actor, wolf)
    action = ParsedAction(
        actor="thorin", verb="move", target="wolf_1", raw_text="I move to the wolf"
    )

    resolved = _resolve_move_target(action, game_state)

    path = resolved.params["path"]
    assert path, "expected a computed path"
    assert game_state.battle_map is not None
    # Matches approach_path's own output exactly for the identical inputs -
    # this function is a thin wrapper, not a second implementation.
    expected = approach_path(
        actor.position, wolf.position, 30, 5, game_state.battle_map.terrain, set()
    )
    assert path == [{"x": p.x, "y": p.y} for p in expected]
    # Stops adjacent (5ft), not on top of the wolf.
    last = path[-1]
    assert abs(last["x"] - wolf.position.x) <= 1 and abs(last["y"] - wolf.position.y) <= 1


def test_dash_doubles_the_speed_budget() -> None:
    # A wolf far enough that a plain 30ft move can't reach adjacent, but a
    # doubled 60ft dash budget can.
    actor = _actor()
    wolf = _wolf(Position(x=9, y=0))
    game_state = _game_state(actor, wolf)
    move_action = ParsedAction(actor="thorin", verb="move", target="wolf_1", raw_text="x")
    dash_action = ParsedAction(actor="thorin", verb="dash", target="wolf_1", raw_text="x")

    move_result = _resolve_move_target(move_action, game_state)
    dash_result = _resolve_move_target(dash_action, game_state)

    move_path = move_result.params.get("path", [])
    dash_path = dash_result.params.get("path", [])
    assert len(dash_path) > len(move_path)
    dash_last = dash_path[-1]
    assert abs(dash_last["x"] - wolf.position.x) <= 1


def test_a_named_target_overrides_a_model_supplied_path_even_if_present() -> None:
    # Live-found: the model set *both* a target AND its own (wrong,
    # non-adjacent) single-entry path in the same response for the exact
    # same utterance - deferring to the already-present path here would
    # silently reproduce the bug this function exists to fix, since exact
    # square math is precisely what the model keeps getting wrong. target
    # is the reliable signal and always wins once it's set.
    actor = _actor()
    wolf = _wolf(Position(x=3, y=0))
    game_state = _game_state(actor, wolf)
    action = ParsedAction(
        actor="thorin",
        verb="move",
        target="wolf_1",
        params={"path": [{"x": 2, "y": 0}]},  # not actually adjacent - a bogus model guess
        raw_text="I move to the wolf",
    )

    resolved = _resolve_move_target(action, game_state)

    assert resolved.params["path"] != [{"x": 2, "y": 0}]
    last = resolved.params["path"][-1]
    assert abs(last["x"] - wolf.position.x) <= 1 and abs(last["y"] - wolf.position.y) <= 1


def test_a_bare_destination_square_with_no_target_is_left_untouched() -> None:
    # No character named at all - a genuinely precise adjacent-square move,
    # unrelated to the target-approach case this function handles.
    actor = _actor()
    wolf = _wolf(Position(x=3, y=0))
    game_state = _game_state(actor, wolf)
    action = ParsedAction(
        actor="thorin",
        verb="move",
        params={"path": [{"x": 1, "y": 0}]},
        raw_text="I step east",
    )

    resolved = _resolve_move_target(action, game_state)

    assert resolved is action


def test_ignores_a_non_move_verb() -> None:
    actor = _actor()
    wolf = _wolf(Position(x=3, y=0))
    game_state = _game_state(actor, wolf)
    action = ParsedAction(actor="thorin", verb="attack", target="wolf_1", raw_text="I attack")

    resolved = _resolve_move_target(action, game_state)

    assert resolved is action


def test_unknown_target_is_left_for_turn_engines_own_error() -> None:
    actor = _actor()
    wolf = _wolf(Position(x=3, y=0))
    game_state = _game_state(actor, wolf)
    action = ParsedAction(actor="thorin", verb="move", target="not-a-real-character", raw_text="x")

    resolved = _resolve_move_target(action, game_state)

    assert resolved is action

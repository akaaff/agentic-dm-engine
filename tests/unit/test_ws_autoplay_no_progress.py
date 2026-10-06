"""A CI run hung for 30 minutes (and a local run for 10+, spinning at 100% CPU
while memory grew) inside the server's autoplay loop. The loop's existing
circuit breaker only counts *rejected* actions; a successful action that never
ends the turn (every iteration appends an event, so it looks like progress)
spun forever. `MAX_ACTIONS_PER_TURN` now forces an end_turn, and gives up
entirely if even that doesn't move the turn pointer."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.api.ws import session as session_module
from src.api.ws.session import MAX_ACTIONS_PER_TURN, create_session, reset_sessions
from src.engine.actions import ParsedAction
from src.engine.position import BattleMap, Position
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def _pc(cid: str, name: str, x: int, *, companion: bool) -> Character:
    return Character(
        id=cid,
        name=name,
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        is_companion=companion,
        hp=12,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=x, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
    )


def _kobold() -> Character:
    return Character(
        id="kobold_1",
        name="Kobold 1",
        race="humanoid",
        class_="Monster",
        monster_index="kobold",
        background="",
        is_pc=False,
        hp=5,
        max_hp=5,
        ac=12,
        level=1,
        position=Position(x=8, y=8),
        stats={"STR": 9, "DEX": 15, "CON": 9, "INT": 8, "WIS": 7, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _game() -> GameState:
    companion = _pc("companion_a", "Ava", 1, companion=True)
    human = _pc("oen", "Oen", 0, companion=False)
    return GameState(
        encounter_id="no_progress_test",
        characters={c.id: c for c in (companion, human, _kobold())},
        turn_order=[companion.id, human.id, "kobold_1"],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=12, height=12, terrain=[["floor"] * 12 for _ in range(12)], spawn_points={}
        ),
    )


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    new_events = state["game_state"].events[state["events_before"] :]
    return {"narration": "[stub]" if new_events else ""}


def _stub_player_agent_noop_move(state: GraphState) -> dict[str, Any]:
    # A companion that "succeeds" forever: a zero-length move spends nothing,
    # emits a move event, and (a move) never ends the turn.
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {
        "parsed_action": ParsedAction(
            actor=actor_id, verb="move", params={"path": []}, raw_text="shuffles in place"
        )
    }


def test_a_successful_action_that_never_ends_the_turn_is_cut_off_by_a_forced_end_turn() -> None:
    game_state = _game()
    session = create_session(
        "no-progress-1",
        game_state,
        human_character_ids={"tok": "oen"},
        graph=build_graph(
            narrator_fn=_stub_narrator,
            player_agent_fn=_stub_player_agent_noop_move,
            scene_image_fn=_stub_scene_image,
        ),
    )

    asyncio.run(session_module._autoplay_non_human_turns(session))

    # The companion shuffled MAX_ACTIONS_PER_TURN times, then the guard forced
    # its turn to end, so the loop reached the human instead of spinning.
    assert session.game_state.turn_order[session.game_state.current_turn] == "oen"
    moves = [e for e in session.game_state.events if e.type == "move"]
    assert len(moves) == MAX_ACTIONS_PER_TURN


class _FrozenGraph:
    """A graph that "resolves" every action without changing anything - what
    a deeper bug would look like. The guard must still terminate."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, graph_input: GraphState) -> dict[str, Any]:
        self.calls += 1
        return {
            "game_state": graph_input["game_state"],
            "narration": "",
            "scene_image_url": None,
        }


def test_it_gives_up_and_logs_if_even_the_forced_end_turn_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(session_module, "log_event", lambda **fields: logged.append(fields))
    frozen = _FrozenGraph()
    session = create_session(
        "no-progress-2",
        _game(),
        human_character_ids={"tok": "oen"},
        graph=frozen,  # type: ignore[arg-type]
    )

    asyncio.run(session_module._autoplay_non_human_turns(session))

    assert frozen.calls == 2 * MAX_ACTIONS_PER_TURN
    (event,) = logged
    assert event["kind"] == "backend_error"
    assert event["source"] == "autoplay_no_progress"
    assert event["actor"] == "companion_a"
    assert event["last_action"]["verb"] == "end_turn"


def test_ordinary_turns_are_unaffected_by_the_guard() -> None:
    # A companion that simply ends its turn each time (the normal shape) gets
    # nowhere near the limit.
    def end_turn(state: GraphState) -> dict[str, Any]:
        if state["parsed_action"] is not None or state["raw_text"]:
            return {}
        gs = state["game_state"]
        actor_id = gs.turn_order[gs.current_turn]
        return {"parsed_action": ParsedAction(actor=actor_id, verb="end_turn", raw_text="x")}

    session = create_session(
        "no-progress-3",
        _game(),
        human_character_ids={"tok": "oen"},
        graph=build_graph(
            narrator_fn=_stub_narrator, player_agent_fn=end_turn, scene_image_fn=_stub_scene_image
        ),
    )
    asyncio.run(session_module._autoplay_non_human_turns(session))
    assert session.game_state.turn_order[session.game_state.current_turn] == "oen"

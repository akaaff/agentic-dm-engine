"""Live-found (a real session's own event log): a companion whose free-text
turn fails to parse into anything legal 3 times in a row gets a forced
end_turn (see _autoplay_non_human_turns's own consecutive_invalid circuit
breaker) - but that verb emits no Event, so narrator_node's "nothing
happened" fast path returns empty narration, and the whole turn vanishes
from the log with zero visible trace. Two live reports in the same round
("Fenwick moved but never attacked", "Grom missed his turn completely")
both turned out to be this exact silent skip, not a movement/turn-order bug.

Forces the circuit breaker deterministically via a player_agent_fn stub
that always answers "invalid" (mirroring real player_agent_node's own
skip-when-already-set escape hatch) instead of a real LLM call - no live
model involved, this is about the WS/graph wiring's own visibility.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    # Mirrors real narrator_node's own "nothing happened" fast path (empty
    # new_events -> empty narration) exactly, since that's the specific
    # behavior this test needs to exercise - just without the real LLM call
    # for the non-empty-events case (the 3 "invalid" attempts each produce a
    # real action_invalid event).
    new_events = state["game_state"].events[state["events_before"] :]
    if not new_events:
        return {"narration": ""}
    return {"narration": "[stub narration]"}


def _stub_player_agent_always_invalid(state: GraphState) -> dict[str, Any]:
    # Mirrors player_agent_node's own "already decided, don't touch it"
    # escape hatch (parsed_action or raw_text already set) - a monster's own
    # choose_monster_action-built parsed_action must pass through untouched,
    # only a companion's genuinely-undecided turn (parsed_action=None,
    # raw_text="") gets forced to "invalid" here instead of a real LLM call.
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {"parsed_action": ParsedAction(actor=actor_id, verb="invalid", raw_text="gibberish")}


@pytest.fixture(autouse=True)
def _isolated_sessions() -> None:
    reset_sessions()


def _human() -> Character:
    return Character(
        id="oen",
        name="Oen",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
    )


def _companion() -> Character:
    return Character(
        id="companion_fenwick",
        name="Fenwick Quickfingers",
        race="Halfling",
        class_="Rogue",
        class_index="rogue",
        background="Acolyte",
        is_pc=True,
        is_companion=True,
        hp=9,
        max_hp=9,
        ac=14,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 8, "DEX": 15, "CON": 13, "INT": 12, "WIS": 10, "CHA": 14},
        speed=25,
        proficiency_bonus=2,
        equipped_weapons=["shortsword"],
    )


def _monster() -> Character:
    # A living non-PC keeps game_state.status "in_progress" - with none at
    # all, _check_victory_defeat's vacuous-victory check would flip status
    # away from "in_progress" and short-circuit the autoplay loop entirely.
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


def test_forced_end_turn_after_repeated_invalid_actions_still_narrates_something() -> None:
    human = _human()
    companion = _companion()
    monster = _monster()
    game_state = GameState(
        encounter_id="forced_end_turn_test",
        characters={c.id: c for c in (companion, human, monster)},
        turn_order=[companion.id, human.id, monster.id],
        current_turn=0,
        round=1,
    )
    create_session(
        "test-forced-end-turn",
        game_state,
        human_character_ids={"tok-oen": human.id},
        graph=build_graph(
            narrator_fn=_stub_narrator,
            player_agent_fn=_stub_player_agent_always_invalid,
            scene_image_fn=_stub_scene_image,
        ),
    )

    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-forced-end-turn?token=tok-oen") as ws:
        narrations = []
        state_updates = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "narration":
                narrations.append(msg["text"])
            elif msg["type"] == "state_update":
                state_updates.append(msg["game_state"])
            elif msg["type"] == "awaiting_input":
                assert msg["actor"] == human.id
                break

    # Exactly 3 "invalid" attempts resolved (each broadcasts a narration
    # message too, per _broadcast_narration - real, non-empty text from the
    # rules engine's own action_invalid path is untouched by this fix), plus
    # the forced end_turn itself as a 4th - which is the one that used to be
    # silently blank.
    assert len(narrations) == 4
    assert narrations[-1].strip() != ""
    assert companion.name in narrations[-1]

    # And the turn genuinely advanced past the companion to the human, not
    # stuck retrying forever.
    final_state = state_updates[-1]
    assert final_state["turn_order"][final_state["current_turn"]] == human.id

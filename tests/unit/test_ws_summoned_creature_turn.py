"""Issue #56, phase A: a summoned creature on the party's side takes its own turn in a
live session. `is_pc` marks its side, but it must be driven by monster_ai (attacking the
nearest enemy) rather than handed to the companion/LLM path - there is no player or persona
behind it - and its turn must show up in the log like any other creature's.

player_agent_fn here raises if it is ever asked about the summon, so a regression that
routes it down the companion path fails loudly instead of quietly calling a model."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api.main import app
from src.api.ws.session import create_session, reset_sessions
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.summons import add_combatant
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState


def _stub_scene_image(_state: GraphState) -> dict[str, Any]:
    return {"scene_image_url": None}


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    new_events = state["game_state"].events[state["events_before"] :]
    return {"narration": "[stub]" if new_events else ""}


def _player_agent_must_not_run(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}
    raise AssertionError("a summoned creature was routed to the companion/LLM path")


@pytest.fixture(autouse=True)
def _isolated_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ALLOW_DEBUG_ACTIONS", True)
    reset_sessions()


def _human() -> Character:
    return Character(
        id="oen",
        name="Oen",
        race="Human",
        class_="Wizard",
        class_index="wizard",
        background="Acolyte",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=12,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 8, "DEX": 12, "CON": 14, "INT": 16, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def test_a_summoned_wolf_attacks_the_nearest_enemy_on_its_own_turn() -> None:
    srd = load_srd()
    human = _human()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=3, y=0))
    goblin.hp = goblin.max_hp = 200  # survives, so the fight stays in progress
    state = GameState(
        encounter_id="summon_turn_test",
        characters={human.id: human, goblin.id: goblin},
        turn_order=[human.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=8, height=3, terrain=[["floor"] * 8 for _ in range(3)], spawn_points={}
        ),
    )
    wolf = monster_to_character(srd.monsters["wolf"], "wolf_a", Position(x=2, y=0))
    add_combatant(state, wolf, human, "Conjure Animals")
    assert state.turn_order == ["oen", "wolf_a", "goblin_1"]

    create_session(
        "test-summon-turn",
        state,
        human_character_ids={"tok-oen": human.id},
        graph=build_graph(
            narrator_fn=_stub_narrator,
            player_agent_fn=_player_agent_must_not_run,
            scene_image_fn=_stub_scene_image,
        ),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/session/test-summon-turn?token=tok-oen") as ws:
        # The human ends their turn; the wolf then goes (monster_ai), then the goblin,
        # and play returns to the human.
        first = ws.receive_json()
        while first["type"] != "awaiting_input":
            first = ws.receive_json()
        assert first["actor"] == "oen"
        ws.send_json(
            {
                "type": "debug_action",
                "action": {"actor": "oen", "verb": "end_turn", "raw_text": "x"},
            }
        )
        last_state: dict[str, Any] = {}
        for _ in range(200):  # bounded: a regression must fail, not hang CI
            msg = ws.receive_json()
            if msg["type"] == "state_update":
                last_state = msg["game_state"]
            elif msg["type"] == "awaiting_input" and last_state:
                break

    attackers = {e["actor"] for e in last_state["events"] if e["type"] in ("attack_roll", "move")}
    assert "wolf_a" in attackers
    assert last_state["characters"]["wolf_a"]["summoned_by"] == "oen"

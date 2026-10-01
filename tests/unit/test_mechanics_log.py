"""A server-side, daily-rotating log of every resolved game mechanic -
see src.observability.mechanics_log's own module docstring for why this
is separate from src.observability.log_event (failures only).

tests/unit/conftest.py's autouse _redirect_mechanics_log fixture already
points mechanics_log.MECHANICS_LOG_PATH at this test's own tmp_path (and
resets the module's handler cache so each test gets a fresh one), so
every test below can read it back directly with zero setup.
"""

from __future__ import annotations

import json
import random

import pytest

from src import config
from src.engine.actions import ParsedAction
from src.engine.events import Event
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes.rules_engine_node import make_rules_engine_node
from src.graph.state_schema import GraphState
from src.observability import mechanics_log


def _actor(character_id: str = "oen") -> Character:
    return Character(
        id=character_id,
        name="Oen",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=10,
        max_hp=10,
        ac=15,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 14, "DEX": 12, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
        speed=30,
        proficiency_bonus=2,
    )


def _game_state(actor: Character) -> GameState:
    return GameState(
        encounter_id="mechanics_log_test",
        characters={actor.id: actor},
        turn_order=[actor.id],
        current_turn=0,
        round=2,
    )


def _read_records() -> list[dict[str, object]]:
    lines = mechanics_log.MECHANICS_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_log_mechanic_writes_a_real_record() -> None:
    actor = _actor()
    game_state = _game_state(actor)
    event = Event(
        round=2,
        turn_index=0,
        actor=actor.id,
        type="dodge",
        payload={"some": "detail"},
    )

    mechanics_log.log_mechanic(event, game_state)

    records = _read_records()
    assert len(records) == 1
    assert records[0]["round"] == 2
    assert records[0]["turn_index"] == 0
    assert records[0]["actor_id"] == "oen"
    assert records[0]["actor_name"] == "Oen"
    assert records[0]["encounter_id"] == "mechanics_log_test"
    assert records[0]["type"] == "dodge"
    assert records[0]["payload"] == {"some": "detail"}


def test_log_mechanic_is_a_no_op_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MECHANICS_LOG_ENABLED", False)
    actor = _actor()
    game_state = _game_state(actor)
    event = Event(round=1, turn_index=0, actor=actor.id, type="dodge", payload={})

    mechanics_log.log_mechanic(event, game_state)

    assert not mechanics_log.MECHANICS_LOG_PATH.exists()


def test_log_mechanic_falls_back_to_the_raw_id_for_an_unknown_actor() -> None:
    actor = _actor()
    game_state = _game_state(actor)
    event = Event(round=1, turn_index=0, actor="someone_else", type="dodge", payload={})

    mechanics_log.log_mechanic(event, game_state)

    records = _read_records()
    assert records[0]["actor_id"] == "someone_else"
    assert records[0]["actor_name"] == "someone_else"


def test_rules_engine_node_logs_every_newly_resolved_event() -> None:
    actor = _actor()
    game_state = _game_state(actor)
    action = ParsedAction(actor=actor.id, verb="dodge", raw_text="I dodge")
    state: GraphState = {
        "game_state": game_state,
        "raw_text": "I dodge",
        "parsed_action": action,
        "events_before": 0,
        "round_before": 2,
        "narration": None,
        "scene_image_url": None,
    }
    node = make_rules_engine_node(rng=random.Random(1))

    node(state)

    records = _read_records()
    assert len(records) == 1
    assert records[0]["type"] == "dodge"
    assert records[0]["actor_id"] == "oen"

"""Collecting real free-text turns that failed to become a legal action,
for hand review before folding the corrected ones into the intent-parser
fine-tuning dataset - see src.training.failed_intents' own module docstring.

Two collection points, tested independently: intent_parser_node's own
"unparseable" case (the model returned verb="invalid") and
rules_engine_node's "rejected" case (a syntactically fine ParsedAction
that turn_engine.resolve_action still rejected) - plus the negative cases
(a valid action logs nothing; a rejected action with no raw_text, i.e. a
debug/scripted/monster-AI action, logs nothing either).

tests/unit/conftest.py's autouse _redirect_failed_intents_log fixture
already points failed_intents.FAILED_INTENTS_PATH at this test's own
tmp_path, so every test below can read it back directly with zero setup.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError
from src.graph.nodes import intent_parser as intent_parser_module
from src.graph.nodes.intent_parser import intent_parser_node
from src.graph.nodes.rules_engine_node import make_rules_engine_node
from src.graph.state_schema import GraphState
from src.training import failed_intents


def _actor(character_id: str = "oen", is_companion: bool = False) -> Character:
    return Character(
        id=character_id,
        name="Oen",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        is_companion=is_companion,
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
        encounter_id="failed_intent_test",
        characters={actor.id: actor},
        turn_order=[actor.id],
        current_turn=0,
        round=1,
    )


def _base_state(actor: Character, raw_text: str, parsed_action: ParsedAction | None) -> GraphState:
    return {
        "game_state": _game_state(actor),
        "raw_text": raw_text,
        "parsed_action": parsed_action,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


def _read_records() -> list[dict[str, Any]]:
    lines = failed_intents.FAILED_INTENTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_log_failed_intent_writes_a_real_record() -> None:
    actor = _actor()
    action = ParsedAction(actor=actor.id, verb="invalid", raw_text="mumble mumble")
    failed_intents.log_failed_intent(
        reason="unparseable",
        actor=actor,
        raw_text="mumble mumble",
        prompt="<prompt>",
        produced_action=action,
    )
    records = _read_records()
    assert len(records) == 1
    assert records[0]["reason"] == "unparseable"
    assert records[0]["actor_id"] == "oen"
    assert records[0]["raw_text"] == "mumble mumble"
    assert records[0]["produced_action"]["verb"] == "invalid"
    assert records[0]["corrected_action"] is None


def test_intent_parser_node_logs_when_the_model_returns_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = _actor(character_id="companion_fenwick", is_companion=True)
    state = _base_state(actor, "I ponder the void meaningfully", None)
    invalid_action = ParsedAction(actor=actor.id, verb="invalid", raw_text=state["raw_text"])
    monkeypatch.setattr(intent_parser_module, "chat_structured", lambda **_kw: invalid_action)

    result = intent_parser_node(state)
    assert result["parsed_action"].verb == "invalid"

    records = _read_records()
    assert len(records) == 1
    assert records[0]["reason"] == "unparseable"
    assert records[0]["actor_id"] == actor.id
    assert records[0]["raw_text"] == "I ponder the void meaningfully"
    assert records[0]["prompt"]  # the real built prompt, non-empty


def test_intent_parser_node_does_not_log_a_valid_action(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _actor()
    state = _base_state(actor, "I take the Dodge action", None)
    valid_action = ParsedAction(actor=actor.id, verb="dodge", raw_text=state["raw_text"])
    monkeypatch.setattr(intent_parser_module, "chat_structured", lambda **_kw: valid_action)

    intent_parser_node(state)

    assert not failed_intents.FAILED_INTENTS_PATH.exists()


def test_rules_engine_node_logs_a_rejected_free_text_action() -> None:
    actor = _actor()
    bad_action = ParsedAction(actor=actor.id, verb="second_wind", raw_text="I catch my breath")
    state = _base_state(actor, "I catch my breath", bad_action)
    node = make_rules_engine_node(rng=random.Random(1))

    with pytest.raises(TurnEngineError):
        node(state)

    records = _read_records()
    assert len(records) == 1
    assert records[0]["reason"] == "rejected"
    assert records[0]["raw_text"] == "I catch my breath"
    assert "second wind" in records[0]["detail"].lower()


def test_rules_engine_node_does_not_log_a_rejected_action_with_no_raw_text() -> None:
    # debug_action/scripted/monster-AI actions never set raw_text - a
    # deliberately-illegal test fixture (or a real debug session) shouldn't
    # be recorded as a real hard case from free text.
    actor = _actor()
    bad_action = ParsedAction(actor=actor.id, verb="second_wind", raw_text="")
    state = _base_state(actor, "", bad_action)
    node = make_rules_engine_node(rng=random.Random(1))

    with pytest.raises(TurnEngineError):
        node(state)

    assert not failed_intents.FAILED_INTENTS_PATH.exists()

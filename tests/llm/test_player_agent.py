"""Live check for the exact failure mode found running the real autoplay for
the first time: a companion's persona-driven turn declaration needs to
actually parse into something other than "invalid", or the encounter can
never advance past that companion's turn (see cli/play.py's
consecutive_invalid circuit breaker and CLAUDE.md for the incident)."""

from __future__ import annotations

import pytest

from src.engine.companions import build_companion, load_companion_spec
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.graph.nodes.intent_parser import intent_parser_node
from src.graph.nodes.player_agent import player_agent_node
from src.graph.state_schema import GraphState

pytestmark = pytest.mark.llm


def _companion_vs_goblin_state(companion_id: str) -> GameState:
    srd = load_srd()
    companion = build_companion(load_companion_spec(companion_id), srd=srd)
    companion.position = Position(x=0, y=0)

    from src.engine.encounter import monster_to_character

    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=1, y=0))

    return GameState(
        encounter_id="test",
        characters={companion.id: companion, goblin.id: goblin},
        turn_order=[companion.id, goblin.id],
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
    )


@pytest.mark.parametrize(
    "companion_id",
    ["grom_ironfist", "silvana_wren", "fenwick_quickfingers", "sister_mira", "pip_larkspur"],
)
def test_every_companion_declares_a_parseable_action_with_a_visible_enemy(
    companion_id: str,
) -> None:
    game_state = _companion_vs_goblin_state(companion_id)

    agent_state: GraphState = {
        "game_state": game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    raw_text = player_agent_node(agent_state)["raw_text"]
    assert raw_text

    parser_state: GraphState = {
        "game_state": game_state,
        "raw_text": raw_text,
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    action = intent_parser_node(parser_state)["parsed_action"]
    assert action.verb != "invalid", f"{companion_id} said {raw_text!r} -> {action}"

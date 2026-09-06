"""Confirms inserting player_agent at the front of the per-action graph
(Day 15) doesn't disturb the existing pipeline: a companion's empty turn
(no raw_text/parsed_action supplied by the caller) still reaches
rules_engine and resolves correctly. Stubs player_agent_fn to hand back a
parsed_action directly (the same "already resolved" bypass every scripted/
debug_action test relies on) so this stays an offline wiring test, not a
retest of player_agent's own prompt-building logic (see
test_player_agent_node.py for that).
"""

from __future__ import annotations

from typing import Any

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.graph.graph_builder import build_graph
from src.graph.state_schema import GraphState

_SRD = load_srd()


def _make_character(char_id: str, *, is_pc: bool, is_companion: bool = False) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        is_companion=is_companion,
        hp=10,
        max_hp=10,
        ac=5,  # low AC so the fixed roll below is a guaranteed hit
        position=Position(x=0, y=0),
        inventory=["dagger"],
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _stub_narrator(state: GraphState) -> dict[str, Any]:
    return {"narration": "[stub narration]"}


def _stub_player_agent(state: GraphState) -> dict[str, Any]:
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return {
        "parsed_action": ParsedAction(
            actor=actor_id, verb="attack", target="goblin_1", raw_text="I attack the goblin."
        )
    }


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def test_companions_empty_turn_flows_through_player_agent_to_resolution() -> None:
    companion = _make_character("companion_grom", is_pc=True, is_companion=True)
    goblin = _make_character("goblin_1", is_pc=False)
    game_state = GameState(
        encounter_id="test",
        characters={companion.id: companion, goblin.id: goblin},
        turn_order=[companion.id, goblin.id],
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
    )

    rng = _FixedRandom([15, 4])  # attack roll of 15 (a clean hit vs AC 5), 4 damage
    graph = build_graph(
        rng=rng,  # type: ignore[arg-type]
        srd=_SRD,
        narrator_fn=_stub_narrator,
        player_agent_fn=_stub_player_agent,
    )

    graph_input: GraphState = {
        "game_state": game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": 0,
        "narration": None,
        "scene_image_url": None,
    }
    result = graph.invoke(graph_input)

    resolved_state = result["game_state"]
    assert resolved_state.characters["goblin_1"].hp < 10
    assert result["narration"] == "[stub narration]"
    event_types = [e.type for e in resolved_state.events]
    assert "attack_roll" in event_types

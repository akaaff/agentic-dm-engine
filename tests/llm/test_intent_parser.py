"""Golden-set test against the real teacher model (qwen2.5:7b-instruct).
Not a full replay-fixture test (LLM output isn't byte-deterministic even at
low temperature) - checks the fields that matter (verb, and target where the
utterance clearly names one), tolerant of everything else."""

from typing import Any

import pytest

from src.cli.play import build_demo_encounter, build_demo_party, demo_initiative_rng
from src.engine.encounter import build_encounter_state
from src.graph.nodes.intent_parser import intent_parser_node
from src.graph.state_schema import GraphState

pytestmark = pytest.mark.llm


def _parse(utterance: str) -> dict[str, Any]:
    encounter = build_demo_encounter()
    party = build_demo_party()
    game_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    state: GraphState = {
        "game_state": game_state,
        "raw_text": utterance,
        "parsed_action": None,
        "events_before": 0,
        "narration": None,
        "scene_image_url": None,
    }
    result = intent_parser_node(state)
    action = result["parsed_action"]
    return {
        "verb": action.verb,
        "target": action.target,
        "raw_text": action.raw_text,
        "skill": action.params.get("skill"),
    }


GOLDEN_CASES = [
    ("I attack goblin_1 with my sword", "attack", "goblin_1", None),
    ("I attack goblin_2", "attack", "goblin_2", None),
    ("I dodge incoming attacks", "dodge", None, None),
    ("I disengage and back away from combat", "disengage", None, None),
    ("I use a healing potion from my inventory", "use_item", None, None),
    ("I would like to order a large pepperoni pizza", "invalid", None, None),
    # Regression case (see CLAUDE.md): in-character phrasing without the
    # word "check" was initially misclassified as invalid.
    ("I try to intimidate the goblin into backing off", "skill_check", None, "intimidation"),
]


@pytest.mark.parametrize("utterance,expected_verb,expected_target,expected_skill", GOLDEN_CASES)
def test_intent_parser_golden_cases(
    utterance: str, expected_verb: str, expected_target: str | None, expected_skill: str | None
) -> None:
    result = _parse(utterance)
    assert result["verb"] == expected_verb, f"utterance={utterance!r} -> {result}"
    if expected_target is not None:
        assert result["target"] == expected_target, f"utterance={utterance!r} -> {result}"
    if expected_skill is not None:
        assert result["skill"] == expected_skill, f"utterance={utterance!r} -> {result}"
    assert result["raw_text"] == utterance


def test_intent_parser_preserves_actor_id() -> None:
    encounter = build_demo_encounter()
    party = build_demo_party()
    game_state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    current_actor = game_state.turn_order[game_state.current_turn]

    state: GraphState = {
        "game_state": game_state,
        "raw_text": "I attack goblin_1",
        "parsed_action": None,
        "events_before": 0,
        "narration": None,
        "scene_image_url": None,
    }
    result = intent_parser_node(state)
    assert result["parsed_action"].actor == current_actor

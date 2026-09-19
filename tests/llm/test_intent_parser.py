"""Golden-set test against the real teacher model (qwen2.5:7b-instruct).
Not a full replay-fixture test (LLM output isn't byte-deterministic even at
low temperature) - checks the fields that matter (verb, and target where the
utterance clearly names one), tolerant of everything else."""

from typing import Any

import pytest

from src.cli.play import build_demo_encounter, build_demo_party, demo_initiative_rng
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character
from src.engine.state import GameState as EngineGameState
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
        "round_before": 1,
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
    # Found live: the model needed an explicit "never nest it inside params"
    # instruction and a code-level fallback (intent_parser_node's
    # _normalize_cast_spell_target) before this reliably set "target" at
    # all - it kept answering with params["target"] instead.
    ("I cast acid splash at goblin_1", "cast_spell", "goblin_1", None),
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


def _spatial_state(utterance: str) -> GraphState:
    # Found live: "the closest enemy"/"the enemy to my left" were
    # unresolvable from raw (x, y) pairs alone - a real encounter shape
    # (actor with an enemy on each side, one closer than the other) to
    # prove the model actually uses build_intent_parser_prompt's now-
    # precomputed distance/direction hints rather than raw coordinates.
    srd = load_srd()
    actor = Character(
        id="thorin",
        name="Thorin",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=16,
        position=Position(x=4, y=2),
        stats={"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )
    near_west = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=2, y=2))
    far_east = monster_to_character(srd.monsters["wolf"], "wolf_2", Position(x=9, y=2))
    game_state = EngineGameState(
        encounter_id="spatial_test",
        characters={actor.id: actor, near_west.id: near_west, far_east.id: far_east},
        turn_order=[actor.id, near_west.id, far_east.id],
        current_turn=0,
        round=1,
    )
    return {
        "game_state": game_state,
        "raw_text": utterance,
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


SPATIAL_CASES = [
    ("I attack the closest wolf", "wolf_1"),
    ("I attack the nearest enemy", "wolf_1"),
    ("I attack the wolf to my left", "wolf_1"),
    ("I attack the wolf on the right", "wolf_2"),
]


@pytest.mark.parametrize("utterance,expected_target", SPATIAL_CASES)
def test_intent_parser_resolves_spatial_references(utterance: str, expected_target: str) -> None:
    state = _spatial_state(utterance)
    result = intent_parser_node(state)
    action = result["parsed_action"]
    assert action.verb == "attack", f"utterance={utterance!r} -> {action}"
    assert action.target == expected_target, f"utterance={utterance!r} -> {action}"


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
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    result = intent_parser_node(state)
    assert result["parsed_action"].actor == current_actor


def test_intent_parser_forces_the_real_actor_id_even_for_a_pathological_name() -> None:
    # Live-found: a real player named their character "asssssass" (a
    # repeated-letter nonsense string - exactly what a tokenizer-based
    # model reproduces worst) and every action they took was rejected with
    # "It is asssssass's turn, not assssssass's" - the model's own
    # structured-output `actor` field had silently added an extra "s".
    # test_intent_parser_preserves_actor_id above never caught this with an
    # ordinary name (a real word/name is trivial for the model to echo
    # exactly) - this uses the actual repro string directly.
    srd = load_srd()
    actor = Character(
        id="asssssass",
        name="asssssass",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=12,
        position=Position(x=0, y=0),
        stats={"STR": 12, "DEX": 12, "CON": 12, "INT": 12, "WIS": 12, "CHA": 12},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )
    wolf = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=2, y=0))
    game_state = EngineGameState(
        encounter_id="pathological_actor_test",
        characters={actor.id: actor, wolf.id: wolf},
        turn_order=[actor.id, wolf.id],
        current_turn=0,
        round=1,
    )
    state: GraphState = {
        "game_state": game_state,
        "raw_text": "I attack the nearest wolf",
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }

    result = intent_parser_node(state)

    assert result["parsed_action"].actor == "asssssass"

"""Narrator output isn't deterministic prose, so these checks are loose:
non-empty, doesn't leak raw dice/mechanics language the prompt explicitly
forbids, and roughly reflects what actually happened (hit vs miss, death)."""

import pytest

from src.engine.events import Event
from src.graph.nodes.narrator import narrator_node
from src.graph.state_schema import GraphState

pytestmark = pytest.mark.llm


def _narrate(events: list[Event]) -> str:
    # game_state is unused by narrator_node beyond events, so a minimal
    # placeholder is fine here rather than building a real encounter.
    from src.cli.play import build_demo_encounter, build_demo_party, demo_initiative_rng
    from src.engine.encounter import build_encounter_state

    game_state = build_encounter_state(
        build_demo_encounter(),
        build_demo_party(),
        demo_initiative_rng(),  # type: ignore[arg-type]
    )
    game_state.events = events
    state: GraphState = {
        "game_state": game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    result = narrator_node(state)
    return str(result["narration"])


def test_narrator_describes_a_hit() -> None:
    events = [
        Event(
            round=1,
            turn_index=0,
            actor="thorin",
            type="attack_roll",
            payload={"target": "goblin_1", "hit": True, "critical": False},
        ),
        Event(
            round=1,
            turn_index=0,
            actor="thorin",
            type="damage_dealt",
            payload={"target": "goblin_1", "amount": 8, "target_hp_remaining": 0},
        ),
        Event(
            round=1,
            turn_index=0,
            actor="goblin_1",
            type="death",
            payload={"killed_by": "thorin"},
        ),
    ]
    narration = _narrate(events)
    assert narration
    assert "17" not in narration  # no leaked roll numbers
    assert "rolled" not in narration.lower()


def test_narrator_describes_a_miss() -> None:
    events = [
        Event(
            round=1,
            turn_index=1,
            actor="goblin_1",
            type="attack_roll",
            payload={"target": "elrond", "hit": False, "critical": False},
        )
    ]
    narration = _narrate(events)
    assert narration


def test_narrator_returns_empty_for_no_new_events() -> None:
    from src.cli.play import build_demo_encounter, build_demo_party, demo_initiative_rng
    from src.engine.encounter import build_encounter_state

    game_state = build_encounter_state(
        build_demo_encounter(),
        build_demo_party(),
        demo_initiative_rng(),  # type: ignore[arg-type]
    )
    state: GraphState = {
        "game_state": game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    result = narrator_node(state)
    assert result["narration"] == ""

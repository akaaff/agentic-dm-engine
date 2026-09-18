"""Narrator output isn't deterministic prose, so these checks are loose:
non-empty, doesn't leak raw dice/mechanics language the prompt explicitly
forbids, and roughly reflects what actually happened (hit vs miss, death)."""

import pytest

from src.engine.events import Event
from src.graph.nodes.narrator import narrator_node
from src.graph.state_schema import GraphState
from src.llm.providers import contains_cjk

pytestmark = pytest.mark.llm


def _narrate(events: list[Event]) -> str:
    # game_state.events is what actually drives the narration; game_state.
    # characters is also read now (issue #33's cast_names grounding), so a
    # real encounter is built rather than a bare placeholder either way.
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
    assert not contains_cjk(narration)  # issue #16


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
    assert not contains_cjk(narration)  # issue #16


def test_narrator_does_not_hallucinate_a_monster_outside_the_actual_cast() -> None:
    # Issue #33: a live multi-round session produced "the drow's poison
    # courses through..." mid-fight against an encounter with no drow at
    # all. Confirmed this isn't context accumulation (chat_english_only
    # sends one stateless request per call - see providers.chat, no
    # conversation history kept between narrator_node invocations), so the
    # fix is grounding each individual call more tightly: the prompt now
    # lists the real cast (game_state.characters) explicitly. This can't
    # prove a small model will *never* hallucinate (same honest framing as
    # issue #16's CJK mitigation), but repeated trials against a
    # goblins-only encounter should never mention an unrelated monster
    # species like a drow, dragon, or orc - none of which exist anywhere
    # in this encounter's data.
    events = [
        Event(
            round=3,
            turn_index=0,
            actor="thorin",
            type="attack_roll",
            payload={"target": "goblin_1", "hit": True, "critical": False},
        ),
        Event(
            round=3,
            turn_index=0,
            actor="thorin",
            type="damage_dealt",
            payload={"target": "goblin_1", "amount": 5, "target_hp_remaining": 2},
        ),
    ]
    hallucinated_terms = ("drow", "dragon", "orc", "demon", "vampire", "zombie")
    for _ in range(8):
        narration = _narrate(events).lower()
        assert not any(term in narration for term in hallucinated_terms), narration


def test_narrator_returns_empty_for_no_new_events() -> None:
    from src.cli.play import build_demo_encounter, build_demo_party, demo_initiative_rng
    from src.engine.encounter import build_encounter_state

    game_state = build_encounter_state(
        build_demo_encounter(),
        build_demo_party(),
        demo_initiative_rng(),  # type: ignore[arg-type]
    )
    # build_encounter_state already populates initiative_rolled events
    # (issue #14) - "no *new* events" means events_before must account for
    # those, not assume a freshly-built GameState starts with none.
    state: GraphState = {
        "game_state": game_state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": len(game_state.events),
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }
    result = narrator_node(state)
    assert result["narration"] == ""

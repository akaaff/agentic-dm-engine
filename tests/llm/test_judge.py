import pytest

from src.graph.nodes.judge import judge_transcript

pytestmark = pytest.mark.llm

_TRANSCRIPT = [
    "Goblin_1 lunges at Grom with a rusty scimitar, but the blade skids off his shield.",
    "Grom roars and brings his battleaxe down on Goblin_1, cleaving through its guard - the "
    "goblin drops, lifeless.",
    "Silvana looses an arrow at Goblin_2, catching it in the shoulder.",
]


def test_judge_scores_are_in_range() -> None:
    result = judge_transcript(_TRANSCRIPT)

    for score in (result.narrative_quality, result.mechanical_consistency, result.overall_score):
        assert 1 <= score <= 10
    assert result.summary

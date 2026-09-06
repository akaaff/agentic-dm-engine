"""Day 15's actual verify gate: full autoplay of the real one-shot campaign
with two AI companions and zero human input, judge-scored at the end. Real
LLM calls throughout (player_agent, intent_parser, narrator, judge) - not
for the default offline test suite. Run manually via
`uv run python -m src.cli.play --autoplay` for a readable trace; this test
just asserts the same run reaches a clean terminal state.
"""

from __future__ import annotations

import pytest

from src.cli.play import run_autoplay
from src.graph.nodes.judge import judge_transcript

pytestmark = pytest.mark.llm


def test_autoplay_reaches_a_terminal_state_with_no_invariant_violations() -> None:
    state, narration_log = run_autoplay(verbose=False)

    assert state.status in ("victory", "defeat")
    assert narration_log
    for character in state.characters.values():
        assert character.hp >= 0

    judged = judge_transcript(narration_log)
    assert 1 <= judged.overall_score <= 10

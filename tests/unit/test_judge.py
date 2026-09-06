"""judge_transcript's own LLM call is exercised live in tests/llm/test_judge.py;
this offline test only checks prompt-building and response wiring, by
monkeypatching chat_structured the same way test_player_agent_node.py stubs
chat()."""

from __future__ import annotations

from typing import Any

import pytest

from src.graph.nodes import judge as judge_module
from src.graph.nodes.judge import JudgeResult, judge_transcript


def test_builds_a_numbered_transcript_and_returns_the_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_chat_structured(
        messages: list[dict[str, str]], schema: type[JudgeResult], temperature: float = 0.2
    ) -> JudgeResult:
        captured["prompt"] = messages[0]["content"]
        return JudgeResult(
            narrative_quality=7,
            mechanical_consistency=9,
            overall_score=8,
            strengths=["clear combat beats"],
            weaknesses=["repetitive verbs"],
            summary="Solid, mechanically faithful narration with some repetition.",
        )

    monkeypatch.setattr(judge_module, "chat_structured", _fake_chat_structured)

    result = judge_transcript(["The goblin lunges at Thorin.", "Thorin parries and counters."])

    assert "1. The goblin lunges at Thorin." in captured["prompt"]
    assert "2. Thorin parries and counters." in captured["prompt"]
    assert result.overall_score == 8


def test_skips_empty_narration_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_chat_structured(
        messages: list[dict[str, str]], schema: type[JudgeResult], temperature: float = 0.2
    ) -> JudgeResult:
        captured["prompt"] = messages[0]["content"]
        return JudgeResult(
            narrative_quality=5,
            mechanical_consistency=5,
            overall_score=5,
            strengths=[],
            weaknesses=[],
            summary="ok",
        )

    monkeypatch.setattr(judge_module, "chat_structured", _fake_chat_structured)

    judge_transcript(["First line.", "", "Second line."])

    assert "First line." in captured["prompt"]
    assert "Second line." in captured["prompt"]

"""Day 25: intent_parser_task's generate_prompt is pure Python (randomized
scenario + template-bank utterance, no LLM call) - fully testable offline.
The live teacher call itself is exercised by generate_synthetic.py's own
tests (Day 24) and, end-to-end for this specific task, by whichever script
actually builds the real dataset."""

from __future__ import annotations

import random

from src.engine.actions import ParsedAction
from src.training.tasks.intent_parser_task import build_intent_parser_task


def test_generate_prompt_embeds_the_utterance_and_actor_context() -> None:
    task = build_intent_parser_task(random.Random(0))

    prompt = task.generate_prompt()

    assert "Current actor: " in prompt
    # Not the literal word "actor" (Day 25 finding: confuses the teacher
    # into echoing back a different id ~49% of the time) - a real name.
    assert "Current actor: actor " not in prompt
    assert "Player's action:" in prompt
    assert task.output_schema is ParsedAction
    assert task.output_schema is ParsedAction


def test_generate_prompt_is_reproducible_from_a_seeded_rng() -> None:
    task_a = build_intent_parser_task(random.Random(42))
    task_b = build_intent_parser_task(random.Random(42))

    prompts_a = [task_a.generate_prompt() for _ in range(10)]
    prompts_b = [task_b.generate_prompt() for _ in range(10)]

    assert prompts_a == prompts_b


def test_generate_prompt_covers_more_than_one_utterance_category_over_many_calls() -> None:
    task = build_intent_parser_task(random.Random(1))

    prompts = [task.generate_prompt() for _ in range(60)]

    # Cheap proxy for "covers multiple categories": at least attack-shaped
    # and skill-check-shaped utterances both showed up in a modest sample.
    assert any("attack" in p for p in prompts)
    assert any(
        any(
            phrase in p
            for phrase in ("sneak", "climb", "intimidate", "persuade", "search", "track")
        )
        for p in prompts
    )
    assert any("dodge" in p.lower() for p in prompts)


def test_generate_prompt_never_produces_an_empty_utterance() -> None:
    task = build_intent_parser_task(random.Random(2))

    for _ in range(30):
        prompt = task.generate_prompt()
        utterance_line = next(
            line for line in prompt.splitlines() if line.startswith("Player's action:")
        )
        assert utterance_line != 'Player\'s action: ""'

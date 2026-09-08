"""Day 24: generate_synthetic's own LLM call (chat_structured) is
monkeypatched, same pattern as test_judge.py/test_campaign_generator.py.
Uses a deliberately trivial toy schema (not ParsedAction or anything
D&D-specific) to keep these tests honest about the module being generic."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.training import generate_synthetic as generate_synthetic_module
from src.training.generate_synthetic import GenerationFailure, generate_dataset, generate_example
from src.training.task_spec import DistillationTask


class _Toy(BaseModel):
    value: int


def _task(generate_prompt: Any = lambda: "prompt") -> DistillationTask:
    return DistillationTask(
        name="toy", output_schema=_Toy, generate_prompt=generate_prompt, teacher_model="toy-model"
    )


def test_generate_example_succeeds_on_the_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def _fake(messages: list[dict[str, str]], schema: type[BaseModel], model: str) -> BaseModel:
        calls.append({"messages": messages, "schema": schema, "model": model})
        return schema.model_validate({"value": 42})

    monkeypatch.setattr(generate_synthetic_module, "chat_structured", _fake)

    example = generate_example(_task(lambda: "describe a number"))

    assert example.input == "describe a number"
    assert example.output == {"value": 42}
    assert len(calls) == 1
    assert calls[0]["model"] == "toy-model"
    assert calls[0]["messages"] == [{"role": "user", "content": "describe a number"}]


def test_generate_example_retries_the_same_prompt_after_a_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def _fake(messages: list[dict[str, str]], schema: type[BaseModel], model: str) -> BaseModel:
        attempts.append(messages[0]["content"])
        if len(attempts) == 1:
            raise ValidationError.from_exception_data("Toy", [])
        return schema.model_validate({"value": 7})

    monkeypatch.setattr(generate_synthetic_module, "chat_structured", _fake)

    example = generate_example(_task(lambda: "fixed prompt"), max_attempts=3)

    assert example.output == {"value": 7}
    # Retried with the exact same prompt, not a freshly-generated one.
    assert attempts == ["fixed prompt", "fixed prompt"]


def test_generate_example_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    def _always_invalid(
        messages: list[dict[str, str]], schema: type[BaseModel], model: str
    ) -> BaseModel:
        raise ValidationError.from_exception_data("Toy", [])

    monkeypatch.setattr(generate_synthetic_module, "chat_structured", _always_invalid)

    with pytest.raises(GenerationFailure, match="toy"):
        generate_example(_task(), max_attempts=2)


def test_generate_dataset_skips_examples_that_never_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def _fake(messages: list[dict[str, str]], schema: type[BaseModel], model: str) -> BaseModel:
        nonlocal call_count
        call_count += 1
        # Every other example fails validation entirely.
        if call_count % 2 == 0:
            raise ValidationError.from_exception_data("Toy", [])
        return schema.model_validate({"value": call_count})

    monkeypatch.setattr(generate_synthetic_module, "chat_structured", _fake)

    examples = generate_dataset(_task(), n=4, max_attempts=1)

    # 4 requested, half fail every attempt (max_attempts=1) and are skipped.
    assert len(examples) == 2
    assert all(isinstance(ex.output["value"], int) for ex in examples)

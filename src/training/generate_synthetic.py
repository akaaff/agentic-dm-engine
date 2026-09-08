"""Schema-agnostic synthetic dataset generation (Day 24).

Calls the teacher model per a DistillationTask's own `generate_prompt`,
validates the response against the task's own `output_schema`, and retries
on validation failure - Ollama's grammar-constrained decoding (see
src.llm.providers.chat_structured) makes a malformed response rare but not
impossible (a schema-valid-shaped response can still fail pydantic's own
value-level validation, e.g. a Literal-typed field, or the teacher
truncating mid-generation). Nothing here knows or cares what the task
actually is - only DistillationTask's interface (task_spec.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from src.llm.providers import chat_structured
from src.training.task_spec import DistillationTask


@dataclass(frozen=True)
class SyntheticExample:
    input: str
    """The full prompt sent to the teacher - also the student's training input."""
    output: dict[str, Any]
    """The teacher's validated structured response, as a JSON-serializable dict."""


class GenerationFailure(RuntimeError):
    pass


def generate_example(task: DistillationTask, max_attempts: int = 3) -> SyntheticExample:
    """One example: a fresh prompt from `task.generate_prompt()`, sent to
    the teacher, retried up to `max_attempts` times if the response doesn't
    validate against `task.output_schema`."""
    prompt = task.generate_prompt()
    last_error: Exception | None = None
    for _attempt in range(max_attempts):
        try:
            result = chat_structured(
                messages=[{"role": "user", "content": prompt}],
                schema=task.output_schema,
                model=task.teacher_model,
            )
        except ValidationError as exc:
            last_error = exc
            continue
        return SyntheticExample(input=prompt, output=result.model_dump(mode="json"))

    raise GenerationFailure(
        f"Task {task.name!r}: teacher response failed to validate after "
        f"{max_attempts} attempts: {last_error}"
    )


def generate_dataset(
    task: DistillationTask, n: int, max_attempts: int = 3
) -> list[SyntheticExample]:
    """Generates up to `n` examples - a single example repeatedly failing
    validation is skipped rather than aborting the whole batch, since a rare
    bad generation shouldn't cost every example already collected."""
    examples: list[SyntheticExample] = []
    for _ in range(n):
        try:
            examples.append(generate_example(task, max_attempts=max_attempts))
        except GenerationFailure:
            continue
    return examples

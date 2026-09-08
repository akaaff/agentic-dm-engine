"""Schema-agnostic synthetic dataset generation (Day 24, concurrency added
scaling up the intent-parser dataset).

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

from concurrent.futures import ThreadPoolExecutor
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


def _generate_from_prompt(
    task: DistillationTask, prompt: str, max_attempts: int
) -> SyntheticExample:
    """The retry loop against one already-built prompt - factored out of
    generate_example so generate_dataset's concurrent path can reuse it
    without touching task.generate_prompt() from multiple threads (see its
    own docstring for why that matters)."""
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


def generate_example(task: DistillationTask, max_attempts: int = 3) -> SyntheticExample:
    """One example: a fresh prompt from `task.generate_prompt()`, sent to
    the teacher, retried up to `max_attempts` times if the response doesn't
    validate against `task.output_schema`."""
    prompt = task.generate_prompt()
    return _generate_from_prompt(task, prompt, max_attempts)


def generate_dataset(
    task: DistillationTask, n: int, max_attempts: int = 3, max_workers: int = 1
) -> list[SyntheticExample]:
    """Generates up to `n` examples - a single example repeatedly failing
    validation is skipped rather than aborting the whole batch, since a rare
    bad generation shouldn't cost every example already collected.

    All `n` prompts are built up front, sequentially, on the calling thread
    *before* any concurrency starts - `task.generate_prompt()` typically
    closes over a shared `random.Random` (see task specs under
    src/training/tasks/), and `random.Random` isn't safe to call from
    multiple threads at once. Only the actual teacher calls (slow, I/O-bound
    HTTP requests that release the GIL while waiting) run concurrently, via
    a thread pool, when `max_workers > 1`. Confirmed live that Ollama itself
    processes concurrent requests in parallel rather than queueing them
    (see CLAUDE.md) - ~4x real throughput at 16-way concurrency against this
    project's actual prompt/schema shapes, not just a trivial prompt.
    """
    prompts = [task.generate_prompt() for _ in range(n)]

    def _attempt(prompt: str) -> SyntheticExample | None:
        try:
            return _generate_from_prompt(task, prompt, max_attempts)
        except GenerationFailure:
            return None

    if max_workers <= 1:
        results = [_attempt(p) for p in prompts]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_attempt, prompts))

    return [example for example in results if example is not None]

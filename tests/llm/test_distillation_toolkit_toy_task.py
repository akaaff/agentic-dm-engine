"""Day 24's actual verify gate: a deliberately unrelated toy task (extract
a person's name and age from a sentence - the exact example from the plan)
run through the full distillation pipeline - generate_synthetic ->
curate_dataset -> evaluate_structured_output - against the real Ollama
teacher, with zero D&D-specific code anywhere on the path. If this test
passes, "the pipeline is schema/task-agnostic" is demonstrated, not just
asserted: everything it touches (DistillationTask, SyntheticExample,
FieldAccuracy) is the identical machinery src/training/tasks/ (Day 25)
points at the real intent-parser task.
"""

from __future__ import annotations

import random

import pytest
from pydantic import BaseModel

from src.llm.providers import chat_structured
from src.training.curate_dataset import curate
from src.training.evaluate_structured_output import score_dataset
from src.training.generate_synthetic import generate_dataset
from src.training.task_spec import DistillationTask

pytestmark = pytest.mark.llm


class _NameAge(BaseModel):
    name: str
    age: int


_SENTENCES = [
    "Maria just turned 34 last week and started a new job.",
    "My grandfather, Tom, celebrated his 80th birthday yesterday.",
    "The new intern, Priya, is only 22 years old.",
    "Kwame is 45 and has been a teacher for two decades.",
    "Little Sofia turned 7 and loves dinosaurs.",
    "At 61, Robert is the oldest member of the hiking club.",
]


def _prompt_for(sentence: str) -> str:
    return (
        "Extract the person's name and age from the sentence below. Respond "
        "with a single JSON object matching the provided schema - nothing else.\n\n"
        f"Sentence: {sentence}"
    )


def _toy_task() -> DistillationTask:
    remaining = iter(_SENTENCES)

    def generate_prompt() -> str:
        sentence = next(remaining, None) or random.choice(_SENTENCES)
        return _prompt_for(sentence)

    return DistillationTask(
        name="toy_name_age", output_schema=_NameAge, generate_prompt=generate_prompt
    )


def test_full_pipeline_runs_end_to_end_on_a_toy_task_with_zero_dnd_code() -> None:
    task = _toy_task()

    # 1. generate_synthetic: real teacher calls, validated against _NameAge.
    raw = generate_dataset(task, n=len(_SENTENCES))
    assert len(raw) >= 4, "expected most of 6 simple extractions to validate cleanly"
    for example in raw:
        assert isinstance(example.output["name"], str) and example.output["name"]
        assert isinstance(example.output["age"], int)

    # 2. curate_dataset: dedup/revalidate/split - no examples lost beyond that.
    split = curate(task, raw, val_fraction=0.2, test_fraction=0.2, rng=random.Random(0))
    total_after_curation = len(split.train) + len(split.val) + len(split.test)
    assert total_after_curation == len(raw)  # nothing was a duplicate or invalid here

    # 3. evaluate_structured_output: score a second independent teacher pass
    # against the first, using real _NameAge instances end-to-end.
    sample = raw[: min(3, len(raw))]
    pairs: list[tuple[_NameAge, _NameAge]] = []
    for example in sample:
        expected = _NameAge.model_validate(example.output)
        actual = chat_structured(
            messages=[{"role": "user", "content": example.input}], schema=_NameAge
        )
        pairs.append((expected, actual))

    accuracy = score_dataset(pairs)
    assert set(accuracy.total) == {"name", "age"}
    assert accuracy.total["name"] == len(sample)
    assert accuracy.total["age"] == len(sample)
    assert 0.0 <= accuracy.overall_accuracy() <= 1.0

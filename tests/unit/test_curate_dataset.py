"""Day 24: curate_dataset is pure logic, no LLM involved - dedup, schema
re-validation, and leak-free splitting, exercised against a trivial toy
schema/dataset."""

from __future__ import annotations

import random

from pydantic import BaseModel

from src.training.curate_dataset import curate, dedupe, revalidate, split_dataset
from src.training.generate_synthetic import SyntheticExample
from src.training.task_spec import DistillationTask


class _Toy(BaseModel):
    value: int


def _toy_task() -> DistillationTask:
    return DistillationTask(name="toy", output_schema=_Toy, generate_prompt=lambda: "unused")


def test_dedupe_keeps_the_first_occurrence_of_a_repeated_input() -> None:
    examples = [
        SyntheticExample(input="a", output={"value": 1}),
        SyntheticExample(input="b", output={"value": 2}),
        SyntheticExample(input="a", output={"value": 99}),  # duplicate input, different output
    ]

    deduped = dedupe(examples)

    assert [ex.input for ex in deduped] == ["a", "b"]
    assert deduped[0].output == {"value": 1}  # first occurrence wins


def test_revalidate_drops_examples_that_no_longer_match_the_schema() -> None:
    examples = [
        SyntheticExample(input="a", output={"value": 1}),
        SyntheticExample(input="b", output={"wrong_field": "oops"}),
        SyntheticExample(input="c", output={"value": "not an int"}),
    ]

    valid = revalidate(_toy_task(), examples)

    assert [ex.input for ex in valid] == ["a"]


def test_split_dataset_respects_fractions_and_conserves_every_example() -> None:
    examples = [SyntheticExample(input=str(i), output={"value": i}) for i in range(20)]

    split = split_dataset(examples, val_fraction=0.2, test_fraction=0.2, rng=random.Random(0))

    assert len(split.val) == 4
    assert len(split.test) == 4
    assert len(split.train) == 12
    # No example lost or duplicated across splits.
    all_inputs = [ex.input for ex in split.train + split.val + split.test]
    assert sorted(all_inputs, key=int) == [str(i) for i in range(20)]


def test_split_dataset_never_leaks_an_input_across_splits() -> None:
    examples = [SyntheticExample(input=str(i), output={"value": i}) for i in range(30)]

    split = split_dataset(examples, val_fraction=0.1, test_fraction=0.1, rng=random.Random(1))

    train_inputs = {ex.input for ex in split.train}
    val_inputs = {ex.input for ex in split.val}
    test_inputs = {ex.input for ex in split.test}

    assert not (train_inputs & val_inputs)
    assert not (train_inputs & test_inputs)
    assert not (val_inputs & test_inputs)


def test_curate_runs_dedupe_then_revalidate_then_split() -> None:
    examples = [
        SyntheticExample(input="a", output={"value": 1}),
        SyntheticExample(input="a", output={"value": 1}),  # exact duplicate, dropped
        SyntheticExample(input="b", output={"bad": "shape"}),  # invalid, dropped
        SyntheticExample(input="c", output={"value": 3}),
        SyntheticExample(input="d", output={"value": 4}),
        SyntheticExample(input="e", output={"value": 5}),
        SyntheticExample(input="f", output={"value": 6}),
        SyntheticExample(input="g", output={"value": 7}),
        SyntheticExample(input="h", output={"value": 8}),
        SyntheticExample(input="i", output={"value": 9}),
        SyntheticExample(input="j", output={"value": 10}),
    ]

    split = curate(_toy_task(), examples, val_fraction=0.2, test_fraction=0.2, rng=random.Random(0))

    total = len(split.train) + len(split.val) + len(split.test)
    assert total == 9  # 11 - 1 duplicate - 1 invalid

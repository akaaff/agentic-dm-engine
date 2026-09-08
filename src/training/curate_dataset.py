"""Generic dataset curation (Day 24): dedup by input text, re-validate every
example against its task's current output_schema, and split into leak-free
train/val/test partitions. Schema-agnostic - operates purely on
SyntheticExample.input/.output, never on a task's specific fields.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.training.generate_synthetic import SyntheticExample
from src.training.task_spec import DistillationTask


@dataclass(frozen=True)
class DatasetSplit:
    train: list[SyntheticExample]
    val: list[SyntheticExample]
    test: list[SyntheticExample]


def dedupe(examples: list[SyntheticExample]) -> list[SyntheticExample]:
    """Drops exact-duplicate inputs, keeping the first occurrence - the
    teacher can (and does) occasionally regenerate an identical prompt from
    a randomized example_generator, especially at small sample counts."""
    seen: set[str] = set()
    deduped: list[SyntheticExample] = []
    for example in examples:
        if example.input in seen:
            continue
        seen.add(example.input)
        deduped.append(example)
    return deduped


def revalidate(task: DistillationTask, examples: list[SyntheticExample]) -> list[SyntheticExample]:
    """Drops any example whose recorded output no longer validates against
    the task's *current* output_schema - defends a dataset loaded from disk
    against having been generated under an older/different schema version,
    rather than trusting that whatever's on disk is still valid."""
    valid: list[SyntheticExample] = []
    for example in examples:
        try:
            task.output_schema.model_validate(example.output)
        except Exception:
            continue
        valid.append(example)
    return valid


def split_dataset(
    examples: list[SyntheticExample],
    val_fraction: float = 0.15,
    test_fraction: float = 0.15,
    rng: random.Random | None = None,
) -> DatasetSplit:
    """Leak-free by construction, not by checking afterward: this only ever
    partitions an already-deduped list by index after shuffling, so the same
    input string can never land in two splits - call dedupe() first."""
    rng = rng or random.Random()
    shuffled = list(examples)
    rng.shuffle(shuffled)

    n = len(shuffled)
    n_val = int(n * val_fraction)
    n_test = int(n * test_fraction)
    val = shuffled[:n_val]
    test = shuffled[n_val : n_val + n_test]
    train = shuffled[n_val + n_test :]
    return DatasetSplit(train=train, val=val, test=test)


def curate(
    task: DistillationTask,
    examples: list[SyntheticExample],
    val_fraction: float = 0.15,
    test_fraction: float = 0.15,
    rng: random.Random | None = None,
) -> DatasetSplit:
    """dedupe -> revalidate -> split, the standard pipeline from a raw
    generate_dataset() batch to something ready to train/eval against."""
    deduped = dedupe(examples)
    valid = revalidate(task, deduped)
    return split_dataset(valid, val_fraction=val_fraction, test_fraction=test_fraction, rng=rng)

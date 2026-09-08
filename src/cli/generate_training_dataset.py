"""CLI: generate + curate a training dataset for one of this project's
distillation tasks (Day 24's generic pipeline, pointed at a real task
config for the first time on Day 25). Writes `data/training/raw/<task>.jsonl`
(every generated example) and `data/training/curated/<task>_{train,val,test}.jsonl`
(deduped, re-validated, split) - both gitignored and regenerable by
re-running this, not committed.

Usage: `uv run python -m src.cli.generate_training_dataset --task intent_parser --n 300`
Needs a live Ollama.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from src.training.curate_dataset import curate
from src.training.generate_synthetic import SyntheticExample, generate_dataset
from src.training.task_spec import DistillationTask
from src.training.tasks.intent_parser_task import build_intent_parser_task

_TASKS: dict[str, Callable[[random.Random], DistillationTask]] = {
    "intent_parser": build_intent_parser_task,
}

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "training"
_RAW_DIR = _DATA_DIR / "raw"
_CURATED_DIR = _DATA_DIR / "curated"


def _write_jsonl(path: Path, examples: list[SyntheticExample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for example in examples:
            f.write(json.dumps({"input": example.input, "output": example.output}) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(_TASKS))
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    task = _TASKS[args.task](rng)

    print(f"Generating {args.n} examples for task {task.name!r} (teacher={task.teacher_model})...")
    raw = generate_dataset(task, n=args.n)
    print(f"  {len(raw)}/{args.n} validated successfully")

    verb_counts = Counter(example.output.get("verb") for example in raw)
    print(f"  verb breakdown: {dict(sorted(verb_counts.items()))}")

    _write_jsonl(_RAW_DIR / f"{task.name}.jsonl", raw)

    split = curate(task, raw, rng=random.Random(args.seed + 1))
    _write_jsonl(_CURATED_DIR / f"{task.name}_train.jsonl", split.train)
    _write_jsonl(_CURATED_DIR / f"{task.name}_val.jsonl", split.val)
    _write_jsonl(_CURATED_DIR / f"{task.name}_test.jsonl", split.test)

    dropped = len(raw) - (len(split.train) + len(split.val) + len(split.test))
    print(
        f"  curated: train={len(split.train)} val={len(split.val)} test={len(split.test)} "
        f"(dropped {dropped} duplicate/invalid)"
    )

    train_inputs = {ex.input for ex in split.train}
    val_inputs = {ex.input for ex in split.val}
    test_inputs = {ex.input for ex in split.test}
    assert not (train_inputs & val_inputs), "train/val overlap!"
    assert not (train_inputs & test_inputs), "train/test overlap!"
    assert not (val_inputs & test_inputs), "val/test overlap!"
    print("  confirmed: zero overlap between train/val/test")

    print(
        f"\nWrote data/training/raw/{task.name}.jsonl and data/training/curated/{task.name}_*.jsonl"
    )


if __name__ == "__main__":
    main()

"""CLI: generate + curate a training dataset for one of this project's
distillation tasks (Day 24's generic pipeline, pointed at a real task
config for the first time on Day 25).

Two modes, split so a large dataset can be built safely in chunks (Day 25
follow-up: scaling to ~10k examples) rather than one long unattended run
that loses everything if it dies partway through:

  Generate one chunk (repeat with a different --seed for each chunk):
    uv run python -m src.cli.generate_training_dataset \
        --task intent_parser --n 2000 --seed 1 --workers 16
  Merge every chunk written so far into one final curated split:
    uv run python -m src.cli.generate_training_dataset --task intent_parser --merge

Chunk files land in `data/training/raw/<task>_seed<seed>.jsonl`; --merge
combines all of them, dedupes/re-validates/splits once (curate_dataset.py),
and writes `data/training/curated/<task>_{train,val,test}.jsonl`. Both
directories are gitignored and regenerable by re-running this, not
committed. Needs a live Ollama.
"""

from __future__ import annotations

import argparse
import random
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from src.training.curate_dataset import curate
from src.training.dataset_io import read_jsonl, write_jsonl
from src.training.generate_synthetic import SyntheticExample, generate_dataset
from src.training.task_spec import DistillationTask
from src.training.tasks.intent_parser_task import build_intent_parser_task

_TASKS: dict[str, Callable[[random.Random], DistillationTask]] = {
    "intent_parser": build_intent_parser_task,
}

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "training"
_RAW_DIR = _DATA_DIR / "raw"
_CURATED_DIR = _DATA_DIR / "curated"


def _generate_chunk(task_name: str, n: int, seed: int, workers: int) -> None:
    rng = random.Random(seed)
    task = _TASKS[task_name](rng)

    print(
        f"Generating {n} examples for task {task.name!r} "
        f"(teacher={task.teacher_model}, workers={workers})..."
    )
    raw = generate_dataset(task, n=n, max_workers=workers)
    print(f"  {len(raw)}/{n} validated successfully")

    verb_counts = Counter(example.output.get("verb") for example in raw)
    print(f"  verb breakdown: {dict(sorted(verb_counts.items()))}")

    chunk_path = _RAW_DIR / f"{task.name}_seed{seed}.jsonl"
    write_jsonl(chunk_path, raw)
    print(f"\nWrote {chunk_path}")


def _merge_and_curate(task_name: str) -> None:
    task = _TASKS[task_name](random.Random(0))  # rng unused for curation, just needs output_schema
    chunk_paths = sorted(_RAW_DIR.glob(f"{task_name}_seed*.jsonl"))
    if not chunk_paths:
        raise SystemExit(f"No raw chunk files found matching {task_name}_seed*.jsonl in {_RAW_DIR}")

    all_examples: list[SyntheticExample] = []
    for path in chunk_paths:
        chunk_examples = read_jsonl(path)
        all_examples.extend(chunk_examples)
        print(f"  loaded {len(chunk_examples)} from {path.name}")
    print(f"Total: {len(all_examples)} examples from {len(chunk_paths)} chunk file(s)")

    split = curate(task, all_examples, rng=random.Random(0))
    dropped = len(all_examples) - (len(split.train) + len(split.val) + len(split.test))
    print(
        f"  curated: train={len(split.train)} val={len(split.val)} test={len(split.test)} "
        f"(dropped {dropped} duplicate/invalid)"
    )

    write_jsonl(_CURATED_DIR / f"{task_name}_train.jsonl", split.train)
    write_jsonl(_CURATED_DIR / f"{task_name}_val.jsonl", split.val)
    write_jsonl(_CURATED_DIR / f"{task_name}_test.jsonl", split.test)

    train_inputs = {ex.input for ex in split.train}
    val_inputs = {ex.input for ex in split.val}
    test_inputs = {ex.input for ex in split.test}
    assert not (train_inputs & val_inputs), "train/val overlap!"
    assert not (train_inputs & test_inputs), "train/test overlap!"
    assert not (val_inputs & test_inputs), "val/test overlap!"
    print("  confirmed: zero overlap between train/val/test")

    print(f"\nWrote data/training/curated/{task_name}_{{train,val,test}}.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(_TASKS))
    parser.add_argument(
        "--n", type=int, default=300, help="Examples to generate (ignored with --merge)."
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Seed for this chunk (ignored with --merge)."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent teacher calls in flight (ignored with --merge).",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Combine every raw chunk file for --task into one curated train/val/test split.",
    )
    args = parser.parse_args()

    if args.merge:
        _merge_and_curate(args.task)
    else:
        _generate_chunk(args.task, n=args.n, seed=args.seed, workers=args.workers)


if __name__ == "__main__":
    main()

"""CLI: run Day 26's isolated eval - base vs. fine-tuned vs. teacher on a
held-out slice of the intent-parser test split - and save the results
table to `data/training/eval_results/` (committed, unlike the regenerable
raw/curated data). Needs a live Ollama (for the teacher column) and a CUDA
GPU (for the two student columns).

  uv run python -m src.cli.evaluate_intent_parser --n 300
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from src.training.dataset_io import read_jsonl
from src.training.evaluate_models import evaluate, scores_to_markdown
from src.training.tasks.intent_parser_task import build_intent_parser_task

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_SPLIT = _PROJECT_ROOT / "data" / "training" / "curated" / "intent_parser_test.jsonl"
_DEFAULT_ADAPTER_DIR = _PROJECT_ROOT / "models" / "intent_parser_lora"
_RESULTS_PATH = _PROJECT_ROOT / "data" / "training" / "eval_results" / "intent_parser_eval.md"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300, help="Test-split examples to evaluate on.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--adapter-dir", type=Path, default=_DEFAULT_ADAPTER_DIR)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--workers", type=int, default=16, help="Concurrent teacher calls.")
    parser.add_argument("--out", type=Path, default=_RESULTS_PATH)
    args = parser.parse_args()

    task = build_intent_parser_task()
    test_examples = read_jsonl(_TEST_SPLIT)
    sample = random.Random(args.seed).sample(test_examples, min(args.n, len(test_examples)))
    print(f"Evaluating on {len(sample)} of {len(test_examples)} held-out test examples...")

    scores = evaluate(
        sample,
        base_model_id=task.base_model,
        adapter_dir=args.adapter_dir,
        teacher_model=task.teacher_model,
        batch_size=args.batch_size,
        teacher_workers=args.workers,
    )

    markdown = scores_to_markdown(scores, len(sample))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()

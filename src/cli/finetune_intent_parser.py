"""CLI: LoRA fine-tune the intent-parser student on the curated dataset
(Day 26). Loads `data/training/curated/intent_parser_{train,val}.jsonl`,
runs `finetune_lora`, and saves the adapter to `models/intent_parser_lora/`
(gitignored). Optionally pushes the adapter to the Hugging Face Hub.

  Full run with wandb loss-curve logging:
    uv run python -m src.cli.finetune_intent_parser --wandb
  Quick smoke test (small subset, no wandb):
    uv run python -m src.cli.finetune_intent_parser --max-train 200 --max-val 50 --epochs 1
  Push the trained adapter to the Hub afterward:
    uv run python -m src.cli.finetune_intent_parser --wandb --push-to-hub

Needs a CUDA GPU. `--wandb` needs `uv run wandb login` done first;
`--push-to-hub` needs `uv run hf auth login` done first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.training.dataset_io import read_jsonl
from src.training.finetune_lora import FineTuneConfig, finetune_lora
from src.training.tasks.intent_parser_task import build_intent_parser_task

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CURATED_DIR = _PROJECT_ROOT / "data" / "training" / "curated"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "models" / "intent_parser_lora"
_DEFAULT_HF_REPO = "akaaff/agentic-dm-intent-parser-lora"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-train", type=int, default=None, help="Cap train split (smoke test).")
    parser.add_argument("--max-val", type=int, default=None, help="Cap val split (smoke test).")
    parser.add_argument("--wandb", action="store_true", help="Log the loss curve to W&B.")
    parser.add_argument("--push-to-hub", action="store_true", help="Push the adapter to the Hub.")
    parser.add_argument("--hf-repo", default=_DEFAULT_HF_REPO)
    args = parser.parse_args()

    task = build_intent_parser_task()
    train_examples = read_jsonl(_CURATED_DIR / "intent_parser_train.jsonl")
    val_examples = read_jsonl(_CURATED_DIR / "intent_parser_val.jsonl")
    if args.max_train is not None:
        train_examples = train_examples[: args.max_train]
    if args.max_val is not None:
        val_examples = val_examples[: args.max_val]

    print(
        f"Fine-tuning {task.base_model} on {len(train_examples)} train / "
        f"{len(val_examples)} val examples -> {args.output_dir}"
    )

    config = FineTuneConfig(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        report_to="wandb" if args.wandb else "none",
        run_name="intent_parser_lora" if args.wandb else None,
    )
    adapter_dir = finetune_lora(task, train_examples, val_examples, config)
    print(f"\nAdapter saved to {adapter_dir}")

    if args.push_to_hub:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.hf_repo, private=True, exist_ok=True, repo_type="model")
        api.upload_folder(folder_path=str(adapter_dir), repo_id=args.hf_repo, repo_type="model")
        print(f"Pushed to https://huggingface.co/{args.hf_repo}")


if __name__ == "__main__":
    main()

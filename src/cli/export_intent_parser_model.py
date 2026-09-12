"""CLI: merge the fine-tuned intent-parser LoRA adapter into its base model
and save the merged result to disk (Day 27 detour - GGUF/Ollama serving).

`local_parser.py` merges the adapter in memory on every load; GGUF
conversion needs the merge sitting on disk instead, since
`convert_hf_to_gguf.py` reads a plain HF model directory, not a PEFT
adapter. This is a one-time export step, not part of the live app.

  uv run python -m src.cli.export_intent_parser_model
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.training.export_merged_model import export_merged_model

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_ADAPTER_DIR = _PROJECT_ROOT / "models" / "intent_parser_lora"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "models" / "intent_parser_merged"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-dir", type=Path, default=_DEFAULT_ADAPTER_DIR)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = export_merged_model(args.adapter_dir, args.output_dir)
    print(f"Merged model saved to {output_dir}")


if __name__ == "__main__":
    main()

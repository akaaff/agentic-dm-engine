"""Reading/writing a SyntheticExample dataset as JSONL - shared by every
CLI under src/cli/ that touches data/training/ (generation, fine-tuning,
eval), so there's one place that defines the on-disk format.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.training.generate_synthetic import SyntheticExample


def write_jsonl(path: Path, examples: list[SyntheticExample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for example in examples:
            f.write(json.dumps({"input": example.input, "output": example.output}) + "\n")


def read_jsonl(path: Path) -> list[SyntheticExample]:
    examples = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            examples.append(SyntheticExample(input=row["input"], output=row["output"]))
    return examples

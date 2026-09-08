"""Day 26: dataset_io's write_jsonl/read_jsonl round trip - the shared
on-disk format every training CLI depends on."""

from __future__ import annotations

from pathlib import Path

from src.training.dataset_io import read_jsonl, write_jsonl
from src.training.generate_synthetic import SyntheticExample


def test_write_then_read_round_trips_every_example(tmp_path: Path) -> None:
    examples = [
        SyntheticExample(input="a", output={"verb": "attack", "target": "goblin_1"}),
        SyntheticExample(input="b", output={"verb": "dodge"}),
    ]
    path = tmp_path / "sub" / "examples.jsonl"

    write_jsonl(path, examples)
    loaded = read_jsonl(path)

    assert loaded == examples


def test_write_jsonl_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "does" / "not" / "exist" / "examples.jsonl"

    write_jsonl(path, [SyntheticExample(input="a", output={})])

    assert path.exists()


def test_write_jsonl_handles_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"

    write_jsonl(path, [])

    assert read_jsonl(path) == []

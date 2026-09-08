"""Day 26: finetune_lora's actual training run needs a real model download
and a GPU, so it isn't unit-testable offline - that's covered by manual
live verification (loss curve + eval table). This test covers the one
piece of pure, offline-testable logic in the module: converting
SyntheticExample pairs into the chat-formatted dataset trl expects."""

from __future__ import annotations

import json

from src.training.finetune_lora import _to_chat_dataset
from src.training.generate_synthetic import SyntheticExample


def test_to_chat_dataset_builds_user_and_assistant_turns() -> None:
    examples = [
        SyntheticExample(input="prompt one", output={"verb": "attack", "target": "goblin_1"}),
        SyntheticExample(input="prompt two", output={"verb": "dodge"}),
    ]

    dataset = _to_chat_dataset(examples)

    assert len(dataset) == 2
    row = dataset[0]
    assert row["messages"] == [
        {"role": "user", "content": "prompt one"},
        {"role": "assistant", "content": json.dumps({"verb": "attack", "target": "goblin_1"})},
    ]


def test_to_chat_dataset_assistant_turn_is_valid_json_matching_the_output() -> None:
    example = SyntheticExample(
        input="p", output={"verb": "skill_check", "params": {"skill": "stealth"}}
    )

    dataset = _to_chat_dataset([example])

    assistant_content = dataset[0]["messages"][1]["content"]
    assert json.loads(assistant_content) == example.output


def test_to_chat_dataset_handles_an_empty_list() -> None:
    dataset = _to_chat_dataset([])
    assert len(dataset) == 0

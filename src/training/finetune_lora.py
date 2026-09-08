"""Generic LoRA fine-tuning (Day 26) - task-agnostic like the rest of
src/training/: takes a DistillationTask (for its base_model) plus
already-curated train/val SyntheticExample lists, and produces a LoRA
adapter via trl's SFTTrainer. No quantization (no bitsandbytes) - the
0.5B-parameter target model fits comfortably in bf16 on a 10GB GPU without
it, and bitsandbytes has a history of rockier Windows support; only worth
reaching for if a future run escalates to a bigger base model and hits real
memory pressure.

Each SyntheticExample becomes one chat-formatted training pair: the full
prompt (already byte-identical to what the live game sends, see
intent_parser_task.py) as the user turn, the teacher's validated JSON output
as the assistant turn. trl's `assistant_only_loss` masks the loss to just
that assistant turn, so the model isn't trained to predict the (highly
variable, randomized) prompt text itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

from src.training.generate_synthetic import SyntheticExample
from src.training.task_spec import DistillationTask

_DEFAULT_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


@dataclass(frozen=True)
class FineTuneConfig:
    output_dir: Path
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = _DEFAULT_TARGET_MODULES
    num_train_epochs: float = 3.0
    per_device_train_batch_size: int = 8
    gradient_accumulation_steps: int = 2
    learning_rate: float = 2e-4
    eval_steps: int = 50
    logging_steps: int = 10
    max_length: int = 1024
    report_to: str = "none"
    """"wandb" to enable live loss-curve logging (Day 26's chosen tool) -
    left as "none" by default so offline/CI runs never need a wandb login."""
    run_name: str | None = None


def _to_chat_dataset(examples: list[SyntheticExample]) -> Dataset:
    return Dataset.from_list(
        [
            {
                "messages": [
                    {"role": "user", "content": example.input},
                    {"role": "assistant", "content": json.dumps(example.output)},
                ]
            }
            for example in examples
        ]
    )


def finetune_lora(
    task: DistillationTask,
    train_examples: list[SyntheticExample],
    val_examples: list[SyntheticExample],
    config: FineTuneConfig,
) -> Path:
    """Runs the LoRA fine-tune and saves the adapter + tokenizer under
    `config.output_dir`, returning that path."""
    tokenizer = AutoTokenizer.from_pretrained(task.base_model)
    model = AutoModelForCausalLM.from_pretrained(task.base_model, dtype=torch.bfloat16)

    lora_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=list(config.target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )

    train_dataset = _to_chat_dataset(train_examples)
    val_dataset = _to_chat_dataset(val_examples)

    training_args = SFTConfig(
        output_dir=str(config.output_dir),
        num_train_epochs=config.num_train_epochs,
        per_device_train_batch_size=config.per_device_train_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        eval_strategy="steps",
        eval_steps=config.eval_steps,
        logging_steps=config.logging_steps,
        save_strategy="epoch",
        save_total_limit=1,
        report_to=config.report_to,
        run_name=config.run_name,
        bf16=True,
        assistant_only_loss=True,
        max_length=config.max_length,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        peft_config=lora_config,
        processing_class=tokenizer,
    )
    trainer.train()

    config.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(config.output_dir))
    tokenizer.save_pretrained(str(config.output_dir))
    return config.output_dir

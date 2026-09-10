"""Day 26's isolated eval: base vs. fine-tuned vs. teacher on a held-out
slice of the intent-parser test split.

Fairness note (see CLAUDE.md): the teacher runs through Ollama's server-side
grammar-constrained decoding and structurally cannot emit invalid JSON; the
base and fine-tuned students run through plain `transformers.generate()`
with no such constraint. So this reports three numbers per model rather
than one blended score - a valid-JSON rate, a strict "parses as a real
ParsedAction" rate, and field-level accuracy over *all* examples (an
unparseable prediction scores every field wrong) - which separates "did
fine-tuning teach reliable formatting" from "did it teach the right verb /
target / skill".
"""

from __future__ import annotations

import gc
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from peft import PeftModel
from pydantic import ValidationError
from transformers import AutoModelForCausalLM, AutoTokenizer

from src.engine.actions import ParsedAction
from src.llm.providers import chat_structured
from src.training.evaluate_structured_output import FieldAccuracy, score_fields
from src.training.generate_synthetic import SyntheticExample

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ModelScore:
    name: str
    valid_json_rate: float
    valid_action_rate: float
    field_accuracy: FieldAccuracy


def _extract_json(text: str) -> dict[str, Any] | None:
    """A student without grammar constraints often wraps the JSON in prose
    or a ```json fence - try a plain parse first, then the first {...} block."""
    candidates = [text]
    match = _JSON_OBJECT_RE.search(text)
    if match:
        candidates.append(match.group(0))
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _run_local_model(
    base_model_id: str,
    adapter_dir: Path | None,
    prompts: list[str],
    batch_size: int,
    max_new_tokens: int,
) -> list[dict[str, Any] | None]:
    tokenizer = AutoTokenizer.from_pretrained(base_model_id)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # transformers/peft's model class hierarchy (base vs. PeftModel-wrapped)
    # isn't cleanly expressible in mypy here - this function just treats the
    # loaded model as a black box to call .generate() on.
    model: Any = AutoModelForCausalLM.from_pretrained(
        base_model_id, dtype=torch.bfloat16, device_map="cuda"
    )
    if adapter_dir is not None:
        model = PeftModel.from_pretrained(model, str(adapter_dir))
    model.eval()

    predictions: list[dict[str, Any] | None] = []
    for start in range(0, len(prompts), batch_size):
        batch = prompts[start : start + batch_size]
        texts = [
            tokenizer.apply_chat_template(
                [{"role": "user", "content": p}], tokenize=False, add_generation_prompt=True
            )
            for p in batch
        ]
        enc = tokenizer(
            texts, return_tensors="pt", padding=True, truncation=True, max_length=1536
        ).to("cuda")
        with torch.no_grad():
            generated = model.generate(
                **enc,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        for seq in generated:
            new_tokens = seq[enc["input_ids"].shape[1] :]
            decoded = str(tokenizer.decode(new_tokens, skip_special_tokens=True))
            predictions.append(_extract_json(decoded))

    del model
    gc.collect()
    torch.cuda.empty_cache()
    return predictions


def _run_teacher(prompts: list[str], model: str, max_workers: int) -> list[dict[str, Any] | None]:
    def _one(prompt: str) -> dict[str, Any] | None:
        try:
            result = chat_structured(
                messages=[{"role": "user", "content": prompt}], schema=ParsedAction, model=model
            )
        except Exception:
            return None
        return result.model_dump(mode="json")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_one, prompts))


def score_predictions(
    name: str, expected: list[dict[str, Any]], predicted: list[dict[str, Any] | None]
) -> ModelScore:
    accuracy = FieldAccuracy()
    valid_json = 0
    valid_action = 0
    for exp, pred in zip(expected, predicted, strict=True):
        if pred is not None:
            valid_json += 1
            try:
                ParsedAction.model_validate(pred)
                valid_action += 1
            except ValidationError:
                pass
        accuracy.merge(score_fields(exp, pred or {}))
    n = len(expected)
    return ModelScore(name, valid_json / n, valid_action / n, accuracy)


def evaluate(
    examples: list[SyntheticExample],
    base_model_id: str,
    adapter_dir: Path,
    teacher_model: str,
    batch_size: int = 16,
    teacher_workers: int = 16,
    max_new_tokens: int = 256,
) -> list[ModelScore]:
    prompts = [ex.input for ex in examples]
    expected = [ex.output for ex in examples]

    return [
        score_predictions(
            "base (no fine-tune)",
            expected,
            _run_local_model(base_model_id, None, prompts, batch_size, max_new_tokens),
        ),
        score_predictions(
            "fine-tuned (LoRA)",
            expected,
            _run_local_model(base_model_id, adapter_dir, prompts, batch_size, max_new_tokens),
        ),
        score_predictions(
            "teacher (Ollama)", expected, _run_teacher(prompts, teacher_model, teacher_workers)
        ),
    ]


def scores_to_markdown(scores: list[ModelScore], n_examples: int) -> str:
    fields = sorted({path for score in scores for path in score.field_accuracy.total})
    header = ["model", "valid JSON", "valid ParsedAction", "field acc (all)", *fields]
    lines = [
        f"# Intent-parser distillation eval ({n_examples} held-out test examples)",
        "",
        "`base` and `fine-tuned` run through plain `transformers.generate()` (no",
        "grammar constraint); `teacher` runs through Ollama's constrained decoding,",
        "which is why it can't emit invalid JSON. `field acc (all)` scores every",
        "example - an unparseable prediction counts every field wrong.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for score in scores:
        row = [
            score.name,
            f"{score.valid_json_rate:.1%}",
            f"{score.valid_action_rate:.1%}",
            f"{score.field_accuracy.overall_accuracy():.1%}",
            *(f"{score.field_accuracy.accuracy(field):.1%}" for field in fields),
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"

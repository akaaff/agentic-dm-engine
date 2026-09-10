"""Day 27: the fine-tuned intent-parser student, served locally via
transformers/peft instead of Ollama.

Same prompt as the teacher path (`build_intent_parser_prompt`), same output
contract (a valid `ParsedAction`). There's no server-side grammar
constraint here, so the raw completion is JSON-parsed and validated with a
small retry (first attempt greedy, then sampled for a different shot);
returning None if nothing validates lets the caller fall back to an
`invalid` action, the same terminal state the graph already handles for an
unparseable turn.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import torch
from peft import AutoPeftModelForCausalLM
from pydantic import ValidationError
from transformers import AutoTokenizer

from src.engine.actions import ParsedAction
from src.llm.providers import extract_json_object

_MAX_NEW_TOKENS = 256
_ATTEMPTS = 3


@cache
def _load(adapter_dir: str) -> tuple[Any, Any]:
    """Loaded once per process - the base model + LoRA adapter together,
    plus the tokenizer saved alongside the adapter."""
    model = AutoPeftModelForCausalLM.from_pretrained(
        adapter_dir, dtype=torch.bfloat16, device_map="cuda"
    )
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(adapter_dir)
    return model, tokenizer


def parse_intent_local(prompt: str, adapter_dir: str) -> ParsedAction | None:
    """Returns a validated ParsedAction, or None if the model never produced
    valid JSON matching the schema across `_ATTEMPTS` tries."""
    model, tokenizer = _load(adapter_dir)
    text = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
    )
    enc = tokenizer(text, return_tensors="pt").to(model.device)
    prompt_len = enc["input_ids"].shape[1]
    pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id

    for attempt in range(_ATTEMPTS):
        with torch.no_grad():
            generated = model.generate(
                **enc,
                max_new_tokens=_MAX_NEW_TOKENS,
                do_sample=attempt > 0,
                temperature=0.7 if attempt > 0 else None,
                pad_token_id=pad_token_id,
            )
        completion = tokenizer.decode(generated[0][prompt_len:], skip_special_tokens=True)
        parsed = extract_json_object(str(completion))
        if parsed is not None:
            try:
                return ParsedAction.model_validate(parsed)
            except ValidationError:
                continue

    return None

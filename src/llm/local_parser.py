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

# A ParsedAction JSON is well under 100 tokens; a low cap keeps a
# non-stopping generation from running away (Day 27 latency finding).
_MAX_NEW_TOKENS = 96
_ATTEMPTS = 3


@cache
def _load(adapter_dir: str) -> tuple[Any, Any]:
    """Loaded once per process. The LoRA adapter is merged into the base
    weights (`merge_and_unload`) so inference is a plain forward pass rather
    than base + (A@B) per layer - a real chunk of the naive transformers
    latency (Day 27)."""
    peft_model = AutoPeftModelForCausalLM.from_pretrained(
        adapter_dir, dtype=torch.bfloat16, device_map="cuda"
    )
    model = peft_model.merge_and_unload()
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
    # Stop on end-of-turn as well as end-of-text - the chat-formatted
    # training data ends each assistant turn with <|im_end|>, and without
    # this the greedy decode runs to max_new_tokens far more often.
    eos_ids = {tokenizer.eos_token_id, tokenizer.convert_tokens_to_ids("<|im_end|>")}
    eos_token_id = sorted(i for i in eos_ids if isinstance(i, int) and i >= 0)

    for attempt in range(_ATTEMPTS):
        with torch.no_grad():
            generated = model.generate(
                **enc,
                max_new_tokens=_MAX_NEW_TOKENS,
                do_sample=attempt > 0,
                temperature=0.7 if attempt > 0 else None,
                pad_token_id=pad_token_id,
                eos_token_id=eos_token_id,
            )
        completion = tokenizer.decode(generated[0][prompt_len:], skip_special_tokens=True)
        parsed = extract_json_object(str(completion))
        if parsed is not None:
            try:
                return ParsedAction.model_validate(parsed)
            except ValidationError:
                continue

    return None

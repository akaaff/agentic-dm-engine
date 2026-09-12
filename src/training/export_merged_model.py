"""Generic: merge a LoRA adapter into its base model and save the result as
a plain HF-format directory (safetensors + tokenizer + config).

Serving the fine-tuned intent-parser through `local_parser.py` merges the
adapter in memory on every process start (`merge_and_unload`, see Day 27).
That's fine for serving through transformers, but GGUF conversion
(`llama.cpp/convert_hf_to_gguf.py`) needs a merged model sitting on disk to
read - it doesn't understand PEFT adapters. This persists that merge once,
task-agnostic (any PEFT adapter dir in, any output dir out), so it plugs
into the distillation toolkit the same way the rest of Day 24-27 did.
"""

from __future__ import annotations

from pathlib import Path

import torch
from peft import AutoPeftModelForCausalLM
from transformers import AutoTokenizer


def export_merged_model(adapter_dir: str | Path, output_dir: str | Path) -> Path:
    """Load `adapter_dir` (a PEFT adapter saved on top of some base model),
    merge the adapter weights into the base model, and save the merged
    result plus its tokenizer to `output_dir`. Returns `output_dir`.
    """
    output_dir = Path(output_dir)
    model = AutoPeftModelForCausalLM.from_pretrained(str(adapter_dir), dtype=torch.bfloat16)
    merged = model.merge_and_unload()
    merged.save_pretrained(output_dir)
    AutoTokenizer.from_pretrained(str(adapter_dir)).save_pretrained(output_dir)
    return output_dir

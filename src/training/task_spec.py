"""Generic task specification for the distillation toolkit (Day 24).

A DistillationTask describes everything *task-specific* about a
distillation target: how to produce one training-example prompt, and what
pydantic schema the teacher's structured output for it must validate
against. Everything else - calling the teacher and retrying on validation
failure (generate_synthetic.py), dataset dedup/re-validation/splitting
(curate_dataset.py), and field-level accuracy scoring (evaluate_structured_
output.py) - is plain Python that only ever touches a DistillationTask
through this interface, never anything D&D-specific. src/training/tasks/
(added Day 25) plugs the real intent-parser in as one instance of this;
tests/llm/test_distillation_toolkit_toy_task.py plugs in a deliberately
unrelated toy task to prove that boundary is real, not aspirational.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from src.config import OLLAMA_TEACHER_MODEL


@dataclass(frozen=True)
class DistillationTask:
    name: str
    """Short slug used for dataset/output file naming, e.g. "intent_parser"."""

    output_schema: type[BaseModel]
    """The pydantic model the teacher's (and later, the fine-tuned student's)
    structured output must validate against."""

    generate_prompt: Callable[[], str]
    """Produces one complete, ready-to-send prompt each call, covering both
    randomizing a training scenario and templating it into text - the same
    string doubles as the student's eventual training input, so there's no
    separate "raw scenario vs. templated prompt" distinction to keep in sync."""

    teacher_model: str = OLLAMA_TEACHER_MODEL
    """Ollama model queried to generate labels for the synthetic dataset."""

    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    """HuggingFace model id the toolkit will LoRA-fine-tune on this task's
    curated dataset - not used until Day 26, but every model choice this
    task needs lives in one place from the start."""

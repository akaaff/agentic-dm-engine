"""Thin Ollama client - direct httpx calls to /api/chat, not langchain-ollama.

Ollama's native structured-output support (the `format` field, a full JSON
schema) constrains generation server-side via grammar-based decoding, which
is more reliable for getting valid JSON matching a pydantic schema out of a
7B model than hoping a function-calling abstraction lines up - confirmed
live against the real ParsedAction schema before building anything on top
of it. There's little benefit to a provider-abstraction layer for a project
that only ever targets one local Ollama instance.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import httpx
from pydantic import BaseModel

from src.config import OLLAMA_BASE_URL, OLLAMA_TEACHER_MODEL

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """`name` without extension, e.g. "intent_parser" -> prompts/intent_parser.md."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


# The first call after Ollama has been idle can involve loading the model
# into memory, observed live at ~30s for qwen2.5:7b-instruct; generation
# itself after that is a few seconds. Generous timeout to cover a cold load.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def chat(
    messages: list[dict[str, str]],
    model: str = OLLAMA_TEACHER_MODEL,
    temperature: float = 0.7,
) -> str:
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return str(response.json()["message"]["content"])


def chat_structured[T: BaseModel](
    messages: list[dict[str, str]],
    schema: type[T],
    model: str = OLLAMA_TEACHER_MODEL,
    temperature: float = 0.2,
) -> T:
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "format": schema.model_json_schema(),
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return schema.model_validate_json(content)

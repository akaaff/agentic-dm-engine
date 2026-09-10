"""Central config - plain env vars with defaults, no framework yet.

Kept deliberately simple on Day 1 (just this project's local Ollama/API URLs).
Revisit with pydantic-settings once Day 8's FastAPI service needs real config
validation.
"""

from __future__ import annotations

import os

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TEACHER_MODEL = os.environ.get("OLLAMA_TEACHER_MODEL", "qwen2.5:7b-instruct")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./agentic_dm_engine.db")

# Day 27: which backend the live intent_parser node uses.
#   "teacher"   - the Ollama 7B model, grammar-constrained (default, unchanged).
#   "finetuned" - the LoRA-distilled 0.5B student loaded via transformers/peft
#                 from INTENT_PARSER_ADAPTER_DIR. Same prompt either way.
INTENT_PARSER_BACKEND = os.environ.get("INTENT_PARSER_BACKEND", "teacher")
INTENT_PARSER_ADAPTER_DIR = os.environ.get("INTENT_PARSER_ADAPTER_DIR", "models/intent_parser_lora")

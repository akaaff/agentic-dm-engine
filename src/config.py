"""Central config - plain env vars with defaults, no framework yet.

Kept deliberately simple on Day 1 (just this project's local Ollama/API URLs).
Revisit with pydantic-settings once Day 8's FastAPI service needs real config
validation.
"""

from __future__ import annotations

import os

# 127.0.0.1, not "localhost": on this Windows machine, httpx resolving
# "localhost" adds a flat ~2.2s per request (confirmed live, Day 27 detour -
# independent of GPU load, model, or request content; disappeared entirely
# once pointed at the literal IPv4 loopback address instead). This was
# silently taxing every LLM call in the app (narrator/player_agent/
# intent_parser/judge/training) since Day 12, not just the new backend that
# happened to surface it - see CLAUDE.md.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_TEACHER_MODEL = os.environ.get("OLLAMA_TEACHER_MODEL", "qwen2.5:7b-instruct")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./agentic_dm_engine.db")

# Day 27 (+ Day 27 detour): which backend the live intent_parser node uses.
#   "teacher"          - the Ollama 7B model, grammar-constrained (default, unchanged).
#   "finetuned"        - the LoRA-distilled 0.5B student loaded via transformers/peft
#                        from INTENT_PARSER_ADAPTER_DIR. Same prompt either way.
#   "finetuned_ollama" - the same distilled student, merged and converted to GGUF,
#                        served by Ollama as INTENT_PARSER_OLLAMA_MODEL - same
#                        llama.cpp engine as the teacher (grammar constraint
#                        turned OFF for this one - see chat_structured_best_effort),
#                        which is what actually makes it faster than "finetuned"
#                        (see CLAUDE.md's Day 27 detour entry).
INTENT_PARSER_BACKEND = os.environ.get("INTENT_PARSER_BACKEND", "teacher")
INTENT_PARSER_ADAPTER_DIR = os.environ.get("INTENT_PARSER_ADAPTER_DIR", "models/intent_parser_lora")
INTENT_PARSER_OLLAMA_MODEL = os.environ.get("INTENT_PARSER_OLLAMA_MODEL", "dm-intent-parser")

# Set SCENE_IMAGES_ENABLED=0 to skip SD-Turbo entirely - frees ~3GB of VRAM
# (e.g. to run the "finetuned" intent-parser student alongside the Ollama
# teacher on a 10GB card, Day 27), or just for faster iteration.
SCENE_IMAGES_ENABLED = os.environ.get("SCENE_IMAGES_ENABLED", "1") != "0"

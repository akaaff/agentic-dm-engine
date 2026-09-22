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

# Issue #40: extra CORS origins beyond the hardcoded local Vite dev ones
# (see api/main.py) - a comma-separated list, e.g. a Cloudflare Tunnel
# hostname (https://xyz.trycloudflare.com) for anyone serving the frontend
# from a different origin than the API itself. Not needed for the common
# single-tunnel case (the Vite dev proxy/a same-origin deployment means the
# browser never makes a cross-origin request at all - see vite.config.ts),
# only for a genuinely split frontend/backend hosting setup.
_extra_cors_origins_raw = os.environ.get("EXTRA_CORS_ORIGINS", "")
EXTRA_CORS_ORIGINS = [origin for origin in _extra_cors_origins_raw.split(",") if origin]

# Issue #42: a single shared passphrase gating every REST/WS endpoint except
# /health - proportionate to the real threat model here ("a few friends
# playing D&D", not a real user/account system - see the issue itself for
# why that's explicitly out of scope). Unset (the default) disables the gate
# entirely, so local dev is unaffected; a genuinely internet-reachable
# deployment (a Cloudflare Tunnel hostname, issue #40) sets this to opt in.
SHARED_ACCESS_PASSPHRASE = os.environ.get("SHARED_ACCESS_PASSPHRASE") or None

# Issue #43: guards the WS endpoint's actual expensive resource - every
# resolved turn (human or auto-played companion/monster) runs a real
# narrator LLM call and a real scene-image GPU generation unconditionally
# (see graph_builder.py's fixed player_agent->intent_parser->rules_engine->
# narrator->scene_image edge chain), so the thing worth bounding isn't
# individual cheap REST reads, it's (a) how many sessions can be pulling on
# that one shared local GPU/Ollama instance at once, and (b) how fast a
# single session's own client can submit new turns. Defaults chosen to be
# generously above real single-player pacing (CLAUDE.md's own measured
# ~7s/round) rather than empirically load-tested against real concurrent
# GPU contention - the issue itself flags that as worth confirming properly
# once this is actually deployed multi-session, not before.
MAX_CONCURRENT_SESSIONS = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))
ACTION_RATE_LIMIT_CAPACITY = float(os.environ.get("ACTION_RATE_LIMIT_CAPACITY", "10"))
ACTION_RATE_LIMIT_PER_MINUTE = float(os.environ.get("ACTION_RATE_LIMIT_PER_MINUTE", "10"))

# Issue #41: the debug_action WS message type injects a fully-formed
# ParsedAction directly, bypassing intent_parser's LLM call and any check
# that the sender controls the named actor - genuinely useful for fast local
# live-verification (used throughout this project's own CLAUDE.md log), but
# a real backdoor (act as any character, including someone else's PC) if the
# app is ever reachable from the internet. Off by default; a local dev
# session opts back in explicitly rather than this defaulting to on for
# "localhost-looking" requests, which would be trivial to spoof.
ALLOW_DEBUG_ACTIONS = os.environ.get("ALLOW_DEBUG_ACTIONS", "0") == "1"

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

# Story-adaptive-encounters Phase 3: caps how many times one live session
# can chain a model-generated story continuation onto an "open" party_choice
# scene (next_scene_id left unset - see campaign_generator.generate_
# continuation and api/ws/session.py's _resolve_party_choice). A narrative
# pacing bound, not a safety one like MAX_CONCURRENT_SESSIONS above - without
# it, a party that keeps steering into more open choices could keep the
# model improvising indefinitely; the generation reached at the cap is told
# to actually end the story (see force_ending) rather than the session just
# silently refusing to continue once it's hit.
MAX_ADAPTIVE_GENERATIONS = int(os.environ.get("MAX_ADAPTIVE_GENERATIONS", "3"))

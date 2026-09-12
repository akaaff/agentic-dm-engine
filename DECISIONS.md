# Architecture decisions

ADR-style log of the non-obvious choices made building this project and why, in the order they were made.

## 1. Deterministic rules engine, zero LLM involvement in game mechanics

All dice, HP, turn order, movement, conditions, attack/damage/save resolution live in `src/engine/`, plain Python, fully unit-tested, no LLM call anywhere in that path. LLMs are used only for parsing free-text intent into a structured action, narrating resolved events, role-playing companion agents, judging playthrough quality, and prompting the image model.

**Why:** the classic failure mode for an "LLM as DM" project is asking the model to do arithmetic and adjudication - models are unreliable at this and it erodes trust in the whole system. Separating mechanics from narration also lets the eval harness (Phase 3+) score narration quality and engine correctness as two independent axes.

**Cost:** more code up front (a real rules engine instead of a prompt), but the engine is small, deterministic, and cheap to test exhaustively.

## 2. SRD 5.1 content via 5e-bits/5e-database (2014/en subset only), CC-BY-4.0 attribution

`scripts/download_srd.py` vendors JSON from `5e-bits/5e-database` (MIT-licensed compilation) but the actual rules/game content is attributed under Wizards of the Coast's CC-BY-4.0 release of SRD 5.1, not the repo's own declared OGL 1.0a license for its compiled dataset - both are valid grants from WotC for this content, CC-BY-4.0 is the simpler one to comply with (a plain attribution notice, see `data/srd/ATTRIBUTION.md`). Only the `2014/en` path is pulled - this project targets 5e SRD 5.1 rules (not the 2024 revision) and English only.

**Why:** legal cleanliness to publish and eventually train on. No other WotC content (published adventures, non-SRD monsters/spells, setting material) is used anywhere in this project.

**Considered and not taken:** using the repo's own declared OGL 1.0a license instead - functionally fine too, but CC-BY-4.0's attribution-only requirement is simpler to satisfy correctly than OGL's Section 15 copy-of-license mechanics.

## 3. React + Vite SPA, not the sibling projects' plain-HTML-no-build-step pattern

`order-fulfillment-platform`'s `web-client/` and `agentic-rag-assistant`'s `web-client/` are both plain HTML/CSS/JS with no build step. This project uses React + Vite instead.

**Why:** this app has real multi-screen state (character creator wizard -> party setup -> campaign select -> live play) plus a live-updating combat grid and a scene-image panel, all needing to react to a stream of WebSocket events. The siblings' single-screen chat UIs didn't need this; this one would get unwieldy fast in vanilla JS.

**Cost:** a build step and a `node_modules` dependency the siblings don't have - accepted, since the UI complexity is genuinely higher here.

## 4. SQLite via SQLAlchemy, not Postgres

Both sibling repos use Postgres (one for pgvector, one for general relational storage plus outbox tables). This project uses SQLite.

**Why:** this is a local single-user app (one player + AI companions, no multi-tenant concerns), and SQLite removes a docker-compose dependency entirely for the core app - no infra to stand up to develop or demo it. Alembic migrations are still used, matching the siblings' migration tooling even though the DB engine differs.

**Cost:** would need revisiting if multiplayer (Phase 8) or heavier concurrent write load ever materializes - SQLite's single-writer model is fine for this project's current shape but not infinitely scalable.

## 5. Local image generation via `stabilityai/sd-turbo`, not SDXL(-Turbo)

Scene images are generated with SD-Turbo (SD1.5-distilled, ~2-3GB VRAM, 1-4 step inference), not full SDXL or SDXL-Turbo (~7GB).

**Why:** the local Ollama teacher model (`qwen2.5:7b-instruct`, ~5-6GB VRAM loaded) and the image model need to coexist on a single RTX 3080 (10GB total). SD-Turbo's smaller footprint means both can stay loaded simultaneously without building GPU model-swap/unload orchestration.

**Measured live on Day 16** (`nvidia-smi` polled every 3s while a real generation ran with the teacher model already loaded): baseline with only the Ollama teacher loaded was ~5.85GB; with SD-Turbo's pipeline also loaded and a generation in flight, combined usage settled at **~9.55GB**, leaving ~0.7GB headroom on the 10GB card. Tighter than the ~7-9GB estimate (closer to the top of that range), but confirmed to fit without any unload/reload orchestration - the decision holds, just with less margin than originally assumed.

**Considered and not taken:** SDXL-Turbo for higher image quality - would likely require unload/reload orchestration between LLM and image calls given the 10GB budget, adding real latency and complexity for a quality gain that isn't central to this project's thesis.

## 6. Campaigns are linear scene chains; dynamic mid-session adaptation deferred but designed for

`src/engine/campaign.py`'s `Scene.next_scene_id` is a fixed linear chain for the initial build - no branching, no in-session improvisation. The WebSocket session layer (`src/api/ws/session.py`) is nonetheless built from Day 11 as a multi-connection registry (not a single hardcoded client), and the scene chain is kept as an easily-mutable in-memory structure rather than a statically resolved plan.

**Why:** the user explicitly wants two extensions after the core build: dynamic on-the-fly campaign adaptation (the DM improvising new scenes mid-session when players go off-script) and multiplayer. Neither is in scope for the initial 26-day build, but both are real priorities for this project specifically (it's a personal project as much as a portfolio one) - so the initial design deliberately avoids choices that would force a rewrite to add them later.

**Cost:** slightly more upfront design care in Day 6/11/20/21 (keeping the scene chain mutable, keeping the session layer connection-aware) for zero near-term functional gain - a bet that the extensions are worth building later.

## 7. A generic distillation toolkit, not a one-off intent-parser fine-tuning script

`src/training/` is built as schema-agnostic pipeline stages (`task_spec.py`'s `DistillationTask`, `generate_synthetic.py`, `curate_dataset.py`, `evaluate_structured_output.py`, `finetune_lora.py`) that take a task-specific spec (prompt template, example generator, output pydantic schema, model names) as their only D&D-aware input. The intent parser is the first `DistillationTask` instance plugged into it, not something the pipeline is hardcoded around.

**Why:** a reusable tool is a stronger standalone portfolio artifact than a task-specific notebook, and it costs nothing to the timeline - the pipeline needed all of this generality anyway to fine-tune one task correctly (calling a teacher, validating structured output, splitting data, running LoRA, scoring field accuracy). The genericity claim is verified, not just asserted: `tests/llm/test_distillation_toolkit_toy_task.py` runs the entire pipeline against a deliberately unrelated toy task (extract a person's name+age from a sentence - chosen specifically because it shares no structural resemblance to `ParsedAction`) before any intent-parser-specific code exists, and it passed clean on the first attempt.

**Cost:** more upfront design discipline (a strict boundary between what the pipeline owns vs. what a task spec supplies) for a project that, as of this build, only ever exercises the toolkit with one real task. A second distillation target (e.g. a narration-quality judge) would need only a new task spec to prove the investment was real, not aspirational - not yet attempted in this build.

**Considered and not taken:** a single bespoke script (`finetune_intent_parser.py` doing everything inline) - simpler for exactly one task, but would have to be substantially rewritten (not just extended) for a second one, and reads as a much smaller portfolio artifact.

## 8. Serve the distilled model through the teacher's own engine (GGUF via Ollama), not the framework it was trained in

The intent-parser student is fine-tuned with `transformers`/`peft`/`trl`, but production serving has three interchangeable backends behind one config flag (`INTENT_PARSER_BACKEND`): the teacher itself, the fine-tuned adapter served via `transformers`/`peft` directly, or the same adapter merged and converted to GGUF and served by Ollama alongside the teacher.

**Why:** the training framework and the serving engine are different concerns, and conflating them cost real latency - a naive `transformers.generate()` serving path gives up the fused kernels, exact stopping, and grammar-constrained decoding that make Ollama's `llama.cpp` engine fast, so the fine-tuned 0.5B was barely faster than the 7B teacher when served that way despite being 14x smaller. Putting the student on the *same* engine as the teacher isolates parameter count as the only remaining variable and lets the size advantage actually show up (measured ~2x latency win - see README).

**Cost:** an extra conversion step (merge adapter -> `llama.cpp`'s `convert_hf_to_gguf.py` -> `ollama create`) outside this project's own `uv`-managed dependency tree, and a real bug surfaced by the switch: Ollama's grammar-constrained decoding (which helps the teacher) corrupted the untyped `ParsedAction.params` field on a model never trained under that specific constraint - worked around with a generic unconstrained-decode-plus-retry helper (`chat_structured_best_effort`) rather than by changing the schema. Full account of both the conversion process and the bug: `CLAUDE.md`'s Day 27 detour entries.

**Considered and not taken:** quantizing the GGUF (e.g. Q4_K_M) for a further size/speed gain - skipped deliberately, since the model is already ~1GB in F16 and quantization would need re-verifying field accuracy against a lossy conversion for a benefit this project didn't need.

# CLAUDE.md

Guidance for working in this repository, in the sibling repos' own convention: commands first, then an engineering log of real gotchas hit while building it - the "why" behind non-obvious decisions, not a restatement of what the code already says.

## Commands

Python 3.13, managed via [uv](https://docs.astral.sh/uv/). Non-packaged app (`tool.uv.package = false`).

- Install deps: `uv sync`
- Lint: `uv run ruff check .` / format: `uv run ruff format .` / type-check: `uv run mypy .`
- Unit tests (fully offline): `uv run pytest`
- Tests needing a live Ollama: `uv run pytest -m llm`
- Tests needing this project's own API running: `uv run pytest -m integration`
- Vendor SRD data: `uv run python scripts/download_srd.py`

(Backend run/migration commands, frontend commands, and training commands will be added here as each phase lands - see `C:\Users\yakra\.claude\plans\radiant-waddling-hellman.md` for the full day-by-day plan.)

Ollama must be running on the host with `qwen2.5:7b-instruct` pulled for anything touching `tests/llm` or the live intent-parser/narrator nodes (Phase 3+).

## Architecture

See `README.md` for a summary and `DECISIONS.md` for the reasoning behind each major choice. Short version: `src/engine/` is a deterministic, LLM-free rules engine (dice/state/turn-order/movement/rules/conditions/character-creation/campaign); `src/graph/` wires LangGraph nodes (intent_parser, rules_engine, narrator, player_agent, judge, scene_image) around a shared `GameState`; `src/llm/` holds the Ollama client and prompt templates; `src/imagegen/` wraps the local SD-Turbo pipeline; `src/api/` is the FastAPI service (REST for character/campaign/companion data, WebSocket for live play); `web/` is the React+Vite frontend.

## Conventions to keep consistent

- **Live-verify before trusting.** Every day's plan step ends with a concrete live check (CLI/API/browser output diffed against a hand-computed fixture, or a saved eval table), not just "tests pass" - matching the pattern from `order-fulfillment-platform` and `agentic-rag-assistant`, which caught real bugs on nearly every build day.
- **Deterministic engine code gets exhaustive unit tests with hand-computed expected values; LLM/image-gen code gets live verification instead of mocking** - a mock only proves the mock is self-consistent, not that the real model call behaves as expected.
- **Commit in small, clearly-described batches**, one coherent chunk of work at a time, not one commit per day.
- Constructor-injected dependencies, matching the sibling repos' pattern.

## Engineering log (gotchas, in the order they were found)

**`uv run pre-commit run --all-files` crashed mypy with "INTERNAL ERROR", but `uv run mypy <same files>` directly never did.** Root cause: pre-commit batches/parallelizes a hook's file arguments across concurrent subprocess invocations once there are enough files, and multiple mypy processes racing on the same `.mypy_cache/` directory corrupts it - a known mypy footgun with its own documented fix. Confirmed by reproducing the exact file set pre-commit passes in one direct `uv run mypy` call (succeeded every time) versus letting pre-commit invoke it (failed). Fixed by adding `require_serial: true` to the mypy hook in `.pre-commit-config.yaml`, forcing pre-commit to run it as one non-parallel invocation.

**`from src.config import ...` didn't resolve under plain `uv run pytest`, unlike the sibling repos' `from app.config import ...`.** Neither sibling repo sets `pythonpath` in `[tool.pytest.ini_options]`, yet their tests import their top-level package fine - whatever makes that work there wasn't reproduced here (not investigated further; not worth the time). Fixed directly and explicitly by adding `pythonpath = ["."]` to `[tool.pytest.ini_options]` in `pyproject.toml`, which reliably puts the repo root on `sys.path` for test collection regardless of package-layout quirks.

**`Mapping[str, int]` did not accept a `dict[Literal["STR", ...], int]` argument under strict mypy**, even though every possible key is a plain string. `Mapping`'s key type parameter is invariant in typeshed (unlike its covariant value type), so a `Literal`-keyed dict is not considered a subtype of a `str`-keyed one. Fixed by typing `validate_standard_array`'s parameter as `Mapping[AbilityScore, int]` (the actual key type in play) rather than trying to generalize to `str` - direct dict-literal call sites in tests still type-check fine via mypy's bidirectional inference against that stricter parameter type.

**The CLI's trace printed the wrong round number for the last action of every round** - a display bug, not an engine bug, but worth remembering how it was caught. `run_scripted`'s verbose print used `state.round` *after* `resolve_action` returned, but `resolve_action` already advances `state.round` as part of the same call when a round wraps - so the action that ends round 1 printed as "[Round 2]". Confirmed the actual `Event.round` values stored in `state.events` were correct all along (captured before the turn-advance step runs) by dumping the raw event log directly and comparing against the printed trace - only the CLI's post-hoc `print()` needed fixing (capture `round_before = state.round` ahead of the call). A reminder that a convenience print statement can be wrong even when the underlying data model is right, and it's worth checking which one actually is before "fixing" anything.

**The SRD 5.1 only includes one background: Acolyte.** Confirmed live on Day 1 by actually inspecting `data/srd/5e-SRD-Backgrounds.json` after downloading it (1 entry) - not assumed from general D&D knowledge, which would suggest Soldier/Criminal/Folk Hero etc. are available too (they're not SRD content, they're in the core Player's Handbook, which this project deliberately doesn't use). Day 5's character creation wizard needs to either offer Acolyte only, or the project authors a small number of original (non-WotC) backgrounds itself - mechanically simple (skill proficiencies + equipment + one feature) and safe to invent, unlike races/classes/monsters/spells which stay strictly SRD-sourced. Decide this explicitly on Day 5 rather than assuming background variety exists.

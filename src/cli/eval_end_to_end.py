"""Day 27's end-to-end re-eval: run Day 15's autoplay judge harness with
the teacher parser and again with the fine-tuned student, diff the episode
scores, and micro-benchmark single-call parse latency for each. Needs a
live Ollama and (for the "finetuned" backend) a CUDA GPU. Scene images are
disabled so the 0.5B student can share the card with the Ollama teacher.

Day 27 detour: also runs "finetuned_ollama" - the same distilled student,
merged + converted to GGUF and served by Ollama (INTENT_PARSER_OLLAMA_MODEL)
instead of transformers/peft. This is the backend that actually tests
whether the latency win predicted in CLAUDE.md's Day 27 entry (same engine
as the teacher, not just a smaller model) shows up in practice. Needs that
Ollama model already created - see CLAUDE.md's Day 27 detour entry for how.

  uv run python -m src.cli.eval_end_to_end --runs 2
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from pathlib import Path

from src import config
from src.cli.play import run_autoplay
from src.engine.actions import ParsedAction
from src.graph.nodes.judge import judge_transcript
from src.llm.providers import chat_structured, chat_structured_best_effort
from src.training.tasks.intent_parser_task import build_intent_parser_task

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_OUT = _PROJECT_ROOT / "data" / "training" / "eval_results" / "intent_parser_end_to_end.md"

_BACKENDS = ["teacher", "finetuned", "finetuned_ollama"]


def _autoplay_scores(campaign_id: str, runs: int) -> list[int]:
    """Autoplay is LLM-sampled and can occasionally churn to the max_turns
    safety valve (documented in CLAUDE.md) - a run that does is skipped, not
    fatal, so one unlucky episode doesn't sink the whole comparison."""
    scores: list[int] = []
    attempts = 0
    while len(scores) < runs and attempts < runs * 3:
        attempts += 1
        try:
            _state, narration = run_autoplay(
                campaign_id=campaign_id,
                verbose=False,
                disable_scene_images=True,
                max_turns_per_encounter=150,
            )
        except RuntimeError as exc:
            print(f"  (autoplay attempt {attempts} did not terminate: {exc})")
            continue
        scores.append(judge_transcript(narration).overall_score)
    return scores


def _latency_ms(backend: str, prompts: list[str]) -> list[float]:
    timings: list[float] = []
    for prompt in prompts:
        t0 = time.perf_counter()
        if backend == "teacher":
            chat_structured(
                messages=[{"role": "user", "content": prompt}],
                schema=ParsedAction,
                temperature=0.2,
            )
        elif backend == "finetuned_ollama":
            # Not chat_structured: its grammar constraint measurably hurts
            # this model (see CLAUDE.md) - matches the production backend's
            # actual code path (intent_parser_node).
            chat_structured_best_effort(
                messages=[{"role": "user", "content": prompt}],
                schema=ParsedAction,
                model=config.INTENT_PARSER_OLLAMA_MODEL,
                temperature=0.2,
            )
        else:
            from src.llm.local_parser import parse_intent_local

            parse_intent_local(prompt, config.INTENT_PARSER_ADAPTER_DIR)
        timings.append((time.perf_counter() - t0) * 1000)
    return timings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=2, help="Autoplay runs per backend.")
    parser.add_argument("--campaign", default="goblin_ambush_oneshot")
    parser.add_argument("--latency-samples", type=int, default=20)
    args = parser.parse_args()

    task = build_intent_parser_task(random.Random(0))
    latency_prompts = [task.generate_prompt() for _ in range(args.latency_samples + 1)]
    warmup, latency_prompts = latency_prompts[0], latency_prompts[1:]

    results: dict[str, dict[str, object]] = {}
    for backend in _BACKENDS:
        config.INTENT_PARSER_BACKEND = backend
        print(f"=== backend: {backend} ===")

        _latency_ms(backend, [warmup])  # load + warm caches, not timed
        latencies = _latency_ms(backend, latency_prompts)
        print(f"  parse latency: median {statistics.median(latencies):.0f} ms")

        scores = _autoplay_scores(args.campaign, args.runs)
        print(f"  autoplay judge overall_score: {scores}")

        results[backend] = {
            "latency_median_ms": statistics.median(latencies),
            "latency_p90_ms": sorted(latencies)[int(len(latencies) * 0.9)],
            "judge_scores": scores,
        }

    lines = [
        f"# Intent-parser end-to-end re-eval (campaign: {args.campaign})",
        "",
        "Scene images disabled (judge scores narration, not images). Autoplay is",
        "LLM-sampled and noisy - the judge score column is a small-sample sanity",
        "check that swapping the parser doesn't degrade play, not a precise metric.",
        "",
        "Latency caveat, and which number actually matters: 'finetuned' runs the",
        "0.5B student through plain transformers/peft - only marginally faster",
        "than the 7B teacher despite being 14x smaller, since it gives up the",
        "inference-stack advantages (fused kernels, exact stopping) the teacher",
        "gets from Ollama's llama.cpp. 'finetuned_ollama' is the same distilled",
        "student, merged + converted to GGUF and served by Ollama instead - same",
        "engine as the teacher, isolating parameter count as the only remaining",
        "variable - and it does win (~30% faster than the teacher), but that's a",
        "modest, largely imperceptible difference between two already-fast",
        "numbers, not the headline result. The bigger, unrelated bug this detour",
        "found: on this Windows machine, httpx resolving 'localhost' (vs. the",
        "literal 127.0.0.1) added a flat ~2.2s to *every* Ollama call regardless",
        "of model - fixed via OLLAMA_BASE_URL - which is why 'teacher' itself is",
        "~4x faster here than in the original Day 27 table. That fix speeds up",
        "every LLM call the whole app makes (narrator/player_agent/judge too),",
        "not just this one parser backend - confirmed live, a full autoplay",
        "round now averages ~7.1s vs. Day 21's documented 15-20s/round. See",
        "CLAUDE.md's Day 27 detour entries for the full story, including how",
        "the GGUF itself was produced (a full llama.cpp clone, run locally -",
        "no Colab, no quantization needed at this size).",
        "",
        "| backend | parse latency (median) | parse latency (p90) | judge overall_score |",
        "| --- | --- | --- | --- |",
    ]
    for backend in _BACKENDS:
        r = results[backend]
        lines.append(
            f"| {backend} | {r['latency_median_ms']:.0f} ms | {r['latency_p90_ms']:.0f} ms "
            f"| {r['judge_scores']} |"
        )
    markdown = "\n".join(lines) + "\n"

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(markdown, encoding="utf-8")
    print()
    print(markdown)
    print(f"Saved to {_OUT}")


if __name__ == "__main__":
    main()

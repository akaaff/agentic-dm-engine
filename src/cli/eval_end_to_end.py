"""Day 27's end-to-end re-eval: run Day 15's autoplay judge harness with
the teacher parser and again with the fine-tuned student, diff the episode
scores, and micro-benchmark single-call parse latency for each. Needs a
live Ollama and (for the fine-tuned backend) a CUDA GPU. Scene images are
disabled so the 0.5B student can share the card with the Ollama teacher.

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
from src.llm.providers import chat_structured
from src.training.tasks.intent_parser_task import build_intent_parser_task

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_OUT = _PROJECT_ROOT / "data" / "training" / "eval_results" / "intent_parser_end_to_end.md"

_BACKENDS = ["teacher", "finetuned"]


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
        "Latency caveat: the fine-tuned 0.5B student runs through plain",
        "transformers/peft (bf16, merged adapter); the teacher runs through",
        "Ollama's llama.cpp (q4, fused kernels, grammar-constrained stopping).",
        "The student is only marginally faster despite being 14x smaller -",
        "at this scale the inference stack matters more than the parameter",
        "count. A real latency win needs the student on the same engine",
        "(GGUF -> Ollama), which this Windows Ollama build can't import",
        "(its experimental safetensors path needs Apple MLX).",
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

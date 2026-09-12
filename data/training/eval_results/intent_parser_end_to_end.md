# Intent-parser end-to-end re-eval (campaign: goblin_ambush_oneshot)

Scene images disabled (judge scores narration, not images). Autoplay is
LLM-sampled and noisy - the judge score column is a small-sample sanity
check that swapping the parser doesn't degrade play, not a precise metric.

Latency caveat: 'finetuned' runs the 0.5B student through plain
transformers/peft (bf16, merged adapter) - only marginally faster than
the 7B teacher despite being 14x smaller, because it gives up the
inference-stack advantages (fused kernels, exact stopping) the teacher
gets from Ollama's llama.cpp. 'finetuned_ollama' is the same distilled
student, merged + converted to GGUF and served by Ollama instead -
same engine as the teacher, isolating parameter count as the only
remaining variable. See CLAUDE.md's Day 27 detour entry for how the
GGUF was produced (a full llama.cpp clone, run locally - no Colab, no
quantization needed at this size).

| backend | parse latency (median) | parse latency (p90) | judge overall_score |
| --- | --- | --- | --- |
| teacher | 726 ms | 783 ms | [7, 7] |
| finetuned | 2731 ms | 3318 ms | [7, 7] |
| finetuned_ollama | 509 ms | 656 ms | [7, 6] |

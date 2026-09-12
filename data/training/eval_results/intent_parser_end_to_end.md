# Intent-parser end-to-end re-eval (campaign: goblin_ambush_oneshot)

Scene images disabled (judge scores narration, not images). Autoplay is
LLM-sampled and noisy - the judge score column is a small-sample sanity
check that swapping the parser doesn't degrade play, not a precise metric.

Latency caveat, and which number actually matters: 'finetuned' runs the
0.5B student through plain transformers/peft - only marginally faster
than the 7B teacher despite being 14x smaller, since it gives up the
inference-stack advantages (fused kernels, exact stopping) the teacher
gets from Ollama's llama.cpp. 'finetuned_ollama' is the same distilled
student, merged + converted to GGUF and served by Ollama instead - same
engine as the teacher, isolating parameter count as the only remaining
variable - and it does win (~30% faster than the teacher), but that's a
modest, largely imperceptible difference between two already-fast
numbers, not the headline result. The bigger, unrelated bug this detour
found: on this Windows machine, httpx resolving 'localhost' (vs. the
literal 127.0.0.1) added a flat ~2.2s to *every* Ollama call regardless
of model - fixed via OLLAMA_BASE_URL - which is why 'teacher' itself is
~4x faster here than in the original Day 27 table. That fix speeds up
every LLM call the whole app makes (narrator/player_agent/judge too),
not just this one parser backend - confirmed live, a full autoplay
round now averages ~7.1s vs. Day 21's documented 15-20s/round. See
CLAUDE.md's Day 27 detour entries for the full story, including how
the GGUF itself was produced (a full llama.cpp clone, run locally -
no Colab, no quantization needed at this size).

| backend | parse latency (median) | parse latency (p90) | judge overall_score |
| --- | --- | --- | --- |
| teacher | 1018 ms | 1337 ms | [7, 7] |
| finetuned | 2725 ms | 3308 ms | [7, 6] |
| finetuned_ollama | 463 ms | 506 ms | [6, 7] |

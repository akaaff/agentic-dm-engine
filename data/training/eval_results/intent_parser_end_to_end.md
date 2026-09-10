# Intent-parser end-to-end re-eval (campaign: goblin_ambush_oneshot)

Scene images disabled (judge scores narration, not images). Autoplay is
LLM-sampled and noisy - the judge score column is a small-sample sanity
check that swapping the parser doesn't degrade play, not a precise metric.

Latency caveat: the fine-tuned 0.5B student runs through plain
transformers/peft (bf16, merged adapter); the teacher runs through
Ollama's llama.cpp (q4, fused kernels, grammar-constrained stopping).
The student is only marginally faster despite being 14x smaller -
at this scale the inference stack matters more than the parameter
count. A real latency win needs the student on the same engine
(GGUF -> Ollama), which this Windows Ollama build can't import
(its experimental safetensors path needs Apple MLX).

| backend | parse latency (median) | parse latency (p90) | judge overall_score |
| --- | --- | --- | --- |
| teacher | 2938 ms | 3008 ms | [7, 7] |
| finetuned | 2714 ms | 3301 ms | [6, 7] |

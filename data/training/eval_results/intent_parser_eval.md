# Intent-parser distillation eval (300 held-out test examples)

`base` and `fine-tuned` run through plain `transformers.generate()` (no
grammar constraint); `teacher` runs through Ollama's constrained decoding,
which is why it can't emit invalid JSON. `field acc (all)` scores every
example - an unparseable prediction counts every field wrong.

| model | valid JSON | valid ParsedAction | field acc (all) | actor | confidence | item_or_spell | params.path | params.skill | params.target | raw_text | target | targets | verb |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base (no fine-tune) | 100.0% | 0.0% | 53.0% | 0.0% | 99.7% | 73.3% | 0.0% | 3.6% | 85.7% | 10.7% | 88.0% | 100.0% | 12.7% |
| fine-tuned (LoRA) | 100.0% | 100.0% | 98.4% | 99.0% | 99.7% | 99.3% | 86.5% | 85.7% | 100.0% | 100.0% | 97.3% | 100.0% | 96.3% |
| teacher (Ollama) | 100.0% | 100.0% | 99.0% | 99.3% | 100.0% | 99.7% | 88.5% | 92.9% | 100.0% | 100.0% | 98.7% | 100.0% | 98.0% |

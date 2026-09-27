"""Collects real (not synthetic) free-text turns that failed to become a
legal action, for hand review before folding the corrected ones into the
intent-parser fine-tuning dataset (src.cli.generate_training_dataset's own
pipeline generates synthetic examples; this augments it with real hard
cases pulled straight out of live play).

Two distinct failure shapes, both worth collecting under one "reason":
- "unparseable": intent_parser_node itself gave up (verb="invalid") -
  the model couldn't map the utterance to any legal action shape at all.
- "rejected": the model produced a syntactically fine ParsedAction, but
  turn_engine.resolve_action's own legality checks still rejected it
  (TurnEngineError) - not always a parser mistake (the utterance itself
  may have described something genuinely illegal, e.g. attacking out of
  range), but often is (a hallucinated target id, a malformed weapon
  name, wrong actor) - left for the human reviewer to tell apart, not
  guessed here.

`corrected_action` is always written as null - a deliberate placeholder
the reviewer fills in by hand once they've decided what the utterance
*should* have parsed to (or leaves null to mean "discard this one").

Append-only JSONL, one real record per failed turn, flushed immediately
(not buffered) - a crash losing the most recent few records would cost
more than the tiny per-write overhead. Best-effort: a write failure here
must never break a real turn, same boundary-tolerance philosophy as
generate_narration_audio's own broad except around a local-model call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.engine.actions import ParsedAction
from src.engine.state import Character

FAILED_INTENTS_PATH = Path("data/training/failed_intents/intent_parser.jsonl")


def log_failed_intent(
    *,
    reason: str,
    actor: Character,
    raw_text: str,
    prompt: str | None,
    produced_action: ParsedAction | None,
    detail: str | None = None,
) -> None:
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "reason": reason,
        "actor_id": actor.id,
        "actor_name": actor.name,
        "is_pc": actor.is_pc,
        "is_companion": actor.is_companion,
        "raw_text": raw_text,
        "prompt": prompt,
        "produced_action": produced_action.model_dump(mode="json") if produced_action else None,
        "detail": detail,
        "corrected_action": None,
    }
    try:
        FAILED_INTENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FAILED_INTENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass

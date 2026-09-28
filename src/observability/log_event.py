"""The one centralized structured log stream for this app. Before this,
the only persisted failure signal anywhere was src.training.failed_intents'
own dedicated file - a genuine unhandled exception in the WebSocket handler,
or the already-present but unconfigured `logging.getLogger(__name__)` calls
in imagegen/audiogen (VRAM skips, TTS failures...), only ever reached the
live terminal's stdout and vanished the instant the process restarted.

Every caller writes through `log_event(kind=..., **fields)` - a `kind`
distinguishes what produced the record ("failed_intent", "backend_error",
"log") so a downstream consumer (scripts/log_watcher.py) can filter/group
without parsing free text. Append-only JSONL, one real record per line,
flushed immediately (not buffered) - the same "a crash losing the most
recent few records costs more than the tiny per-write overhead" reasoning
failed_intents.py already established. Best-effort: a write failure here
must never break a real turn, same boundary-tolerance philosophy as
generate_narration_audio's own broad except around a local-model call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENTS_LOG_PATH = Path("data/logs/events.jsonl")


def log_event(kind: str, **fields: Any) -> None:
    record = {"timestamp": datetime.now(UTC).isoformat(), "kind": kind, **fields}
    try:
        EVENTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass

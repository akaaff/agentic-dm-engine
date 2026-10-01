"""A server-side log of every resolved game mechanic - attack rolls,
damage dealt (already post-resistance/immunity/vulnerability, see
rules.monster_damage_multiplier), saving throws, conditions applied,
moves, initiative, everything turn_engine.resolve_action ever appends to
GameState.events - separate from src.observability.log_event, which only
ever captures *failures* (rejected/unparseable intents, backend errors).
User-requested: a reviewable record of mechanics actually applied during
play, not just what went wrong.

One JSON line per Event, written through a standard library
TimedRotatingFileHandler (`when="midnight"`) rather than hand-rolled
rotation logic - rotates to `mechanics.log.YYYY-MM-DD` once per day,
keeping `config.MECHANICS_LOG_RETENTION_DAYS` days before the oldest is
deleted automatically. Lazily configured once per process (the handler
itself is stateful - opening it twice would double-write), guarded by
`config.MECHANICS_LOG_ENABLED` the same way TTS_ENABLED/
SCENE_IMAGES_ENABLED gate their own features."""

from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from src import config
from src.engine.events import Event
from src.engine.state import GameState

MECHANICS_LOG_PATH = Path("data/logs/mechanics.log")

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    MECHANICS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mechanics")
    # logging.getLogger caches by name at the module level, independent of
    # this file's own `_logger` cache - reconfiguring (the offline test
    # suite resets `_logger` to None per test, see conftest.py) would
    # otherwise just keep *adding* handlers to the same long-lived logger
    # object, each still holding its own open file handle. Closing before
    # replacing matters concretely on Windows: a leaked open handle on a
    # pytest tmp_path blocks that directory's own teardown.
    for old_handler in list(logger.handlers):
        logger.removeHandler(old_handler)
        old_handler.close()
    handler = TimedRotatingFileHandler(
        MECHANICS_LOG_PATH,
        when="midnight",
        interval=1,
        backupCount=config.MECHANICS_LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.setLevel(logging.INFO)
    logger.propagate = False  # never bubble into the root/"src" handlers
    logger.addHandler(handler)
    _logger = logger
    return logger


def log_mechanic(event: Event, game_state: GameState) -> None:
    """Best-effort, same boundary-tolerance stance as every other
    observability sink in this project - a logging failure must never
    break a real turn."""
    if not config.MECHANICS_LOG_ENABLED:
        return
    actor = game_state.characters.get(event.actor)
    record = {
        "timestamp": event.timestamp,
        "round": event.round,
        "turn_index": event.turn_index,
        "actor_id": event.actor,
        "actor_name": actor.name if actor else event.actor,
        "encounter_id": game_state.encounter_id,
        "type": event.type,
        "payload": event.payload,
    }
    try:
        _get_logger().info(json.dumps(record, default=str))
    except OSError:
        pass

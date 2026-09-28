"""Deterministic "is this the same underlying problem as a previous
occurrence" fingerprinting - the actual duplicate-detection decision
scripts/log_watcher.py makes, kept pure Python rather than asked of an
LLM (the same "give Python the fact, don't ask an LLM to judge it" split
this project uses everywhere else - cast_spell's target field, the
closest-enemy resolution, the out-of-range redirect). A signature is a
short, stable string: two records describing the same recurring problem
(same rejection reason, same weapon/spell, just a different specific
actor/target id or exact distance) normalize to the identical signature;
two genuinely different problems don't. This is what "is this a
duplicate" is actually decided on - never fuzzy LLM judgment, so it can't
drift or hallucinate a false duplicate/non-duplicate from one run to the
next."""

from __future__ import annotations

import re
from typing import Any

_ID_OR_NUMBER = re.compile(r"\b[\w-]*\d[\w-]*\b")
_TRACEBACK_FRAME = re.compile(r'File "([^"]+)", line (\d+)')


def _normalize(text: str) -> str:
    """Strips anything that looks like a specific id/number (character
    ids like "kobold_1", distances like "25ft", counts...) so two
    otherwise-identical messages differing only in which specific
    creature/number was involved collapse to the same normalized text.
    Doesn't catch every possible variable substring (a bare identifier
    like "companion_grom" has no digit in it at all) - see
    _last_own_frame below for backend_error's own, more reliable
    signature basis."""
    return _ID_OR_NUMBER.sub("#", text)


def _last_own_frame(traceback_text: str) -> str:
    """The deepest traceback frame that's actually this project's own
    code (not a third-party library's internals, e.g. Starlette/
    langgraph) - a far more reliable "is this the same bug" signal than
    the exception's own message text, which can embed any runtime-
    specific value (an id, a count...) with no reliable way to strip all
    of them generically the way _normalize does for a known shape like a
    weapon-range message. Two crashes raised from the identical line of
    our own code are almost certainly the same underlying bug regardless
    of which specific value triggered it that time."""
    own_frames = [
        (path, line)
        for path, line in _TRACEBACK_FRAME.findall(traceback_text)
        if "site-packages" not in path and ".venv" not in path
    ]
    if not own_frames:
        return ""
    path, line = own_frames[-1]
    # Normalize away the absolute path prefix so this doesn't depend on
    # which machine/checkout location the watcher happens to run from.
    normalized_path = path.replace("\\", "/").rsplit("agentic-dm-engine/", 1)[-1]
    return f"{normalized_path}:{line}"


def compute_signature(record: dict[str, Any]) -> str:
    kind = record.get("kind", "unknown")
    if kind == "failed_intent":
        reason = record.get("reason", "")
        detail = record.get("detail") or ""
        # The produced action's own verb/item_or_spell narrows this
        # further than "reason" alone - two "rejected" failures for
        # completely different verbs shouldn't collapse into one
        # signature just because both happen to say "rejected".
        produced = record.get("produced_action") or {}
        verb = produced.get("verb", "")
        item = produced.get("item_or_spell") or ""
        return f"failed_intent:{reason}:{verb}:{item}:{_normalize(detail)}"
    if kind == "backend_error":
        exc_type = record.get("exc_type", "")
        frame = _last_own_frame(record.get("traceback") or "")
        if frame:
            return f"backend_error:{exc_type}:{frame}"
        # No traceback (or nothing recognizable in it) - fall back to the
        # normalized message rather than collapsing every such record
        # into one signature regardless of exc_type/content.
        return f"backend_error:{exc_type}:{_normalize(record.get('message', ''))}"
    if kind == "log":
        logger = record.get("logger", "")
        level = record.get("level", "")
        message = record.get("message", "")
        # Only the first line - a traceback appended by logger.exception
        # varies call-to-call (line numbers, local values) even for the
        # exact same underlying problem, so including it would defeat
        # deduplication entirely.
        first_line = message.splitlines()[0] if message else ""
        return f"log:{logger}:{level}:{_normalize(first_line)}"
    return f"{kind}:unknown"

"""Attaches a handler to the "src" logger namespace so every already-
existing (but previously unconfigured, and so effectively lost) plain
logging.warning/exception call across the app - imagegen/audiogen's own
VRAM-skip and generation-failure warnings, mainly (see their own
`logger = logging.getLogger(__name__)` module-level loggers) - lands in
the centralized log stream too, with zero changes to either of those
files: this is a handler, not a rewrite of every call site.

Deliberately scoped to the "src" namespace (`logging.getLogger("src")`,
which every `src.*` module logger is a child of and propagates up to) -
not the true root logger - so uvicorn's own noisy per-request access-log
output ("uvicorn"/"uvicorn.access"/"uvicorn.error" loggers, not children
of "src") is never captured here. This is about this app's own code, not
web-server traffic."""

from __future__ import annotations

import logging

from src.observability.log_event import log_event


class _CentralLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        log_event(
            kind="log",
            logger=record.name,
            level=record.levelname,
            message=self.format(record),
        )


_configured = False


def configure_logging() -> None:
    """Idempotent - safe to call more than once (e.g. re-imported across
    tests or a hot-reload) without attaching a duplicate handler."""
    global _configured
    if _configured:
        return
    logging.getLogger("src").addHandler(_CentralLogHandler(level=logging.WARNING))
    _configured = True

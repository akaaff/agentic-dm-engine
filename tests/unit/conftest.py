from pathlib import Path

import pytest

from src import config
from src.observability import log_event, mechanics_log


@pytest.fixture(autouse=True)
def _redirect_events_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """intent_parser_node's "unparseable" check and rules_engine_node's
    "rejected"/backend-error checks all write to this one centralized
    stream (src.observability.log_event) unconditionally, outside the
    graph - same "not shielded by build_graph(narrator_fn=...)" gap
    _disable_tts documents just below. Redirected to a throwaway tmp_path
    for the whole offline suite so a test's own deliberately-invalid/
    rejected fixture actions never write into the real data/logs/
    directory."""
    monkeypatch.setattr(log_event, "EVENTS_LOG_PATH", tmp_path / "events.jsonl")


@pytest.fixture(autouse=True)
def _redirect_mechanics_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """rules_engine_node logs every successfully-resolved Event to this
    separate stream unconditionally too - same reasoning as
    _redirect_events_log above. mechanics_log._get_logger caches its
    TimedRotatingFileHandler at module scope (correct for a real
    long-running process - a handler should only ever be opened once),
    so redirecting MECHANICS_LOG_PATH alone isn't enough once any earlier
    test has already triggered that cache: the module-level cache itself
    is reset here too, so every test gets a fresh handler bound to its
    own tmp_path rather than silently reusing whichever path the first
    test in the process happened to configure."""
    monkeypatch.setattr(mechanics_log, "MECHANICS_LOG_PATH", tmp_path / "mechanics.log")
    monkeypatch.setattr(mechanics_log, "_logger", None)


@pytest.fixture(autouse=True)
def _disable_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike scene images (only ever generated from inside the real graph,
    which every WS test already stubs out via build_graph - see e.g.
    test_ws_session.py's own _stub_narrator/_stub_scene_image), narration
    audio is generated directly by src.api.ws.session._broadcast_narration
    itself, outside the graph entirely - stubbing the graph doesn't prevent
    it. Left enabled, every narration-touching WS test in this directory
    would invoke the real Kokoro pipeline (confirmed live: it does, right
    down to a real model download on a clean cache). Disabled here for the
    whole directory, the same "LLM/local-model code gets live verification,
    not mocking in the offline suite" stance imagegen/llm tests already
    enforce via their own directory-scoped conftest.py, just via a feature
    flag instead of a directory split (TTS isn't its own pytest marker/
    directory - it's exercised by ordinary WS tests that would otherwise
    need this disabled one at a time)."""
    monkeypatch.setattr(config, "TTS_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_scene_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Narration illustration (src.graph.nodes.narration_illustration) is
    called directly from api/ws/session.py when scene narration is broadcast,
    outside the graph - so stubbing the graph's own scene_image_fn doesn't
    shield it, same gap _disable_tts documents above. Left enabled it would
    call a real Ollama and load the SD-Turbo pipeline from any test that walks
    a campaign's narrative scenes."""
    monkeypatch.setattr(config, "SCENE_IMAGES_ENABLED", False)

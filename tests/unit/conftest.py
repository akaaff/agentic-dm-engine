import pytest

from src import config


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

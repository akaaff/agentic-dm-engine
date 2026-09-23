"""Pure-Python logic in src/audiogen/service.py - the actual Kokoro model
call itself gets live verification only, matching this project's established
"LLM/local-model code gets live verification, not mocking" stance (same as
imagegen/llm.providers). Both cases here short-circuit before ever touching
the Kokoro pipeline, so they stay genuinely offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config
from src.audiogen.service import generate_narration_audio


def test_generate_narration_audio_skips_empty_text(tmp_path: Path) -> None:
    assert generate_narration_audio("", "am_michael", output_dir=tmp_path) is None
    assert generate_narration_audio("   ", "am_michael", output_dir=tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_generate_narration_audio_rejects_unknown_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TTS_BACKEND", "not-a-real-backend")
    assert generate_narration_audio("hello", "am_michael", output_dir=tmp_path) is None

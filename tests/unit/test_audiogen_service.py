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


def test_kokoro_falls_back_to_the_cpu_when_the_gpu_is_short_of_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found live: with the scene-image model already holding the GPU, Kokoro used to be
    switched off for the whole process. It now loads on the CPU instead."""
    import sys
    import types

    from src.audiogen import service

    devices: list[object] = []
    floated: list[bool] = []

    class FakeModel:
        def float(self) -> FakeModel:
            floated.append(True)
            return self

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            devices.append(kwargs.get("device"))
            self.model = FakeModel()

    fake_module = types.ModuleType("kokoro")
    fake_module.KPipeline = FakePipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kokoro", fake_module)
    monkeypatch.setattr(service, "_has_enough_vram", lambda _bytes: False)
    service._load_kokoro_pipeline.cache_clear()
    try:
        pipeline = service._load_kokoro_pipeline()
        assert isinstance(pipeline, FakePipeline)
        assert devices == ["cpu"]
        assert floated == [True]  # forced back to float32 (see _load_kokoro_pipeline)
        service._load_kokoro_pipeline.cache_clear()
        monkeypatch.setattr(service, "_has_enough_vram", lambda _bytes: True)
        service._load_kokoro_pipeline()
        assert devices == ["cpu", "cuda"]
    finally:
        service._load_kokoro_pipeline.cache_clear()

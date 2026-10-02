"""Narration illustration (src/graph/nodes/narration_illustration.py + the
scene_narration wiring in api/ws/session.py): a group of out-of-combat
narration lines gets ONE picture, attached to the group's first line. The
LLM condensing call and the image pipeline are stubbed - their real behavior
is live-verified, not mocked-in-agreement here; what's pinned is everything
deterministic around them (the cap, the fallback, the attachment rule)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src import config
from src.api.ws import session as ws_session_module
from src.graph.nodes import narration_illustration as ni


def _fake_chat(reply: str) -> Any:
    def chat(**_kwargs: Any) -> str:
        return reply

    return chat


def test_prompt_is_the_models_description_behind_the_style_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni, "chat_english_only", _fake_chat("A torch-lit cave mouth at dusk."))

    prompt = ni.build_narration_image_prompt(["The party reaches a cave.", "Torches gutter."])

    assert prompt == ni.STYLE_PREFIX + "A torch-lit cave mouth at dusk."


def test_an_overlong_description_is_capped_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni, "chat_english_only", _fake_chat(" ".join(["word"] * 200)))

    prompt = ni.build_narration_image_prompt(["x"])

    assert prompt.removeprefix(ni.STYLE_PREFIX).split() == ["word"] * ni.MAX_DESCRIPTION_WORDS


def test_a_failed_condensing_call_falls_back_to_the_narration_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_kwargs: Any) -> str:
        raise httpx.ConnectError("ollama is down")

    monkeypatch.setattr(ni, "chat_english_only", boom)

    prompt = ni.build_narration_image_prompt(["Mist over the forest path."])

    assert prompt == ni.STYLE_PREFIX + "Mist over the forest path."


def test_illustrate_returns_a_media_url_for_the_generated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SCENE_IMAGES_ENABLED", True)
    monkeypatch.setattr(ni, "chat_english_only", _fake_chat("A cave."))
    monkeypatch.setattr(ni, "generate_scene_image", lambda _prompt: Path("abc123.png"))

    assert ni.illustrate_narration(["The cave."]) == "/media/scene-images/abc123.png"


def test_illustrate_is_none_when_disabled_empty_or_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def generate(prompt: str) -> Path | None:
        calls.append(prompt)
        return None

    monkeypatch.setattr(ni, "chat_english_only", _fake_chat("A cave."))
    monkeypatch.setattr(ni, "generate_scene_image", generate)

    monkeypatch.setattr(config, "SCENE_IMAGES_ENABLED", False)
    assert ni.illustrate_narration(["The cave."]) is None
    assert calls == []  # disabled: nothing is even attempted

    monkeypatch.setattr(config, "SCENE_IMAGES_ENABLED", True)
    assert ni.illustrate_narration(["", "   "]) is None
    assert calls == []  # nothing to illustrate

    assert ni.illustrate_narration(["The cave."]) is None
    assert len(calls) == 1  # attempted, skipped/failed -> no picture, no error


def test_one_image_is_attached_to_the_first_line_of_a_group_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def illustrate(lines: list[str]) -> str:
        seen.append(lines)
        return "/media/scene-images/x.png"

    monkeypatch.setattr(ws_session_module, "illustrate_narration", illustrate)

    messages = ws_session_module._scene_narration_messages(["one", "two", "three"])

    assert seen == [["one", "two", "three"]]  # one image for the whole group
    assert [m.get("image_url") for m in messages] == ["/media/scene-images/x.png", None, None]
    assert [m["text"] for m in messages] == ["one", "two", "three"]
    assert {m["type"] for m in messages} == {"scene_narration"}


def test_a_group_with_no_image_has_no_image_key_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_session_module, "illustrate_narration", lambda _lines: None)

    messages = ws_session_module._scene_narration_messages(["one"])

    assert "image_url" not in messages[0]  # existing exact-shape consumers unaffected

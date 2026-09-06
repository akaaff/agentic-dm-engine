"""scene_image_node's cadence logic (Day 16): generate at scene start or a
round boundary, never on an ordinary mid-round action. Stays offline by
monkeypatching generate_scene_image - the real SD-Turbo call is exercised
live in tests/imagegen/test_service.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes import scene_image_node as scene_image_module
from src.graph.nodes.scene_image_node import scene_image_node
from src.graph.state_schema import GraphState


def _make_character(char_id: str, *, is_pc: bool) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        hp=10,
        max_hp=10,
        ac=15,
        position=Position(x=1, y=2),
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _make_state(round_: int) -> GameState:
    pc = _make_character("thorin", is_pc=True)
    goblin = _make_character("goblin_1", is_pc=False)
    return GameState(
        encounter_id="goblin_ambush",
        characters={"thorin": pc, "goblin_1": goblin},
        turn_order=["thorin", "goblin_1"],
        current_turn=0,
        round=round_,
        events=[],
        status="in_progress",
    )


def _graph_state(round_: int, *, events_before: int, round_before: int) -> GraphState:
    return {
        "game_state": _make_state(round_),
        "raw_text": "",
        "parsed_action": None,
        "events_before": events_before,
        "round_before": round_before,
        "narration": None,
        "scene_image_url": None,
    }


def _explode(*args: Any, **kwargs: Any) -> Path:
    raise AssertionError("generate_scene_image should not have been called")


def test_generates_an_image_at_scene_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scene_image_module, "generate_scene_image", lambda prompt: Path("/tmp/out/scene.png")
    )
    result = scene_image_node(_graph_state(1, events_before=0, round_before=1))
    # HTTP-servable URL (Day 21), not the raw filesystem path
    # generate_scene_image actually saved to.
    assert result == {"scene_image_url": "/media/scene-images/scene.png"}


def test_generates_an_image_at_a_round_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scene_image_module, "generate_scene_image", lambda prompt: Path("/tmp/out/scene.png")
    )
    # events_before > 0 (not the very first action), but round advanced
    # since this graph invocation was last set up.
    result = scene_image_node(_graph_state(2, events_before=3, round_before=1))
    assert result == {"scene_image_url": "/media/scene-images/scene.png"}


def test_skips_generation_mid_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scene_image_module, "generate_scene_image", _explode)
    result = scene_image_node(_graph_state(1, events_before=3, round_before=1))
    assert result == {"scene_image_url": None}


def test_returns_none_when_generation_is_skipped_or_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scene_image_module, "generate_scene_image", lambda prompt: None)
    result = scene_image_node(_graph_state(1, events_before=0, round_before=1))
    assert result == {"scene_image_url": None}


def test_prompt_mentions_allegiance_and_hp(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_generate(prompt: str) -> Path:
        captured["prompt"] = prompt
        return Path("/tmp/out/scene.png")

    monkeypatch.setattr(scene_image_module, "generate_scene_image", _fake_generate)
    scene_image_node(_graph_state(1, events_before=0, round_before=1))

    assert "Thorin (ally, 10/10 HP)" in captured["prompt"]
    assert "Goblin_1 (enemy, 10/10 HP)" in captured["prompt"]
    assert "goblin ambush" in captured["prompt"]

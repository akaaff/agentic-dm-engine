"""Day 23: campaign_generator's own LLM call (chat_structured) is
monkeypatched, same pattern as test_judge.py - these tests exercise the
validate/retry loop, the clamping of monster_count/dc into their documented
ranges, and that the assembled Campaign/Encounter files round-trip through
the exact same loaders a hand-authored campaign uses. A live-LLM smoke test
belongs in tests/llm/, not here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from src.engine.campaign import Campaign
from src.engine.encounter import Encounter
from src.llm import campaign_generator as campaign_generator_module
from src.llm.campaign_generator import GenerationError, generate_campaign

_ONE_SHOT_SCENES: list[dict[str, Any]] = [
    {"type": "narrative_beat", "narrative_intro": "The party arrives at a quiet crossroads."},
    {
        "type": "combat",
        "narrative_intro": "Goblins ambush from the treeline!",
        "combat": {"monster_index": "goblin", "monster_count": 3},
    },
    {"type": "narrative_beat", "narrative_intro": "The road is clear again."},
]


def _fake_chat_structured(scenes: list[dict[str, Any]], title: str = "Generated Title") -> Any:
    """Returns a chat_structured stand-in bound to a fixed scenes payload -
    it validates against whatever dynamic schema `generate_campaign` passes
    in, exactly like the real Ollama response would."""

    def _fake(
        messages: list[dict[str, str]], schema: type[BaseModel], temperature: float = 0.2
    ) -> BaseModel:
        return schema.model_validate(
            {"title": title, "description": "A short test campaign.", "scenes": scenes}
        )

    return _fake


def test_generate_campaign_writes_a_valid_one_shot_and_matching_encounter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        campaign_generator_module, "chat_structured", _fake_chat_structured(_ONE_SHOT_SCENES)
    )
    campaigns_dir = tmp_path / "campaigns"
    encounters_dir = tmp_path / "campaigns" / "encounters"

    campaign = generate_campaign(
        campaign_id="test_gen_one_shot",
        size="one_shot",
        campaigns_dir=campaigns_dir,
        encounters_dir=encounters_dir,
    )

    assert campaign.id == "test_gen_one_shot"
    assert campaign.title == "Generated Title"
    assert [s.id for s in campaign.scenes] == ["scene_1", "scene_2", "scene_3"]
    assert [s.next_scene_id for s in campaign.scenes] == ["scene_2", "scene_3", None]

    combat_scene = campaign.scenes[1]
    assert combat_scene.type == "combat"
    assert combat_scene.encounter_ref == "test_gen_one_shot__scene_2"

    # The campaign file round-trips through the exact same loader a
    # hand-authored one uses.
    saved = Campaign.model_validate(
        yaml.safe_load((campaigns_dir / "test_gen_one_shot.yaml").read_text(encoding="utf-8"))
    )
    assert saved == campaign

    # So does the generated encounter.
    encounter = Encounter.model_validate(
        yaml.safe_load(
            (encounters_dir / "test_gen_one_shot__scene_2.yaml").read_text(encoding="utf-8")
        )
    )
    assert encounter.id == "test_gen_one_shot__scene_2"
    assert len(encounter.monsters) == 3
    assert all(m.monster_index == "goblin" for m in encounter.monsters)
    assert len(encounter.party_spawn_points) >= 2


def test_generate_campaign_clamps_monster_count_and_dc_to_documented_ranges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenes: list[dict[str, Any]] = [
        {"type": "narrative_beat", "narrative_intro": "Intro."},
        {
            "type": "combat",
            "narrative_intro": "A huge swarm attacks!",
            "combat": {"monster_index": "kobold", "monster_count": 99},
        },
        {"type": "narrative_beat", "narrative_intro": "Outro."},
    ]
    monkeypatch.setattr(campaign_generator_module, "chat_structured", _fake_chat_structured(scenes))

    campaign = generate_campaign(
        campaign_id="test_gen_clamp",
        size="one_shot",
        campaigns_dir=tmp_path / "campaigns",
        encounters_dir=tmp_path / "campaigns" / "encounters",
    )

    encounter = Encounter.model_validate(
        yaml.safe_load(
            (tmp_path / "campaigns" / "encounters" / "test_gen_clamp__scene_2.yaml").read_text(
                encoding="utf-8"
            )
        )
    )
    # monster_count=99 clamped to the documented max of 4.
    assert len(encounter.monsters) == 4
    assert campaign.id == "test_gen_clamp"


def test_generate_campaign_retries_after_an_invalid_scene_count_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []
    bad_scenes = [{"type": "narrative_beat", "narrative_intro": "Not enough scenes."}]

    def _fake(
        messages: list[dict[str, str]], schema: type[BaseModel], temperature: float = 0.2
    ) -> BaseModel:
        calls.append(1)
        scenes = bad_scenes if len(calls) == 1 else _ONE_SHOT_SCENES
        return schema.model_validate({"title": "T", "description": "D", "scenes": scenes})

    monkeypatch.setattr(campaign_generator_module, "chat_structured", _fake)

    campaign = generate_campaign(
        campaign_id="test_gen_retry",
        size="one_shot",
        campaigns_dir=tmp_path / "campaigns",
        encounters_dir=tmp_path / "campaigns" / "encounters",
    )

    assert len(calls) == 2
    assert len(campaign.scenes) == 3


def test_generate_campaign_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A one_shot with zero combat scenes never satisfies "exactly 1 combat".
    always_bad = [
        {"type": "narrative_beat", "narrative_intro": "A."},
        {"type": "narrative_beat", "narrative_intro": "B."},
        {"type": "narrative_beat", "narrative_intro": "C."},
    ]
    monkeypatch.setattr(
        campaign_generator_module, "chat_structured", _fake_chat_structured(always_bad)
    )

    with pytest.raises(GenerationError, match="combat"):
        generate_campaign(
            campaign_id="test_gen_fail",
            size="one_shot",
            campaigns_dir=tmp_path / "campaigns",
            encounters_dir=tmp_path / "campaigns" / "encounters",
        )

    # Nothing partially written on failure.
    assert not (tmp_path / "campaigns" / "test_gen_fail.yaml").exists()


def test_generate_campaign_builds_a_skill_challenge_scene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenes: list[dict[str, Any]] = [
        {"type": "narrative_beat", "narrative_intro": "Intro."},
        {
            "type": "skill_challenge",
            "narrative_intro": "A locked door bars the way.",
            "skill_challenge": {
                "skill": "athletics",
                "dc": 30,
                "success_text": "The door bursts open.",
                "failure_text": "The door holds firm.",
            },
        },
        {
            "type": "combat",
            "narrative_intro": "Wolves howl nearby.",
            "combat": {"monster_index": "wolf", "monster_count": 1},
        },
        {"type": "narrative_beat", "narrative_intro": "Outro."},
    ]
    monkeypatch.setattr(campaign_generator_module, "chat_structured", _fake_chat_structured(scenes))

    campaign = generate_campaign(
        campaign_id="test_gen_skill",
        size="short_arc",
        campaigns_dir=tmp_path / "campaigns",
        encounters_dir=tmp_path / "campaigns" / "encounters",
    )

    skill_scene = campaign.scenes[1]
    assert skill_scene.type == "skill_challenge"
    assert skill_scene.skill_challenge_def is not None
    assert skill_scene.skill_challenge_def.skill == "athletics"
    # dc=30 clamped to the documented max of 15.
    assert skill_scene.skill_challenge_def.dc == 15

    combat_scene = campaign.scenes[2]
    # monster_count=1 clamped up to the documented min of 2.
    encounter = Encounter.model_validate(
        yaml.safe_load(
            (
                tmp_path / "campaigns" / "encounters" / f"{combat_scene.encounter_ref}.yaml"
            ).read_text(encoding="utf-8")
        )
    )
    assert len(encounter.monsters) == 2

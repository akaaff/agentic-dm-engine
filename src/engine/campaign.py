"""Campaign definitions - a linear sequence of scenes. Authored as YAML
under data/campaigns/.

Scene chains are kept as plain data (not resolved into anything more rigid)
deliberately: Phase 8's planned dynamic campaign adaptation needs to splice
new scenes into this chain mid-session, so nothing here should assume the
chain is fixed once loaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

DEFAULT_CAMPAIGNS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "campaigns"

CampaignSize = Literal["one_shot", "short_arc", "full"]
SceneType = Literal["narrative_beat", "combat", "skill_challenge", "roleplay"]


class Scene(BaseModel):
    id: str
    type: SceneType
    narrative_intro: str
    encounter_ref: str | None = None
    """Set for `combat` scenes - looked up via encounter.load_encounter()."""
    skill_challenge_def: dict[str, Any] | None = None
    next_scene_id: str | None = None
    """None marks the last scene in the campaign."""


class Campaign(BaseModel):
    id: str
    title: str
    size: CampaignSize
    description: str
    scenes: list[Scene]

    def first_scene(self) -> Scene:
        return self.scenes[0]

    def scene_by_id(self, scene_id: str) -> Scene:
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        raise KeyError(f"No scene {scene_id!r} in campaign {self.id!r}")


def load_campaign(campaign_id: str, campaigns_dir: Path = DEFAULT_CAMPAIGNS_DIR) -> Campaign:
    path = campaigns_dir / f"{campaign_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Campaign.model_validate(data)


def load_all_campaigns(campaigns_dir: Path = DEFAULT_CAMPAIGNS_DIR) -> list[Campaign]:
    """Non-recursive glob - naturally excludes campaigns_dir/encounters/,
    which holds Encounter YAML, not Campaign YAML."""
    return [
        Campaign.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(campaigns_dir.glob("*.yaml"))
    ]

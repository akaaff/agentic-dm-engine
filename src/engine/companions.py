"""Pregen companion roster - character-creation "recipes" authored as YAML,
run through the same create_character() pipeline used for player-made
characters (never hand-computed HP/AC values that could drift from the
engine's actual derivation logic)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from src.engine.character_creation import create_character
from src.engine.srd_loader import SrdIndex
from src.engine.state import AbilityScore, Character

DEFAULT_COMPANIONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companions"


class CompanionSpec(BaseModel):
    character_id: str
    name: str
    persona: str
    race_index: str
    class_index: str
    background_index: str
    base_ability_scores: dict[AbilityScore, int]
    chosen_skills: list[str]
    chosen_equipment: list[str] = []


def load_companion_spec(
    companion_id: str, companions_dir: Path = DEFAULT_COMPANIONS_DIR
) -> CompanionSpec:
    path = companions_dir / f"{companion_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CompanionSpec.model_validate(data)


def load_all_companion_specs(companions_dir: Path = DEFAULT_COMPANIONS_DIR) -> list[CompanionSpec]:
    return [
        CompanionSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(companions_dir.glob("*.yaml"))
    ]


def load_companion_spec_by_character_id(
    character_id: str, companions_dir: Path = DEFAULT_COMPANIONS_DIR
) -> CompanionSpec | None:
    """`character_id` (e.g. "companion_grom") is a different namespace from
    load_companion_spec's filename-stem argument (e.g. "grom_ironfist") -
    both exist, just as different fields on the same CompanionSpec. Every
    caller outside this module (the API, the WS session layer) only ever
    sees the roster via Character.id (== CompanionSpec.character_id), so
    they need this lookup, not load_companion_spec directly - conflating
    the two was a real bug (see CLAUDE.md, Day 19)."""
    return next(
        (s for s in load_all_companion_specs(companions_dir) if s.character_id == character_id),
        None,
    )


def build_companion(spec: CompanionSpec, srd: SrdIndex | None = None) -> Character:
    """is_pc=True (not a monster - counts toward the party for
    victory/defeat checking in turn_engine) and is_companion=True (marks it
    as AI-controlled for the Day 13 player_agent node, distinct from the
    human's own character)."""
    return create_character(
        character_id=spec.character_id,
        name=spec.name,
        race_index=spec.race_index,
        class_index=spec.class_index,
        background_index=spec.background_index,
        base_ability_scores=spec.base_ability_scores,
        chosen_skills=spec.chosen_skills,
        chosen_equipment=spec.chosen_equipment,
        is_pc=True,
        is_companion=True,
        persona=spec.persona,
        srd=srd,
    )

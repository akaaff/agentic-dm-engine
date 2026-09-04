"""Loads the vendored SRD 5.1 JSON into index-keyed dicts.

Deliberately NOT modeled as pydantic schemas for every field - the SRD JSON
carries far more detail (full spell text, monster special abilities, nested
equipment-category URLs) than this engine needs. Consumers pull the specific
fields they need directly from the raw dict; `index` (the SRD's own stable
slug, e.g. "goblin", "chain-mail") is the lookup key everywhere in this
project instead of display names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "srd"

SrdEntry = dict[str, Any]


@dataclass(frozen=True)
class SrdIndex:
    monsters: dict[str, SrdEntry]
    spells: dict[str, SrdEntry]
    equipment: dict[str, SrdEntry]
    equipment_categories: dict[str, SrdEntry]
    conditions: dict[str, SrdEntry]
    races: dict[str, SrdEntry]
    subraces: dict[str, SrdEntry]
    classes: dict[str, SrdEntry]
    subclasses: dict[str, SrdEntry]
    backgrounds: dict[str, SrdEntry]


def _load_indexed(data_dir: Path, filename: str) -> dict[str, SrdEntry]:
    items = json.loads((data_dir / filename).read_text(encoding="utf-8"))
    return {item["index"]: item for item in items}


@cache
def load_srd(data_dir: Path = DEFAULT_DATA_DIR) -> SrdIndex:
    return SrdIndex(
        monsters=_load_indexed(data_dir, "5e-SRD-Monsters.json"),
        spells=_load_indexed(data_dir, "5e-SRD-Spells.json"),
        equipment=_load_indexed(data_dir, "5e-SRD-Equipment.json"),
        equipment_categories=_load_indexed(data_dir, "5e-SRD-Equipment-Categories.json"),
        conditions=_load_indexed(data_dir, "5e-SRD-Conditions.json"),
        races=_load_indexed(data_dir, "5e-SRD-Races.json"),
        subraces=_load_indexed(data_dir, "5e-SRD-Subraces.json"),
        classes=_load_indexed(data_dir, "5e-SRD-Classes.json"),
        subclasses=_load_indexed(data_dir, "5e-SRD-Subclasses.json"),
        backgrounds=_load_indexed(data_dir, "5e-SRD-Backgrounds.json"),
    )

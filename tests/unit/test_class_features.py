"""Issue #103: the level-1 class features are listed (name + description + what this
game does with them) on the class endpoint, for the creator preview and the sheet."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.engine.class_features import LEVEL_1_FEATURES, level_1_features
from src.engine.srd_loader import load_srd


def test_every_srd_class_has_level_one_features() -> None:
    assert set(LEVEL_1_FEATURES) == set(load_srd().classes)
    for features in LEVEL_1_FEATURES.values():
        assert features
        assert all(f.name and f.desc for f in features)


def test_an_unknown_class_has_none() -> None:
    assert level_1_features("artificer") == []


@pytest.mark.parametrize(
    ("class_index", "names"),
    [
        ("ranger", ["Favored Enemy", "Natural Explorer"]),
        ("rogue", ["Expertise", "Sneak Attack", "Thieves' Cant"]),
        ("druid", ["Druidic", "Spellcasting"]),
        ("fighter", ["Fighting Style", "Second Wind"]),
    ],
)
def test_the_feature_names_match_the_srd(class_index: str, names: list[str]) -> None:
    assert [f.name for f in level_1_features(class_index)] == names


def test_flavor_only_features_say_so_and_modeled_ones_do_not() -> None:
    ranger = {f.name: f for f in level_1_features("ranger")}
    assert ranger["Favored Enemy"].note and "Flavor only" in ranger["Favored Enemy"].note
    rogue = {f.name: f for f in level_1_features("rogue")}
    assert rogue["Sneak Attack"].note is None
    assert rogue["Thieves' Cant"].note and "Flavor only" in rogue["Thieves' Cant"].note


def test_the_class_endpoint_returns_them() -> None:
    detail = TestClient(app).get("/characters/classes/ranger").json()
    assert [f["name"] for f in detail["features"]] == ["Favored Enemy", "Natural Explorer"]
    assert all(f["desc"] for f in detail["features"])
    assert detail["features"][0]["note"].startswith("Flavor only")
    assert TestClient(app).get("/characters/classes/barbarian").json()["features"][0]["note"]

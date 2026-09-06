from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app


@pytest.fixture
def client() -> Generator[TestClient]:
    # StaticPool: a plain "sqlite:///:memory:" engine hands out a fresh,
    # separate in-memory DB per connection by default - each request in a
    # test would otherwise see an empty DB. StaticPool keeps one connection
    # (and thus one DB) alive for the engine's whole lifetime.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db() -> Generator[Session]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


_VALID_FIGHTER_BODY = {
    "character_id": "thorin",
    "name": "Thorin",
    "race_index": "human",
    "class_index": "fighter",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
    "chosen_skills": ["skill-athletics", "skill-perception"],
    "chosen_equipment": ["chain-mail", "shield"],
}


def test_list_races_includes_human(client: TestClient) -> None:
    response = client.get("/characters/races")
    assert response.status_code == 200
    indices = {r["index"] for r in response.json()}
    assert "human" in indices
    assert "elf" in indices
    human = next(r for r in response.json() if r["index"] == "human")
    assert human["ability_bonuses"] == {"str": 1, "dex": 1, "con": 1, "int": 1, "wis": 1, "cha": 1}


def test_get_class_detail_exposes_skill_choice_count_and_options(client: TestClient) -> None:
    response = client.get("/characters/classes/fighter")
    assert response.status_code == 200
    body = response.json()
    assert body["skill_choose"] == 2
    assert "skill-athletics" in body["skill_options"]
    assert len(body["skill_options"]) == 8


def test_get_class_detail_sums_multiple_proficiency_choice_pools(client: TestClient) -> None:
    # Regression guard for the Bard two-pool gotcha (see CLAUDE.md) - the
    # wizard needs this same count to ask for the right number of choices.
    response = client.get("/characters/classes/bard")
    assert response.status_code == 200
    assert response.json()["skill_choose"] == 6


def test_get_unknown_class_detail_returns_404(client: TestClient) -> None:
    response = client.get("/characters/classes/not-a-class")
    assert response.status_code == 404


def test_list_equipment_returns_only_weapons_and_armor(client: TestClient) -> None:
    response = client.get("/characters/equipment")
    assert response.status_code == 200
    body = response.json()
    categories = {item["category"] for item in body}
    assert categories == {"weapon", "armor"}
    indices = {item["index"] for item in body}
    assert "longsword" in indices
    assert "chain-mail" in indices


def test_list_classes_includes_fighter_with_hit_die(client: TestClient) -> None:
    response = client.get("/characters/classes")
    assert response.status_code == 200
    fighter = next(c for c in response.json() if c["index"] == "fighter")
    assert fighter["hit_die"] == 10


def test_list_backgrounds_returns_only_acolyte(client: TestClient) -> None:
    response = client.get("/characters/backgrounds")
    assert response.status_code == 200
    assert [b["index"] for b in response.json()] == ["acolyte"]


def test_create_character_end_to_end_and_persists(client: TestClient) -> None:
    create_response = client.post("/characters", json=_VALID_FIGHTER_BODY)
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["hp"] == 12
    assert created["ac"] == 18
    assert created["stats"]["STR"] == 16

    fetch_response = client.get("/characters/thorin")
    assert fetch_response.status_code == 200
    fetched = fetch_response.json()
    assert fetched["hp"] == 12
    assert fetched["ac"] == 18
    assert fetched["inventory"] == created["inventory"]
    # Regression guard: found live via the Day 17 character-creator wizard's
    # own re-fetch-after-create check - CharacterRecord predates
    # Character.class_index/skill_proficiencies (Day 14/13) and silently
    # dropped both on every single create, regardless of class.
    assert fetched["class_index"] == "fighter"
    assert set(fetched["skill_proficiencies"]) == {
        "skill-athletics",
        "skill-perception",
        "skill-insight",
        "skill-religion",
    }


def test_get_unknown_character_returns_404(client: TestClient) -> None:
    response = client.get("/characters/does-not-exist")
    assert response.status_code == 404


def test_create_duplicate_character_id_returns_409(client: TestClient) -> None:
    client.post("/characters", json=_VALID_FIGHTER_BODY)
    response = client.post("/characters", json=_VALID_FIGHTER_BODY)
    assert response.status_code == 409


def test_create_character_with_wrong_skill_count_returns_400(client: TestClient) -> None:
    body = {**_VALID_FIGHTER_BODY, "chosen_skills": ["skill-athletics"]}
    response = client.post("/characters", json=body)
    assert response.status_code == 400

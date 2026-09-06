from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.db.models import Base, CampaignProgress
from src.api.db.session import get_db
from src.api.main import app

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


@pytest.fixture
def client() -> Generator[TestClient]:
    # Same StaticPool in-memory setup as test_api_characters.py - a session
    # endpoint needs to look up a real persisted character.
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


def _create_thorin(client: TestClient) -> None:
    response = client.post("/characters", json=_VALID_FIGHTER_BODY)
    assert response.status_code == 201


def test_start_session_persists_a_campaign_progress_row(client: TestClient) -> None:
    _create_thorin(client)

    response = client.post(
        "/sessions",
        json={
            "campaign_id": "goblin_ambush_oneshot",
            "character_id": "thorin",
            "companion_ids": ["companion_grom", "companion_silvana"],
        },
    )
    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert session_id

    # Verify persistence the same way test_api_characters.py does for
    # characters: an independent read, not just trusting the create
    # response - via SQLAlchemy directly, since there's no GET /sessions.
    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    assert record.campaign_id == "goblin_ambush_oneshot"
    assert record.current_scene_id == "intro"
    assert record.party_character_ids == ["thorin", "companion_grom", "companion_silvana"]
    assert record.status == "in_progress"


def test_start_session_with_no_companions(client: TestClient) -> None:
    _create_thorin(client)

    response = client.post(
        "/sessions",
        json={"campaign_id": "goblin_ambush_oneshot", "character_id": "thorin"},
    )
    assert response.status_code == 201


def test_start_session_rejects_unknown_campaign(client: TestClient) -> None:
    _create_thorin(client)

    response = client.post(
        "/sessions",
        json={"campaign_id": "does-not-exist", "character_id": "thorin"},
    )
    assert response.status_code == 404


def test_start_session_rejects_unknown_character(client: TestClient) -> None:
    response = client.post(
        "/sessions",
        json={"campaign_id": "goblin_ambush_oneshot", "character_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_start_session_rejects_unknown_companion(client: TestClient) -> None:
    _create_thorin(client)

    response = client.post(
        "/sessions",
        json={
            "campaign_id": "goblin_ambush_oneshot",
            "character_id": "thorin",
            "companion_ids": ["not-a-real-companion"],
        },
    )
    assert response.status_code == 404

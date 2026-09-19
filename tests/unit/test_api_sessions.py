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


# Issue #44 - the lobby/join/start flow. _create_thorin already exists for
# the legacy single-shot tests above; a second real character (Elrond, the
# same one Day-7's demo party uses) covers the actual multi-human case.
_VALID_WIZARD_BODY = {
    "character_id": "elrond",
    "name": "Elrond",
    "race_index": "elf",
    "class_index": "wizard",
    "background_index": "acolyte",
    "base_ability_scores": {"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
    "chosen_skills": ["skill-arcana", "skill-history"],
    "chosen_equipment": ["dagger"],
}


def _create_elrond(client: TestClient) -> None:
    response = client.post("/characters", json=_VALID_WIZARD_BODY)
    assert response.status_code == 201


def _create_lobby(client: TestClient, campaign_id: str = "goblin_ambush_oneshot") -> str:
    response = client.post("/sessions/lobby", json={"campaign_id": campaign_id})
    assert response.status_code == 201
    session_id: str = response.json()["session_id"]
    return session_id


def test_create_lobby_persists_an_open_campaign_progress_row(client: TestClient) -> None:
    session_id = _create_lobby(client)

    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    assert record.campaign_id == "goblin_ambush_oneshot"
    assert record.status == "open"
    assert record.party_character_ids == []
    assert record.player_tokens == {}


def test_create_lobby_rejects_unknown_campaign(client: TestClient) -> None:
    response = client.post("/sessions/lobby", json={"campaign_id": "does-not-exist"})
    assert response.status_code == 404


def test_join_lobby_with_new_character_claims_a_seat_and_issues_a_token(
    client: TestClient,
) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)

    response = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    assert response.status_code == 200
    body = response.json()
    assert body["character_id"] == "thorin"
    assert body["token"]

    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    assert record.party_character_ids == ["thorin"]
    assert record.player_tokens == {body["token"]: "thorin"}


def test_join_lobby_two_players_get_distinct_tokens_and_both_land_in_the_party(
    client: TestClient,
) -> None:
    _create_thorin(client)
    _create_elrond(client)
    session_id = _create_lobby(client)

    r1 = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    r2 = client.post(f"/sessions/{session_id}/join", json={"character_id": "elrond"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    token1, token2 = r1.json()["token"], r2.json()["token"]
    assert token1 != token2

    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    assert record.party_character_ids == ["thorin", "elrond"]
    assert record.player_tokens == {token1: "thorin", token2: "elrond"}


def test_join_lobby_rejects_unknown_character(client: TestClient) -> None:
    session_id = _create_lobby(client)
    response = client.post(f"/sessions/{session_id}/join", json={"character_id": "does-not-exist"})
    assert response.status_code == 404


def test_join_lobby_rejects_unknown_session(client: TestClient) -> None:
    _create_thorin(client)
    response = client.post("/sessions/does-not-exist/join", json={"character_id": "thorin"})
    assert response.status_code == 404


def test_join_lobby_requires_character_id_or_token(client: TestClient) -> None:
    session_id = _create_lobby(client)
    response = client.post(f"/sessions/{session_id}/join", json={})
    assert response.status_code == 400


def test_join_lobby_is_idempotent_for_a_retried_join(client: TestClient) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)

    first = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    second = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    assert first.json()["token"] == second.json()["token"]

    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    # Not doubled - the retry didn't add a second party seat for the same character.
    assert record.party_character_ids == ["thorin"]


def test_join_lobby_with_existing_token_resumes_the_same_character(client: TestClient) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)
    token = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"}).json()[
        "token"
    ]

    resume = client.post(f"/sessions/{session_id}/join", json={"token": token})
    assert resume.status_code == 200
    assert resume.json() == {"token": token, "character_id": "thorin"}


def test_join_lobby_rejects_unknown_token(client: TestClient) -> None:
    session_id = _create_lobby(client)
    response = client.post(f"/sessions/{session_id}/join", json={"token": "not-a-real-token"})
    assert response.status_code == 404


def test_join_lobby_rejects_a_new_seat_once_the_lobby_has_started(client: TestClient) -> None:
    _create_thorin(client)
    _create_elrond(client)
    session_id = _create_lobby(client)
    client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    client.post(f"/sessions/{session_id}/start", json={})

    response = client.post(f"/sessions/{session_id}/join", json={"character_id": "elrond"})
    assert response.status_code == 409


def test_join_lobby_with_existing_token_still_resumes_after_the_lobby_has_started(
    client: TestClient,
) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)
    token = client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"}).json()[
        "token"
    ]
    client.post(f"/sessions/{session_id}/start", json={})

    resume = client.post(f"/sessions/{session_id}/join", json={"token": token})
    assert resume.status_code == 200
    assert resume.json() == {"token": token, "character_id": "thorin"}


def test_start_lobby_fills_remaining_seats_with_companions_and_flips_status(
    client: TestClient,
) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)
    client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})

    response = client.post(
        f"/sessions/{session_id}/start", json={"companion_ids": ["companion_grom"]}
    )
    assert response.status_code == 200
    assert response.json()["party_character_ids"] == ["thorin", "companion_grom"]

    db = next(app.dependency_overrides[get_db]())
    record = db.get(CampaignProgress, session_id)
    assert record is not None
    assert record.status == "in_progress"
    assert record.party_character_ids == ["thorin", "companion_grom"]


def test_start_lobby_rejects_when_already_started(client: TestClient) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)
    client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    client.post(f"/sessions/{session_id}/start", json={})

    response = client.post(f"/sessions/{session_id}/start", json={})
    assert response.status_code == 409


def test_start_lobby_rejects_an_empty_party(client: TestClient) -> None:
    session_id = _create_lobby(client)
    response = client.post(f"/sessions/{session_id}/start", json={})
    assert response.status_code == 400


def test_start_lobby_rejects_unknown_companion(client: TestClient) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)
    client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})

    response = client.post(
        f"/sessions/{session_id}/start", json={"companion_ids": ["not-a-real-companion"]}
    )
    assert response.status_code == 404


def test_get_lobby_status_reflects_join_and_start(client: TestClient) -> None:
    _create_thorin(client)
    session_id = _create_lobby(client)

    open_status = client.get(f"/sessions/{session_id}")
    assert open_status.status_code == 200
    assert open_status.json() == {
        "session_id": session_id,
        "campaign_id": "goblin_ambush_oneshot",
        "status": "open",
        "party_character_ids": [],
    }

    client.post(f"/sessions/{session_id}/join", json={"character_id": "thorin"})
    client.post(f"/sessions/{session_id}/start", json={"companion_ids": ["companion_grom"]})

    started_status = client.get(f"/sessions/{session_id}")
    assert started_status.status_code == 200
    assert started_status.json()["status"] == "in_progress"
    assert started_status.json()["party_character_ids"] == ["thorin", "companion_grom"]


def test_get_lobby_status_rejects_unknown_session(client: TestClient) -> None:
    response = client.get("/sessions/does-not-exist")
    assert response.status_code == 404

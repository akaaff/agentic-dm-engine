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

    dwarf = next(r for r in response.json() if r["index"] == "dwarf")
    trait_names = {t["name"] for t in dwarf["traits"]}
    assert "Darkvision" in trait_names
    darkvision = next(t for t in dwarf["traits"] if t["name"] == "Darkvision")
    assert darkvision["desc"]  # real SRD text, not asserting exact wording


def test_get_class_detail_exposes_skill_choice_count_and_options(client: TestClient) -> None:
    response = client.get("/characters/classes/fighter")
    assert response.status_code == 200
    body = response.json()
    assert body["skill_choose"] == 2
    assert "skill-athletics" in body["skill_options"]
    assert len(body["skill_options"]) == 8


def test_get_class_detail_restricts_equipment_options_to_proficient_gear(
    client: TestClient,
) -> None:
    response = client.get("/characters/classes/wizard")
    assert response.status_code == 200
    options = set(response.json()["equipment_options"])
    assert options == {"dagger", "dart", "sling", "quarterstaff", "crossbow-light"}
    assert "plate-armor" not in options
    assert "longsword" not in options


def test_get_class_detail_sums_multiple_proficiency_choice_pools(client: TestClient) -> None:
    # Regression guard for the Bard two-pool gotcha (see CLAUDE.md) - the
    # wizard needs this same count to ask for the right number of choices.
    response = client.get("/characters/classes/bard")
    assert response.status_code == 200
    assert response.json()["skill_choose"] == 6


def test_get_class_detail_skips_monks_nested_tool_or_instrument_choice(
    client: TestClient,
) -> None:
    # Regression guard: Monk's 2nd proficiency_choices entry ("one type of
    # artisan's tools or one musical instrument") nests a choice inside each
    # option instead of a flat reference - the only entry SRD-wide shaped
    # that way. Crashed this endpoint with a raw KeyError before the fix
    # (caught live - see CLAUDE.md). Tool/instrument proficiencies aren't
    # modeled by this project, so that entry should be skipped entirely:
    # only the 2 real skill choices should be required.
    response = client.get("/characters/classes/monk")
    assert response.status_code == 200
    body = response.json()
    assert body["skill_choose"] == 2
    assert set(body["skill_options"]) == {
        "skill-acrobatics",
        "skill-athletics",
        "skill-history",
        "skill-insight",
        "skill-religion",
        "skill-stealth",
    }


def test_get_class_detail_exposes_known_spells_pool_with_real_detail(client: TestClient) -> None:
    # Issue #30: a "Spells Known" caster (Bard) exposes its level-1 spell
    # pool with real mechanical detail, not just bare names - same spirit as
    # #15's equipment detail.
    response = client.get("/characters/classes/bard")
    assert response.status_code == 200
    body = response.json()
    assert body["spells_known"] == 4
    pool = {s["index"]: s for s in body["known_spells_pool"]}
    assert "healing-word" in pool
    healing_word = pool["healing-word"]
    assert healing_word["level"] == 1
    assert healing_word["casting_time"] == "1 bonus action"
    assert healing_word["range"] == "60 feet"
    assert healing_word["heal_dice"] == "1d4 + MOD"
    # Every entry is real level-1 SRD data, no cantrips leaking in.
    assert all(s["level"] == 1 for s in pool.values())


def test_get_class_detail_spells_known_is_zero_for_a_non_caster(client: TestClient) -> None:
    response = client.get("/characters/classes/fighter")
    assert response.status_code == 200
    body = response.json()
    assert body["spells_known"] == 0
    assert body["known_spells_pool"] == []


def test_get_class_detail_exposes_starting_equipment(client: TestClient) -> None:
    # Issue #32: the class's fixed starting kit (not the optional
    # proficiency-gated picker) - previously never exposed at all, so the
    # wizard had no way to show a Barbarian's real explorer's pack + 4
    # javelins before character creation actually ran.
    response = client.get("/characters/classes/barbarian")
    assert response.status_code == 200
    starting_equipment = response.json()["starting_equipment"]
    assert {
        "index": "explorers-pack",
        "name": "Explorer's Pack",
        "quantity": 1,
    } in starting_equipment
    assert {"index": "javelin", "name": "Javelin", "quantity": 4} in starting_equipment


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


def test_list_skills_exposes_real_srd_descriptions(client: TestClient) -> None:
    response = client.get("/characters/skills")
    assert response.status_code == 200
    body = response.json()
    perception = next(s for s in body if s["index"] == "skill-perception")
    assert perception["ability"] == "WIS"
    assert perception["desc"]  # real SRD text, not asserting exact wording
    indices = {s["index"] for s in body}
    assert "skill-athletics" in indices  # matches ClassDetail.skill_options' format


def test_list_classes_includes_fighter_with_hit_die(client: TestClient) -> None:
    response = client.get("/characters/classes")
    assert response.status_code == 200
    fighter = next(c for c in response.json() if c["index"] == "fighter")
    assert fighter["hit_die"] == 10


def test_list_backgrounds_returns_only_acolyte(client: TestClient) -> None:
    response = client.get("/characters/backgrounds")
    assert response.status_code == 200
    body = response.json()
    assert [b["index"] for b in body] == ["acolyte"]
    # Issue #32: the background's own fixed starting kit, same gap as
    # ClassDetail.starting_equipment above - BackgroundSummary previously
    # had no equipment field at all.
    starting_equipment = body[0]["starting_equipment"]
    assert {
        "index": "clothes-common",
        "name": "Clothes, common",
        "quantity": 1,
    } in starting_equipment
    assert {"index": "pouch", "name": "Pouch", "quantity": 1} in starting_equipment


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


def test_create_character_persists_class_resources_and_level_fields(client: TestClient) -> None:
    # Issue #29 regression: CharacterRecord never had columns for
    # class_resources/level/hit_die_sides/hit_dice_remaining/
    # saving_throw_proficiencies at all - found live via a real WS session
    # (which always reloads a character from the DB, never reuses the
    # in-memory create response) reporting "no rage uses remaining" on a
    # freshly-created Barbarian who had never raged. Same bug shape as
    # test_create_character_end_to_end_and_persists's own class_index/
    # skill_proficiencies regression guard above, just a second batch of
    # fields that fell through the same gap.
    body = {
        "character_id": "grosh",
        "name": "Grosh",
        "race_index": "human",
        "class_index": "barbarian",
        "background_index": "acolyte",
        "base_ability_scores": {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        "chosen_skills": ["skill-athletics", "skill-intimidation"],
    }
    client.post("/characters", json=body)

    fetched = client.get("/characters/grosh").json()
    assert fetched["level"] == 1
    assert fetched["hit_die_sides"] == 12
    assert fetched["hit_dice_remaining"] == 1
    assert fetched["class_resources"] == {"rage": 2}
    assert set(fetched["saving_throw_proficiencies"]) == {"STR", "CON"}


def test_create_character_persists_known_spells(client: TestClient) -> None:
    # Issue #30 - same "a live WS session reloads from the DB" persistence
    # gap as class_resources above, closed from the start this time (the
    # migration/round-trip were added in the same pass as the feature, not
    # found live after the fact).
    body = {
        "character_id": "pip",
        "name": "Pip",
        "race_index": "halfling",
        "class_index": "bard",
        "background_index": "acolyte",
        "base_ability_scores": {"STR": 8, "DEX": 14, "CON": 12, "INT": 10, "WIS": 13, "CHA": 15},
        "chosen_skills": [
            "skill-performance",
            "skill-persuasion",
            "skill-deception",
            "skill-acrobatics",
            "skill-history",
            "skill-insight",
        ],
        "chosen_spells": ["healing-word", "thunderwave", "sleep", "charm-person"],
    }
    create_response = client.post("/characters", json=body)
    assert create_response.status_code == 201
    assert create_response.json()["known_spells"] == [
        "healing-word",
        "thunderwave",
        "sleep",
        "charm-person",
    ]

    fetched = client.get("/characters/pip").json()
    assert fetched["known_spells"] == ["healing-word", "thunderwave", "sleep", "charm-person"]


def test_create_character_persists_fighting_style(client: TestClient) -> None:
    body = {**_VALID_FIGHTER_BODY, "fighting_style": "dueling"}
    client.post("/characters", json=body)

    fetched = client.get("/characters/thorin").json()
    assert fetched["fighting_style"] == "dueling"


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

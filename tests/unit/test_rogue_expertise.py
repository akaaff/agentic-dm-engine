"""Issue #85: Rogue Expertise - two of the rogue's skill proficiencies get a
doubled proficiency bonus. Chosen at creation (validated), applied to every
check that uses the skill, shown in the roll breakdown, and persisted."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.db.models import Base
from src.api.db.session import get_db
from src.api.main import app
from src.engine.actions import ParsedAction
from src.engine.character_creation import CharacterCreationError, create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


_SKILLS = ["skill-stealth", "skill-sleight-of-hand", "skill-acrobatics", "skill-deception"]


def _rogue(expertise: list[str] | None = None, class_index: str = "rogue") -> Character:
    return create_character(
        character_id="fenwick",
        name="Fenwick",
        race_index="halfling",
        class_index=class_index,
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 15, "CON": 12, "INT": 10, "WIS": 13, "CHA": 14},
        chosen_skills=_SKILLS
        if class_index == "rogue"
        else ["skill-athletics", "skill-perception"],
        chosen_expertise=expertise,
    )


def test_a_rogue_picks_two_of_their_skills() -> None:
    rogue = _rogue(["skill-stealth", "skill-deception"])
    assert rogue.expertise == ["skill-stealth", "skill-deception"]


@pytest.mark.parametrize(
    ("choice", "message"),
    [
        (None, "exactly 2"),
        (["skill-stealth"], "exactly 2"),
        (["skill-stealth", "skill-stealth"], "exactly 2"),
        (["skill-stealth", "skill-arcana"], "proficient in"),
    ],
)
def test_expertise_choices_are_validated(choice: list[str] | None, message: str) -> None:
    with pytest.raises(CharacterCreationError, match=message):
        _rogue(choice)


def test_only_a_rogue_can_have_expertise() -> None:
    with pytest.raises(CharacterCreationError, match="doesn't have Expertise"):
        _rogue(["skill-athletics", "skill-perception"], class_index="fighter")
    assert _rogue(None, class_index="fighter").expertise == []


def _state(actor: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=5, y=0))
    return GameState(
        encounter_id="expertise_test",
        characters={actor.id: actor, goblin.id: goblin},
        turn_order=[actor.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _check(skill: str) -> ParsedAction:
    return ParsedAction(
        actor="fenwick", verb="skill_check", params={"skill": skill}, raw_text="I check"
    )


def test_an_expert_skill_adds_double_proficiency_and_says_so_in_the_breakdown() -> None:
    # DEX 15 + halfling 2 = 17 (+3), proficiency 2 doubled to 4: d20 10 -> 17.
    rogue = _rogue(["skill-stealth", "skill-deception"])
    state = _state(rogue)
    resolve_action(state, _check("stealth"), _FixedRandom([10]))  # type: ignore[arg-type]

    event = next(e for e in state.events if e.type == "skill_check")
    assert event.payload["roll_total"] == 17
    assert ["expertise", 4] in [list(b) for b in event.payload["modifier_breakdown"]]
    assert not any(b[0] == "proficiency" for b in event.payload["modifier_breakdown"])


def test_a_skill_without_expertise_still_adds_a_single_proficiency() -> None:
    rogue = _rogue(["skill-stealth", "skill-deception"])
    state = _state(rogue)
    resolve_action(state, _check("acrobatics"), _FixedRandom([10]))  # type: ignore[arg-type]

    event = next(e for e in state.events if e.type == "skill_check")
    assert event.payload["roll_total"] == 15  # 10 + 3 + 2
    assert ["proficiency", 2] in [list(b) for b in event.payload["modifier_breakdown"]]


def test_hide_uses_stealth_expertise() -> None:
    rogue = _rogue(["skill-stealth", "skill-deception"])
    state = _state(rogue)
    resolve_action(
        state,
        ParsedAction(actor="fenwick", verb="hide", raw_text="I hide"),
        _FixedRandom([10]),  # type: ignore[arg-type]
    )
    event = next(e for e in state.events if e.type == "skill_check")
    assert event.payload["roll_total"] == 17


# --- persistence ----------------------------------------------------------


@pytest.fixture
def client() -> Generator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
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


def test_expertise_survives_a_create_then_refetch_round_trip(client: TestClient) -> None:
    body = {
        "character_id": "fenwick",
        "name": "Fenwick",
        "race_index": "halfling",
        "class_index": "rogue",
        "background_index": "acolyte",
        "base_ability_scores": {"STR": 8, "DEX": 15, "CON": 12, "INT": 10, "WIS": 13, "CHA": 14},
        "chosen_skills": _SKILLS,
        "chosen_expertise": ["skill-stealth", "skill-sleight-of-hand"],
    }
    created = client.post("/characters", json=body)
    assert created.status_code == 201
    assert created.json()["expertise"] == ["skill-stealth", "skill-sleight-of-hand"]

    refetched = client.get("/characters/fenwick")
    assert refetched.json()["expertise"] == ["skill-stealth", "skill-sleight-of-hand"]


def test_creating_a_rogue_without_expertise_is_a_clear_400(client: TestClient) -> None:
    body = {
        "character_id": "fenwick",
        "name": "Fenwick",
        "race_index": "halfling",
        "class_index": "rogue",
        "background_index": "acolyte",
        "base_ability_scores": {"STR": 8, "DEX": 15, "CON": 12, "INT": 10, "WIS": 13, "CHA": 14},
        "chosen_skills": _SKILLS,
    }
    response = client.post("/characters", json=body)
    assert response.status_code == 400
    assert "Expertise" in response.json()["detail"]


def test_the_vendored_rogue_companion_has_expertise() -> None:
    from src.engine.companions import build_companion, load_companion_spec

    fenwick = build_companion(load_companion_spec("fenwick_quickfingers"))
    assert fenwick.expertise == ["skill-stealth", "skill-sleight-of-hand"]

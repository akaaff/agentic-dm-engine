"""SQLAlchemy round-trip smoke test - runs against an in-memory SQLite DB
created directly from the models (not via Alembic), which is exactly what
Day 8 needs to prove: the ORM layer itself works. Alembic's own migration
was separately verified live against a real file-based DB (see CLAUDE.md)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.db.models import Base, CampaignProgress, CharacterRecord, Episode


def test_character_record_round_trips() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            CharacterRecord(
                id="thorin",
                name="Thorin",
                race="Human",
                class_="Fighter",
                background="Acolyte",
                hp=12,
                max_hp=12,
                ac=18,
                speed=30,
                proficiency_bonus=2,
                stats={"STR": 16, "DEX": 15, "CON": 14, "INT": 13, "WIS": 11, "CHA": 9},
                inventory=["chain-mail", "shield"],
            )
        )
        session.commit()

    with Session(engine) as session:
        restored = session.get(CharacterRecord, "thorin")
        assert restored is not None
        assert restored.name == "Thorin"
        assert restored.hp == 12
        assert restored.stats["DEX"] == 15
        assert restored.inventory == ["chain-mail", "shield"]
        assert restored.is_pc is True
        assert restored.spell_slots == {}
        assert restored.created_at is not None


def test_campaign_progress_and_episode_round_trip() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            CampaignProgress(
                id="session-1",
                campaign_id="goblin_ambush_oneshot",
                current_scene_id="ambush_combat",
                party_character_ids=["thorin", "elrond"],
            )
        )
        session.add(
            Episode(
                id="episode-1",
                campaign_id="goblin_ambush_oneshot",
                judge_score=0.85,
                transcript={"events": []},
            )
        )
        session.commit()

    with Session(engine) as session:
        progress = session.get(CampaignProgress, "session-1")
        assert progress is not None
        assert progress.party_character_ids == ["thorin", "elrond"]
        assert progress.status == "in_progress"

        episode = session.get(Episode, "episode-1")
        assert episode is not None
        assert episode.judge_score == 0.85
        assert episode.transcript == {"events": []}

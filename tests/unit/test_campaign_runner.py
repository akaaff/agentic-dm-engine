"""Day 22: campaign_runner walks a campaign's scene chain between combat
encounters - narrative beats and skill challenges resolved deterministically
(no LLM), same hand-computed-fixture rigor as turn_engine's own skill_check
tests (tests/unit/test_turn_engine_new_verbs.py)."""

from __future__ import annotations

import pytest

from src.engine.campaign import Campaign, Scene, SkillChallengeDef, load_campaign
from src.engine.campaign_runner import advance_to_next_encounter, resolve_skill_challenge
from src.engine.character_creation import create_character
from src.engine.srd_loader import load_srd
from src.engine.state import Character


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _two_person_party() -> list[Character]:
    # Thorin (fighter): WIS 10 -> mod 0, not proficient in Survival.
    # Elrond (wizard): WIS 13 -> mod +1, not proficient in Survival either -
    # the better WIS mod makes him the "champion" for a WIS-governed check
    # regardless of class flavor, since the engine only looks at the number.
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


_CHALLENGE = SkillChallengeDef(
    skill="survival",
    dc=11,
    success_text="Success flavor.",
    failure_text="Failure flavor.",
)


def test_resolve_skill_challenge_picks_the_better_modifier_and_succeeds() -> None:
    srd = load_srd()
    party = _two_person_party()

    # Elrond's modifier is +1 (best in the party); roll 15 -> total 16 >= dc 11.
    success, narration = resolve_skill_challenge(
        _CHALLENGE,
        party,
        srd,
        _FixedRandom([15]),  # type: ignore[arg-type]
    )

    assert success is True
    assert "Elrond" in narration
    assert "Success flavor." in narration


def test_resolve_skill_challenge_reports_failure_below_the_dc() -> None:
    srd = load_srd()
    party = _two_person_party()
    challenge = SkillChallengeDef(
        skill="survival", dc=20, success_text="Success flavor.", failure_text="Failure flavor."
    )

    # Elrond's modifier is +1; roll 5 -> total 6 < dc 20.
    success, narration = resolve_skill_challenge(
        challenge,
        party,
        srd,
        _FixedRandom([5]),  # type: ignore[arg-type]
    )

    assert success is False
    assert "Failure flavor." in narration


def test_resolve_skill_challenge_skips_dead_party_members() -> None:
    srd = load_srd()
    thorin, elrond = _two_person_party()
    elrond.is_dead = True  # better modifier, but dead - Thorin must attempt it instead

    success, narration = resolve_skill_challenge(
        _CHALLENGE,
        [thorin, elrond],
        srd,
        _FixedRandom([15]),  # type: ignore[arg-type]
    )

    assert "Thorin" in narration
    assert "Elrond" not in narration
    # Thorin's modifier is 0 -> total 15, still clears dc 11.
    assert success is True


def test_advance_to_next_encounter_walks_narrative_and_skill_scenes_to_combat() -> None:
    campaign = load_campaign("wolf_den_short_arc")
    srd = load_srd()
    party = _two_person_party()

    combat_scene, narration = advance_to_next_encounter(
        campaign,
        campaign.first_scene(),
        party,
        srd,
        _FixedRandom([15]),  # type: ignore[arg-type]
    )

    assert combat_scene is not None
    assert combat_scene.id == "wolf_den_combat"
    # intro's narrative_intro, the skill-challenge scene's own narrative_intro,
    # its resolved outcome text, then the combat scene's own narrative_intro -
    # every scene contributes its narrative_intro, skill_challenge additionally
    # contributes its roll outcome.
    assert len(narration) == 4
    assert "farmer" in narration[0]
    assert "tree line" in narration[1]
    assert "tracks" in narration[2]
    assert "den mouth" in narration[3]


def test_advance_to_next_encounter_returns_none_at_the_end_of_the_chain() -> None:
    campaign = load_campaign("wolf_den_short_arc")
    srd = load_srd()
    party = _two_person_party()
    outro = campaign.scene_by_id("outro")

    result_scene, narration = advance_to_next_encounter(
        campaign,
        outro,
        party,
        srd,
        _FixedRandom([]),  # type: ignore[arg-type]
    )

    assert result_scene is None
    assert narration == [outro.narrative_intro]


def test_advance_to_next_encounter_raises_for_skill_challenge_missing_def() -> None:
    campaign = Campaign(
        id="broken",
        title="Broken",
        size="one_shot",
        description="",
        scenes=[Scene(id="s1", type="skill_challenge", narrative_intro="...", next_scene_id=None)],
    )
    srd = load_srd()
    party = _two_person_party()

    with pytest.raises(ValueError, match="skill_challenge_def"):
        advance_to_next_encounter(
            campaign,
            campaign.first_scene(),
            party,
            srd,
            _FixedRandom([]),  # type: ignore[arg-type]
        )

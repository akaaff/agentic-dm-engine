import pytest

from src.engine.campaign import SkillChallengeDef, load_all_campaigns, load_campaign


def test_load_campaign_parses_scene_chain() -> None:
    campaign = load_campaign("goblin_ambush_oneshot")

    assert campaign.id == "goblin_ambush_oneshot"
    assert campaign.size == "one_shot"
    assert [s.id for s in campaign.scenes] == ["intro", "ambush_combat", "outro"]
    assert campaign.first_scene().id == "intro"


def test_scene_chain_links_to_the_combat_encounter() -> None:
    campaign = load_campaign("goblin_ambush_oneshot")

    combat_scene = campaign.scene_by_id("ambush_combat")
    assert combat_scene.type == "combat"
    assert combat_scene.encounter_ref == "goblin_ambush"
    assert combat_scene.next_scene_id == "outro"

    outro = campaign.scene_by_id(combat_scene.next_scene_id)
    assert outro.type == "narrative_beat"
    assert outro.next_scene_id is None


def test_scene_by_id_raises_for_unknown_scene() -> None:
    campaign = load_campaign("goblin_ambush_oneshot")
    with pytest.raises(KeyError):
        campaign.scene_by_id("does_not_exist")


def test_next_scene_walks_the_chain_and_returns_none_at_the_end() -> None:
    campaign = load_campaign("goblin_ambush_oneshot")
    intro = campaign.first_scene()

    combat = campaign.next_scene(intro)
    assert combat is not None and combat.id == "ambush_combat"

    outro = campaign.next_scene(combat)
    assert outro is not None and outro.id == "outro"

    assert campaign.next_scene(outro) is None


def test_load_all_campaigns_excludes_the_encounters_subdirectory() -> None:
    campaigns = load_all_campaigns()
    # Day 22 added the short-arc and full campaigns alongside the Day 6
    # one-shot - sorted by filename, per load_all_campaigns' glob.
    assert [c.id for c in campaigns] == [
        "goblin_ambush_oneshot",
        "kobold_warren_full",
        "wolf_den_short_arc",
    ]


def test_short_arc_and_full_campaign_have_the_expected_scene_counts_and_sizes() -> None:
    short_arc = load_campaign("wolf_den_short_arc")
    assert short_arc.size == "short_arc"
    assert 3 <= len(short_arc.scenes) <= 4

    full = load_campaign("kobold_warren_full")
    assert full.size == "full"
    assert 6 <= len(full.scenes) <= 8


def test_skill_challenge_scene_parses_a_typed_skill_challenge_def() -> None:
    short_arc = load_campaign("wolf_den_short_arc")
    scene = short_arc.scene_by_id("track_the_wolves")

    assert scene.type == "skill_challenge"
    assert isinstance(scene.skill_challenge_def, SkillChallengeDef)
    assert scene.skill_challenge_def.skill == "survival"
    assert scene.skill_challenge_def.dc == 12

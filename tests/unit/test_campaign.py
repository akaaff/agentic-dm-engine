import pytest

from src.engine.campaign import load_all_campaigns, load_campaign


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


def test_load_all_campaigns_excludes_the_encounters_subdirectory() -> None:
    campaigns = load_all_campaigns()
    assert [c.id for c in campaigns] == ["goblin_ambush_oneshot"]

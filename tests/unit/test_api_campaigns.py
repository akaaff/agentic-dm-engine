from fastapi.testclient import TestClient

from src.api.main import app


def test_list_campaigns_returns_all_three_sizes() -> None:
    client = TestClient(app)
    response = client.get("/campaigns")
    assert response.status_code == 200
    campaigns = response.json()
    # Day 22 added the short-arc and full campaigns alongside the Day 6 one-shot.
    assert {c["id"]: c["size"] for c in campaigns} == {
        "goblin_ambush_oneshot": "one_shot",
        "kobold_warren_full": "full",
        "wolf_den_short_arc": "short_arc",
    }
    assert all(c["description"] for c in campaigns)


def test_get_campaign_returns_full_scene_list() -> None:
    client = TestClient(app)
    response = client.get("/campaigns/goblin_ambush_oneshot")
    assert response.status_code == 200
    campaign = response.json()
    assert [s["id"] for s in campaign["scenes"]] == ["intro", "ambush_combat", "outro"]
    combat_scene = next(s for s in campaign["scenes"] if s["id"] == "ambush_combat")
    assert combat_scene["encounter_ref"] == "goblin_ambush"


def test_get_unknown_campaign_returns_404() -> None:
    client = TestClient(app)
    response = client.get("/campaigns/does-not-exist")
    assert response.status_code == 404

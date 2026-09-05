from fastapi.testclient import TestClient

from src.api.main import app


def test_list_campaigns_returns_the_one_shot() -> None:
    client = TestClient(app)
    response = client.get("/campaigns")
    assert response.status_code == 200
    campaigns = response.json()
    assert len(campaigns) == 1
    assert campaigns[0]["id"] == "goblin_ambush_oneshot"
    assert campaigns[0]["size"] == "one_shot"
    assert campaigns[0]["description"]


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

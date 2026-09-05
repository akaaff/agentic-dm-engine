from fastapi.testclient import TestClient

from src.api.main import app


def test_list_companions_returns_all_five_with_personas() -> None:
    client = TestClient(app)
    response = client.get("/companions")
    assert response.status_code == 200
    companions = response.json()
    assert len(companions) == 5

    ids = {c["id"] for c in companions}
    assert ids == {
        "companion_grom",
        "companion_fenwick",
        "companion_mira",
        "companion_silvana",
        "companion_pip",
    }
    for companion in companions:
        assert companion["persona"]
        assert companion["is_pc"] is True
        assert companion["is_companion"] is True
        assert companion["hp"] > 0

from fastapi.testclient import TestClient

from src.api.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    # passphrase_required added by issue #42 - False here since
    # SHARED_ACCESS_PASSPHRASE is unset in the test environment.
    assert response.json() == {"status": "ok", "passphrase_required": False}

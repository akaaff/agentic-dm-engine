"""Day 21: DEFAULT_OUTPUT_DIR is mounted as a real StaticFiles route so a
generated scene image is actually loadable by the browser, not just a path
on disk the frontend has no way to fetch."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.imagegen.service import DEFAULT_OUTPUT_DIR, MEDIA_URL_PREFIX


@pytest.fixture
def sample_image() -> Generator[str]:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = DEFAULT_OUTPUT_DIR / "test-fixture-image.png"
    path.write_bytes(b"not a real png, just bytes for the test")
    try:
        yield path.name
    finally:
        path.unlink(missing_ok=True)


def test_a_generated_image_is_servable_over_http(sample_image: str) -> None:
    client = TestClient(app)
    response = client.get(f"{MEDIA_URL_PREFIX}/{sample_image}")
    assert response.status_code == 200
    assert response.content == b"not a real png, just bytes for the test"


def test_unknown_image_returns_404() -> None:
    client = TestClient(app)
    response = client.get(f"{MEDIA_URL_PREFIX}/does-not-exist.png")
    assert response.status_code == 404

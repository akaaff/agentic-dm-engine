import httpx
import pytest

from src.config import API_BASE_URL


@pytest.fixture(scope="session", autouse=True)
def _require_api() -> None:
    """Skip every integration test if this project's own FastAPI service
    isn't running (doesn't exist until Day 8 - skips cleanly until then).
    Catches httpx.TransportError broadly (not just ConnectError), since an
    unclaimed localhost port times out rather than refusing the connection
    on this machine."""
    try:
        httpx.get(f"{API_BASE_URL}/health", timeout=2.0)
    except httpx.TransportError:
        pytest.skip(f"API not reachable at {API_BASE_URL} - start it to run integration tests")

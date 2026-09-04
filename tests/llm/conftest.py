import httpx
import pytest

from src.config import OLLAMA_BASE_URL


@pytest.fixture(scope="session", autouse=True)
def _require_ollama() -> None:
    """Skip every test in this directory if Ollama isn't reachable, rather
    than failing with a raw connection error."""
    try:
        httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0)
    except httpx.TransportError:
        pytest.skip(f"Ollama not reachable at {OLLAMA_BASE_URL}")

import pytest
import torch


@pytest.fixture(scope="session", autouse=True)
def _require_cuda() -> None:
    """Skip every test in this directory if there's no CUDA GPU, rather than
    failing with a confusing pipeline-load error - the same shape as
    tests/llm/conftest.py's "skip if Ollama unreachable" gate."""
    if not torch.cuda.is_available():
        pytest.skip("No CUDA device available")

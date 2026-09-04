from src import config


def test_config_imports() -> None:
    assert config.OLLAMA_BASE_URL
    assert config.API_BASE_URL

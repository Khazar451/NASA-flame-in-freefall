import pytest


@pytest.fixture(autouse=True)
def isolated_llm_cache(tmp_path, monkeypatch):
    """Every test gets its own empty LLM cache under tmp_path/llm_cache."""
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm_cache"))

import pytest
from pydantic import ValidationError

from doc_extractor_api.core.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "some-model")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider == "anthropic"
    assert settings.llm_model == "some-model"


def test_api_keys_are_hidden_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "not-a-real-key" not in repr(settings)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "not-a-real-key"


def test_invalid_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(ValidationError, match="llm_provider"):
        Settings(_env_file=None)  # type: ignore[call-arg]

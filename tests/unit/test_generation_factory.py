from __future__ import annotations

import pytest

from rag.config import Settings
from rag.generation.factory import build_language_model
from rag.generation.openrouter import OpenRouterLanguageModel


def test_factory_builds_openrouter_from_ordered_free_model_list():
    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="test-key",
        openrouter_models="first/model:free, second/model:free",
    )

    model = build_language_model(settings)

    assert isinstance(model, OpenRouterLanguageModel)
    assert model.models == ["first/model:free", "second/model:free"]
    model.close()


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Allowed providers: gemini, openrouter"):
        build_language_model(Settings(llm_provider="unknown"))


def test_factory_requires_provider_key():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is required"):
        build_language_model(Settings(llm_provider="openrouter"))

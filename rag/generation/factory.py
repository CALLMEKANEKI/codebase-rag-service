from __future__ import annotations

from rag.config import Settings
from rag.generation.base import LanguageModel
from rag.generation.gemini import GeminiLanguageModel
from rag.generation.openrouter import OpenRouterLanguageModel


def build_language_model(settings: Settings) -> LanguageModel:
    """Build only an explicitly supported, configured provider."""
    provider = settings.llm_provider.strip().lower()

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return GeminiLanguageModel(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_ms=settings.llm_timeout_ms,
        )

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter"
            )
        models = settings.openrouter_model_list
        if not models:
            raise ValueError("OPENROUTER_MODELS must contain at least one model")
        return OpenRouterLanguageModel(
            api_key=settings.openrouter_api_key,
            models=models,
            timeout_ms=settings.llm_timeout_ms,
            site_url=settings.openrouter_site_url,
            app_name=settings.openrouter_app_name,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER: {provider}. Allowed providers: gemini, openrouter"
    )

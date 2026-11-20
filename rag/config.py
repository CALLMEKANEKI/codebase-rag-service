from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    rag_api_key: str = Field(default="", alias="RAG_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    llm_provider: str = Field(default="openrouter", alias="LLM_PROVIDER")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_models: str = Field(default="openrouter/free", alias="OPENROUTER_MODELS")
    openrouter_site_url: str = Field(default="", alias="OPENROUTER_SITE_URL")
    openrouter_app_name: str = Field(default="Codebase RAG Service", alias="OPENROUTER_APP_NAME")
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL",
    )
    embedding_device: str = Field(default="auto", alias="EMBEDDING_DEVICE")
    qdrant_path: Path = Field(default=Path("storage/qdrant"), alias="QDRANT_PATH")
    embedding_cache_path: Path = Field(
        default=Path("storage/embedding_cache.sqlite"),
        alias="EMBEDDING_CACHE_PATH",
    )
    index_storage_path: Path = Field(
        default=Path("storage/indexes"),
        alias="INDEX_STORAGE_PATH",
    )
    index_registry_path: Path = Field(
        default=Path("storage/index_registry.sqlite"),
        alias="INDEX_REGISTRY_PATH",
    )
    llm_timeout_ms: int = Field(default=60_000, alias="LLM_TIMEOUT_MS")

    @property
    def openrouter_model_list(self) -> list[str]:
        return [model.strip() for model in self.openrouter_models.split(",") if model.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

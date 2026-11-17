from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rag_api_key: str = Field(default="", alias="RAG_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
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
    llm_timeout_ms: int = Field(default=30_000, alias="LLM_TIMEOUT_MS")


@lru_cache
def get_settings() -> Settings:
    return Settings()

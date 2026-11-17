from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from rag.embeddings.base import Embedder


class SQLiteEmbeddingCache:
    """Persistent embedding cache keyed by model and chunking configuration."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        chunker_version: str,
        header_enabled: bool,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.chunker_version = chunker_version
        self.header_enabled = header_enabled
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                cache_key TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector_json TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def make_key(self, embedder: Embedder, text: str) -> str:
        value = "|".join(
            (
                embedder.model_name,
                str(embedder.dim),
                self.chunker_version,
                str(self.header_enabled).lower(),
                text,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> list[float] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT vector_json FROM embedding_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(
        self,
        cache_key: str,
        *,
        model_name: str,
        dimension: int,
        vector: list[float],
    ) -> None:
        if len(vector) != dimension:
            raise ValueError("Embedding vector dimension does not match cache metadata")
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO embedding_cache
                    (cache_key, model_name, dimension, vector_json)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, model_name, dimension, json.dumps(vector)),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class CachedEmbedder:
    def __init__(self, embedder: Embedder, cache: SQLiteEmbeddingCache) -> None:
        self.embedder = embedder
        self.cache = cache

    @property
    def dim(self) -> int:
        return self.embedder.dim

    @property
    def model_name(self) -> str:
        return self.embedder.model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing_texts: list[str] = []
        missing_indices: list[int] = []

        for index, text in enumerate(texts):
            key = self.cache.make_key(self.embedder, text)
            cached = self.cache.get(key)
            if cached is None:
                missing_indices.append(index)
                missing_texts.append(text)
            else:
                results[index] = cached

        if missing_texts:
            vectors = self.embedder.embed_documents(missing_texts)
            if len(vectors) != len(missing_texts):
                raise ValueError("Embedder returned an unexpected number of vectors")
            for index, text, vector in zip(
                missing_indices, missing_texts, vectors, strict=True
            ):
                if len(vector) != self.dim:
                    raise ValueError("Embedder returned an unexpected vector dimension")
                key = self.cache.make_key(self.embedder, text)
                self.cache.put(
                    key,
                    model_name=self.model_name,
                    dimension=self.dim,
                    vector=vector,
                )
                results[index] = vector

        return [vector for vector in results if vector is not None]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

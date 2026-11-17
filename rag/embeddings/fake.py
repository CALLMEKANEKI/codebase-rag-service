from __future__ import annotations

import hashlib
import math


class FakeEmbedder:
    """Deterministic hash embedder for tests and offline CI."""

    def __init__(self, dim: int = 32) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        self._dim = dim
        self._model_name = f"fake-sha256-{dim}"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[index % len(digest)] / 127.5 - 1.0 for index in range(self._dim)]
        norm = math.sqrt(sum(value * value for value in raw))
        if norm == 0:
            return [0.0] * self._dim
        return [value / norm for value in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

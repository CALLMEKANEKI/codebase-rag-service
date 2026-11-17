from __future__ import annotations

from typing import Any, Protocol


class VectorStore(Protocol):
    def create_collection(self, index_id: str, vector_size: int) -> None: ...

    def upsert(
        self,
        index_id: str,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None: ...

    def search(
        self, index_id: str, vector: list[float], top_k: int
    ) -> list[dict[str, Any]]: ...

    def delete(self, index_id: str) -> None: ...

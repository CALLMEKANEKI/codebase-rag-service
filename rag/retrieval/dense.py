from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.embeddings.base import Embedder
from rag.vectorstore.base import VectorStore


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: dict[str, Any]
    score: float
    rank: int


class DenseRetriever:
    def __init__(self, embedder: Embedder, vector_store: VectorStore) -> None:
        self.embedder = embedder
        self.vector_store = vector_store

    def retrieve(
        self,
        index_id: str,
        query: str,
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        vector = self.embedder.embed_query(query)
        hits = self.vector_store.search(index_id, vector, top_k)
        return [
            RetrievedChunk(
                chunk=hit["payload"],
                score=float(hit["score"]),
                rank=rank,
            )
            for rank, hit in enumerate(hits, start=1)
        ]

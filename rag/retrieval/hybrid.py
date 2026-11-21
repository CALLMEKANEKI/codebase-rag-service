from __future__ import annotations

from rag.retrieval.bm25 import BM25Retriever
from rag.retrieval.dense import DenseRetriever, RetrievedChunk


class HybridRetriever:
    """Fuse dense and BM25 rankings using Reciprocal Rank Fusion."""

    mode = "hybrid"

    def __init__(
        self,
        dense: DenseRetriever,
        bm25: BM25Retriever,
        *,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self.dense = dense
        self.bm25 = bm25
        self.rrf_k = rrf_k

    def retrieve(
        self, index_id: str, query: str, top_k: int = 10
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        dense_results = self.dense.retrieve(index_id, query, top_k=top_k)
        lexical_results = self.bm25.retrieve(index_id, query, top_k=top_k)
        fused: dict[str, dict] = {}
        for result in dense_results:
            key = str(result.chunk.get("chunk_id") or id(result.chunk))
            item = fused.setdefault(key, {"chunk": result.chunk, "score": 0.0})
            item["score"] += 1.0 / (self.rrf_k + result.rank)
        for result in lexical_results:
            key = str(result.chunk.get("chunk_id") or id(result.chunk))
            item = fused.setdefault(key, {"chunk": result.chunk, "score": 0.0})
            item["score"] += 1.0 / (self.rrf_k + result.rank)
        ordered = sorted(
            fused.values(),
            key=lambda item: (
                -item["score"],
                str(item["chunk"].get("chunk_id") or ""),
            ),
        )
        return [
            RetrievedChunk(chunk=item["chunk"], score=item["score"], rank=rank)
            for rank, item in enumerate(ordered[:top_k], start=1)
        ]

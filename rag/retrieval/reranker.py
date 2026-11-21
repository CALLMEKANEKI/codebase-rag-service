from __future__ import annotations

from typing import Any, Protocol

from rag.retrieval.dense import RetrievedChunk


class PairScorer(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> Any: ...


class CrossEncoderReranker:
    """Optional local cross-encoder reranker, loaded only when first requested."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        *,
        device: str = "auto",
        scorer: PairScorer | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._scorer = scorer

    def _get_scorer(self) -> PairScorer:
        if self._scorer is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "Install requirements-ml.txt to enable cross-encoder reranking"
                ) from exc
            kwargs = {} if self.device == "auto" else {"device": self.device}
            self._scorer = CrossEncoder(self.model_name, **kwargs)
        return self._scorer

    def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        *,
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if not candidates:
            return []
        pairs = [
            (
                query,
                " ".join(
                    part
                    for part in (
                        str(item.chunk.get("file_path") or ""),
                        str(item.chunk.get("qualname") or item.chunk.get("symbol_name") or ""),
                        str(item.chunk.get("text") or ""),
                    )
                    if part
                ),
            )
            for item in candidates
        ]
        raw_scores = self._get_scorer().predict(pairs)
        scores = [float(score) for score in raw_scores]
        if len(scores) != len(candidates):
            raise ValueError("Reranker returned an unexpected number of scores")
        ordered = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: (-pair[1], pair[0].rank),
        )
        return [
            RetrievedChunk(chunk=item.chunk, score=score, rank=rank)
            for rank, (item, score) in enumerate(ordered[:top_k], start=1)
        ]

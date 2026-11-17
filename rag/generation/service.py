from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.generation.base import LanguageModel
from rag.generation.citations import validate_citations
from rag.generation.context import build_context
from rag.retrieval.dense import DenseRetriever


@dataclass(frozen=True)
class QueryResult:
    answer: str
    citations: list[dict[str, Any]]
    usage: dict[str, int | None] | None
    debug: dict[str, Any] | None


class QueryService:
    def __init__(
        self, retriever: DenseRetriever, language_model: LanguageModel
    ) -> None:
        self.retriever = retriever
        self.language_model = language_model

    def query(
        self,
        index_id: str,
        question: str,
        *,
        top_k: int = 5,
        debug: bool = False,
    ) -> QueryResult:
        retrieved = self.retriever.retrieve(index_id, question, top_k=top_k)
        context = build_context(retrieved)
        if not context.sources:
            return QueryResult(
                answer="I could not find enough evidence in the indexed repository to answer that question.",
                citations=[],
                usage=None,
                debug={"mode": "dense", "retrieved": []} if debug else None,
            )

        generated = self.language_model.generate(
            question=question,
            context=context.text,
        )
        answer, citations = validate_citations(generated.text, context.sources)
        usage = None
        if generated.input_tokens is not None or generated.output_tokens is not None:
            usage = {
                "input_tokens": generated.input_tokens,
                "output_tokens": generated.output_tokens,
            }
        debug_info = None
        if debug:
            included_chunk_ids = {id(source.chunk) for source in context.sources}
            debug_info = {
                "mode": "dense",
                "retrieved": [
                    {
                        "chunk_id": str(item.chunk.get("chunk_id") or ""),
                        "file_path": str(item.chunk.get("file_path") or ""),
                        "symbol": (
                            item.chunk.get("qualname")
                            or item.chunk.get("symbol_name")
                        ),
                        "symbol_kind": item.chunk.get("symbol_kind"),
                        "start_line": int(item.chunk.get("start_line") or 1),
                        "end_line": int(
                            item.chunk.get("end_line")
                            or item.chunk.get("start_line")
                            or 1
                        ),
                        "score": float(item.score),
                        "used_in_context": id(item.chunk) in included_chunk_ids,
                    }
                    for item in retrieved
                ],
            }
        return QueryResult(answer, citations, usage, debug_info)

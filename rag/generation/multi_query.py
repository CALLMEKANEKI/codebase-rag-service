from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

import httpx

from rag.generation.citations import no_evidence_answer, validate_citations
from rag.generation.context import build_context
from rag.retrieval.bm25 import BM25Retriever
from rag.retrieval.dense import RetrievedChunk
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.reranker import CrossEncoderReranker, RerankingRetriever


_EVIDENCE_SUFFICIENCY_MIN_CHUNKS = 2
_MAX_SCOPE = 5
_RETRIEVAL_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_SCOPE,
    thread_name_prefix="rag-multi-retrieve",
)


def _non_empty_results(results: list[RetrievedChunk]) -> list[RetrievedChunk]:
    return [
        item
        for item in results
        if str(item.chunk.get("text") or "").strip()
    ]


def _retrieve_for_index(
    *,
    index_id: str,
    question: str,
    top_k: int,
    mode: str,
    rerank: bool,
    query_service: Any,
    index_storage_path: Path,
) -> list[RetrievedChunk]:
    dense = query_service.retriever
    if mode == "dense":
        retriever = dense
    elif mode == "bm25":
        retriever = BM25Retriever(index_storage_path)
    else:
        retriever = HybridRetriever(
            dense,
            BM25Retriever(index_storage_path),
        )
        if rerank:
            retriever = RerankingRetriever(retriever, CrossEncoderReranker())
    return retriever.retrieve(index_id, question, top_k=top_k)


def _fuse_by_repository(
    results_by_repository: list[tuple[dict[str, str], list[RetrievedChunk]]],
    *,
    top_k: int,
) -> list[RetrievedChunk]:
    """Apply rank fusion, then interleave repositories to prevent context crowding."""
    fused_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    per_repository: list[list[dict[str, Any]]] = []

    for repository, results in results_by_repository:
        ranked: list[dict[str, Any]] = []
        for result in results:
            chunk = dict(result.chunk)
            chunk["repository_id"] = repository["repository_id"]
            chunk["index_id"] = repository["index_id"]
            chunk["display_name"] = repository["display_name"]
            chunk["commit_sha"] = repository.get("commit_sha")
            chunk["focused"] = repository.get("focused") == "true"
            chunk["used_as_fallback"] = repository.get("used_as_fallback") == "true"
            chunk_id = str(chunk.get("chunk_id") or "")
            key = (repository["index_id"], chunk_id or str(id(result.chunk)))
            item = fused_by_key.setdefault(
                key,
                {
                    "chunk": chunk,
                    "score": 0.0,
                    "repository_id": repository["repository_id"],
                },
            )
            item["score"] += 1.0 / (60 + result.rank)
            ranked.append(item)
        per_repository.append(ranked)

    # Stable round-robin selection gives every searched repository a fair chance
    # before the global top_k context budget is applied.
    ordered: list[dict[str, Any]] = []
    positions = [0] * len(per_repository)
    while len(ordered) < top_k:
        progressed = False
        for index, ranked in enumerate(per_repository):
            position = positions[index]
            if position < len(ranked):
                candidate = ranked[position]
                positions[index] += 1
                if candidate not in ordered:
                    ordered.append(candidate)
                    progressed = True
                    if len(ordered) >= top_k:
                        break
        if not progressed:
            break

    return [
        RetrievedChunk(chunk=item["chunk"], score=item["score"], rank=rank)
        for rank, item in enumerate(ordered[:top_k], start=1)
    ]


def run_multi_query(
    *,
    question: str,
    scope: list[dict[str, str]],
    focus_repository_ids: list[str],
    top_k: int,
    mode: str,
    rerank: bool,
    query_service: Any,
    registry: Any,
    index_storage_path: str | Path,
    vector_store: Any = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Retrieve within the authorized Scope, expand soft Focus, and generate once."""
    started = time.monotonic()
    deadline = started + timeout_seconds
    storage_root = Path(index_storage_path)

    if not 1 <= len(scope) <= _MAX_SCOPE:
        raise ValueError("scope must contain between 1 and 5 repositories")
    repository_ids = [item["repository_id"] for item in scope]
    index_ids = [item["index_id"] for item in scope]
    if (
        len(set(repository_ids)) != len(repository_ids)
        or len(set(index_ids)) != len(index_ids)
    ):
        raise ValueError("scope repository_id and index_id values must be unique")
    if len(set(focus_repository_ids)) != len(focus_repository_ids):
        raise ValueError("focus_repository_ids must be unique")
    if not set(focus_repository_ids).issubset(repository_ids):
        raise ValueError("focus_repository_ids must be a subset of Scope")
    if mode not in {"dense", "bm25", "hybrid"}:
        raise ValueError("mode must be dense, bm25, or hybrid")
    if rerank and mode != "hybrid":
        raise ValueError("rerank requires hybrid mode")

    by_repository_id = {item["repository_id"]: item for item in scope}
    focused = (
        [by_repository_id[item] for item in focus_repository_ids]
        if focus_repository_ids
        else list(scope)
    )
    remaining = (
        [
            item
            for item in scope
            if item["repository_id"] not in set(focus_repository_ids)
        ]
        if focus_repository_ids
        else []
    )

    outcomes: dict[str, dict[str, Any]] = {}
    retrieved_groups: list[tuple[dict[str, str], list[RetrievedChunk]]] = []

    def search_repository(repository: dict[str, str], *, is_fallback: bool) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("Multi-index request deadline elapsed")
        repository_id = repository["repository_id"]
        index_id = repository["index_id"]
        source_started = time.monotonic()
        outcome = {
            "repository_id": repository_id,
            "index_id": index_id,
            "commit_sha": None,
            "outcome": "failed",
            "reason": "retrieval_error",
            "evidence_count": 0,
            "latency_ms": None,
        }
        outcomes[repository_id] = outcome

        row = registry.get(index_id)
        if row is None:
            outcome.update(outcome="unavailable", reason="index_not_found")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return
        outcome["commit_sha"] = row.get("commit_sha")
        if row.get("status") != "ready":
            outcome.update(outcome="unavailable", reason="index_not_ready")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return

        index_root = storage_root / index_id
        if not (index_root / "manifest.json").is_file():
            outcome.update(outcome="unavailable", reason="index_not_found")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return
        if mode in {"bm25", "hybrid"} and not (index_root / "bm25.sqlite3").is_file():
            outcome.update(outcome="unavailable", reason="storage_error")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return
        if vector_store is not None and mode in {"dense", "hybrid"}:
            try:
                if not vector_store.collection_exists(index_id):
                    outcome.update(outcome="unavailable", reason="index_not_ready")
                    outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
                    return
            except Exception:
                outcome.update(outcome="unavailable", reason="storage_error")
                outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
                return

        try:
            retrieval = _RETRIEVAL_EXECUTOR.submit(
                _retrieve_for_index,
                index_id=index_id,
                question=question,
                top_k=min(20, max(top_k, 5)),
                mode=mode,
                rerank=rerank,
                query_service=query_service,
                index_storage_path=storage_root,
            )
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                retrieval.cancel()
                raise TimeoutError("Multi-index request deadline elapsed")
            try:
                candidates = _non_empty_results(
                    retrieval.result(timeout=remaining_seconds)
                )
            except FutureTimeoutError as exc:
                retrieval.cancel()
                raise TimeoutError("Multi-index request deadline elapsed") from exc
        except (TimeoutError, httpx.TimeoutException):
            raise
        except OSError:
            outcome.update(outcome="unavailable", reason="storage_error")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return
        except Exception:
            outcome.update(outcome="failed", reason="retrieval_error")
            outcome["latency_ms"] = max(
                0, int((time.monotonic() - source_started) * 1000)
            )
            return

        if time.monotonic() >= deadline:
            raise TimeoutError("Multi-index request deadline elapsed")
        outcome["evidence_count"] = len(candidates)
        outcome["outcome"] = (
            "searched_with_evidence" if candidates else "searched_no_evidence"
        )
        outcome["reason"] = None
        outcome["latency_ms"] = max(0, int((time.monotonic() - source_started) * 1000))
        if candidates:
            metadata = {
                "repository_id": repository_id,
                "index_id": index_id,
                "display_name": repository["display_name"],
                "commit_sha": row.get("commit_sha"),
                "focused": (
                    "true"
                    if repository_id in set(focus_repository_ids)
                    else "false"
                ),
                "used_as_fallback": "true" if is_fallback else "false",
            }
            retrieved_groups.append((metadata, candidates))

    for repository in focused:
        search_repository(repository, is_fallback=False)

    focused_outcomes = [outcomes[item["repository_id"]] for item in focused]
    focused_available = any(
        item["outcome"] in {"searched_with_evidence", "searched_no_evidence"}
        for item in focused_outcomes
    )
    # Initial measurable policy: two non-empty chunks across the selected Focus
    # are sufficient to avoid expanding retrieval; fewer trigger in-Scope fallback.
    focused_evidence_count = sum(item["evidence_count"] for item in focused_outcomes)
    focus_sufficient = (
        not focus_repository_ids
        or focused_evidence_count >= _EVIDENCE_SUFFICIENCY_MIN_CHUNKS
    )

    fallback_triggered = bool(
        focus_repository_ids and remaining and not focus_sufficient
    )
    fallback_reason = None
    if fallback_triggered:
        fallback_reason = (
            "focused_evidence_insufficient"
            if focused_available
            else "focused_sources_unavailable"
        )
        for repository in remaining:
            search_repository(repository, is_fallback=True)
    elif remaining:
        for repository in remaining:
            existing_row = registry.get(repository["index_id"])
            outcomes[repository["repository_id"]] = {
                "repository_id": repository["repository_id"],
                "index_id": repository["index_id"],
                "commit_sha": existing_row.get("commit_sha") if existing_row else None,
                "outcome": "not_searched",
                "reason": "focus_evidence_sufficient",
                "evidence_count": 0,
                "latency_ms": None,
            }

    if time.monotonic() >= deadline:
        raise TimeoutError("Multi-index request deadline elapsed")

    # Scope order is preserved in the response regardless of retrieval order.
    repository_outcomes = [outcomes[item["repository_id"]] for item in scope]
    available_count = sum(
        item["outcome"] in {
            "searched_with_evidence",
            "searched_no_evidence",
        }
        for item in repository_outcomes
    )
    if available_count == 0:
        raise AllIndexesUnavailable(repository_outcomes)

    fused = _fuse_by_repository(retrieved_groups, top_k=top_k)
    context = build_context(fused)
    usage = None
    citations: list[dict[str, Any]] = []
    if no_evidence_answer(question).startswith("Tôi"):
        answer = (
            "Các repository trong Conversation Scope chưa cung cấp đủ bằng chứng. "
            "Hãy kiểm tra lại Scope/Focus hoặc làm rõ nếu câu hỏi liên quan "
            "đến mã nguồn "
            "nằm ngoài các repository đã lập chỉ mục."
        )
    else:
        answer = (
            "The indexed repositories in this conversation Scope did not "
            "provide enough "
            "evidence. Check the Scope/Focus selection or clarify if the "
            "question is about "
            "code outside the indexed repositories."
        )
    answer_status = "no_evidence"

    if context.sources:
        if time.monotonic() >= deadline:
            raise TimeoutError("Multi-index request deadline elapsed")
        focus_names = [
            by_repository_id[item]["display_name"] for item in focus_repository_ids
        ]
        fallback_names = [
            item["display_name"]
            for item in remaining
            if outcomes[item["repository_id"]]["outcome"] in {
                "searched_with_evidence",
                "searched_no_evidence",
                "unavailable",
                "failed",
            }
        ]
        if no_evidence_answer(question).startswith("Tôi"):
            focus_label = (
                ", ".join(focus_names)
                if focus_names
                else "không có; tìm trong toàn bộ Scope"
            )
            guidance = (
                f"Repository được chọn làm Focus: {focus_label}. "
                "Các repository fallback đã được thử: "
                f"{', '.join(fallback_names) if fallback_names else 'không có'}. "
                "Chỉ sử dụng các đoạn mã được cung cấp. Nếu bằng chứng đến "
                "từ repository fallback, "
                "hãy nói rõ điều đó trong câu trả lời."
            )
        else:
            focus_label = (
                ", ".join(focus_names)
                if focus_names
                else "none; search the full Scope"
            )
            guidance = (
                f"User focus repositories: {focus_label}. "
                "Fallback repositories attempted: "
                f"{', '.join(fallback_names) if fallback_names else 'none'}. "
                "Use only the supplied excerpts. If evidence comes from a "
                "fallback repository, "
                "make that explicit in the answer."
            )
        model = query_service.language_model
        remaining_seconds = max(0.1, deadline - time.monotonic())
        if hasattr(model, "generate_with_timeout"):
            generated = model.generate_with_timeout(
                question=f"{guidance}\n\nQuestion: {question}",
                context=context.text,
                timeout_seconds=remaining_seconds,
            )
        else:
            generated = model.generate(
                question=f"{guidance}\n\nQuestion: {question}",
                context=context.text,
            )
        answer, citations = validate_citations(
            generated.text,
            context.sources,
            question=question,
        )
        if citations and fallback_triggered:
            cited_fallback_names = []
            sources_by_ref = {item.ref_id: item.chunk for item in context.sources}
            for citation in citations:
                source_chunk = sources_by_ref[citation["ref_id"]]
                if source_chunk.get("used_as_fallback"):
                    name = str(
                        source_chunk.get("display_name")
                        or "another in-Scope repository"
                    )
                    if name not in cited_fallback_names:
                        cited_fallback_names.append(name)
            if cited_fallback_names:
                fallback_label = ", ".join(cited_fallback_names)
                if no_evidence_answer(question).startswith("Tôi"):
                    disclosure = (
                        "Repository trong Focus chưa cung cấp đủ bằng chứng; "
                        "câu trả lời này có sử dụng thêm nguồn trong Scope: "
                        f"{fallback_label}.\n\n"
                    )
                else:
                    disclosure = (
                        "The selected Focus did not provide enough evidence; "
                        "this answer also uses in-Scope fallback source(s): "
                        f"{fallback_label}.\n\n"
                    )
                answer = disclosure + answer
        answer_status = "answered" if citations else "no_evidence"
        if not citations:
            if answer.startswith("Tôi") or no_evidence_answer(question).startswith(
                "Tôi"
            ):
                answer = (
                    "Các repository trong Conversation Scope chưa cung cấp "
                    "đủ bằng chứng. "
                    "Hãy kiểm tra lại Scope/Focus hoặc làm rõ nếu câu hỏi liên quan "
            "đến mã nguồn "
                    "nằm ngoài các repository đã lập chỉ mục."
                )
            else:
                answer = (
                    "The indexed repositories in this conversation Scope did not "
            "provide enough "
                    "evidence. Check the Scope/Focus selection or clarify if the "
            "question is about "
                    "code outside the indexed repositories."
                )
        if generated.input_tokens is not None or generated.output_tokens is not None:
            usage = {
                "input_tokens": generated.input_tokens,
                "output_tokens": generated.output_tokens,
            }
        for citation in citations:
            source = next(
                item
                for item in context.sources
                if item.ref_id == citation["ref_id"]
            )
            chunk = source.chunk
            citation.update(
                repository_id=chunk["repository_id"],
                index_id=chunk["index_id"],
                commit_sha=chunk.get("commit_sha"),
            )

    execution_status = (
        "partial"
        if any(
            item["outcome"] in {"unavailable", "failed"}
            for item in repository_outcomes
        )
        else "completed"
    )
    return {
        "execution_status": execution_status,
        "answer_status": answer_status,
        "answer": answer,
        "focus": {
            "requested_repository_ids": focus_repository_ids,
            "searched_focus_repository_ids": [
                repository_id
                for repository_id in focus_repository_ids
                if outcomes[repository_id]["outcome"] in {
                    "searched_with_evidence",
                    "searched_no_evidence",
                }
            ],
            "fallback_repository_ids": [
                item["repository_id"]
                for item in remaining
                if outcomes[item["repository_id"]]["outcome"] != "not_searched"
            ],
            "fallback_triggered": fallback_triggered,
            "fallback_reason": fallback_reason,
        },
        "repository_outcomes": repository_outcomes,
        "citations": citations,
        "usage": usage,
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


class AllIndexesUnavailable(RuntimeError):
    def __init__(self, repository_outcomes: list[dict[str, Any]]) -> None:
        self.repository_outcomes = repository_outcomes
        super().__init__("No supplied index is available for querying")

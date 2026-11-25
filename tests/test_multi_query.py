from __future__ import annotations

from pathlib import Path

from rag.generation.multi_query import AllIndexesUnavailable, run_multi_query
from rag.generation.service import QueryResult


class FakeRetriever:
    def __init__(self, chunks_by_index):
        self.chunks_by_index = chunks_by_index
    def retrieve(self, index_id, question, top_k=10):
        return self.chunks_by_index.get(index_id, [])[:top_k]


class FakeLanguageModel:
    def __init__(self, text="Evidence found [1]."):
        self.text = text
        self.calls = 0
    def generate(self, *, question, context):
        self.calls += 1
        return type("Generated", (), {"text": self.text, "input_tokens": 3, "output_tokens": 2})()


class FakeRegistry:
    def __init__(self, rows):
        self.rows = rows
    def get(self, index_id):
        return self.rows.get(index_id)


def make_chunk(chunk_id, text):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "file_path": f"{chunk_id}.py",
        "start_line": 1,
        "end_line": 2,
        "symbol_name": "run",
    }


def make_result(chunk, score=0.9, rank=1):
    from rag.retrieval.dense import RetrievedChunk
    return RetrievedChunk(chunk=chunk, score=score, rank=rank)


def setup(tmp_path, chunks, rows):
    root = tmp_path / "indexes"
    for index_id in rows:
        directory = root / index_id
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
    model = FakeLanguageModel()
    service = type("Service", (), {
        "retriever": FakeRetriever(chunks),
        "language_model": model,
    })()
    return root, service, model, FakeRegistry(rows)


def test_multi_query_uses_focused_evidence_without_searching_rest(tmp_path):
    rows = {
        "idx_a": {"status": "ready", "commit_sha": "a" * 40},
        "idx_b": {"status": "ready", "commit_sha": "b" * 40},
    }
    root, service, model, registry = setup(
        tmp_path,
        {"idx_a": [make_result(make_chunk("a1", "first")), make_result(make_chunk("a2", "second"))]},
        rows,
    )
    result = run_multi_query(
        question="Explain this code",
        scope=[
            {"repository_id": "repo-a", "index_id": "idx_a", "display_name": "A"},
            {"repository_id": "repo-b", "index_id": "idx_b", "display_name": "B"},
        ],
        focus_repository_ids=["repo-a"],
        top_k=4,
        mode="dense",
        rerank=False,
        query_service=service,
        registry=registry,
        index_storage_path=root,
    )
    assert result["focus"]["fallback_triggered"] is False
    assert result["repository_outcomes"][1]["outcome"] == "not_searched"
    assert model.calls == 1


def test_multi_query_falls_back_inside_scope_and_keeps_citation_provenance(tmp_path):
    rows = {
        "idx_a": {"status": "ready", "commit_sha": "a" * 40},
        "idx_b": {"status": "ready", "commit_sha": "b" * 40},
    }
    root, service, model, registry = setup(
        tmp_path,
        {
            "idx_a": [make_result(make_chunk("a1", "only one focused chunk"))],
            "idx_b": [make_result(make_chunk("b1", "fallback one")), make_result(make_chunk("b2", "fallback two"))],
        },
        rows,
    )
    result = run_multi_query(
        question="Explain this code",
        scope=[
            {"repository_id": "repo-a", "index_id": "idx_a", "display_name": "A"},
            {"repository_id": "repo-b", "index_id": "idx_b", "display_name": "B"},
        ],
        focus_repository_ids=["repo-a"],
        top_k=4,
        mode="dense",
        rerank=False,
        query_service=service,
        registry=registry,
        index_storage_path=root,
    )
    assert result["focus"]["fallback_triggered"] is True
    assert result["focus"]["fallback_reason"] == "focused_evidence_insufficient"
    assert result["focus"]["fallback_repository_ids"] == ["repo-b"]
    assert all("repository_id" in citation and "index_id" in citation for citation in result["citations"])
    assert model.calls == 1


def test_multi_query_rejects_focus_outside_scope(tmp_path):
    root, service, model, registry = setup(tmp_path, {}, {})
    try:
        run_multi_query(
            question="Explain this code",
            scope=[{"repository_id": "repo-a", "index_id": "idx_a", "display_name": "A"}],
            focus_repository_ids=["repo-b"],
            top_k=4,
            mode="dense",
            rerank=False,
            query_service=service,
            registry=registry,
            index_storage_path=root,
        )
    except ValueError as exc:
        assert "subset" in str(exc)
    else:
        raise AssertionError("out-of-scope focus must be rejected")


def test_multi_query_reports_all_sources_unavailable(tmp_path):
    root, service, model, registry = setup(tmp_path, {}, {})
    try:
        run_multi_query(
            question="Explain this code",
            scope=[{"repository_id": "repo-a", "index_id": "idx_missing", "display_name": "A"}],
            focus_repository_ids=[],
            top_k=4,
            mode="dense",
            rerank=False,
            query_service=service,
            registry=registry,
            index_storage_path=root,
        )
    except AllIndexesUnavailable as exc:
        assert exc.repository_outcomes[0]["reason"] == "index_not_found"
    else:
        raise AssertionError("all unavailable indexes must raise")
